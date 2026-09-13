"""数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Notebook:
    id: str
    name: str


@dataclass
class Document:
    id: str
    notebook_id: str
    title: str
    path: str  # 人类可读路径，例如 /开发/Java/Spring Boot
    parent_id: Optional[str] = None


@dataclass
class Asset:
    relative_path: str  # 在 assets/ 下的相对路径
    source_path: str  # 思源 API 中的 path
    doc_id: str
    doc_path: str
    # 附件 MIME，写入 Trilium attachment meta 时使用；spreadsheet 类型会被规范化。
    mime: Optional[str] = None


@dataclass
class ExportResult:
    document: Document
    markdown: str = ""
    error: Optional[str] = None
    assets: list[Asset] = field(default_factory=list)
    internal_link_count: int = 0
    converted_link_count: int = 0
    broken_link_count: int = 0


@dataclass
class MigrationStats:
    documents_found: int = 0
    documents_exported: int = 0
    documents_failed: int = 0
    documents_excluded: int = 0
    internal_links_found: int = 0
    internal_links_converted: int = 0
    broken_links: int = 0
    assets_found: int = 0
    assets_copied: int = 0
    assets_missing: int = 0
    unsupported_database_blocks: int = 0
    unsupported_embedded_queries: int = 0