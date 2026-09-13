"""Markdown 转换：处理链接占位符、附件引用与不可解析内容。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .models import Document, ExportResult, MigrationStats


SIYUAN_BLOCK_REF = re.compile(r"siyuan://blocks/([0-9]{14}-[a-z0-9]{6,8})")
SIYUAN_DOC_REF = re.compile(r"\(\s*siyuan://blocks/([0-9]{14}-[a-z0-9]{6,8})\s*\)")
PLACEHOLDER_TARGET = "[[S2T_TARGET:{sid}]]"
PLACEHOLDER_PATTERN = re.compile(r"\[\[S2T_TARGET:([^\]]+)\]\]")


@dataclass
class ConversionResult:
    markdown: str
    stats: MigrationStats


class MarkdownConverter:
    """扫描 Markdown，写入链接占位符并记录统计。"""

    def __init__(self, stats: MigrationStats):
        self._stats = stats

    def convert(self, result: ExportResult, block_to_doc: dict[str, str]) -> ConversionResult:
        stats = MigrationStats()
        text = result.markdown

        internal_count = 0
        converted_count = 0
        broken_count = 0

        def replace_block_ref(match: re.Match[str]) -> str:
            nonlocal internal_count, converted_count, broken_count
            sid = match.group(1)
            internal_count += 1
            target_doc_id = block_to_doc.get(sid)
            if not target_doc_id:
                broken_count += 1
                return match.group(0)
            converted_count += 1
            return PLACEHOLDER_TARGET.format(sid=target_doc_id)

        text = SIYUAN_BLOCK_REF.sub(replace_block_ref, text)

        stats.internal_links_found = internal_count
        stats.internal_links_converted = converted_count
        stats.broken_links = broken_count
        stats.unsupported_database_blocks += len(re.findall(r"```siyuan-database", text))
        stats.unsupported_embedded_queries += len(re.findall(r"```siyuan-query", text))

        return ConversionResult(markdown=text, stats=stats)


def replace_placeholders(text: str, source_doc_id_to_trilium: dict[str, str]) -> str:
    """将占位符替换为 Trilium noteId。"""

    def replace(match: re.Match[str]) -> str:
        sid = match.group(1)
        trilium_id = source_doc_id_to_trilium.get(sid)
        if not trilium_id:
            return match.group(0)
        return f"[[{trilium_id}]]"

    return PLACEHOLDER_PATTERN.sub(replace, text)


def collect_referenced_documents(
    exports: dict[str, ExportResult],
    block_to_doc: dict[str, str],
) -> set[str]:
    """收集所有 Markdown 中被引用的源文档 ID。"""

    referenced: set[str] = set()
    for result in exports.values():
        if result.error:
            continue
        for match in PLACEHOLDER_PATTERN.finditer(replace_text_with_block_refs(result.markdown, block_to_doc)):
            referenced.add(match.group(1))
    return referenced


def replace_text_with_block_refs(text: str, block_to_doc: dict[str, str]) -> str:
    """临时把 siyuan://blocks/{id} 映射为文档占位符，供收集引用使用。"""

    def replace(match: re.Match[str]) -> str:
        sid = match.group(1)
        target = block_to_doc.get(sid, sid)
        return PLACEHOLDER_TARGET.format(sid=target)

    return SIYUAN_BLOCK_REF.sub(replace, text)