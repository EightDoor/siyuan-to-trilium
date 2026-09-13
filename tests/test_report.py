"""报告生成单元测试。"""

import json
from pathlib import Path

from src.report import build_report, sha256_of, write_report, write_summary
from src.models import MigrationStats


def test_report_includes_export(tmp_path: Path):
    stats = MigrationStats(
        documents_found=2,
        documents_exported=2,
        documents_excluded=5,
    )
    zip_path = tmp_path / "siyuan-archive.zip"
    zip_path.write_bytes(b"hello")
    markdown_dir = tmp_path / "markdown-export" / "siyuan-archive"
    markdown_dir.mkdir(parents=True)

    report = build_report(
        stats=stats,
        export_path=zip_path,
        markdown_dir=markdown_dir,
        excluded_notebooks={"nb-arch": "归档"},
        failures=[],
        unresolved_links=[],
        missing_assets=[],
    )

    assert report["documents"]["found"] == 2
    assert report["documents"]["excluded"] == 5
    assert report["documents"]["excluded_notebooks"] == [{"notebook_id": "nb-arch", "name": "归档"}]
    assert report["export"]["generated"] is True
    assert report["export"]["zip_path"] == str(zip_path)
    assert report["export"]["zip_sha256"] == sha256_of(zip_path)
    assert "Trilium" in report["export"]["import_instructions"]


def test_write_report_creates_file(tmp_path: Path):
    stats = MigrationStats()
    zip_path = tmp_path / "siyuan-archive.zip"
    zip_path.write_bytes(b"x")
    markdown_dir = tmp_path / "markdown-export" / "siyuan-archive"
    markdown_dir.mkdir(parents=True)

    report = build_report(
        stats=stats,
        export_path=zip_path,
        markdown_dir=markdown_dir,
        excluded_notebooks={},
        failures=[],
        unresolved_links=[],
        missing_assets=[],
    )
    out = write_report(report, tmp_path)
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "export" in payload
    summary = write_summary(report, tmp_path)
    assert summary.exists()
    text = summary.read_text()
    assert "SiYuan" in text
    assert "Markdown Export" in text
