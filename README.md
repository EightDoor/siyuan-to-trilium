# siyuan-to-trilium

[English](./README_EN.md) | 中文

将思源笔记的内容导出为可直接导入 [Trilium](https://github.com/zadam/trilium) 的 Markdown ZIP。

> Trilium 自带的「恢复备份」使用未加密 SQLite 数据库。本仓库只生成 **带 `!!!meta.json` 的 Markdown ZIP**，不再启动、修改或产出任何 Trilium 数据库；导入由用户在自己的 Trilium 实例里完成。

## 功能

- 通过思源 HTTP API 拉取所有笔记本与文档（支持多级目录树）
- 保留原始顺序、层级与 Markdown 内容
- 按笔记同目录复制附件（图片、PDF、Office、压缩包等），文件名 `<标题>_<附件>`
- 内部链接转换为 `[[S2T_NOTE:<标题>]]` 占位符，导入后由 Trilium 按标题自动解析为内部链接
- 生成的 ZIP 符合 Trilium 的内部导入格式（`formatVersion=2`），在 Trilium 中通过 **Import into note → Markdown (ZIP)** 即可还原完整笔记树

## 环境

- Python ≥ 3.10
- 思源笔记（桌面端 / Docker），需开启 HTTP API 并拿到 Token
- Trilium（用于最终导入 ZIP）

## 安装

推荐使用 `uv`：

```bash
git clone https://github.com/<your-org>/siyuan-to-trilium.git
cd siyuan-to-trilium
uv sync
```

或使用 `pip`：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

依赖：`requests`、`typer`。

## 使用

### 1. 准备思源 Token

打开思源 → `设置` → `关于` → 复制 API Token。

### 2. 运行导出

```bash
export SIYUAN_TOKEN="<your-token>"

uv run python -m src.main \
  --siyuan-url http://127.0.0.1:6806 \
  --siyuan-token "$SIYUAN_TOKEN" \
  --output ./output
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--siyuan-url` | 思源 API 地址，默认 `http://127.0.0.1:6806` |
| `--siyuan-token` | 思源 API Token（也可通过环境变量 `SIYUAN_TOKEN` 传入） |
| `--output / -o` | 输出目录，默认 `./output` |
| `--exclude-notebooks` | 按名称排除的笔记本，逗号分隔；默认不排除（包含「归档」笔记本） |
| `--keep-assets / --clean-assets` | 是否保留已下载的附件缓存，默认 `--clean-assets` |
| `--no-progress` | 关闭实时进度显示 |

导出完成后：

- Markdown 目录：`./output/markdown-export/siyuan-archive/`
- 可导入 ZIP：`./output/markdown-export/siyuan-archive.zip`

### 3. 在 Trilium 中导入

在 Trilium 笔记树中右键任意笔记 → `Import into note` → `Markdown (ZIP)` → 选择生成的 `siyuan-archive.zip`。

ZIP 根目录包含 `!!!meta.json`，Trilium 会按 meta 还原笔记层级、Markdown 类型与顺序。

### 4. 清理导入后顶部的自动生成内容

思源笔记的 YAML front matter（`title` / `date` / `lastmod`）在导入后会被
Trilium 转换为文档顶部的 `<h2>` 标题块。本仓库提供独立的 Trilium JS Backend
脚本，在导入完成后一次性清理这些元数据。

- 脚本：[`scripts/trilium-cleanup-imported-metadata.js`](./scripts/trilium-cleanup-imported-metadata.js)
- 使用文档：[`docs/trilium-cleanup-imported-metadata.md`](./docs/trilium-cleanup-imported-metadata.md)

## 关键设计

- **`meta.files` 顶层笔记本列表**：Trilium 的 `getMeta` 从 `meta.files` 起逐段匹配 ZIP 路径，外层包一层 `root` 会让第一段笔记本名匹配失败，进而把 xlsx 等附件按普通文件处理触发 spreadsheet 解析报错。
- **附件与所属 Markdown 同目录**：文件名 `<标题>_<附件>`，`getAttachmentMeta` 会按 `dataFileName` 在 children 中命中，避免误识别。
- **ZIP 先序写入**：父文档先于子文档，附件紧随所属文档且早于子文档；否则 Trilium 顺序解析时会报 `Parent note 'xxx' was not found.`。
- **`xlsx / xls / csv` 的 MIME 归一化为 `application/octet-stream`**：避免 Trilium 在 `convertSpreadsheetContent` 阶段对内容不可解析的附件报错。

## 项目结构

```
src/
  main.py               # Typer CLI 入口
  config.py             # 配置数据类
  siyuan.py             # 思源 HTTP API 客户端
  inventory.py          # 笔记本与文档树索引（按 hpath 构建层级）
  converter.py          # Markdown 块级元素转换与链接占位符
  assets.py             # 附件收集、下载与本地 fallback
  migration.py          # 端到端迁移流程
  trilium_archive.py    # !!!meta.json 与 ZIP 生成
  progress.py           # 实时进度上报
  report.py             # migration-report.json 输出

scripts/
  trilium-cleanup-imported-metadata.js   # Trilium JS Backend：清理导入后顶部元数据

docs/
  trilium-cleanup-imported-metadata.md   # 清理脚本使用文档

tests/
  test_siyuan.py
  test_inventory.py
  test_converter.py
  test_assets.py
  test_progress.py
  test_report.py
  test_trilium_archive.py
```

## 开发

```bash
uv sync
uv run pytest -q
```

测试使用 `tmp_path` 构造隔离的思源 inventory / Markdown 内容，不依赖真实思源服务。

## 安全与隐私

- 默认不向任何外部服务发送你的笔记内容；所有 API 请求只发往 `--siyuan-url` 指向的思源实例。
- Token 仅用于 HTTP 请求头，不写入任何导出文件。
- `--output` 指向的目录可能包含完整笔记正文，发布前请自行清理。

## 已知限制

- 只生成 Markdown ZIP，不再产出或修改 Trilium 数据库；导入需在你的 Trilium 实例里手动完成。
- 思源中 `database` / `embedded query` 等块在 Markdown 中没有通用对应，会标注为 unsupported 并跳过，不会阻断导出。
- 内部链接以「标题」匹配；同名文档在导入时 Trilium 会按 `notePath` 与 `title` 自动解析，存在多义标题时可能链接到第一个命中。

## 致谢

- [思源笔记](https://github.com/siyuan-note/siyuan)
- [Trilium Notes](https://github.com/zadam/trilium)

## License

MIT
