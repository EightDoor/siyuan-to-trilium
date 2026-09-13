"""思源笔记树两遍扫描：先建立映射，再导出 Markdown。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Document, ExportResult, Notebook
from .progress import ProgressReporter
from .siyuan import SiyuanClient

# 默认排除的笔记本名称：默认为空，即不排除任何笔记本（含归档）。
# 保留常量便于 CLI 或调用方显式传入。
DEFAULT_EXCLUDED_NOTEBOOKS: list[str] = []


@dataclass
class Inventory:
    notebooks: dict[str, Notebook] = field(default_factory=dict)
    documents: dict[str, Document] = field(default_factory=dict)
    document_children: dict[str, list[str]] = field(default_factory=dict)
    doc_path_to_id: dict[str, str] = field(default_factory=dict)
    block_to_document: dict[str, str] = field(default_factory=dict)
    exports: dict[str, ExportResult] = field(default_factory=dict)
    # 被排除笔记本映射：id -> name
    excluded_notebooks: dict[str, str] = field(default_factory=dict)
    # 被排除笔记本下的文档总数
    excluded_documents_count: int = 0


class InventoryBuilder:
    def __init__(
        self,
        client: SiyuanClient,
        progress: Optional[ProgressReporter] = None,
        excluded_notebooks: Optional[list[str]] = None,
    ):
        self._client = client
        self._progress = progress or ProgressReporter(enabled=False)
        # None 表示使用默认值（默认不排除任何笔记本）；显式传入空列表同样不排除
        self._excluded_notebooks = (
            list(excluded_notebooks)
            if excluded_notebooks is not None
            else list(DEFAULT_EXCLUDED_NOTEBOOKS)
        )

    def build_inventory(self) -> Inventory:
        inventory = Inventory()
        excluded_names = set(self._excluded_notebooks)
        notebooks = self._client.list_notebooks()
        total = len(notebooks)
        for idx, nb in enumerate(notebooks, start=1):
            if not isinstance(nb, dict) or "id" not in nb or "name" not in nb:
                continue
            notebook = Notebook(id=nb["id"], name=nb["name"])
            self._progress.update(
                "siyuan.scan",
                idx,
                total,
                f"扫描笔记本 {notebook.name}",
            )
            # 第一步即跳过被排除笔记本：登记黑名单并统计其文档数，不写入索引
            if notebook.name in excluded_names:
                inventory.excluded_notebooks[notebook.id] = notebook.name
                inventory.excluded_documents_count += self._count_documents(notebook.id)
                continue
            inventory.notebooks[notebook.id] = notebook
            self._index_notebook(notebook.id, inventory)
        return inventory

    def export_all(self, inventory: Inventory) -> None:
        for doc_id, doc in inventory.documents.items():
            inventory.exports[doc_id] = self._export_document(doc)

    def export_one(self, document: Document) -> ExportResult:
        return self._export_document(document)

    # ===== Pass 1 =====

    def _index_notebook(self, notebook_id: str, inventory: Inventory) -> None:
        """构建完整文档树。

        思路：用 SQL 一次拉取笔记本下所有 ``type='d'`` 文档（包含 ``hpath``），
        然后根据 ``hpath`` 拆分父子路径。SiYuan SQL 的 ``blocks.parent_id``
        对文档块总是为空，``hpath`` 才是真实父子结构。
        """

        try:
            rows = self._client.query_sql(
                f"SELECT id, hpath FROM blocks "
                f"WHERE box='{notebook_id}' AND type='d' "
                f"ORDER BY hpath LIMIT 100000"
            )
        except Exception:
            rows = []

        if isinstance(rows, list) and rows:
            self._index_via_hpath(notebook_id, rows, inventory)
        # 块引用映射（思源中 `siyuan://blocks/<id>` 主要指向块而不是文档根，
        # 这里仍用 SQL 兜底，保持历史行为）
        self._index_blocks_in_notebook(notebook_id, inventory)

    def _count_documents(self, notebook_id: str) -> int:
        """统计被排除笔记本下的文档数，仅计数不写入 inventory。"""

        try:
            rows = self._client.query_sql(
                f"SELECT id FROM blocks WHERE box='{notebook_id}' AND type='d' "
                f"LIMIT 100000"
            )
        except Exception:
            rows = []
        if isinstance(rows, list) and rows:
            return sum(
                1
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            )
        return self._count_via_listing(notebook_id, "/")

    def _count_via_listing(self, notebook_id: str, path: str) -> int:
        try:
            payload = self._client.list_docs_by_path(notebook_id, path)
        except Exception:
            return 0
        files = payload.get("files", []) if isinstance(payload, dict) else []
        if not isinstance(files, list):
            return 0
        count = 0
        for entry in files:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            count += 1
            sub_count = entry.get("subFileCount", 0)
            if isinstance(sub_count, int) and sub_count > 0:
                name = entry.get("name", "Untitled")
                hpath = f"{path.rstrip('/')}/{name}"
                count += self._count_via_listing(notebook_id, hpath)
        return count

    def _index_blocks_in_notebook(self, notebook_id: str, inventory: Inventory) -> None:
        """通过 SQL 索引非文档块到其所属文档。"""

        try:
            rows = self._client.query_sql(
                f"SELECT id, root_id FROM blocks WHERE box='{notebook_id}' AND type != 'd' "
                f"LIMIT 100000"
            )
        except Exception:
            return
        for row in rows:
            block_id = row.get("id")
            root_id = row.get("root_id")
            if not isinstance(block_id, str):
                continue
            target = root_id if isinstance(root_id, str) else block_id
            inventory.block_to_document[block_id] = target

    def _index_via_listing(
        self,
        notebook_id: str,
        path: str,
        parent_id: Optional[str],
        inventory: Inventory,
    ) -> None:
        # 旧路径已弃用：listDocsByPath 不返回子文档 path，无法用路径递归。
        # 保留作为兜底：若 SQL 不可用则尝试逐层 listDocsByPath，但子级依然缺失。
        return

    def _index_via_hpath(
        self,
        notebook_id: str,
        rows: list[dict],
        inventory: Inventory,
    ) -> None:
        """用 SQL 返回的 hpath 拆解出父子关系，构建多级文档树。"""

        # 先建立 doc_id 与 hpath 的映射（hpath 形如 /A/B/C）
        docs_by_hpath: dict[str, tuple[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            doc_id = row.get("id")
            hpath = row.get("hpath")
            if not isinstance(doc_id, str) or not isinstance(hpath, str):
                continue
            title = hpath.rstrip("/").rsplit("/", 1)[-1] or hpath.lstrip("/") or "Untitled"
            document = Document(
                id=doc_id,
                notebook_id=notebook_id,
                title=title,
                path=hpath,
                parent_id=None,
            )
            inventory.documents[doc_id] = document
            inventory.doc_path_to_id[hpath] = doc_id
            inventory.block_to_document[doc_id] = doc_id
            inventory.document_children.setdefault(doc_id, [])
            docs_by_hpath[hpath] = (doc_id, title)

        # 按 hpath 字典序排序保证兄弟顺序稳定（思源 UI 默认按 hpath 排序）
        for hpath in sorted(docs_by_hpath):
            doc_id, _ = docs_by_hpath[hpath]
            # 父 hpath：去掉最后一段
            if hpath == "/":
                parent_hpath = None
            else:
                parent_hpath = hpath.rsplit("/", 1)[0] or "/"
            parent_id: Optional[str]
            if parent_hpath is None or parent_hpath == "/":
                parent_id = None
            else:
                parent_doc = docs_by_hpath.get(parent_hpath)
                parent_id = parent_doc[0] if parent_doc else None
            # 写入文档对象的 parent_id
            inventory.documents[doc_id].parent_id = parent_id
            # 父节点的 children 列表
            if parent_id:
                inventory.document_children.setdefault(parent_id, []).append(doc_id)
            else:
                inventory.document_children.setdefault(notebook_id, []).append(doc_id)

    # ===== Pass 2 =====

    def _export_document(self, document: Document) -> ExportResult:
        result = ExportResult(document=document)
        try:
            data = self._client.export_md(document.id)
        except Exception as exc:
            result.error = str(exc)
            return result

        markdown = data.get("content", "") if isinstance(data, dict) else ""
        if not isinstance(markdown, str):
            markdown = ""
        result.markdown = markdown
        return result