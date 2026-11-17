# combine_dots_ocr

Combine [dots.mocr](https://github.com/studio-dots-ai/dots.mocr) ([Live demo](https://dotsocr.xiaohongshu.com/)) per-page Markdown files into one Markdown document.

> You upload a PDF to dots.mocr, download the zip output, extract it — and get one Markdown file per page. This script merges them back into a single document, in page order.

---

## What it does

| | |
|---|---|
| **Input** | A folder with `demo_<id>_page_<N>.md` files (plus optional `_nohf`, `.json`, and image variants) |
| **Output** | One `combined.md` with all pages stitched together, plus optional JSON report and extracted images |
| **No install** | Just run `python3 combine_dots_ocr.py` — standard library only |

---

## Quick start

### Interactive (recommended first time)

```bash
python3 combine_dots_ocr.py
```

You'll be prompted for the document title. Everything else uses sensible defaults.

### CLI (fast / automation)

```bash
# Basic merge
python3 combine_dots_ocr.py ./dotsocr_output -o combined.md

# Web-ready: collapse repeated headers, extract images, add front matter + TOC
python3 combine_dots_ocr.py ./dotsocr_output -o combined.md \
  --source md \
  --dedupe-headers collapse \
  --extract-assets assets \
  --front-matter \
  --toc
```

### Multiple extracted zips (recursive) — one document per subfolder

If you extracted several dots.mocr zips into subfolders (one document per folder), use `--recursive`. Each subfolder is processed independently and produces its own Markdown file inside that folder:

```bash
python3 combine_dots_ocr.py ./all_extractions --recursive
```

You'll be asked: **"Name each output? (y/n)"**

- **n** (default) — each output is named after its folder (e.g. `doc_a/doc_a.md`, `doc_b/doc_b.md`)
- **y** — you're prompted to paste one custom name per line; blank line to finish

> Each subfolder must contain its own complete set of page files (`page_1`, `page_2`, …). The script does **not** merge page 1 from folder A with page 2 from folder B.

---

## Features

### Core
- **Page ordering** — merges `page_<N>.md` files in numeric order
- **Source selection** — choose `md`, `nohf`, or `auto` (prefers `nohf`, falls back to `md`)
- **Recursive scan** (`-r`) — walk subfolders for page files (useful after extracting multiple zips)
- **Gap detection** (`--strict`) — fail if page numbers are missing

### Output cleanup
- **Repeated header/footer handling** — `detect`, `collapse`, or `annotate` repeated page edges
- **Data-URI image extraction** (`--extract-assets`) — pulls embedded base64 images out into files
- **Printed-page detection** — reads printed page numbers from page edges when available

### Metadata & reporting
- **YAML front matter** (`--front-matter`, on by default) — title, page range, source mode, generated date
- **Table of contents** (`--toc`)
- **JSON manifest** (`--manifest`) — detailed per-page report written to a file
- **JSON summary** (`--json`) — prints a compact JSON summary to stdout (off by default)
- **Dry run** (`--dry-run`) — analyze and report without writing any files

---

## Usage examples

### Merge a single dots.mocr output

```bash
python3 combine_dots_ocr.py ./pdf_out -o my_document.md
```

### Web-ready output (collapse repeated headers, extract images, add front matter + TOC)

```bash
python3 combine_dots_ocr.py ./pdf_out -o my_document.md \
  --source md \
  --dedupe-headers collapse \
  --extract-assets assets \
  --toc
```

### Only analyze, don't write anything

```bash
python3 combine_dots_ocr.py ./pdf_out --dry-run
```

### Get a machine-readable summary

```bash
python3 combine_dots_ocr.py ./pdf_out --json
```

---

## All options

| Option | Short | What it does | Default |
|---|---|---|---|
| `input_dir` | — | Folder containing `page_*.md` files | `.` |
| `-o, --output` | — | Output Markdown file path | `<input_dir>/<slug>.md` |
| `--source` | — | Page source: `auto`, `md`, or `nohf` | `auto` |
| `--dedupe-headers` | — | Repeated edge handling: `off`, `detect`, `collapse`, `annotate` | `off` |
| `--edge-lines` | — | How many top/bottom lines to scan for headers/footers | `2` |
| `--repeat-threshold` | — | How many pages a pattern must repeat on to count | `3` |
| `--extract-assets` | — | Directory to extract embedded images into | (none) |
| `--assets-prefix` | — | URL prefix used in Markdown links for extracted assets | folder name |
| `--toc` | — | Add a table of contents | off |
| `--front-matter` | — | Include YAML front matter | on |
| `--no-front-matter` | — | Disable YAML front matter | — |
| `--title` | — | Document title for front matter | folder name |
| `--include-source-dir` | — | Add source folder path to front matter | off |
| `--manifest` | — | Write detailed JSON report. Pass without a value to auto-name `<slug>.manifest.json` next to the output, or give a custom path | (none) |
| `--separator` | — | Text between merged pages | `\n\n---\n\n` |
| `--strict` | — | Fail on missing pages or gaps | off |
| `--dry-run` | — | Analyze only, write nothing | off |
| `--json` | — | Print JSON summary to stdout | off |
| `--recursive` | `-r` | Treat each subfolder as a separate document (one output per folder) | off |
| `--version` | — | Show version and exit | — |

Run without arguments for interactive mode.

---

## JSON output: `--json` vs `--manifest`

These are independent and serve different purposes:

| | `--json` | `--manifest PATH` |
|---|---|---|
| **Where it goes** | Printed to stdout | Written to a file |
| **Detail level** | Compact summary (output path, page count, gaps, byte count, etc.) | Full per-page report (sources, extracted assets, detected/removed headers/footers, char counts, etc.) |
| **Default** | Off | Off (only runs when you pass a path) |

Example:

```bash
# Human-friendly run + machine summary
python3 combine_dots_ocr.py ./pdf_out -o out.md --json > summary.json

# Detailed report auto-named next to output (out.manifest.json)
python3 combine_dots_ocr.py ./pdf_out -o out.md --manifest

# Detailed report saved to a custom path
python3 combine_dots_ocr.py ./pdf_out -o out.md --manifest ./reports/audit.json
```

---

## Output files

Running the script produces, e.g.:

```
combined.md            # single merged Markdown document (always)
assets/                # extracted images (only with --extract-assets)
combined.manifest.json # detailed JSON report (only with --manifest; auto-named next to output)
```

Each page in `combined.md` gets an HTML anchor (`#page-1`, `#page-2`, …) and source comments, so you can link directly to a page.

> The manifest file is **not** created by default. Pass `--manifest` (auto-names `<slug>.manifest.json` next to output) or `--manifest path/to/file.json` for a custom path.

---

<details>
<summary>Technical notes</summary>

- Page files are matched with regex: `^(?P<prefix>.+)_page_(?P<num>\d+)(?P<suffix>_nohf)?\.md$`
- Repeated header/footer detection normalizes numbers to `<NUM>` and Roman numerals to `<ROM>` before comparing, so page numbers like "3" and "4" don't break detection.
- Data-URIs are decoded by MIME type and written as `.png`, `.jpg`, `.gif`, `.webp`, `.svg`, or `.bin`.
- Interactive mode prompts only for the document title; all other settings use web-friendly defaults.
- In `--recursive` mode, each subfolder is processed independently — one Markdown document per subfolder, written inside that subfolder. Page numbers are not shared across subfolders.

</details>
