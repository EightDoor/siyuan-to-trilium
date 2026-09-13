"""converter 模块单元测试。"""

from src.converter import MarkdownConverter, replace_placeholders, PLACEHOLDER_TARGET
from src.models import Document, ExportResult, MigrationStats


def _make_result(markdown: str) -> ExportResult:
    return ExportResult(
        document=Document(id="doc-a", notebook_id="nb-1", title="A", path="/A"),
        markdown=markdown,
    )


def test_internal_siyuan_block_ref_converted_to_placeholder():
    stats = MigrationStats()
    result = _make_result("see (siyuan://blocks/20210101000000-aaaaaa) here")
    block_to_doc = {"20210101000000-aaaaaa": "doc-b"}

    converted = MarkdownConverter(stats).convert(result, block_to_doc)

    assert PLACEHOLDER_TARGET.format(sid="doc-b") in converted.markdown
    assert converted.stats.internal_links_found == 1
    assert converted.stats.internal_links_converted == 1
    assert converted.stats.broken_links == 0


def test_unresolved_siyuan_ref_counted_as_broken():
    stats = MigrationStats()
    result = _make_result("see (siyuan://blocks/20210101000000-aaaaaa) here")

    converted = MarkdownConverter(stats).convert(result, {})

    assert converted.stats.broken_links == 1
    assert converted.stats.internal_links_converted == 0


def test_replace_placeholders_substitutes_trilium_id():
    text = "see [[S2T_TARGET:doc-b]] now"
    out = replace_placeholders(text, {"doc-b": "triliumXYZ"})
    assert out == "see [[triliumXYZ]] now"


def test_database_block_marker_counted():
    stats = MigrationStats()
    result = _make_result("```siyuan-database\nfoo\n```")

    converted = MarkdownConverter(stats).convert(result, {})

    assert converted.stats.unsupported_database_blocks == 1


def test_external_links_are_left_alone():
    stats = MigrationStats()
    result = _make_result("[example](https://example.com)")

    converted = MarkdownConverter(stats).convert(result, {})

    assert converted.stats.internal_links_found == 0
    assert "https://example.com" in converted.markdown