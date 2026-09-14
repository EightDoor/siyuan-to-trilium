# siyuan-to-trilium

[English](./README_EN.md) | [中文](./README.md)

Export SiYuan notes into a Markdown ZIP that can be imported directly into [Trilium](https://github.com/zadam/trilium).

> Trilium's built-in "restore backup" relies on an unencrypted SQLite database. This repository only produces a **Markdown ZIP with `!!!meta.json`**. It no longer launches, modifies, or produces any Trilium database; the import is performed by the user in their own Trilium instance.

## Features

- Pulls all notebooks and documents via the SiYuan HTTP API (supports multi-level directory trees)
- Preserves the original order, hierarchy, and Markdown content
- Copies assets (images, PDFs, Office files, archives, etc.) into the same directory as their parent note, with file names formatted as `<title>_<asset>`
- Converts internal links into `[[S2T_NOTE:<title>]]` placeholders, which Trilium resolves to internal links by title after import
- The generated ZIP conforms to Trilium's internal import format (`formatVersion=2`). Use **Import into note → Markdown (ZIP)** in Trilium to restore the full note tree

## Requirements

- Python ≥ 3.10
- SiYuan Notes (desktop / Docker), with the HTTP API enabled and a Token issued
- Trilium (for importing the resulting ZIP)

## Installation

Recommended via `uv`:

```bash
git clone https://github.com/<your-org>/siyuan-to-trilium.git
cd siyuan-to-trilium
uv sync
```

Or via `pip`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Dependencies: `requests`, `typer`.

## Usage

### 1. Obtain a SiYuan Token

Open SiYuan → `Settings` → `About` → copy the API Token.

### 2. Run the export

```bash
export SIYUAN_TOKEN="<your-token>"

uv run python -m src.main \
  --siyuan-url http://127.0.0.1:6806 \
  --siyuan-token "$SIYUAN_TOKEN" \
  --output ./output
```

Parameters:

| Parameter | Description |
| --- | --- |
| `--siyuan-url` | SiYuan API base URL, defaults to `http://127.0.0.1:6806` |
| `--siyuan-token` | SiYuan API Token (can also be supplied via the `SIYUAN_TOKEN` environment variable) |
| `--output / -o` | Output directory, defaults to `./output` |
| `--exclude-notebooks` | Comma-separated list of notebook names to exclude; by default no notebook is excluded (the "Archive" notebook is included) |
| `--keep-assets / --clean-assets` | Whether to keep the downloaded asset cache; defaults to `--clean-assets` |
| `--no-progress` | Disable real-time progress display |

After export:

- Markdown directory: `./output/markdown-export/siyuan-archive/`
- Importable ZIP: `./output/markdown-export/siyuan-archive.zip`

### 3. Import into Trilium

In the Trilium note tree, right-click any note → `Import into note` → `Markdown (ZIP)` → select the generated `siyuan-archive.zip`.

The ZIP root contains `!!!meta.json`, which Trilium uses to reconstruct note hierarchy, Markdown type, and order.

### 4. Clean up auto-generated metadata at the top of imported notes

The YAML front matter from SiYuan (`title` / `date` / `lastmod`) is converted into
an `<h2>` block at the top of each note during import. This repo ships an
independent Trilium JS Backend script that removes that metadata after the import
is complete.

- Script: [`scripts/trilium-cleanup-imported-metadata.js`](./scripts/trilium-cleanup-imported-metadata.js)
- Usage guide: [`docs/trilium-cleanup-imported-metadata.md`](./docs/trilium-cleanup-imported-metadata.md)

## Key design decisions

- **`meta.files` lists notebooks at the top level**: Trilium's `getMeta` walks the ZIP path starting from `meta.files`. Wrapping everything in an extra `root` segment breaks the first notebook-name match and causes attachments like `.xlsx` to be treated as ordinary files, triggering spreadsheet parse errors.
- **Assets live alongside their Markdown file**: file names use `<title>_<asset>`, so `getAttachmentMeta` matches via `dataFileName` against the children list and avoids misidentification.
- **Pre-order ZIP writing**: parents are written before children, and assets are written right after their parent note but before any children. Otherwise Trilium's sequential parsing reports `Parent note 'xxx' was not found.`
- **`xlsx / xls / csv` MIME normalized to `application/octet-stream`**: prevents Trilium from failing inside `convertSpreadsheetContent` on attachments whose content cannot be parsed as a spreadsheet.

## Project structure

```
src/
  main.py               # Typer CLI entry point
  config.py             # Configuration dataclass
  siyuan.py             # SiYuan HTTP API client
  inventory.py          # Notebook and document tree index (built from hpath)
  converter.py          # Markdown block-level conversion and link placeholders
  assets.py             # Asset collection, download, and local fallback
  migration.py          # End-to-end migration pipeline
  trilium_archive.py    # !!!meta.json and ZIP generation
  progress.py           # Real-time progress reporting
  report.py             # migration-report.json output

scripts/
  trilium-cleanup-imported-metadata.js   # Trilium JS Backend: clean up imported top metadata

docs/
  trilium-cleanup-imported-metadata.md   # Usage guide for the cleanup script

tests/
  test_siyuan.py
  test_inventory.py
  test_converter.py
  test_assets.py
  test_progress.py
  test_report.py
  test_trilium_archive.py
```

## Development

```bash
uv sync
uv run pytest -q
```

Tests build isolated SiYuan inventory / Markdown fixtures with `tmp_path`, so they do not require a running SiYuan instance.

## Security and privacy

- By default, note content is never sent to any external service. All API requests go only to the SiYuan instance at `--siyuan-url`.
- The Token is only used as an HTTP request header and is never written into any export file.
- The directory at `--output` may contain full note bodies. Clean it up yourself before publishing.

## Known limitations

- Only the Markdown ZIP is produced; no Trilium database is generated or modified. Import must be triggered manually in your Trilium instance.
- Blocks such as `database` / `embedded query` in SiYuan have no generic Markdown equivalent. They are flagged as unsupported and skipped without aborting the export.
- Internal links are matched by title. After import, Trilium resolves them via `notePath` and `title`; ambiguous titles may resolve to the first match.

## Acknowledgements

- [SiYuan Notes](https://github.com/siyuan-note/siyuan)
- [Trilium Notes](https://github.com/zadam/trilium)

## License

MIT
