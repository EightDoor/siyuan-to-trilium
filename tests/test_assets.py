"""AssetCollector 资源路径前缀测试。"""

import hashlib
from unittest.mock import MagicMock

from src.assets import AssetCollector
from src.models import Document, ExportResult, MigrationStats
from src.siyuan import SiyuanError


def _collector(tmp_path):
    client = MagicMock()
    client.get_file.return_value = b"binary"
    stats = MigrationStats()
    return AssetCollector(client, tmp_path, stats), client


def _document():
    return Document(id="doc1", notebook_id="nb1", title="T", path="/T")


def test_relative_ref_gets_data_prefix(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(document=_document(), markdown="![img](assets/foo.png)")

    assets = collector.collect_for(result)

    client.get_file.assert_called_once_with("/data/assets/foo.png")
    assert len(assets) == 1
    assert not assets[0].relative_path.startswith("/data/")


def test_prefixed_ref_keeps_single_prefix(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(document=_document(), markdown="![img](/data/assets/foo.png)")

    collector.collect_for(result)

    client.get_file.assert_called_once_with("/data/assets/foo.png")


def test_external_link_is_ignored(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(
        document=_document(),
        markdown="![img](https://example.com/a.png)\n[x](mailto:a@b.com)",
    )

    assets = collector.collect_for(result)

    assert assets == []
    client.get_file.assert_not_called()


def test_html_img_is_collected(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(document=_document(), markdown='<img src="images/x.jpg">')

    assets = collector.collect_for(result)

    client.get_file.assert_called_once_with("/data/images/x.jpg")
    assert len(assets) == 1
    assert assets[0].relative_path.startswith("assets/")
    assert assets[0].relative_path.endswith("-x.jpg")
    assert (tmp_path / assets[0].relative_path).exists()


def test_html_img_with_relative_path_collected(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(
        document=_document(), markdown='<img src="../../assets/y.png">'
    )

    assets = collector.collect_for(result)

    client.get_file.assert_called_once_with("/data/assets/y.png")
    assert len(assets) == 1
    assert assets[0].relative_path.startswith("assets/")
    assert assets[0].relative_path.endswith("-y.png")


def test_html_bg_url_is_collected(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(
        document=_document(),
        markdown='<div style="background-image: url(images/bg.jpg)"></div>',
    )

    assets = collector.collect_for(result)

    client.get_file.assert_called_once_with("/data/images/bg.jpg")
    assert len(assets) == 1
    assert assets[0].relative_path.endswith("-bg.jpg")


def test_external_links_skipped(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(
        document=_document(),
        markdown=(
            '<a href="https://example.com/a">x</a>\n'
            '<a href="mailto:a@b.com">y</a>\n'
            '<img src="data:image/png;base64,AAAA">'
        ),
    )

    assets = collector.collect_for(result)

    assert assets == []
    client.get_file.assert_not_called()


def test_url_encoded_asset_filename(tmp_path):
    collector, _client = _collector(tmp_path)
    document = _document()

    digest = hashlib.sha1(
        f"{document.id}:assets/CleanShot 2024-09-13.png".encode("utf-8")
    ).hexdigest()[:10]

    # 真实文件名含空格，落盘时保持字面字符（不做 URL 编码）
    assert (
        AssetCollector._target_filename(document, "assets/CleanShot 2024-09-13.png")
        == f"{digest}-CleanShot 2024-09-13.png"
    )

    result = ExportResult(
        document=document,
        markdown="![img](assets/CleanShot 2024-09-13.png)",
    )
    assets = collector.collect_for(result)

    assert len(assets) == 1
    assert assets[0].relative_path == f"assets/{digest}-CleanShot 2024-09-13.png"


def test_rewrite_map_keeps_original_key(tmp_path):
    collector, _client = _collector(tmp_path)
    document = _document()
    reference = "assets/CleanShot%202024-09-13.png"

    result = ExportResult(document=document, markdown=f"![img]({reference})")
    collector.collect_for(result)

    # key 保留 Markdown 中的原始（URL 编码）引用，value 指向未编码的落盘文件
    assert reference in collector.rewrite_map
    digest = hashlib.sha1(
        f"{document.id}:{reference}".encode("utf-8")
    ).hexdigest()[:10]
    assert (
        collector.rewrite_map[reference]
        == f"assets/{digest}-CleanShot 2024-09-13.png"
    )


def test_unencoded_asset_path_used_for_api(tmp_path):
    collector, client = _collector(tmp_path)
    reference = (
        "assets/CleanShot%202024-09-13%20at%2018.12.22-20240913181230-9m92f68.png"
    )
    result = ExportResult(document=_document(), markdown=f"![img]({reference})")

    collector.collect_for(result)

    # 思源 /api/file/getFile 只接受字面字符，不能传 URL 编码路径
    client.get_file.assert_called_once_with(
        "/data/assets/CleanShot 2024-09-13 at 18.12.22-20240913181230-9m92f68.png"
    )


def test_unencoded_basename_in_zip(tmp_path):
    collector, _client = _collector(tmp_path)
    reference = "assets/CleanShot%202024-09-13-20240913181230-9m92f68.png"
    result = ExportResult(document=_document(), markdown=f"![img]({reference})")

    assets = collector.collect_for(result)

    assert len(assets) == 1
    basename = assets[0].relative_path.split("/")[-1]
    assert basename.endswith("-CleanShot 2024-09-13-20240913181230-9m92f68.png")
    assert "%20" not in basename


def test_rewrite_map_original_key_preserved(tmp_path):
    collector, _client = _collector(tmp_path)
    document = _document()
    reference = "assets/CleanShot%202024-09-13.png"

    result = ExportResult(document=document, markdown=f"![img]({reference})")
    collector.collect_for(result)

    digest = hashlib.sha1(
        f"{document.id}:{reference}".encode("utf-8")
    ).hexdigest()[:10]
    # key 是原始 Markdown 字符串（含 %20），value 是未编码的 ZIP 内路径
    assert reference in collector.rewrite_map
    assert (
        collector.rewrite_map[reference]
        == f"assets/{digest}-CleanShot 2024-09-13.png"
    )


def test_template_placeholder_skipped(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(
        document=_document(),
        markdown=(
            "![a](ACCESS_TOKEN_URL)\n"
            "![b]({{xxx}})\n"
            "![c](item.headPortrait)\n"
            "![d](department.image)\n"
            "![e]({链接})"
        ),
    )

    assets = collector.collect_for(result)

    assert assets == []
    client.get_file.assert_not_called()


def test_unknown_extension_skipped(tmp_path):
    collector, client = _collector(tmp_path)
    result = ExportResult(
        document=_document(), markdown="![a](documents/foo.unknown_ext)"
    )

    assets = collector.collect_for(result)

    assert assets == []
    client.get_file.assert_not_called()


def test_local_workspace_fallback(tmp_path):
    client = MagicMock()
    client.get_file.side_effect = SiyuanError("api 下载失败")
    stats = MigrationStats()
    collector = AssetCollector(client, tmp_path, stats, workspace_path=tmp_path)

    box_ref = "2023-02 185c4e73014b479aa02f9afae0501dde/Untitled.png"
    source_file = tmp_path / box_ref
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"local-binary")

    result = ExportResult(document=_document(), markdown=f"![img]({box_ref})")

    assets = collector.collect_for(result)

    assert len(assets) == 1
    assert assets[0].relative_path.startswith("assets/")
    assert assets[0].relative_path.endswith("-Untitled.png")
    copied = tmp_path / assets[0].relative_path
    assert copied.exists()
    assert copied.read_bytes() == b"local-binary"
    assert stats.assets_copied == 1
    assert stats.assets_missing == 0
    # 重写映射以原始引用为 key。
    assert collector.rewrite_map[box_ref] == assets[0].relative_path


def test_xlsx_mime_normalized(tmp_path):
    """xlsx 附件的 mime 应从 spreadsheet 类型降级为 octet-stream。"""

    collector, _client = _collector(tmp_path)
    source_path = "assets/东乌旗电脑部署文档-20231007114547-08uac4w.xlsx"
    result = ExportResult(document=_document(), markdown=f"![x]({source_path})")

    assets = collector.collect_for(result)

    assert len(assets) == 1
    assert assets[0].source_path == source_path
    assert assets[0].mime == "application/octet-stream"


def test_csv_mime_normalized(tmp_path):
    """csv 附件的 mime 应从 text/csv 降级为 octet-stream。"""

    collector, _client = _collector(tmp_path)
    result = ExportResult(document=_document(), markdown="[csv](assets/data.csv)")

    assets = collector.collect_for(result)

    assert len(assets) == 1
    assert assets[0].mime == "application/octet-stream"
