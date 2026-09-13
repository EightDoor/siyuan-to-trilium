"""CLI 入口。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from .config import MigrationConfig, SiyuanConfig
from .migration import Migration
from .progress import ProgressReporter


app = typer.Typer(help="SiYuan → Trilium Markdown 导出工具（生成可导入 ZIP）。")


@app.command()
def main(
    siyuan_url: str = typer.Option(
        "http://127.0.0.1:6806",
        "--siyuan-url",
        help="思源 API 地址",
    ),
    siyuan_token: str = typer.Option(
        "", "--siyuan-token", help="思源 API Token（也可通过 SIYUAN_TOKEN 环境变量）"
    ),
    output: Path = typer.Option(
        Path("./output"), "--output", "-o", help="输出目录"
    ),
    exclude_notebooks: str = typer.Option(
        "",
        "--exclude-notebooks",
        help="按名称排除的笔记本，逗号分隔；默认不排除",
    ),
    keep_assets: bool = typer.Option(
        False,
        "--keep-assets/--clean-assets",
        help="是否保留已下载到 output/assets 的附件缓存",
    ),
    no_progress: bool = typer.Option(
        False, "--no-progress", help="关闭实时进度显示"
    ),
) -> None:
    """执行 Markdown 导出。"""

    siyuan_tok = siyuan_token or os.environ.get("SIYUAN_TOKEN", "")
    if not siyuan_tok:
        typer.echo("错误: 缺少思源 Token（通过 --siyuan-token 或 SIYUAN_TOKEN）", err=True)
        sys.exit(2)

    excluded = [name.strip() for name in exclude_notebooks.split(",") if name.strip()]
    progress = ProgressReporter(enabled=not no_progress)

    siyuan_cfg = SiyuanConfig(url=siyuan_url, token=siyuan_tok)
    migration_cfg = MigrationConfig(
        output_dir=output,
        keep_temp=keep_assets,
    )

    migration = Migration(
        siyuan_cfg,
        migration_cfg,
        excluded_notebooks=excluded,
        progress=progress,
    )
    result = migration.run()

    typer.echo("\n导出完成")
    typer.echo(f"Markdown 目录: {result.export.markdown_dir}")
    typer.echo(f"ZIP 文件:      {result.export.zip_path}")
    typer.echo(json.dumps(result.report["documents"], ensure_ascii=False))
    typer.echo(json.dumps(result.report["links"], ensure_ascii=False))
    typer.echo(json.dumps(result.report["assets"], ensure_ascii=False))
    typer.echo(json.dumps(result.report["export"], ensure_ascii=False))


if __name__ == "__main__":
    app()
