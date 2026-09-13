"""迁移报告生成。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import MigrationStats


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_report(
    stats: MigrationStats,
    export_path: Path,
    markdown_dir: Path,
    excluded_notebooks: dict[str, str],
    failures: list[dict[str, Any]],
    unresolved_links: list[dict[str, Any]],
    missing_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造 markdown 导出报告。"""

    export_size = export_path.stat().st_size if export_path.exists() else 0
    markdown_size = markdown_dir.stat().st_size if markdown_dir.exists() else 0
    excluded_list = [
        {"notebook_id": nb_id, "name": name}
        for nb_id, name in sorted(excluded_notebooks.items(), key=lambda kv: kv[1])
    ]
    report = {
        "documents": {
            "found": stats.documents_found,
            "exported": stats.documents_exported,
            "failed": stats.documents_failed,
            "excluded": stats.documents_excluded,
            "excluded_notebooks": excluded_list,
        },
        "links": {
            "internal_found": stats.internal_links_found,
            "converted": stats.internal_links_converted,
            "unresolved": stats.broken_links,
        },
        "assets": {
            "found": stats.assets_found,
            "copied": stats.assets_copied,
            "missing": stats.assets_missing,
        },
        "unsupported": {
            "database_blocks": stats.unsupported_database_blocks,
            "embedded_queries": stats.unsupported_embedded_queries,
        },
        "export": {
            "generated": export_path.exists(),
            "zip_path": str(export_path),
            "zip_size_bytes": export_size,
            "zip_sha256": sha256_of(export_path) if export_path.exists() else "",
            "markdown_dir": str(markdown_dir),
            "markdown_size_bytes": markdown_size,
            "import_instructions": (
                "在 Trilium Notes 中右键笔记树 → Import into note → Markdown (ZIP)；"
                "选择生成的 siyuan-archive.zip。ZIP 根目录包含 !!!meta.json，"
                "Trilium 会按 meta 还原笔记层级、Markdown 类型与顺序。"
            ),
        },
        "failures": failures,
        "unresolved_links": unresolved_links,
        "missing_assets": missing_assets,
    }
    return report


def write_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "migration-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report_path


def write_summary(report: dict[str, Any], output_dir: Path) -> Path:
    summary_path = output_dir / "migration-summary.txt"
    lines = [
        "SiYuan → Markdown Export",
        "========================",
        "",
        "Documents",
        f"  Found:                  {report['documents']['found']}",
        f"  Exported:               {report['documents']['exported']}",
        f"  Failed:                 {report['documents']['failed']}",
        f"  Excluded:               {report['documents']['excluded']}",
    ]
    if report["documents"].get("excluded_notebooks"):
        names = ", ".join(nb["name"] for nb in report["documents"]["excluded_notebooks"])
        lines.append(f"  Excluded notebooks:     {names}")
    lines += [
        "",
        "Links",
        f"  Internal links:         {report['links']['internal_found']}",
        f"  Converted:              {report['links']['converted']}",
        f"  Broken:                 {report['links']['unresolved']}",
        "",
        "Assets",
        f"  Found:                  {report['assets']['found']}",
        f"  Copied:                 {report['assets']['copied']}",
        f"  Missing:                {report['assets']['missing']}",
        "",
        "Unsupported",
        f"  Database blocks:        {report['unsupported']['database_blocks']}",
        f"  Embedded queries:       {report['unsupported']['embedded_queries']}",
        "",
        "Export",
        f"  ZIP generated:          {report['export']['generated']}",
        f"  ZIP path:               {report['export']['zip_path']}",
        f"  Markdown dir:           {report['export']['markdown_dir']}",
        "",
        report["export"]["import_instructions"],
        "",
    ]
    summary_path.write_text("\n".join(lines))
    return summary_path
