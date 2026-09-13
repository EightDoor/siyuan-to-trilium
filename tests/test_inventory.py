"""Inventory 构建测试：黑名单排除与基于 hpath 的多级层级。"""

from unittest.mock import MagicMock

from src.inventory import InventoryBuilder


def _make_client(notebooks, docs_by_nb):
    """构造伪造的 SiyuanClient。

    docs_by_nb: dict[notebook_id, list[(doc_id, hpath)]]
    返回 SQL 中符合 ``box=... AND type='d'`` 的全部文档。
    listDocsByPath 已被新实现弃用，保留 mock 防止异常。
    """

    client = MagicMock()
    client.list_notebooks.return_value = notebooks
    client.list_docs_by_path.return_value = {"files": []}

    def fake_sql(stmt):
        for nb_id, entries in docs_by_nb.items():
            if f"box='{nb_id}'" in stmt and "type='d'" in stmt:
                return [{"id": d_id, "hpath": hp} for d_id, hp in entries]
        return []

    client.query_sql.side_effect = fake_sql
    return client


def test_excludes_notebook_and_preserves_listing_order():
    notebooks = [
        {"id": "2022-A", "name": "归档"},
        {"id": "2022-B", "name": "普通"},
    ]
    # 子文档 id 故意不按字典序排列，用于验证顺序来自 hpath 字典序
    docs = {
        "2022-A": [
            ("a1", "/归档/A1"),
            ("a1-1", "/归档/A1/A1-1"),
            ("a2", "/归档/A2"),
        ],
        "2022-B": [
            ("b1", "/普通/B1"),
            ("b1-a", "/普通/B1/B1-a"),
            ("b1-b", "/普通/B1/B1-b"),
            ("b1-c", "/普通/B1/B1-c"),
            ("b2", "/普通/B2"),
        ],
    }
    excluded_doc_ids = ["a1", "a2", "a1-1"]

    client = _make_client(notebooks, docs)
    # 默认已不排除任何笔记本，这里显式传入以验证黑名单排除逻辑
    inventory = InventoryBuilder(
        client, excluded_notebooks=["归档"]
    ).build_inventory()

    # 黑名单登记与排除统计
    assert inventory.excluded_notebooks == {"2022-A": "归档"}
    assert inventory.excluded_documents_count == len(excluded_doc_ids)

    # 归档笔记本的文档不进入 documents
    assert set(inventory.documents) == {"b1", "b2", "b1-a", "b1-b", "b1-c"}
    assert all(doc.notebook_id == "2022-B" for doc in inventory.documents.values())
    assert set(inventory.notebooks) == {"2022-B"}

    # 子文档顺序按 hpath 字典序排序
    assert inventory.document_children["b1"] == ["b1-a", "b1-b", "b1-c"]
    assert inventory.document_children["2022-B"] == ["b1", "b2"]


def test_hpath_used_for_full_tree():
    """hpath 拆父路径必须构建完整多级树（顶级 -> 子级 -> 孙级）。"""

    notebooks = [{"id": "nb-1", "name": "笔记"}]
    docs = {
        "nb-1": [
            ("d1", "/笔记/一级"),
            ("d2", "/笔记/一级/二级"),
            ("d3", "/笔记/一级/二级/三级"),
        ],
    }

    client = _make_client(notebooks, docs)
    inventory = InventoryBuilder(client).build_inventory()

    assert set(inventory.documents) == {"d1", "d2", "d3"}

    # 笔记本 → 子 → 孙
    assert inventory.document_children["nb-1"] == ["d1"]
    assert inventory.document_children["d1"] == ["d2"]
    assert inventory.document_children["d2"] == ["d3"]
    assert inventory.document_children["d3"] == []

    # parent_id 链与递归层级一致
    assert inventory.documents["d1"].parent_id is None
    assert inventory.documents["d2"].parent_id == "d1"
    assert inventory.documents["d3"].parent_id == "d2"


def test_custom_excluded_notebooks_can_disable_default():
    notebooks = [
        {"id": "2022-A", "name": "归档"},
        {"id": "2022-B", "name": "普通"},
    ]
    docs = {
        "2022-A": [("a1", "/归档/A1")],
        "2022-B": [("b1", "/普通/B1")],
    }

    client = _make_client(notebooks, docs)
    inventory = InventoryBuilder(client, excluded_notebooks=[]).build_inventory()

    assert inventory.excluded_notebooks == {}
    assert inventory.excluded_documents_count == 0
    assert set(inventory.documents) == {"a1", "b1"}


def test_custom_excluded_notebooks_by_name():
    notebooks = [
        {"id": "2022-A", "name": "归档"},
        {"id": "2022-B", "name": "普通"},
    ]
    docs = {
        "2022-A": [("a1", "/归档/A1")],
        "2022-B": [("b1", "/普通/B1")],
    }

    client = _make_client(notebooks, docs)
    inventory = InventoryBuilder(client, excluded_notebooks=["普通"]).build_inventory()

    assert inventory.excluded_notebooks == {"2022-B": "普通"}
    assert inventory.excluded_documents_count == 1
    assert set(inventory.documents) == {"a1"}


def test_default_excludes_nothing():
    """不传 excluded_notebooks 时默认不排除任何笔记本，包括归档。"""

    notebooks = [
        {"id": "2022-A", "name": "归档"},
        {"id": "2022-B", "name": "普通"},
    ]
    docs = {
        "2022-A": [("a1", "/归档/A1")],
        "2022-B": [("b1", "/普通/B1")],
    }

    client = _make_client(notebooks, docs)
    inventory = InventoryBuilder(client).build_inventory()

    assert inventory.excluded_notebooks == {}
    assert inventory.excluded_documents_count == 0
    assert set(inventory.documents) == {"a1", "b1"}
    assert set(inventory.notebooks) == {"2022-A", "2022-B"}


def test_sql_query_includes_limit():
    """所有 blocks 表查询都必须显式 LIMIT 100000，避免 SQLite 默认 64 行截断。"""

    notebooks = [
        {"id": "2022-A", "name": "归档"},
        {"id": "2022-B", "name": "普通"},
    ]
    docs = {
        "2022-A": [("a1", "/归档/A1")],
        "2022-B": [("b1", "/普通/B1")],
    }

    client = _make_client(notebooks, docs)
    InventoryBuilder(client).build_inventory()

    statements = [call.args[0] for call in client.query_sql.call_args_list]
    assert statements, "应当至少执行一次 SQL 查询"
    for stmt in statements:
        assert "LIMIT 100000" in stmt, f"SQL 缺少 LIMIT 100000: {stmt}"

    # 文档查询额外要求按 hpath 升序，保证兄弟顺序稳定
    doc_stmt = next(s for s in statements if "hpath" in s)
    assert "ORDER BY hpath" in doc_stmt
