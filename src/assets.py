"""附件收集与下载。"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from .models import Asset, ExportResult, MigrationStats
from .siyuan import SiyuanClient


# Trilium 在导入时若附件 mime 为以下值，会按 spreadsheet 笔记类型解析，
# 进而依赖动态模块 parse_from_xlsx.js，缺失时中断整个 ZIP 导入。
# 统一降级为 octet-stream，使 Trilium 按普通文件附件处理。
_SPREADSHEET_MIMES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
}


def _normalize_attachment_mime(mime: str) -> str:
    """把 csv/xlsx 等触发 Trilium spreadsheet 自动转换的 mime 改成 octet-stream。"""

    return "application/octet-stream" if mime in _SPREADSHEET_MIMES else mime


IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HTML_IMG_PATTERN = re.compile(
    r"""<img[^>]+(?:src|data-src)=["']([^"']+)["']""",
    re.IGNORECASE,
)
HTML_BG_PATTERN = re.compile(
    r"""url\(([^)\s"']+)\)""",
    re.IGNORECASE,
)
HTML_LINK_PATTERN = re.compile(
    r"""(?:href|src)=["']([^"']+)["']""",
    re.IGNORECASE,
)


def _normalize_source_path(ref: str) -> str:
    """去除 ./ 与 ../ 前缀，统一为工作空间内路径（如 assets/foo.png）。"""

    path = ref.strip()
    while path.startswith(("./", "../")):
        path = path[2:] if path.startswith("./") else path[3:]
    return path


def _lookup_rewrite(rewrite_map: dict[str, str], ref: str) -> Optional[str]:
    """按原始引用查找重写目标，找不到时用归一化路径再试一次。"""

    target = rewrite_map.get(ref)
    if target is None:
        target = rewrite_map.get(_normalize_source_path(ref))
    return target


# 可被当作附件下载的扩展名白名单。其余后缀（如 Angular 模板里的变量
# `item.headPortrait`、CSS 占位等）不作为附件处理，避免无效 API 调用与 missing 噪声。
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".mp3", ".mp4", ".wav", ".mov", ".avi", ".mkv",
    ".json", ".txt", ".md", ".csv", ".yaml", ".yml",
    ".js", ".css",  # HTML 嵌入代码会引用
}


