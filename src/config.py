"""配置与数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SiyuanConfig:
    url: str = "http://127.0.0.1:6806"
    token: str = ""
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_retries: int = 3


@dataclass
class TriliumConfig:
    app_path: str = "/Applications/Trilium Notes.app/Contents/MacOS/trilium"
    port: int = 12345
    host: str = "127.0.0.1"
    etapi_path: str = "/etapi"
    data_dir: Path = field(default_factory=lambda: Path("./output/trilium-data"))
    boot_timeout: float = 60.0


@dataclass
class MigrationConfig:
    output_dir: Path = Path("./output")
    keep_temp: bool = True
    trilium_target_version: str = "0.105.0"