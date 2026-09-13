"""trilium_archive 测试。"""

import html
import json
import zipfile
from pathlib import Path
from typing import Optional

from src.models import Asset, Document, ExportResult, Notebook
from src.inventory import Inventory
from src.trilium_archive import (
    ARCHIVE_ASSETS_DIR,
    build_markdown_export,
    build_trilium_zip,
    markdown_export_content_root,
)

# 可触发 Trilium spreadsheet 解析的扩展名（命中即需被识别为附件）。
SPREADSHEET_SUFFIXES = {".xlsx", ".xls", ".csv"}


def _export(
    doc_id: str,
    path: str,
    markdown: str,
    assets: Optional[list[Asset]] = None,
) -> ExportResult:
    return ExportResult(
        document=Document(
            id=doc_id,
            notebook_id="nb-1",
            title=path.strip("/").split("/")[-1],
            path=path,
        ),
        markdown=markdown,
        assets=assets or [],
    )


def _make_inventory(
    notebooks: list[Notebook],
    documents: list[Document],
    exports: dict[str, ExportResult],
    document_children: Optional[dict[str, list[str]]] = None,
    excluded_notebooks: Optional[dict[str, str]] = None,
) -> Inventory:
    inv = Inventory()
    for notebook in notebooks:
        inv.notebooks[notebook.id] = notebook
    for document in documents:
        inv.documents[document.id] = document
    inv.exports = exports
    inv.document_children = document_children or {}
    if excluded_notebooks is not None:
        inv.excluded_notebooks = excluded_notebooks
    return inv


def _walk_notes(note: dict) -> list[dict]:
    notes = [note]
    for child in note.get("children", []):
        notes.extend(_walk_notes(child))
    return notes


def _all_notes(files: list[dict]) -> list[dict]:
    """展开 ``meta["files"]``（顶层笔记本列表）下的所有 note。"""

    notes: list[dict] = []
    for note in files:
        notes.extend(_walk_notes(note))
    return notes


def _write_staged_asset(content_root: Path, relative_path: str, data: bytes = b"bytes") -> None:
    """模拟 AssetCollector 已把附件下载到 content_root/assets/ 下。"""

    target = content_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _note_by_data_file(files: list[dict], data_file_name: str) -> dict:
    for note in _all_notes(files):
        if note.get("dataFileName") == data_file_name:
            return note
    raise AssertionError(f"未找到 dataFileName={data_file_name} 的 note")


def test_build_zip_creates_markdown_files(tmp_path: Path):
    inv = Inventory()
    inv.notebooks["nb-1"] = Notebook(id="nb-1", name="开发")
    inv.documents["doc-a"] = Document(
        id="doc-a", notebook_id="nb-1", title="Spring Boot", path="/开发/Java/Spring Boot"
    )
    inv.documents["doc-b"] = Document(
        id="doc-b", notebook_id="nb-1", title="MyBatis", path="/开发/Java/MyBatis"
    )
    inv.exports = {
        "doc-a": _export("doc-a", "/开发/Java/Spring Boot", "# Spring Boot\n\nhello"),
        "doc-b": _export("doc-b", "/开发/Java/MyBatis", "# MyBatis\n\nfoo"),
    }

    zip_path = build_trilium_zip(inv, inv.exports, {}, tmp_path)

    assert zip_path.exists()
    assert zip_path.suffix == ".zip"

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "!!!meta.json" in names
        assert "开发/Spring Boot.md" in names
        assert "开发/MyBatis.md" in names
        assert not any("1-Spring Boot.md" in name for name in names)
        assert not any("2-MyBatis.md" in name for name in names)


def test_markdown_rewrite_replaces_local_links():
    from src.trilium_archive import _rewrite_markdown

    text = "see ![](assets/foo.png) and [doc](doc.md)"
    out = _rewrite_markdown(text, {"assets/foo.png": "Page_abc-foo.png"})
    assert "(Page_abc-foo.png)" in out
    assert "doc.md" in out  # 未在 map 中则保留


