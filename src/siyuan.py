"""思源 API 客户端封装。"""

from __future__ import annotations

import time
from typing import Any, Optional, Union

import requests

from .config import SiyuanConfig


class SiyuanError(Exception):
    """思源 API 调用错误。"""


class SiyuanClient:
    """封装思源 HTTP API 的最小客户端。"""

    def __init__(self, config: SiyuanConfig):
        self._config = config
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Token {config.token}",
                "Content-Type": "application/json",
            }
        )

    def _post(self, endpoint: str, payload: Optional[dict[str, Any]] = None) -> Any:
        url = self._url(endpoint)
        last_error: Optional[Exception] = None
        for attempt in range(self._config.max_retries):
            try:
                response = self._session.post(
                    url,
                    json=payload or {},
                    timeout=(self._config.connect_timeout, self._config.read_timeout),
                )
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
                continue

            if response.status_code == 202:
                # 思源使用 202 表示业务异常
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                raise SiyuanError(
                    f"思源 API 业务异常 {endpoint}: code={body.get('code')} msg={body.get('msg')}"
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise SiyuanError(f"思源 API 返回非 JSON {endpoint}: {exc}") from exc

            if body.get("code") not in (0, None):
                raise SiyuanError(
                    f"思源 API 错误 {endpoint}: code={body.get('code')} msg={body.get('msg')}"
                )

            data = body.get("data")
            return data if data is not None else {}

        raise SiyuanError(f"思源 API 重试失败 {endpoint}: {last_error}")

    def _url(self, endpoint: str) -> str:
        base = self._config.url.rstrip("/")
        return f"{base}/api/{endpoint.lstrip('/')}"

    # ===== Notebooks =====

    def list_notebooks(self) -> list[dict[str, Any]]:
        result = self._post("notebook/lsNotebooks")
        notebooks = result.get("notebooks", []) if isinstance(result, dict) else []
        return notebooks if isinstance(notebooks, list) else []

    # ===== Filetree =====

    def get_root_doc_id(self, notebook_id: str) -> Optional[str]:
        ids = self._post("filetree/getIDsByHPath", {"notebook": notebook_id, "path": "/"})
        if isinstance(ids, list) and ids:
            value = ids[0]
            return value if isinstance(value, str) else None
        return None

    def get_child_blocks(self, block_id: str) -> list[dict[str, Any]]:
        result = self._post("block/getChildBlocks", {"id": block_id})
        return result if isinstance(result, list) else []

    def query_sql(self, stmt: str) -> list[dict[str, Any]]:
        """直接调用 /api/query/sql，仅在开发/调试模式可用。"""

        result = self._post("query/sql", {"stmt": stmt})
        return result if isinstance(result, list) else []

    def list_docs_by_path(self, notebook_id: str, path: str = "/") -> dict[str, Any]:
        """列出指定路径下的子文档项。"""

        result = self._post("filetree/listDocsByPath", {"notebook": notebook_id, "path": path})
        return result if isinstance(result, dict) else {}

    def get_hpath_by_id(self, block_id: str) -> str:
        result = self._post("filetree/getHPathByID", {"id": block_id})
        return result if isinstance(result, str) else ""

    def get_path_by_id(self, block_id: str) -> dict[str, Any]:
        result = self._post("filetree/getPathByID", {"id": block_id})
        return result if isinstance(result, dict) else {}

    # ===== Export =====

    def export_md(self, doc_id: str) -> dict[str, Any]:
        result = self._post("export/exportMdContent", {"id": doc_id})
        return result if isinstance(result, dict) else {}

    # ===== Assets =====

    def get_file(self, path: str) -> bytes:
        """下载资源文件二进制。"""

        url = self._url("file/getFile")
        response = self._session.post(
            url,
            json={"path": path},
            timeout=(self._config.connect_timeout, self._config.read_timeout),
        )
        if response.status_code != 200:
            raise SiyuanError(
                f"下载资源失败 {path}: status={response.status_code} body={response.text[:200]}"
            )
        return response.content