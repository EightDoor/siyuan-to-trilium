"""构建 Trilium v0.105.0 可识别的内部导入 ZIP。

Trilium 只有在 ZIP 根目录读到 ``!!!meta.json``（formatVersion=2）时，
才会按 meta 还原 note 类型、层级与顺序；否则它会按标题重新排序。
因此本模块生成的 ZIP 根目录直接放置 ``!!!meta.json``，不额外嵌套目录层。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .assets import _normalize_attachment_mime
from .inventory import Inventory
from .models import Document, ExportResult, Notebook

# 归档目录名：仅用于文件系统落盘目录，不参与 notePath。
ARCHIVE_ROOT_NAME = "siyuan-archive"
# Markdown 导出目录名（content_root 的父目录）。
MARKDOWN_EXPORT_DIR_NAME = "markdown-export"
# 附件下载暂存目录名：AssetCollector 先把资源下载到 content_root 的该子目录，
# 归档写出时再按笔记同目录复制，最后删除暂存目录，ZIP 内不再有顶层 assets/。
ARCHIVE_ASSETS_DIR = "assets"
META_FILE_NAME = "!!!meta.json"

# 保留中文，仅替换文件系统不允许的字符
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def build_trilium_zip(
    inventory: Inventory,
    exports: dict[str, ExportResult],
    rewrite_map: dict[str, str],
    archive_root: Path,
) -> Path:
    """生成 Trilium 可识别的内部导入 ZIP，返回 ZIP 路径。

    内容先写入 ``archive_root/<ARCHIVE_ROOT_NAME>/``，再打包；
    ZIP 根目录直接是 ``!!!meta.json`` 及所有笔记文件。
    """

    archive_root.mkdir(parents=True, exist_ok=True)
    content_root = archive_root / ARCHIVE_ROOT_NAME
    entry_order = _write_archive(
        inventory,
        exports,
        rewrite_map,
        content_root,
        assets_root=content_root / ARCHIVE_ASSETS_DIR,
    )

    zip_path = archive_root / "trilium-import.zip"
    _zip_directory(content_root, zip_path, entry_order=entry_order)
    return zip_path


def build_markdown_export(
    inventory: Inventory,
    exports: dict[str, ExportResult],
    rewrite_map: dict[str, str],
    output_dir: Path,
) -> tuple[Path, Path]:
    """生成 Markdown 目录树与可导入 ZIP。

    返回 ``(markdown_export_dir, zip_path)``：
    - 目录：``output_dir/markdown-export/<ARCHIVE_ROOT_NAME>/``
    - ZIP：``output_dir/markdown-export/<ARCHIVE_ROOT_NAME>.zip``（ZIP 根目录即 ``!!!meta.json``）

    附件下载暂存于 ``content_root/<ARCHIVE_ASSETS_DIR>``（见
    :func:`markdown_export_content_root`），归档写出时按笔记同目录复制，
    暂存目录随后删除，因此 ZIP 内附件与所属 Markdown 同级。
    """

    markdown_export_dir = output_dir / MARKDOWN_EXPORT_DIR_NAME
    content_root = markdown_export_content_root(output_dir)
    entry_order = _write_archive(
        inventory,
        exports,
        rewrite_map,
        content_root,
        assets_root=content_root / ARCHIVE_ASSETS_DIR,
    )

    zip_path = markdown_export_dir / f"{ARCHIVE_ROOT_NAME}.zip"
    _zip_directory(content_root, zip_path, entry_order=entry_order)
    return markdown_export_dir, zip_path


def markdown_export_content_root(output_dir: Path) -> Path:
    """返回 Markdown 导出归档内容的落盘根目录。

    附件下载与归档写出共用该目录：附件先暂存于其 ``assets/`` 子目录，
    归档写出把 Markdown/meta 写到该根目录，并把附件复制到所属笔记同目录，
    随后删除暂存 ``assets/``，再由 ``_zip_directory`` 整体打包。
    """

    return output_dir / MARKDOWN_EXPORT_DIR_NAME / ARCHIVE_ROOT_NAME


# ===== 归档内容 =====


def _write_archive(
    inventory: Inventory,
    exports: dict[str, ExportResult],
    rewrite_map: dict[str, str],
    content_root: Path,
    assets_root: Path,
) -> list[str]:
    """写出 ``!!!meta.json``、Markdown 与同目录附件到 content_root。

    返回 ZIP 条目的先序相对路径列表（见 :func:`_archive_walk_order`），供
    ``_zip_directory`` 按父 note 先于子 note 的顺序写包。

    顶层笔记本直接作为 ``meta["files"]`` 的元素，不再包一层 ``root`` 包装 note：
    Trilium ``zip.ts:getMeta`` 从 ``meta.files`` 起逐段匹配 ZIP 路径，若最外层是
    ``root``，第一段笔记本名就匹配不到任何 ``dataFileName``/``dirFileName``，
    会退回按普通文件识别（xlsx 触发 spreadsheet 解析而报错）。
    """

    content_root.mkdir(parents=True, exist_ok=True)

    # 按目录分桶记录已用文件名，保证同目录内文档/附件文件名唯一
    used_filenames: dict[str, set[str]] = {}
    excluded = _excluded_notebooks(inventory)
    notebook_notes: list[dict[str, Any]] = []
    for position, notebook in enumerate(_ordered_notebooks(inventory), start=1):
        if _is_excluded_notebook(notebook, excluded):
            continue
        notebook_notes.append(
            _build_notebook_note(
                notebook,
                position,
                inventory,
                exports,
                rewrite_map,
                content_root,
                assets_root,
                used_filenames,
            )
        )

    meta = {
        "formatVersion": 2,
        "appVersion": "0.0.0",
        "files": notebook_notes,
    }
    (content_root / META_FILE_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 附件已按所属笔记同目录复制，暂存目录不再打包，避免 ZIP 顶层出现 assets/。
    if assets_root == content_root / ARCHIVE_ASSETS_DIR:
        shutil.rmtree(assets_root, ignore_errors=True)

    return _archive_walk_order(meta)


def _build_notebook_note(
    notebook: Notebook,
    position: int,
    inventory: Inventory,
    exports: dict[str, ExportResult],
    rewrite_map: dict[str, str],
    content_root: Path,
    assets_root: Path,
    used_filenames: dict[str, set[str]],
) -> dict[str, Any]:
    """笔记本 note：空内容占位，children 为其下文档（递归）。"""

    safe_name = _safe_name(notebook.name)
    data_file_name = f"{safe_name}.md"
    (content_root / data_file_name).write_text("", encoding="utf-8")

    children: list[dict[str, Any]] = []
    for index, doc_id in enumerate(_top_level_document_ids(inventory, notebook.id), start=1):
        note = _build_document_note(
            doc_id,
            note_path=[notebook.name],
            dir_path=safe_name,
            sibling_index=index,
            inventory=inventory,
            exports=exports,
            rewrite_map=rewrite_map,
            content_root=content_root,
            assets_root=assets_root,
            used_filenames=used_filenames,
        )
        if note is not None:
            children.append(note)

    return {
        "isClone": False,
        "noteId": _stable_note_id(notebook.id),
        "notePath": [notebook.name],
        "title": notebook.name,
        "notePosition": position * 10,
        "prefix": None,
        "isExpanded": False,
        "type": "text",
        "mime": "text/markdown",
        "format": "markdown",
        "attributes": [],
        "dataFileName": data_file_name,
        "dirFileName": safe_name,
        "children": children,
        "attachments": [],
    }


def _build_document_note(
    doc_id: str,
    note_path: list[str],
    dir_path: str,
    sibling_index: int,
    inventory: Inventory,
    exports: dict[str, ExportResult],
    rewrite_map: dict[str, str],
    content_root: Path,
    assets_root: Path,
    used_filenames: dict[str, set[str]],
) -> dict[str, Any] | None:
    """文档 note：写入 Markdown，并把附件复制到所属 Markdown 同目录。

    附件按官方导出格式与所属 Markdown 同级放置，basename 形如
    ``<note_title>_<attachment_basename>``；Trilium ``getMeta`` 解析
    ``<笔记本>/<title>_<att>`` 时，第二段在笔记本 children 中找不到 note，
    会调用 ``getAttachmentMeta`` 遍历 children[].attachments 按 dataFileName
    命中，从而识别为附件而不是普通文件（后者会对 xlsx 触发 spreadsheet 解析）。

    meta 中的 ``dataFileName`` / ``dirFileName`` 只写当前层级名（basename），
    绝不能带父路径：Trilium 按 ``/`` 逐段匹配，带路径前缀时永远匹配不到。
    """

    document = inventory.documents.get(doc_id)
    if document is None:
        return None

    # 同目录按桶去重：文档与附件共用 used，保证同目录内文件名唯一
    used = used_filenames.setdefault(dir_path, set())
    data_filename = _unique_filename(used, _safe_name(document.title))
    used.add(data_filename)
    file_stem = Path(data_filename).stem

    child_ids = _child_document_ids(inventory, doc_id)
    # 有子文档才需要 dirFileName 作为目录入口，且同样只保留当前层名字
    dir_file_name = file_stem if child_ids else None
    child_dir_path = f"{dir_path}/{file_stem}" if dir_path else file_stem

    note: dict[str, Any] = {
        "isClone": False,
        "noteId": document.id,
        "notePath": note_path + [document.title],
        "title": document.title,
        "notePosition": sibling_index * 10,
        "prefix": None,
        "isExpanded": False,
        "type": "text",
        "mime": "text/markdown",
        "format": "markdown",
        "attributes": [],
        "dirFileName": dir_file_name,
        "children": [],
        "attachments": [],
    }

    result = exports.get(doc_id)
    if result is not None and not result.error:
        note["dataFileName"] = data_filename
        doc_dir = content_root / dir_path if dir_path else content_root
        doc_dir.mkdir(parents=True, exist_ok=True)

        attachments, final_names = _write_sibling_attachments(
            document, result, file_stem, doc_dir, assets_root, used
        )
        note["attachments"] = attachments

        target = doc_dir / data_filename
        target.write_text(
            _rewrite_markdown(
                result.markdown,
                _document_rewrite_map(rewrite_map, final_names),
            ),
            encoding="utf-8",
        )
    # 导出缺失或失败的笔记只作为目录占位，不写 dataFileName

    children: list[dict[str, Any]] = []
    for index, child_id in enumerate(child_ids, start=1):
        child = _build_document_note(
            child_id,
            note_path + [document.title],
            child_dir_path,
            index,
            inventory,
            exports,
            rewrite_map,
            content_root,
            assets_root,
            used_filenames,
        )
        if child is not None:
            children.append(child)
    note["children"] = children
    return note


def _write_sibling_attachments(
    document: Document,
    result: ExportResult,
    file_stem: str,
    doc_dir: Path,
    assets_root: Path,
    used: set[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """把文档附件复制到其 Markdown 同目录，返回 attachment meta 与名称映射。

    返回 ``(attachments, final_names)``：``final_names`` 以附件暂存相对路径
    （``asset.relative_path``）为键、同目录最终文件名为值，供 Markdown 重写。
    """

    attachments: list[dict[str, Any]] = []
    final_names: dict[str, str] = {}
    for asset in result.assets:
        att_basename = Path(asset.relative_path).name
        candidate = _safe_name(f"{file_stem}_{att_basename}")
        final_name = _unique_asset_filename(used, candidate)
        used.add(final_name)
        final_names[asset.relative_path] = final_name

        source = assets_root / att_basename
        if source.is_file():
            target = doc_dir / final_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

        mime = _normalize_attachment_mime(
            mimetypes.guess_type(final_name)[0] or "application/octet-stream"
        )
        attachments.append(
            {
                "attachmentId": hashlib.sha1(
                    f"{document.id}::{asset.relative_path}".encode("utf-8")
                ).hexdigest()[:12],
                "title": final_name,
                "role": "file",
                "mime": mime,
                "dataFileName": final_name,
                "position": 10,
            }
        )
    return attachments, final_names


def _document_rewrite_map(
    rewrite_map: dict[str, str], final_names: dict[str, str]
) -> dict[str, str]:
    """把指向本笔记附件的引用改写到同目录最终文件名。

    全局 ``rewrite_map`` 的值为暂存相对路径 ``assets/<name>``；这里同时处理
    原始引用（如 ``assets/foo.png``）与已重写引用（``assets/<sha1>-foo.png``）
    两种情况，使重写后的 Markdown 引用与 ZIP 内实际文件名一致。
    """

    if not final_names:
        return rewrite_map
    local = dict(rewrite_map)
    for key, value in list(rewrite_map.items()):
        final = final_names.get(value)
        if final is not None:
            local[key] = final
    for relative_path, final in final_names.items():
        local[relative_path] = final
    return local


# ===== 名称与顺序辅助 =====


def _ordered_notebooks(inventory: Inventory) -> list[Notebook]:
    """按 lsNotebooks 顺序返回笔记本。

    未登记在 inventory.notebooks 中的文档所属笔记本兜底补一个，避免文档静默丢失。
    """

    notebooks = list(inventory.notebooks.values())
    known = {notebook.id for notebook in notebooks}
    for document in inventory.documents.values():
        if document.notebook_id not in known:
            known.add(document.notebook_id)
            notebooks.append(Notebook(id=document.notebook_id, name=document.notebook_id))
    return notebooks


def _excluded_notebooks(inventory: Inventory) -> set[str]:
    """返回被排除笔记本的 id 与名称集合（兼容 dict / 可迭代两种形态）。"""

    raw = getattr(inventory, "excluded_notebooks", None)
    if isinstance(raw, dict):
        excluded = {str(key) for key in raw}
        excluded.update(str(name) for name in raw.values())
        return excluded
    if raw:
        return {str(item) for item in raw}
    return set()


def _is_excluded_notebook(notebook: Notebook, excluded: set[str]) -> bool:
    return notebook.id in excluded or notebook.name in excluded


def _top_level_document_ids(inventory: Inventory, notebook_id: str) -> list[str]:
    """笔记本下第一层文档，优先使用 document_children 中登记的显式顺序。"""

    registered = _document_children(inventory).get(notebook_id)
    if registered:
        return [doc_id for doc_id in registered if doc_id in inventory.documents]

    documents = inventory.documents
    return [
        document.id
        for document in documents.values()
        if document.notebook_id == notebook_id
        and (not document.parent_id or document.parent_id not in documents)
    ]


def _child_document_ids(inventory: Inventory, parent_id: str) -> list[str]:
    registered = _document_children(inventory).get(parent_id)
    if registered:
        return [doc_id for doc_id in registered if doc_id in inventory.documents]
    return [
        document.id
        for document in inventory.documents.values()
        if document.parent_id == parent_id
    ]


def _document_children(inventory: Inventory) -> dict[str, list[str]]:
    children = getattr(inventory, "document_children", None)
    return children if isinstance(children, dict) else {}


def _archive_walk_order(meta: dict) -> list[str]:
    """按 ``meta.files`` 先序遍历生成 ZIP 条目的相对路径列表。

    顺序：``!!!meta.json`` → 笔记本 Markdown → 文档 Markdown → 该文档附件 →
    子文档（递归）。Trilium 顺序解析 ZIP，父 note 必须先于子 note 出现，否则报
    ``Parent note 'xxx' was not found.``；附件则须紧随所属 Markdown，早于其子文档。

    路径与 ``_build_notebook_note`` / ``_build_document_note`` 的落盘路径完全一致：
    笔记本 Markdown 在 content_root 根目录，文档位于父级 ``dirFileName`` 目录下，
    附件与其所属 Markdown 同目录。
    """

    order: list[str] = [META_FILE_NAME]
    for notebook in meta.get("files", []):
        data_file_name = notebook.get("dataFileName")
        if data_file_name:
            order.append(data_file_name)
        notebook_dir = notebook.get("dirFileName") or ""
        for child in notebook.get("children", []):
            _append_document_order(child, notebook_dir, order)
    return order


def _append_document_order(note: dict, dir_prefix: str, order: list[str]) -> None:
    """先序追加单个文档条目：自身 Markdown → 附件 → 递归子文档。

    ``dir_prefix`` 是当前文档 Markdown 所在目录（已安全化），与
    ``_build_document_note`` 落盘时的 ``dir_path`` 一致。
    """

    data_file_name = note.get("dataFileName")
    if data_file_name:
        order.append(_join_zip_path(dir_prefix, data_file_name))

    # 附件与所属 Markdown 同目录，紧随其后、先于子文档
    for attachment in note.get("attachments", []):
        attachment_name = attachment.get("dataFileName")
        if attachment_name:
            order.append(_join_zip_path(dir_prefix, attachment_name))

    # 子文档位于父文档 dirFileName 目录下；导出失败但仍有子文档时 dirFileName 已设置
    child_dir_name = note.get("dirFileName")
    if not child_dir_name and data_file_name:
        child_dir_name = Path(data_file_name).stem
    child_prefix = (
        _join_zip_path(dir_prefix, child_dir_name) if child_dir_name else dir_prefix
    )
    for child in note.get("children", []):
        _append_document_order(child, child_prefix, order)


def _join_zip_path(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _stable_note_id(raw: str) -> str:
    """取 id 的稳定后缀并去掉破折号，作为笔记本 noteId。"""

    candidate = raw.rsplit("-", 1)[-1] if "-" in raw else raw
    candidate = re.sub(r"[^0-9A-Za-z_]", "", candidate)
    return candidate or "note"


def _safe_name(name: str) -> str:
    """ASCII 安全化：替换文件系统非法字符，保留中文。"""

    cleaned = _UNSAFE_FILENAME.sub("_", name).strip().rstrip(". ")
    return cleaned or "untitled"


def _unique_filename(used: set[str], stem: str, suffix: str = ".md") -> str:
    """返回在 used 中不存在的 filename（stem+suffix），冲突时加 ' (n)'。

    不修改传入的 used，调用方需自行 ``used.add(filename)`` 登记。
    """

    candidate = f"{stem}{suffix}"
    if candidate not in used:
        return candidate
    index = 2
    while f"{stem} ({index}){suffix}" in used:
        index += 1
    return f"{stem} ({index}){suffix}"


def _unique_asset_filename(used: set[str], candidate: str) -> str:
    """返回在 used 中不存在的附件文件名，冲突时在扩展名前加 ' (n)'。"""

    if candidate not in used:
        return candidate
    stem = Path(candidate).stem
    suffix = "".join(Path(candidate).suffixes)
    index = 2
    while f"{stem} ({index}){suffix}" in used:
        index += 1
    return f"{stem} ({index}){suffix}"


# ===== Markdown / ZIP =====


def _rewrite_markdown(text: str, rewrite_map: dict[str, str]) -> str:
    """将本地附件引用改写为同目录文件名（不写路径前缀）。

    附件与所属 Markdown 同目录，``rewrite_map`` 的值已是最终文件名，因此
    直接替换即可；未命中的引用保持原样。
    """

    image_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    html_attr_pattern = re.compile(r"""(?:href|src)=["']([^"']+)["']""", re.IGNORECASE)
    html_bg_pattern = re.compile(r"""url\(([^)\s"']+)\)""", re.IGNORECASE)

    def resolve(ref: str) -> str:
        return rewrite_map.get(ref, ref)

    def replace_image(match: re.Match[str]) -> str:
        alt = match.group(0).split("](")[0][2:]
        return f"![{alt}]({resolve(match.group(1))})"

    def replace_link(match: re.Match[str]) -> str:
        label = match.group(0).split("](")[0][1:]
        return f"[{label}]({resolve(match.group(1))})"

    def replace_html_attr(match: re.Match[str]) -> str:
        ref = match.group(1)
        target = resolve(ref)
        if target == ref:
            return match.group(0)
        offset = match.start(1) - match.start()
        return match.group(0)[:offset] + target + match.group(0)[offset + len(ref):]

    text = link_pattern.sub(replace_link, image_pattern.sub(replace_image, text))
    text = html_attr_pattern.sub(replace_html_attr, text)
    text = html_bg_pattern.sub(replace_html_attr, text)
    return text


def _zip_directory(
    source_dir: Path,
    zip_path: Path,
    *,
    nest_under_root: bool = False,
    entry_order: list[str] | None = None,
) -> None:
    """把 source_dir 内文件按 ``entry_order`` 打包进 ZIP。

    - ``nest_under_root=False``（采用）：ZIP 根目录直接是 ``!!!meta.json``，
      Trilium 才能识别 meta 中的 note 类型与顺序。
    - ``nest_under_root=True``：额外套一层 source_dir 名称，仅用于对比验证。
    - ``entry_order``：内容根目录下的相对路径列表，按序写入；缺文件立即报错，
      避免静默丢失。为 ``None`` 时退回字典序遍历（仅用于兼容旧调用）。
    """

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if entry_order is not None:
        missing = [rel for rel in entry_order if not (source_dir / rel).is_file()]
        if missing:
            raise FileNotFoundError(f"ZIP entry missing: {missing[:3]}")
        files = [source_dir / rel for rel in entry_order]
        relatives = list(entry_order)
    else:
        files = [path for path in sorted(source_dir.rglob("*")) if path.is_file()]
        relatives = [path.relative_to(source_dir).as_posix() for path in files]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, relative in zip(files, relatives):
            arcname = f"{source_dir.name}/{relative}" if nest_under_root else relative
            zf.write(path, arcname)