def test_build_markdown_export_meta_format(tmp_path: Path):
    notebooks = [Notebook(id="nb-1", name="开发"), Notebook(id="nb-arch", name="归档")]
    documents = [
        Document(id="doc-a", notebook_id="nb-1", title="A", path="/开发/A"),
        Document(id="doc-b", notebook_id="nb-1", title="B", path="/开发/B"),
        Document(id="doc-arch", notebook_id="nb-arch", title="Archived", path="/归档/Archived"),
    ]
    exports = {
        "doc-a": _export("doc-a", "/开发/A", "# A"),
        "doc-b": _export("doc-b", "/开发/B", "# B"),
        "doc-arch": _export("doc-arch", "/归档/Archived", "# Archived"),
    }
    inv = _make_inventory(
        notebooks,
        documents,
        exports,
        document_children={"nb-1": ["doc-a", "doc-b"], "nb-arch": ["doc-arch"]},
        excluded_notebooks={"nb-arch": "归档"},
    )

    markdown_export_dir, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    assert markdown_export_dir == tmp_path / "markdown-export"
    assert zipfile.is_zipfile(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "!!!meta.json" in names
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

        assert meta["formatVersion"] == 2
        # meta.files 直接是顶层笔记本，不再包 root 包装 note
        notebook_titles = [notebook["title"] for notebook in meta["files"]]
        assert "归档" not in notebook_titles
        assert notebook_titles == ["开发"]

        notebook = meta["files"][0]
        assert notebook["notePath"] == ["开发"]
        # 不再有虚拟 assets note
        assert all(child.get("dirFileName") != ARCHIVE_ASSETS_DIR for child in notebook["children"])

        child_titles = [child["title"] for child in notebook["children"]]
        assert child_titles == ["A", "B"]

        positions = [child["notePosition"] for child in notebook["children"]]
        assert all(later > earlier for earlier, later in zip(positions, positions[1:]))

        # 所有 note 的 dataFileName 均为当前层级的 Markdown 文件名（basename），
        # 且对应文件确实存在于 ZIP 中（按落盘 basename 匹配）。
        zip_basenames = {Path(name).name for name in names}
        for note in _all_notes(meta["files"]):
            data_file_name = note.get("dataFileName")
            if data_file_name is None:
                continue
            assert data_file_name.endswith(".md")
            assert "/" not in data_file_name
            assert data_file_name in zip_basenames


def test_per_note_sibling_attachments(tmp_path: Path):
    """附件应与所属 Markdown 同目录，文件名 <title>_<att_basename>。"""

    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, "assets/119e846694-book.xlsx", b"xlsx-bytes")
    _write_staged_asset(content_root, "assets/abc1234567-pic.png", b"png-bytes")

    document = Document(
        id="doc-a", notebook_id="nb-1", title="东乌旗大屏", path="/凌动科技/东乌旗大屏"
    )
    assets = [
        Asset(
            relative_path="assets/119e846694-book.xlsx",
            source_path="/assets/book.xlsx",
            doc_id="doc-a",
            doc_path="/凌动科技/东乌旗大屏",
        ),
        Asset(
            relative_path="assets/abc1234567-pic.png",
            source_path="/assets/pic.png",
            doc_id="doc-a",
            doc_path="/凌动科技/东乌旗大屏",
        ),
    ]
    exports = {
        "doc-a": _export(
            "doc-a",
            "/凌动科技/东乌旗大屏",
            "![](assets/pic.png)\n\n[book](assets/book.xlsx)",
            assets=assets,
        )
    }
    rewrite_map = {
        "assets/pic.png": "assets/abc1234567-pic.png",
        "assets/book.xlsx": "assets/119e846694-book.xlsx",
    }
    inv = _make_inventory(
        [Notebook(id="nb-1", name="凌动科技")],
        [document],
        exports,
        document_children={"nb-1": ["doc-a"]},
    )

    _, zip_path = build_markdown_export(inv, exports, rewrite_map, tmp_path)

    xlsx_name = "东乌旗大屏_119e846694-book.xlsx"
    png_name = "东乌旗大屏_abc1234567-pic.png"

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))
        markdown = zf.read("凌动科技/东乌旗大屏.md").decode("utf-8")

    assert "凌动科技/东乌旗大屏.md" in names
    assert f"凌动科技/{xlsx_name}" in names
    assert f"凌动科技/{png_name}" in names
    # 不再有顶层 assets/ 目录
    assert not any(name == ARCHIVE_ASSETS_DIR or name.startswith(f"{ARCHIVE_ASSETS_DIR}/") for name in names)

    # meta.files 是顶层笔记本，文档是其 children
    assert [nb["title"] for nb in meta["files"]] == ["凌动科技"]
    assert [child["title"] for child in meta["files"][0]["children"]] == ["东乌旗大屏"]

    note = _note_by_data_file(meta["files"], "东乌旗大屏.md")
    assert note["notePath"] == ["凌动科技", "东乌旗大屏"]
    data_file_names = {att["dataFileName"] for att in note["attachments"]}
    assert data_file_names == {xlsx_name, png_name}
    for att in note["attachments"]:
        assert att["attachmentId"]
        assert "/" not in att["dataFileName"]

    # Markdown 引用为同目录 basename，不带 assets/ 前缀
    assert f"({xlsx_name})" in markdown
    assert f"({png_name})" in markdown
    assert "assets/" not in markdown