class AssetCollector:
    """下载思源资源到本地目录，并重写 Markdown 引用。"""

    def __init__(
        self,
        client: SiyuanClient,
        output_root: Path,
        stats: MigrationStats,
        workspace_path: Optional[Path] = None,
    ):
        self._client = client
        self._output_root = output_root
        self._stats = stats
        # 思源工作空间本地数据目录（.../data），API 下载失败时按本地路径回退复制。
        self._workspace_path = workspace_path
        self._rewrite_map: dict[str, str] = {}
        # 已成功收集的归一化源路径，用于去重（Markdown 与 HTML 模式可能重叠）。
        self._collected_sources: set[str] = set()

    @property
    def rewrite_map(self) -> dict[str, str]:
        return self._rewrite_map

    def collect_for(self, result: ExportResult) -> list[Asset]:
        """收集 Markdown 中的附件链接并写入本地。"""

        if result.error:
            return result.assets

        assets_root = self._output_root / "assets"
        assets_root.mkdir(parents=True, exist_ok=True)

        text = result.markdown
        references: list[str] = []
        for pattern in (
            IMAGE_PATTERN,
            LINK_PATTERN,
            HTML_IMG_PATTERN,
            HTML_BG_PATTERN,
            HTML_LINK_PATTERN,
        ):
            for match in pattern.finditer(text):
                references.append(match.group(1))

        for ref in references:
            asset = self._handle_reference(result.document, ref, assets_root)
            if asset:
                result.assets.append(asset)
        return result.assets

    def _handle_reference(
        self, document, ref: str, assets_root: Path
    ) -> Optional[Asset]:
        cleaned = ref.strip()
        if not cleaned or cleaned.startswith(("http://", "https://", "data:", "mailto:")):
            return None

        # 模板占位符 / 纯变量名（不含 / 或 .）—— 跳过。
        if "/" not in cleaned and "." not in cleaned:
            return None

        # 含大括号 / 双大括号 / 美元符号的模板变量 —— 跳过。
        if any(c in cleaned for c in ("{", "}", "$", "<", ">")):
            return None

        # 后缀不是常见附件扩展名 —— 跳过（避免把 CSS 字体、Angular 模板占位当附件）。
        suffix = Path(cleaned.split("?", 1)[0]).suffix.lower()
        if suffix and suffix not in ALLOWED_EXTENSIONS:
            return None

        source_path = _normalize_source_path(cleaned)
        if source_path in self._collected_sources:
            # 同一引用已被收集过（Markdown 与 HTML 模式可能重叠），跳过避免重复下载。
            return None

        self._stats.assets_found += 1
        target_name = self._target_filename(document, source_path)
        target_path = assets_root / target_name

        # 思源的 /api/file/getFile 要求路径以工作空间根 /data/ 开头，
        # 而 Markdown/HTML 中的引用是相对路径，需要补全前缀。
        api_path = source_path
        if not api_path.startswith("/data/"):
            api_path = f"/data/{api_path}"
        # 思源拒绝 URL 编码形式的 basename，只接受字面字符（空格、@、中文等），
        # 因此调用接口前必须解码 Markdown 中可能存在的编码引用。
        api_path = unquote(api_path)

        try:
            content = self._client.get_file(api_path)
            target_path.write_bytes(content)
        except Exception:
            # API 下载失败时，尝试从思源工作空间按本地路径直接复制。
            if not self._copy_from_workspace(cleaned, target_path):
                self._stats.assets_missing += 1
                return None

        self._stats.assets_copied += 1
        # key 保留原始引用（含 URL 编码），供 Markdown 重写时精确匹配。
        self._rewrite_map[cleaned] = f"assets/{target_name}"
        self._collected_sources.add(source_path)
        # target_name 含 sha1 前缀，但 MIME 推断只看扩展名，结果不受影响。
        mime = mimetypes.guess_type(target_name)[0] or "application/octet-stream"
        mime = _normalize_attachment_mime(mime)
        return Asset(
            relative_path=f"assets/{target_name}",
            source_path=source_path,
            doc_id=document.id,
            doc_path=document.path,
            mime=mime,
        )

    def _copy_from_workspace(self, cleaned: str, target_path: Path) -> bool:
        """按 ``<box_id>/<file>`` 形式从本地工作空间复制资源。

        思源 API 对含空格等特殊字符的 box 路径可能返回空文件或报错，此时直接读取
        ``<workspace>/<ref>``。仅当文件确实存在且位于工作空间内时才复制。
        """

        if self._workspace_path is None:
            return False

        local_ref = _normalize_source_path(unquote(cleaned.split("?", 1)[0]))
        # 去掉可能存在的 /data/ 前缀，其余按工作空间内相对路径解析。
        if local_ref.startswith("/data/"):
            local_ref = local_ref[len("/data/"):]
        elif local_ref.startswith("/"):
            return False

        try:
            candidate = (self._workspace_path / local_ref).resolve()
            workspace_root = self._workspace_path.resolve()
        except OSError:
            return False

        # 防止 ../ 路径穿越，只允许工作空间内的文件。
        if workspace_root != candidate and workspace_root not in candidate.parents:
            return False
        if not candidate.is_file():
            return False

        try:
            target_path.write_bytes(candidate.read_bytes())
        except OSError:
            return False
        return True

    @staticmethod
    def _target_filename(document, source_path: str) -> str:
        digest = hashlib.sha1(f"{document.id}:{source_path}".encode("utf-8")).hexdigest()[:10]
        # 源路径可能带 URL 编码，先解码取真实 basename。
        # 落盘文件名保持字面字符（不编码），与重写后的 Markdown 引用一致。
        basename = Path(unquote(source_path)).name or "asset"
        return f"{digest}-{basename}"


def rewrite_markdown_references(text: str, rewrite_map: dict[str, str]) -> str:
    """将 Markdown/HTML 中指向本地资源的链接重写为相对路径。"""

    def replace_image(match: re.Match[str]) -> str:
        prefix = match.group(0).split("](")[0]
        alt = prefix[2:]
        target = _lookup_rewrite(rewrite_map, match.group(1))
        if target is None:
            target = match.group(1)
        return f"![{alt}]({target})"

    def replace_link(match: re.Match[str]) -> str:
        prefix = match.group(0).split("](")[0]
        label = prefix[1:]
        target = _lookup_rewrite(rewrite_map, match.group(1))
        if target is None:
            target = match.group(1)
        return f"[{label}]({target})"

    def replace_html_attr(match: re.Match[str]) -> str:
        ref = match.group(1)
        target = _lookup_rewrite(rewrite_map, ref)
        if target is None:
            return match.group(0)
        offset = match.start(1) - match.start()
        return match.group(0)[:offset] + target + match.group(0)[offset + len(ref):]

    text = IMAGE_PATTERN.sub(replace_image, text)
    text = LINK_PATTERN.sub(replace_link, text)
    text = HTML_IMG_PATTERN.sub(replace_html_attr, text)
    text = HTML_BG_PATTERN.sub(replace_html_attr, text)
    text = HTML_LINK_PATTERN.sub(replace_html_attr, text)
    return text