"""Trilium 适配器：导入 ZIP、生成备份。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .config import TriliumConfig
from .trilium_runner import TriliumRunner


class TriliumAdapter:
    """通过 ETAPI 导入 ZIP 并生成 .db 备份。"""

    def __init__(self, runner: TriliumRunner, config: TriliumConfig):
        self._runner = runner
        self._config = config

    @property
    def headers(self) -> dict[str, str]:
        token = self._runner.token
        headers: dict[str, str] = {"Content-Type": "application/octet-stream"}
        if token:
            headers["Authorization"] = token
        return headers

    def import_zip(self, zip_path: Path, parent_note_id: str = "root") -> dict[str, Any]:
        """POST /notes/{noteId}/import"""

        url = f"{self._runner.etapi_url}/notes/{parent_note_id}/import"
        with zip_path.open("rb") as fh:
            response = requests.post(
                url,
                data=fh.read(),
                headers=self._import_headers(),
                timeout=300,
            )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Trilium 导入失败: {response.status_code} {response.text[:200]}")
        return response.json() if response.text else {}

    def create_backup(self, name: str) -> None:
        """PUT /backup/{name}"""

        url = f"{self._runner.etapi_url}/backup/{name}"
        response = requests.put(url, headers=self._auth_headers(), timeout=120)
        if response.status_code not in (200, 204):
            raise RuntimeError(f"Trilium 备份失败: {response.status_code} {response.text[:200]}")

    def list_root_children(self) -> list[dict[str, Any]]:
        url = f"{self._runner.etapi_url}/notes/root"
        response = requests.get(url, headers=self._auth_headers(), timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        return data.get("childNoteIds", []) if isinstance(data, dict) else []

    def get_note(self, note_id: str) -> Optional[dict[str, Any]]:
        url = f"{self._runner.etapi_url}/notes/{note_id}"
        response = requests.get(url, headers=self._auth_headers(), timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None

    def walk_tree(self, parent_id: str = "root") -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        stack = [parent_id]
        visited: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            note = self.get_note(current)
            if not note:
                continue
            notes.append(note)
            children = note.get("childNoteIds", [])
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, str):
                        stack.append(child)
        return notes

    def update_note_content(self, note_id: str, content: str) -> None:
        url = f"{self._runner.etapi_url}/notes/{note_id}/content"
        response = requests.put(
            url,
            data=content.encode("utf-8"),
            headers=self._content_headers(),
            timeout=30,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"更新 Trilium note 内容失败 {note_id}: {response.status_code} {response.text[:200]}"
            )

    def find_backup_file(self, backup_name: str) -> Path:
        candidates = [
            self._runner.data_dir / "backup" / f"backup-{backup_name}.db",
            self._runner.data_dir / f"backup-{backup_name}.db",
        ]
        if self._runner.uses_external:
            # 外部实例：无法定位本地路径，需要通过 API 或临时实例生成
            raise RuntimeError("外部 Trilium 实例无法直接定位备份文件路径")
        deadline = time.time() + 30
        while time.time() < deadline:
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            time.sleep(1)
        raise FileNotFoundError(f"未找到 Trilium 备份文件: {backup_name}")

    @staticmethod
    def integrity_check(db_path: Path) -> str:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute("PRAGMA integrity_check")
            row = cursor.fetchone()
        return row[0] if row else "unknown"

    # ===== Header helpers =====

    def _import_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/octet-stream"}
        token = self._runner.token
        if token:
            headers["Authorization"] = token
        return headers

    def _content_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "text/plain"}
        token = self._runner.token
        if token:
            headers["Authorization"] = token
        return headers

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        token = self._runner.token
        if token:
            headers["Authorization"] = token
        return headers