def test_filename_no_numeric_prefix(tmp_path: Path):
    inv = _make_inventory(
        [Notebook(id="nb-1", name="开发")],
        [
            Document(
                id="doc-a",
                notebook_id="nb-1",
                title="Spring Boot",
                path="/开发/Spring Boot",
            )
        ],
        {"doc-a": _export("doc-a", "/开发/Spring Boot", "# Spring Boot")},
        document_children={"nb-1": ["doc-a"]},
    )

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    assert "开发/Spring Boot.md" in names
    assert not any("1-Spring Boot.md" in name for name in names)

    note = _all_notes(meta["files"])[-1]
    assert note["dataFileName"] == "Spring Boot.md"


def test_filename_collision_renamed(tmp_path: Path):
    documents = [
        Document(id="doc-1", notebook_id="nb-1", title="Note", path="/开发/Note"),
        Document(id="doc-2", notebook_id="nb-1", title="Note", path="/开发/Note"),
    ]
    exports = {
        "doc-1": _export("doc-1", "/开发/Note", "# first"),
        "doc-2": _export("doc-2", "/开发/Note", "# second"),
    }
    inv = _make_inventory(
        [Notebook(id="nb-1", name="开发")],
        documents,
        exports,
        document_children={"nb-1": ["doc-1", "doc-2"]},
    )

    _, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    notebook = meta["files"][0]
    data_file_names = [child["dataFileName"] for child in notebook["children"]]
    assert data_file_names == ["Note.md", "Note (2).md"]
    zip_basenames = {Path(name).name for name in names}
    for data_file_name in data_file_names:
        assert data_file_name in zip_basenames


def test_excluded_notebook_excluded_from_meta(tmp_path: Path):
    notebooks = [Notebook(id="nb-1", name="保留"), Notebook(id="nb-2", name="归档")]
    documents = [
        Document(id="doc-a", notebook_id="nb-1", title="A", path="/保留/A"),
        Document(id="doc-arch", notebook_id="nb-2", title="Archived", path="/归档/Archived"),
    ]
    exports = {
        "doc-a": _export("doc-a", "/保留/A", "# A"),
        "doc-arch": _export("doc-arch", "/归档/Archived", "# Archived"),
    }
    inv = _make_inventory(
        notebooks,
        documents,
        exports,
        document_children={"nb-1": ["doc-a"], "nb-2": ["doc-arch"]},
        excluded_notebooks={"nb-2": "归档"},
    )

    _, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        assert zipfile.is_zipfile(zip_path)
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    assert [notebook["title"] for notebook in meta["files"]] == ["保留"]
    all_titles = [note["title"] for note in _all_notes(meta["files"])]
    assert "归档" not in all_titles


