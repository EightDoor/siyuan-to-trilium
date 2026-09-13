"""通过 ETAPI 直接在 Trilium 中按层级和顺序创建 note。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import requests

from .models import Document, ExportResult
from .progress import ProgressReporter


@dataclass
class TriliumNoteHandle:
    """记录 Trilium 已创建 note 的 ID 与路径，供子文档和链接替换使用。"""

    trilium_id: str
    title: str
    path: str
    parent_trilium_id: Optional[str] = None
    note_position: int = 10


class TriliumImporter:
    """基于 ETAPI 在 sandbox 下按层级与兄弟顺序创建笔记。"""

    def __init__(
        self,
        etapi_url: str,
        token: str,
        sandbox_id: str,
        progress: Optional[ProgressReporter] = None,
        request_timeout: float = 30.0,
    ):
        self._url = etapi_url
        self._token = token
        self._sandbox_id = sandbox_id
        self._progress = progress or ProgressReporter(enabled=False)
        self._timeout = request_timeout
        self._handles: dict[str, TriliumNoteHandle] = {}
        self._path_to_handle: dict[str, TriliumNoteHandle] = {}
        self._position_counter: dict[str, int] = {}

    @property
    def handles(self) -> dict[str, TriliumNoteHandle]:
        return self._handles

    def import_documents(
        self,
        inventory_docs: dict[str, Document],
        exports: dict[str, ExportResult],
        rewrite_map: dict[str, str],
        children_index: dict[str, list[str]],
    ) -> dict[str, str]:
        """按思源顺序创建笔记，返回 doc_id -> trilium_note_id 的映射。"""

        # 1. 按笔记本分组并按 hpath 排序
        notebook_groups: dict[str, list[Document]] = {}
        for doc in inventory_docs.values():
            notebook_groups.setdefault(doc.notebook_id, []).append(doc)

        doc_to_trilium: dict[str, str] = {}
        for notebook_id, docs in notebook_groups.items():
            sorted_docs = self._sort_documents(docs, children_index)
            root_handle = self._create_notebook_root(notebook_id)
            if not root_handle:
                continue
            self._position_counter[root_handle.trilium_id] = 10
            for doc in sorted_docs:
                handle = self._create_doc_chain(
                    doc,
                    root_handle,
                    exports.get(doc.id),
                    rewrite_map,
                    children_index,
                )
                if handle:
                    doc_to_trilium[doc.id] = handle.trilium_id
                    self._handles[doc.id] = handle
                    self._path_to_handle[doc.path] = handle
                    self._progress.update(
                        "trilium.import",
                        len(self._handles),
                        len(inventory_docs),
                        doc.path,
                    )
        return doc_to_trilium

    # ===== Sort =====

    def _sort_documents(
        self,
        docs: list[Document],
        children_index: dict[str, list[str]],
    ) -> list[Document]:
        """按父级分组、按子顺序稳定排序。"""

        doc_by_id = {d.id: d for d in docs}
        ordered: list[Document] = []
        visited: set = set()

        def visit(doc: Document) -> None:
            if doc.id in visited:
                return
            visited.add(doc.id)
            ordered.append(doc)
            for child_id in children_index.get(doc.id, []):
                child = doc_by_id.get(child_id)
                if child:
                    visit(child)

        roots = [d for d in docs if not d.parent_id or d.parent_id not in doc_by_id]
        roots.sort(key=lambda d: d.path)
        for root in roots:
            visit(root)

        # 处理孤立文档
        for doc in docs:
            if doc.id not in visited:
                ordered.append(doc)
        return ordered

    # ===== Creation =====

    def _create_notebook_root(self, notebook_id: str) -> Optional[TriliumNoteHandle]:
        """在 sandbox 下为每个笔记本创建一个一级容器 note。"""

        notebook_title = f"notebook-{notebook_id[:8]}"
        response = self._post(
            "/create-note",
            payload={
                "parentNoteId": self._sandbox_id,
                "title": notebook_title,
                "type": "text",
                "mime": "text/html",
                "content": f"<p>由 siyuan-to-trilium 从思源笔记本 {notebook_id} 迁移。</p>",
                "notePosition": self._next_position(self._sandbox_id),
            },
        )
        note_id = self._extract_note_id(response)
        if not note_id:
            return None
        return TriliumNoteHandle(
            trilium_id=note_id,
            title=notebook_title,
            path=f"/{notebook_title}",
            parent_trilium_id=self._sandbox_id,
            note_position=10,
        )

    def _create_doc_chain(
        self,
        doc: Document,
        root_handle: TriliumNoteHandle,
        export_result: Optional[ExportResult],
        rewrite_map: dict[str, str],
        children_index: dict[str, list[str]],
    ) -> Optional[TriliumNoteHandle]:
        """递归创建文档。父文档若未在 children_index 中存在，则挂到根。"""

        parent_handle = root_handle
        if doc.parent_id and doc.parent_id in self._handles:
            parent_handle = self._handles[doc.parent_id]

        markdown = export_result.markdown if export_result and not export_result.error else ""
        rewritten = self._rewrite_markdown(markdown, rewrite_map) if markdown else ""

        response = self._post(
            "/create-note",
            payload={
                "parentNoteId": parent_handle.trilium_id,
                "title": doc.title,
                "type": "text",
                "mime": "text/markdown",
                "content": rewritten,
                "notePosition": self._next_position(parent_handle.trilium_id),
            },
        )
        note_id = self._extract_note_id(response)
        if not note_id:
            return None

        # 后续将 type 升级为 markdown，触发 Trilium Markdown 预览
        self._patch_note_type(note_id, "markdown", "text/markdown")

        # 上传附件（如有）
        if export_result:
            for asset in export_result.assets:
                self._upload_attachment(note_id, asset)

        return TriliumNoteHandle(
            trilium_id=note_id,
            title=doc.title,
            path=doc.path,
            parent_trilium_id=parent_handle.trilium_id,
            note_position=10,
        )

    def _upload_attachment(self, note_id: str, asset) -> None:
        """通过 ETAPI 上传单个附件。"""

        url = f"{self._url}/attachments"
        headers = {"Authorization": self._token} if self._token else {}
        try:
            with open(asset.relative_path, "rb") as fh:
                files = {"file": (Path(asset.relative_path).name, fh, "application/octet-stream")}
                data = {"ownerId": note_id, "role": "file", "mime": "application/octet-stream"}
                response = requests.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=self._timeout,
                )
            if response.status_code not in (200, 201):
                # 附件上传失败不阻塞导入
                self._progress.update(
                    "trilium.attachment",
                    0,
                    1,
                    f"附件上传失败: {asset.relative_path} ({response.status_code})",
                )
        except Exception as exc:
            self._progress.update(
                "trilium.attachment",
                0,
                1,
                f"附件异常: {asset.relative_path} ({exc})",
            )

    # ===== Helpers =====

    def _next_position(self, parent_id: str) -> int:
        current = self._position_counter.get(parent_id, 0)
        current += 10
        self._position_counter[parent_id] = current
        return current

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._url}{path}"
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = self._token
        response = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"ETAPI 调用失败 {path}: {response.status_code} {response.text[:200]}"
            )
        if not response.text:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    @staticmethod
    def _extract_note_id(response: dict) -> Optional[str]:
        if not isinstance(response, dict):
            return None
        note = response.get("note") or response.get("branch", {})
        if isinstance(note, dict):
            value = note.get("noteId")
            if isinstance(value, str):
                return value
        if "noteId" in response and isinstance(response["noteId"], str):
            return response["noteId"]
        return None

    @staticmethod
    def _rewrite_markdown(text: str, rewrite_map: dict[str, str]) -> str:
        import re

        image_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
        link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")

        def replace_image(match: re.Match[str]) -> str:
            alt = match.group(0).split("](")[0][2:]
            target = rewrite_map.get(match.group(1), match.group(1))
            return f"![{alt}]({target})"

        def replace_link(match: re.Match[str]) -> str:
            label = match.group(0).split("](")[0][1:]
            target = rewrite_map.get(match.group(1), match.group(1))
            return f"[{label}]({target})"

        return link_pattern.sub(replace_link, image_pattern.sub(replace_image, text))

    def _patch_note_type(self, note_id: str, note_type: str, mime: str) -> None:
        """将 note 类型从 text 升级到 markdown。"""

        url = f"{self._url}/notes/{note_id}"
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = self._token
        try:
            requests.patch(
                url,
                json={"type": note_type, "mime": mime},
                headers=headers,
                timeout=self._timeout,
            )
        except Exception:
            # 类型升级失败不应阻塞导入
            pass
        try:
            requests.patch(
                url,
                json={"type": note_type, "mime": mime},
                headers=headers,
                timeout=self._timeout,
            )
        except Exception:
            # 类型升级失败不应阻塞导入
            pass


# 在文件末尾注入 Path 引用以避免顶层 import
from pathlib import Path  # noqa: E402