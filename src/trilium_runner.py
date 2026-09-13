"""Trilium 实例管理：优先复用现有实例，必要时启动新实例。"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from .config import TriliumConfig


class TriliumRunner:
    """Trilium 实例管理：

    - external_url 非空时，仅作为客户端复用外部实例，不启动新进程
    - external_url 为空时，按配置启动隔离的临时实例
    """

    def __init__(self, config: TriliumConfig, external_url: Optional[str] = None, external_token: Optional[str] = None):
        self._config = config
        self._external_url = external_url
        self._external_token = external_token
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._owns_process = False

    def __enter__(self) -> "TriliumRunner":
        if self._external_url:
            self._wait_until_ready(self._external_url)
            return self
        self._start_local()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owns_process:
            self._stop_local()

    # ===== Local lifecycle =====

    def _start_local(self) -> None:
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["TRILIUM_DATA_DIR"] = str(self._config.data_dir)
        env["TRILIUM_NETWORK_PORT"] = str(self._config.port)
        env["TRILIUM_NETWORK_HOST"] = self._config.host
        cmd = [self._config.app_path]
        self._process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._owns_process = True
        self._wait_until_ready(self.base_url)

    def _stop_local(self) -> None:
        if self._process and self._process.poll() is None:
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._owns_process = False

    # ===== URL helpers =====

    @property
    def base_url(self) -> str:
        if self._external_url:
            return self._external_url.rstrip("/")
        return f"http://{self._config.host}:{self._config.port}"

    @property
    def etapi_url(self) -> str:
        if self._external_url:
            # 兼容两种写法：已包含 /etapi 或未包含
            base = self._external_url.rstrip("/")
            if base.endswith("/etapi"):
                return base
            return f"{base}{self._config.etapi_path}"
        return f"{self.base_url}{self._config.etapi_path}"

    @property
    def token(self) -> str:
        return self._external_token or ""

    @property
    def data_dir(self) -> Path:
        return self._config.data_dir

    @property
    def uses_external(self) -> bool:
        return self._external_url is not None

    # ===== Health =====

    def _wait_until_ready(self, url: str) -> None:
        deadline = time.time() + self._config.boot_timeout
        last_error = ""
        while time.time() < deadline:
            try:
                response = requests.get(f"{url}/etapi/app-info", timeout=2)
            except requests.RequestException as exc:
                last_error = str(exc)
                time.sleep(1)
                continue
            if response.status_code in (200, 401):
                # 401 表示 ETAPI 已就绪但需要 Token；说明服务在线
                return
            last_error = f"status={response.status_code}"
            time.sleep(1)
        raise RuntimeError(f"Trilium 未就绪：{last_error or 'timeout'}")

    def app_info(self) -> dict:
        headers = self._auth_headers()
        response = requests.get(f"{self.etapi_url}/app-info", headers=headers, timeout=5)
        response.raise_for_status()
        return response.json()

    def _auth_headers(self) -> dict[str, str]:
        token = self.token
        if token:
            return {"Authorization": token}
        return {}