def test_attachment_placed_beside_document(tmp_path: Path):
    """附件落盘在文档 Markdown 同目录，不再有顶层 assets/。"""

    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, "assets/abc-pic.png", b"png-bytes")

    document = Document(id="doc-a", notebook_id="nb-1", title="Page", path="/开发/Page")
    asset = Asset(
        relative_path="assets/abc-pic.png",
        source_path="/assets/abc-pic.png",
        doc_id="doc-a",
        doc_path="/开发/Page",
    )
    exports = {"doc-a": _export("doc-a", "/开发/Page", "# Page", assets=[asset])}
    inv = _make_inventory(
        [Notebook(id="nb-1", name="开发")],
        [document],
        exports,
        document_children={"nb-1": ["doc-a"]},
    )

    _, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    note = _note_by_data_file(meta["files"], "Page.md")
    assert note["dataFileName"] == "Page.md"
    assert [att["dataFileName"] for att in note["attachments"]] == ["Page_abc-pic.png"]
    assert "开发/Page.md" in names
    assert "开发/Page_abc-pic.png" in names
    assert not any(name.startswith(f"{ARCHIVE_ASSETS_DIR}/") for name in names)


def test_url_encoded_attachment_in_archive(tmp_path: Path):
    """URL 编码的附件名不做解码，落盘文件名与 meta 的 dataFileName 保持一致。"""

    encoded_name = "9m92f68-CleanShot%202024-09-13.png"
    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, f"assets/{encoded_name}", b"png-bytes")

    document = Document(id="doc-a", notebook_id="nb-1", title="Page", path="/开发/Page")
    asset = Asset(
        relative_path=f"assets/{encoded_name}",
        source_path="/assets/CleanShot 2024-09-13.png",
        doc_id="doc-a",
        doc_path="/开发/Page",
    )
    exports = {
        "doc-a": _export(
            "doc-a",
            "/开发/Page",
            "# Page\n\n![](assets/CleanShot%202024-09-13.png)",
            assets=[asset],
        )
    }
    inv = _make_inventory(
        [Notebook(id="nb-1", name="开发")],
        [document],
        exports,
        document_children={"nb-1": ["doc-a"]},
    )

    _, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    expected_name = f"Page_{encoded_name}"
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    assert f"开发/{expected_name}" in names
    attachment = _note_by_data_file(meta["files"], "Page.md")["attachments"][0]
    assert attachment["dataFileName"] == expected_name
    assert isinstance(attachment.get("attachmentId"), str)
    assert attachment["attachmentId"]


def test_attachment_id_is_stable(tmp_path: Path):
    """同一文档重复构建时，基于 note_id + 附件路径的 attachmentId 应保持一致。"""

    def build(output_dir: Path) -> str:
        content_root = markdown_export_content_root(output_dir)
        _write_staged_asset(content_root, "assets/abc-pic.png", b"png-bytes")

        document = Document(id="doc-a", notebook_id="nb-1", title="Page", path="/开发/Page")
        asset = Asset(
            relative_path="assets/abc-pic.png",
            source_path="/assets/abc-pic.png",
            doc_id="doc-a",
            doc_path="/开发/Page",
        )
        exports = {"doc-a": _export("doc-a", "/开发/Page", "# Page", assets=[asset])}
        inv = _make_inventory(
            [Notebook(id="nb-1", name="开发")],
            [document],
            exports,
            document_children={"nb-1": ["doc-a"]},
        )

        _, zip_path = build_markdown_export(inv, exports, {}, output_dir)
        with zipfile.ZipFile(zip_path) as zf:
            meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))
        return _note_by_data_file(meta["files"], "Page.md")["attachments"][0]["attachmentId"]

    first = build(tmp_path / "first")
    second = build(tmp_path / "second")

    assert first
    assert first == second


