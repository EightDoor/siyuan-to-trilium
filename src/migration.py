"""迁移主流程。

只输出 Markdown 导出（目录 + 可导入 ZIP），不再启动或修改 Trilium 实例。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .assets import AssetCollector, rewrite_markdown_references
from .config import MigrationConfig, SiyuanConfig
from .converter import (
    PLACEHOLDER_PATTERN,
    PLACEHOLDER_TARGET,
    MarkdownConverter,
)
from .inventory import Inventory, InventoryBuilder
from .models import MigrationStats
from .progress import ProgressReporter
from .report import build_report, write_report, write_summary
from .siyuan import SiyuanClient
from .trilium_archive import build_markdown_export, markdown_export_content_root


@dataclass
class MarkdownLink:
    """导出时解析的内部链接目标。

    Trilium 不支持用 ``[[noteId]]`` 直接定位导入的笔记（导入时 noteId 会重新生成），
    因此导出的 Markdown 使用 ``[[S2T_NOTE:<标题>]]`` 占位符，文档清单生成时会写入
    ``!!!meta.json`` 的 children 关系，Trilium 在导入时也会自动把同名标题解析为内部链接。
    """

    title: str
    exists: bool = True


@dataclass
class MarkdownExportResult:
    markdown_dir: Path
    zip_path: Path


class MigrationResult:
    def __init__(self, report: dict, export: MarkdownExportResult):
        self.report = report
        self.export = export


class Migration:
    def __init__(
        self,
        siyuan_config: SiyuanConfig,
        migration_config: MigrationConfig,
        excluded_notebooks: Optional[list[str]] = None,
        progress: Optional[ProgressReporter] = None,
    ):
        self._siyuan_config = siyuan_config
        self._migration_config = migration_config
        self._excluded_notebooks = excluded_notebooks
        self._progress = progress or ProgressReporter(enabled=False)

    def run(self) -> MigrationResult:
        output_dir = self._migration_config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        stats = MigrationStats()
        failures: list[dict] = []
        unresolved: list[dict] = []
        missing_assets: list[dict] = []
        excluded_notebooks: dict[str, str] = {}

        # ===== Pass 1：扫描笔记本与文档树 =====
        self._progress.update("siyuan.scan", 0, 1, "正在读取笔记本与文档树")
        client = SiyuanClient(self._siyuan_config)
        builder = InventoryBuilder(
            client,
            progress=self._progress,
            excluded_notebooks=self._excluded_notebooks,
        )
        inventory: Inventory = builder.build_inventory()
        excluded_notebooks = dict(inventory.excluded_notebooks)
        stats.documents_found = len(inventory.documents)
        stats.documents_excluded = inventory.excluded_documents_count
        self._progress.update(
            "siyuan.scan",
            1,
            1,
            f"共发现 {stats.documents_found} 个文档（排除 {inventory.excluded_documents_count}）",
        )

        # ===== Pass 2：导出 Markdown =====
        total_docs = len(inventory.documents)
        self._progress.update("siyuan.export", 0, total_docs, "正在导出 Markdown")
        for idx, (doc_id, doc) in enumerate(inventory.documents.items(), start=1):
            result = builder.export_one(doc)
            inventory.exports[doc_id] = result
            self._progress.update(
                "siyuan.export",
                idx,
                total_docs,
                f"{doc.path} -> {'OK' if not result.error else 'FAIL'}",
            )

        # ===== Pass 3：链接转换 + 附件收集 =====
        self._progress.update("convert", 0, total_docs, "转换链接与附件")
        converter = MarkdownConverter(stats)
        # 附件直接下载到归档内容根目录的 assets/ 下，随 build_markdown_export
        # 一并打进 ZIP，Markdown 中的 assets/<name> 引用因此可在 ZIP 内解析。
        # API 下载失败时回退到本地工作空间 data 目录按 box/doc 路径复制。
        workspace_path = Path(
            os.environ.get("SIYUAN_WORKSPACE", "/Users/zhoukai/SiYuan/data")
        )
        collector = AssetCollector(
            client,
            markdown_export_content_root(output_dir),
            stats,
            workspace_path=workspace_path,
        )

        for idx, (doc_id, result) in enumerate(inventory.exports.items(), start=1):
            if result.error:
                stats.documents_failed += 1
                failures.append(
                    {"doc_id": doc_id, "path": result.document.path, "error": result.error}
                )
                self._progress.update(
                    "convert", idx, total_docs, f"跳过: {result.document.path}"
                )
                continue

            collector.collect_for(result)
            converted = converter.convert(result, inventory.block_to_document)
            result.markdown = rewrite_markdown_references(
                converted.markdown, collector.rewrite_map
            )
            stats.documents_exported += 1
            for field_name in (
                "internal_links_found",
                "internal_links_converted",
                "broken_links",
                "unsupported_database_blocks",
                "unsupported_embedded_queries",
            ):
                current = getattr(stats, field_name)
                setattr(stats, field_name, current + getattr(converted.stats, field_name))
            if converted.stats.broken_links:
                unresolved.append(
                    {
                        "doc_id": doc_id,
                        "path": result.document.path,
                        "broken": converted.stats.broken_links,
                    }
                )
            self._progress.update("convert", idx, total_docs, f"{result.document.path}")

        # ===== Pass 4：解析内部链接占位符 =====
        # Trilium 导入时不复用 noteId，因此把占位符替换为基于文档标题的
        # `[[S2T_NOTE:<标题>]]`，让 Trilium 在导入时按标题自动建立内部链接。
        doc_id_to_title = {
            doc_id: doc.title for doc_id, doc in inventory.documents.items()
        }
        for result in inventory.exports.values():
            if result.error:
                continue
            result.markdown = _resolve_placeholders_to_titles(
                result.markdown, doc_id_to_title
            )

        # ===== Pass 5：写出 Markdown 目录 + ZIP =====
        self._progress.update("export", 0, 1, "生成 Markdown 目录与 ZIP")
        markdown_export_dir, zip_path = build_markdown_export(
            inventory=inventory,
            exports=inventory.exports,
            rewrite_map=collector.rewrite_map,
            output_dir=output_dir,
        )
        self._progress.update(
            "export",
            1,
            1,
            f"{zip_path.name} ({zip_path.stat().st_size} bytes)",
        )

        # ===== 报告 =====
        self._progress.update("report", 0, 1, "生成 migration-report.json")
        report = build_report(
            stats=stats,
            export_path=zip_path,
            markdown_dir=markdown_export_dir,
            excluded_notebooks=excluded_notebooks,
            failures=failures,
            unresolved_links=unresolved,
            missing_assets=missing_assets,
        )
        write_report(report, output_dir)
        write_summary(report, output_dir)
        self._progress.update("report", 1, 1, "完成")
        self._progress.finish()

        # 附件现已随归档内容落盘到 markdown-export 内，无额外的临时 assets 目录需要清理。

        return MigrationResult(
            report=report,
            export=MarkdownExportResult(
                markdown_dir=markdown_export_dir, zip_path=zip_path
            ),
        )


def _resolve_placeholders_to_titles(text: str, doc_id_to_title: dict[str, str]) -> str:
    """把 ``[[S2T_TARGET:<doc_id>]]`` 替换为 ``[[S2T_NOTE:<标题>]]``。"""

    def replace(match) -> str:
        sid = match.group(1)
        title = doc_id_to_title.get(sid)
        if not title:
            return match.group(0)
        return f"[[S2T_NOTE:{title}]]"

    return PLACEHOLDER_PATTERN.sub(replace, text)