def test_xlsx_attachment_mime_is_octet_stream(tmp_path: Path):
    """归档写出时 xlsx 附件 mime 必须为 octet-stream，避免 Trilium 走 spreadsheet 导入。"""

    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, "assets/abc-foo.xlsx", b"xlsx-bytes")

    document = Document(id="doc-a", notebook_id="nb-1", title="Page", path="/开发/Page")
    asset = Asset(
        relative_path="assets/abc-foo.xlsx",
        source_path="/assets/foo.xlsx",
        doc_id="doc-a",
        doc_path="/开发/Page",
    )
    exports = {
        "doc-a": _export(
            "doc-a", "/开发/Page", "# Page\n\n![](assets/foo.xlsx)", assets=[asset]
        )
    }
    inv = _make_inventory(
        [Notebook(id="nb-1", name="开发")],
        [document],
        exports,
        document_children={"nb-1": ["doc-a"]},
    )

    _, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    assert "开发/Page_abc-foo.xlsx" in names
    attachment = _note_by_data_file(meta["files"], "Page.md")["attachments"][0]
    assert attachment["mime"] == "application/octet-stream"
    assert attachment["dataFileName"] == "Page_abc-foo.xlsx"
    assert isinstance(attachment.get("attachmentId"), str)
    assert attachment["attachmentId"]


def test_meta_dataFileName_is_basename(tmp_path: Path):
    """meta 的 dataFileName / dirFileName 只能含当前层级名，不能带父路径。"""

    inv = _nested_inventory()

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    all_notes = _all_notes(meta["files"])
    for note in all_notes:
        data_file_name = note.get("dataFileName")
        if data_file_name:
            assert "/" not in data_file_name, data_file_name
        dir_file_name = note.get("dirFileName")
        if dir_file_name:
            assert "/" not in dir_file_name, dir_file_name

    parent = all_notes[-2]
    child = all_notes[-1]
    assert parent["title"] == "父"
    assert parent["dataFileName"] == "父.md"
    assert parent["dirFileName"] == "父"
    assert child["title"] == "子"
    assert child["dataFileName"] == "子.md"
    assert child["dirFileName"] is None


def test_markdown_link_is_sibling_basename(tmp_path: Path):
    """子文档 Markdown 中的附件引用应为同目录 basename，不再回溯 ../。"""

    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, "assets/sha1111111-pic.png", b"png-bytes")

    notebooks = [Notebook(id="nb-1", name="开发")]
    documents = [
        Document(id="doc-parent", notebook_id="nb-1", title="父", path="/开发/父"),
        Document(
            id="doc-child",
            notebook_id="nb-1",
            title="子",
            path="/开发/父/子",
            parent_id="doc-parent",
        ),
    ]
    child_asset = Asset(
        relative_path="assets/sha1111111-pic.png",
        source_path="/assets/pic.png",
        doc_id="doc-child",
        doc_path="/开发/父/子",
    )
    exports = {
        "doc-parent": _export("doc-parent", "/开发/父", "# 父"),
        "doc-child": _export(
            "doc-child", "/开发/父/子", "![](assets/pic.png)", assets=[child_asset]
        ),
    }
    rewrite_map = {"assets/pic.png": "assets/sha1111111-pic.png"}
    inv = _make_inventory(
        notebooks,
        documents,
        exports,
        document_children={"nb-1": ["doc-parent"], "doc-parent": ["doc-child"]},
    )

    _, zip_path = build_markdown_export(inv, exports, rewrite_map, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        content = zf.read("开发/父/子.md").decode("utf-8")

    assert "开发/父/子_sha1111111-pic.png" in names
    assert "(子_sha1111111-pic.png)" in content
    assert "../" not in content
    assert "assets/" not in content


# ===== Trilium getMeta 复刻 =====


def _get_attachment_meta(parent_note: dict, data_file_name: str) -> dict:
    """复刻 Trilium ``getAttachmentMeta``：仅遍历 children[].attachments。"""

    for note_meta in parent_note.get("children", []):
        for attachment_meta in note_meta.get("attachments", []):
            if attachment_meta.get("dataFileName") == data_file_name:
                return {"noteMeta": note_meta, "attachmentMeta": attachment_meta}
    return {}


def _get_meta(meta: dict, file_path: str) -> dict:
    """复刻 Trilium zip.ts ``getMeta`` 的完整逐段解析逻辑。

    与真实实现一致：游标初始为合成 import root（``children = meta.files``），
    逐段在 ``dataFileName`` / ``dirFileName`` 中查找；某段找不到 note 时回退到
    ``getAttachmentMeta(parent, segment)`` 遍历 ``parent.children[].attachments``。
    返回 ``{noteMeta, attachmentMeta}``，均未命中时返回 ``{}``。
    """

    if meta is None:
        return {}

    path_segments = file_path.split("/")
    cursor: Optional[dict] = {
        "isImportRoot": True,
        "children": meta.get("files", []),
        "dataFileName": "",
    }
    parent: Optional[dict] = None

    for segment in path_segments:
        if not cursor or not cursor.get("children"):
            return {}
        segment = html.unescape(segment)
        parent = cursor
        cursor = next(
            (
                child
                for child in parent["children"]
                if child.get("dataFileName") == segment
                or child.get("dirFileName") == segment
            ),
            None,
        )
        if cursor is None:
            return _get_attachment_meta(parent, segment)

    return {"noteMeta": cursor, "attachmentMeta": None}


def test_trilium_getMeta_simulation_for_xlsx(tmp_path: Path):
    """按 zip.ts getMeta 逻辑解析：ZIP 内所有 spreadsheet 文件都必须命中 note 或附件。"""

    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, "assets/sha-parent1-book.xlsx", b"xlsx-parent")
    _write_staged_asset(content_root, "assets/sha-child11-book.csv", b"csv-child")

    notebooks = [Notebook(id="nb-1", name="AI")]
    documents = [
        Document(id="doc-parent", notebook_id="nb-1", title="DeepSeek", path="/AI/DeepSeek"),
        Document(
            id="doc-child",
            notebook_id="nb-1",
            title="子标题",
            path="/AI/DeepSeek/子标题",
            parent_id="doc-parent",
        ),
    ]
    parent_asset = Asset(
        relative_path="assets/sha-parent1-book.xlsx",
        source_path="/assets/book.xlsx",
        doc_id="doc-parent",
        doc_path="/AI/DeepSeek",
    )
    child_asset = Asset(
        relative_path="assets/sha-child11-book.csv",
        source_path="/assets/book.csv",
        doc_id="doc-child",
        doc_path="/AI/DeepSeek/子标题",
    )
    exports = {
        "doc-parent": _export(
            "doc-parent", "/AI/DeepSeek", "[book](assets/book.xlsx)", assets=[parent_asset]
        ),
        "doc-child": _export(
            "doc-child", "/AI/DeepSeek/子标题", "[csv](assets/book.csv)", assets=[child_asset]
        ),
    }
    rewrite_map = {
        "assets/book.xlsx": "assets/sha-parent1-book.xlsx",
        "assets/book.csv": "assets/sha-child11-book.csv",
    }
    inv = _make_inventory(
        notebooks,
        documents,
        exports,
        document_children={"nb-1": ["doc-parent"], "doc-parent": ["doc-child"]},
    )

    _, zip_path = build_markdown_export(inv, exports, rewrite_map, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    spreadsheet_paths = [
        Path(name)
        for name in names
        if Path(name).suffix.lower() in SPREADSHEET_SUFFIXES
    ]
    assert spreadsheet_paths, "测试用例应至少包含一个 spreadsheet 文件"

    attachment_hits = 0
    for path in spreadsheet_paths:
        resolved = _get_meta(meta, path.as_posix())
        assert resolved != {}, f"{path} 未被识别为 note/附件，会触发 spreadsheet 解析"
        attachment_meta = resolved.get("attachmentMeta")
        if attachment_meta is not None:
            attachment_hits += 1
            # 命中附件时实际文件名必须与 ZIP 内 basename 一致，否则仍会被当普通文件处理
            assert attachment_meta["dataFileName"] == path.name

    assert attachment_hits == len(spreadsheet_paths)


def test_meta_files_top_level_notebooks(tmp_path: Path):
    """meta.files 直接是顶层笔记本列表，不包 root 包装 note。"""

    notebooks = [Notebook(id="nb-1", name="凌动科技"), Notebook(id="nb-2", name="AI")]
    documents = [
        Document(id="doc-a", notebook_id="nb-1", title="东乌旗大屏", path="/凌动科技/东乌旗大屏"),
        Document(id="doc-b", notebook_id="nb-2", title="DeepSeek", path="/AI/DeepSeek"),
    ]
    exports = {
        "doc-a": _export("doc-a", "/凌动科技/东乌旗大屏", "# 东乌旗大屏"),
        "doc-b": _export("doc-b", "/AI/DeepSeek", "# DeepSeek"),
    }
    inv = _make_inventory(
        notebooks,
        documents,
        exports,
        document_children={"nb-1": ["doc-a"], "nb-2": ["doc-b"]},
    )

    _, zip_path = build_markdown_export(inv, exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    assert len(meta["files"]) == 2
    assert [notebook["title"] for notebook in meta["files"]] == ["凌动科技", "AI"]
    assert meta["files"][0]["title"] == "凌动科技"
    assert meta["files"][0]["dirFileName"] == "凌动科技"
    assert [doc["title"] for doc in meta["files"][0]["children"]] == ["东乌旗大屏"]


def test_no_root_md_in_zip(tmp_path: Path):
    """新结构不再有 root.md 占位文件。"""

    inv = _nested_inventory()

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    assert "!!!meta.json" in names
    assert "root.md" not in names


def test_notePath_no_root_prefix(tmp_path: Path):
    """所有 note 的 notePath 都不应以 root 开头。"""

    inv = _nested_inventory()

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    for note in _all_notes(meta["files"]):
        assert note["notePath"], note["title"]
        assert note["notePath"][0] != "root", note["notePath"]


def test_meta_no_slash_in_dataFileName(tmp_path: Path):
    """所有 doc 与 attachment 的 dataFileName 均不能含 '/'。"""

    content_root = markdown_export_content_root(tmp_path)
    _write_staged_asset(content_root, "assets/sha-parent1-book.xlsx", b"xlsx")
    _write_staged_asset(content_root, "assets/sha-child11-pic.png", b"png")

    inv = _nested_inventory()

    parent_asset = Asset(
        relative_path="assets/sha-parent1-book.xlsx",
        source_path="/assets/book.xlsx",
        doc_id="doc-parent",
        doc_path="/开发/父",
    )
    child_asset = Asset(
        relative_path="assets/sha-child11-pic.png",
        source_path="/assets/pic.png",
        doc_id="doc-child",
        doc_path="/开发/父/子",
    )
    inv.exports["doc-parent"].assets = [parent_asset]
    inv.exports["doc-child"].assets = [child_asset]

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        meta = json.loads(zf.read("!!!meta.json").decode("utf-8"))

    attachment_count = 0
    for note in _all_notes(meta["files"]):
        data_file_name = note.get("dataFileName")
        if data_file_name is not None:
            assert "/" not in data_file_name, data_file_name
        dir_file_name = note.get("dirFileName")
        if dir_file_name is not None:
            assert "/" not in dir_file_name, dir_file_name
        for attachment in note.get("attachments", []):
            attachment_count += 1
            assert "/" not in attachment["dataFileName"], attachment["dataFileName"]
    assert attachment_count == 2


def _nested_inventory() -> Inventory:
    """构造 2 级层级：笔记本 开发 / 父 / 子。"""

    notebooks = [Notebook(id="nb-1", name="开发")]
    documents = [
        Document(id="doc-parent", notebook_id="nb-1", title="父", path="/开发/父"),
        Document(id="doc-child", notebook_id="nb-1", title="子", path="/开发/父/子"),
    ]
    exports = {
        "doc-parent": _export("doc-parent", "/开发/父", "# 父"),
        "doc-child": _export("doc-child", "/开发/父/子", "# 子"),
    }
    return _make_inventory(
        notebooks,
        documents,
        exports,
        document_children={"nb-1": ["doc-parent"], "doc-parent": ["doc-child"]},
    )


# ===== ZIP 条目先序 =====


def _nested_with_attachments(content_root: Path) -> tuple[Inventory, dict[str, str]]:
    """笔记本 AI / 父 DeepSeek / 子 子标题，父子各带一个附件。"""

    _write_staged_asset(content_root, "assets/sha-parent1-book.xlsx", b"xlsx-parent")
    _write_staged_asset(content_root, "assets/sha-child11-book.csv", b"csv-child")

    notebooks = [Notebook(id="nb-1", name="AI")]
    documents = [
        Document(id="doc-parent", notebook_id="nb-1", title="DeepSeek", path="/AI/DeepSeek"),
        Document(
            id="doc-child",
            notebook_id="nb-1",
            title="子标题",
            path="/AI/DeepSeek/子标题",
            parent_id="doc-parent",
        ),
    ]
    parent_asset = Asset(
        relative_path="assets/sha-parent1-book.xlsx",
        source_path="/assets/book.xlsx",
        doc_id="doc-parent",
        doc_path="/AI/DeepSeek",
    )
    child_asset = Asset(
        relative_path="assets/sha-child11-book.csv",
        source_path="/assets/book.csv",
        doc_id="doc-child",
        doc_path="/AI/DeepSeek/子标题",
    )
    exports = {
        "doc-parent": _export(
            "doc-parent", "/AI/DeepSeek", "[book](assets/book.xlsx)", assets=[parent_asset]
        ),
        "doc-child": _export(
            "doc-child", "/AI/DeepSeek/子标题", "[csv](assets/book.csv)", assets=[child_asset]
        ),
    }
    rewrite_map = {
        "assets/book.xlsx": "assets/sha-parent1-book.xlsx",
        "assets/book.csv": "assets/sha-child11-book.csv",
    }
    return (
        _make_inventory(
            notebooks,
            documents,
            exports,
            document_children={"nb-1": ["doc-parent"], "doc-parent": ["doc-child"]},
        ),
        rewrite_map,
    )


def test_zip_entry_order_meta_first(tmp_path: Path):
    """!!!meta.json 永远是 ZIP 的第一个条目且只出现一次。"""

    inv = _nested_inventory()

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    assert names[0] == "!!!meta.json"
    assert names.count("!!!meta.json") == 1


def test_zip_entry_order_notebook_first(tmp_path: Path):
    """笔记本 Markdown 必须先于该笔记本下任何子条目。"""

    inv = _nested_inventory()

    _, zip_path = build_markdown_export(inv, inv.exports, {}, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    # 笔记本 Markdown 在 content_root 根目录，紧随 meta
    assert names == ["!!!meta.json", "开发.md", "开发/父.md", "开发/父/子.md"]


def test_zip_entry_order_parent_before_child(tmp_path: Path):
    """父 doc Markdown 先于其附件，附件先于子 doc Markdown。"""

    content_root = markdown_export_content_root(tmp_path)
    inv, rewrite_map = _nested_with_attachments(content_root)

    _, zip_path = build_markdown_export(inv, inv.exports, rewrite_map, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    assert names == [
        "!!!meta.json",
        "AI.md",
        "AI/DeepSeek.md",
        "AI/DeepSeek_sha-parent1-book.xlsx",
        "AI/DeepSeek/子标题.md",
        "AI/DeepSeek/子标题_sha-child11-book.csv",
    ]


def test_zip_entry_order_full_simulation(tmp_path: Path):
    """完整导出后逐条验证先序不变式：父 doc → 父附件 → 子 doc → 子附件。"""

    content_root = markdown_export_content_root(tmp_path)
    inv, rewrite_map = _nested_with_attachments(content_root)

    _, zip_path = build_markdown_export(inv, inv.exports, rewrite_map, tmp_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    parent_idx = next(i for i, n in enumerate(names) if n.endswith("/DeepSeek.md"))
    child_indices = [i for i, n in enumerate(names) if "/DeepSeek/" in n]
    parent_att_indices = [i for i, n in enumerate(names) if "book.xlsx" in n]

    assert names[0] == "!!!meta.json"
    assert child_indices, "测试用例应包含子文档"
    assert parent_att_indices, "测试用例应包含父附件"
    # 父 doc 先于所有子条目
    assert all(parent_idx < i for i in child_indices)
    # 父附件先于子 doc
    assert all(a < c for a in parent_att_indices for c in child_indices)
    # 父 doc 先于父附件
    assert all(parent_idx < a for a in parent_att_indices)

