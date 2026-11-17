#!/usr/bin/env python3
"""
Combine dots.mocr per-page markdown outputs into a single markdown document.

Interactive mode: run without arguments to be prompted for options.
CLI mode: provide arguments directly.

Designed for folders containing files like:
  demo_<id>_page_<N>.md
  demo_<id>_page_<N>_nohf.md
  demo_<id>_page_<N>.json (optional)
  demo_<id>_page_<N>.jpg  (optional)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__version__ = "1.0.0"


def slugify(s: str) -> str:
    """Sanitize to a safe filename/folder name: keep spaces, drop path-unsafe chars."""
    s = s.strip()
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "assets"


def urlquote(s: str) -> str:
    """URL-encode a path segment so markdown image links render with spaces."""
    from urllib.parse import quote
    return quote(s, safe="/")

PAGE_RE = re.compile(r"^(?P<prefix>.+)_page_(?P<num>\d+)(?P<suffix>_nohf)?\.md$", re.IGNORECASE)
DATA_URI_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<uri>data:(?P<mime>[^;,]+)?(?:;base64)?,(?P<data>[^\)]+))\)")
PRINTED_PAGE_RE = re.compile(
    r"^\s*(?:page\s*)?(?P<num>(?:\d{1,4}|[ivxlcdm]{1,8}))(?:\s*/\s*(?:\d{1,4}|[ivxlcdm]{1,8}))?\s*$",
    re.IGNORECASE,
)


@dataclass
class PageChoice:
    index: int
    source_path: Path
    source_kind: str  # md or nohf
    md_path: Optional[Path]
    nohf_path: Optional[Path]
    image_path: Optional[Path]
    json_path: Optional[Path]


def discover_pages(input_dir: Path, recursive: bool = False) -> Dict[int, Dict[str, Path]]:
    pages: Dict[int, Dict[str, Path]] = defaultdict(dict)
    seen: Dict[int, str] = {}
    iterator = input_dir.rglob("*") if recursive else input_dir.iterdir()
    for p in iterator:
        if not p.is_file():
            continue
        m = PAGE_RE.match(p.name)
        if m:
            idx = int(m.group("num"))
            key = "nohf" if m.group("suffix") else "md"
            if idx in seen and seen[idx] != str(p.parent):
                print(f"warning: duplicate page {idx} found in {p.parent} (already in {seen[idx]}); later file wins", file=sys.stderr)
            seen[idx] = str(p.parent)
            pages[idx][key] = p
            continue
        m2 = re.match(r"^(?P<prefix>.+)_page_(?P<num>\d+)\.(?P<ext>jpg|jpeg|png|json)$", p.name, re.IGNORECASE)
        if m2:
            idx = int(m2.group("num"))
            ext = m2.group("ext").lower()
            key = "image" if ext in ("jpg", "jpeg", "png") else ext
            if idx in seen and seen[idx] != str(p.parent):
                print(f"warning: duplicate page {idx} found in {p.parent} (already in {seen[idx]}); later file wins", file=sys.stderr)
            seen[idx] = str(p.parent)
            pages[idx][key] = p
    return pages


def find_page_subdirs(input_dir: Path) -> List[Path]:
    """Return subdirectories that contain page markdown files, sorted by name."""
    subdirs: List[Path] = []
    for p in sorted(input_dir.iterdir()):
        if not p.is_dir():
            continue
        for child in p.iterdir():
            if child.is_file() and PAGE_RE.match(child.name):
                subdirs.append(p)
                break
    return subdirs


def choose_source(pages: Dict[int, Dict[str, Path]], mode: str) -> List[PageChoice]:
    indices = sorted(pages.keys())
    choices: List[PageChoice] = []
    for idx in indices:
        entry = pages[idx]
        md = entry.get("md")
        nohf = entry.get("nohf")
        if mode == "nohf":
            chosen = nohf or md
            kind = "nohf" if nohf else "md"
        elif mode == "md":
            chosen = md or nohf
            kind = "md" if md else "nohf"
        else:  # auto
            chosen = nohf or md
            kind = "nohf" if nohf else "md"
        if not chosen:
            continue
        choices.append(PageChoice(
            index=idx,
            source_path=chosen,
            source_kind=kind,
            md_path=md,
            nohf_path=nohf,
            image_path=entry.get("image"),
            json_path=entry.get("json"),
        ))
    return choices


def normalize_edge_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\b\d{1,4}\b", "<NUM>", s)
    s = re.sub(r"\b[ivxlcdm]{1,8}\b", "<ROM>", s, flags=re.IGNORECASE)
    return s


def extract_printed_page(text: str, edge_lines: int = 2) -> Optional[str]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    candidates = lines[:edge_lines] + lines[-edge_lines:] if lines else []
    for ln in candidates:
        s = ln.strip().strip("-–—_ ")
        m = PRINTED_PAGE_RE.match(s)
        if m:
            return m.group("num")
    return None


def detect_repeated_edges(texts: List[str], edge_lines: int, threshold: int) -> Tuple[set, set]:
    top_counter = Counter()
    bottom_counter = Counter()
    for t in texts:
        lines = [ln.rstrip() for ln in t.splitlines()]
        nonempty = [ln for ln in lines if ln.strip()]
        tops = [normalize_edge_line(ln) for ln in nonempty[:edge_lines]]
        bottoms = [normalize_edge_line(ln) for ln in nonempty[-edge_lines:]] if nonempty else []
        for x in set(tops):
            if x:
                top_counter[x] += 1
        for x in set(bottoms):
            if x:
                bottom_counter[x] += 1
    repeated_tops = {k for k, v in top_counter.items() if v >= threshold and len(k) <= 160}
    repeated_bottoms = {k for k, v in bottom_counter.items() if v >= threshold and len(k) <= 160}
    return repeated_tops, repeated_bottoms


def apply_edge_handling(text: str, edge_lines: int, repeated_tops: set, repeated_bottoms: set, mode: str):
    if mode == "off":
        return text, [], [], [], []
    lines = text.splitlines()
    nonempty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    top_idx = nonempty_idx[:edge_lines]
    bottom_idx = nonempty_idx[-edge_lines:] if nonempty_idx else []
    detected_h, detected_f, removed_h, removed_f = [], [], [], []
    remove_set = set()
    for i in top_idx:
        norm = normalize_edge_line(lines[i])
        if norm in repeated_tops:
            detected_h.append(lines[i].strip())
            if mode in ("collapse", "annotate"):
                removed_h.append(lines[i].strip())
                remove_set.add(i)
    for i in bottom_idx:
        norm = normalize_edge_line(lines[i])
        if norm in repeated_bottoms:
            detected_f.append(lines[i].strip())
            if mode in ("collapse", "annotate"):
                removed_f.append(lines[i].strip())
                remove_set.add(i)
    if mode == "detect":
        return text, detected_h, detected_f, [], []
    new_lines = [ln for i, ln in enumerate(lines) if i not in remove_set]
    new_text = "\n".join(new_lines).strip()
    return new_text, detected_h, detected_f, removed_h, removed_f


def extract_data_uris(text: str, assets_dir: Path, rel_assets_prefix: str, page_index: int, counters: Dict[str, int]):
    assets_dir.mkdir(parents=True, exist_ok=True)
    extracted = []

    def repl(m: re.Match) -> str:
        alt = m.group("alt") or "image"
        mime = (m.group("mime") or "application/octet-stream").lower()
        b64 = m.group("data")
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            return m.group(0)
        ext = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
        }.get(mime, ".bin")
        counters["img"] += 1
        h = hashlib.sha1(raw).hexdigest()[:10]
        fname = f"page_{page_index:04d}_img_{counters['img']:02d}_{h}{ext}"
        out_path = assets_dir / fname
        if not out_path.exists():
            out_path.write_bytes(raw)
        rel = f"{urlquote(rel_assets_prefix)}/{fname}".replace("\\", "/")
        extracted.append({"file": str(out_path), "rel": rel, "mime": mime, "bytes": len(raw)})
        safe_alt = alt.replace("\n", " ").strip()
        return f"![{safe_alt}]({rel})"

    new_text = DATA_URI_RE.sub(repl, text)
    return new_text, extracted


def build_toc(entries: List[dict]) -> str:
    lines = ["## Table of Contents", ""]
    for e in entries:
        label = f"Page {e['index']}"
        if e.get("printed_page") and str(e.get("printed_page")) != str(e["index"]):
            label += f" (printed {e['printed_page']})"
        lines.append(f"- [{label}](#page-{e['index']})")
    return "\n".join(lines)


def prompt_interactive(defaults: dict) -> argparse.Namespace:
    """Interactive prompt for configuration. Returns an argparse.Namespace."""
    print("\n=== dots.mocr Markdown Combiner (Interactive) ===")
    print(f"Input directory: {defaults['input_dir']}")

    # Only prompt: document title — used for BOTH output file name and assets folder
    default_title = defaults['input_dir'].name
    title = input(f"Document title [{default_title}]: ").strip() or default_title

    slug = slugify(title)
    output_path = (defaults['input_dir'] / f"{slug}.md").resolve()
    assets_dir = output_path.parent / slug

    # Sensible defaults (web-friendly)
    source_mode = "md"
    dedupe = "collapse"
    extract_assets = True
    front_matter = True
    toc = False  # website sidebar handles navigation
    manifest_path = None  # only written when user explicitly passes --manifest
    strict = False
    dry_run = False

    print(f"Using defaults: source=md, dedupe=collapse, extract_assets=yes, front_matter=yes, toc=no")
    print(f"Output: {output_path}")
    print(f"Assets: {assets_dir}")
    print(f"Manifest: {manifest_path}")

    return argparse.Namespace(
        input_dir=defaults['input_dir'],
        output=output_path,
        source=source_mode,
        dedupe_headers=dedupe,
        edge_lines=2,
        repeat_threshold=3,
        extract_assets=assets_dir if extract_assets else None,
        assets_prefix=slug,
        toc=toc,
        front_matter=front_matter,
        title=title,
        manifest=manifest_path,
        separator="\n\n---\n\n",
        strict=strict,
        dry_run=dry_run,
        json=False,
        recursive=False,
    )


def process_single_directory(input_dir: Path, args: argparse.Namespace) -> int:
    """Process one directory of page files into a single Markdown document. Returns exit code."""
    if not input_dir.is_dir():
        print(f"error: input_dir not found: {input_dir}", file=sys.stderr)
        return 2

    pages = discover_pages(input_dir, recursive=False)
    if not pages:
        print(f"error: no page markdown files found in {input_dir}", file=sys.stderr)
        return 2

    all_indices = sorted(pages.keys())
    expected = list(range(all_indices[0], all_indices[-1] + 1))
    gaps = [i for i in expected if i not in pages]
    if gaps and args.strict:
        print(f"error: page gaps detected: {gaps}", file=sys.stderr)
        return 2

    choices = choose_source(pages, args.source)
    if not choices:
        print("error: no selectable page sources found", file=sys.stderr)
        return 2
    if args.strict:
        for idx in all_indices:
            entry = pages[idx]
            if args.source == "nohf" and "nohf" not in entry and "md" not in entry:
                print(f"error: missing source for page {idx}", file=sys.stderr); return 2
            if args.source == "md" and "md" not in entry and "nohf" not in entry:
                print(f"error: missing source for page {idx}", file=sys.stderr); return 2
            if args.source == "auto" and "md" not in entry and "nohf" not in entry:
                print(f"error: missing source for page {idx}", file=sys.stderr); return 2

    texts = [c.source_path.read_text(encoding="utf-8", errors="replace") for c in choices]

    repeated_tops, repeated_bottoms = (set(), set())
    if args.dedupe_headers != "off":
        repeated_tops, repeated_bottoms = detect_repeated_edges(texts, args.edge_lines, args.repeat_threshold)

    output_path = Path(args.output).resolve() if args.output else (input_dir / f"{slugify(input_dir.name)}.md").resolve()
    assets_dir = Path(args.extract_assets).resolve() if args.extract_assets else None
    assets_prefix = args.assets_prefix
    if assets_dir:
        if assets_prefix is None:
            assets_prefix = assets_dir.name
        if not args.dry_run:
            assets_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "input_dir": str(input_dir),
        "output": str(output_path),
        "source_mode": args.source,
        "dedupe_headers": args.dedupe_headers,
        "page_count": len(choices),
        "page_range": [choices[0].index, choices[-1].index],
        "gaps": gaps,
        "repeated_edge_patterns": {"top": sorted(repeated_tops), "bottom": sorted(repeated_bottoms)},
        "pages": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    combined_parts: List[str] = []
    title = args.title or output_path.stem.replace("_", " ").replace("-", " ")
    if args.front_matter:
        fm_lines = [
            "---",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"pages: {len(choices)}",
            f"page_start: {choices[0].index}",
            f"page_end: {choices[-1].index}",
            f"source_mode: {args.source}",
            f"generated_at: {json.dumps(manifest['generated_at'], ensure_ascii=False)}",
            "---",
            "",
        ]
        # Only include source_dir if user explicitly wants it (optional)
        if hasattr(args, 'include_source_dir') and args.include_source_dir:
            fm_lines.insert(2, f"source_dir: {json.dumps(str(input_dir), ensure_ascii=False)}")
        combined_parts.append("\n".join(fm_lines))

    entries_for_toc = []
    page_render_blocks = []
    asset_counter = {"img": 0}

    for choice, raw_text in zip(choices, texts):
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        printed = extract_printed_page(text, edge_lines=args.edge_lines)
        text2, det_h, det_f, rem_h, rem_f = apply_edge_handling(
            text, args.edge_lines, repeated_tops, repeated_bottoms, args.dedupe_headers
        )
        extracted_assets = []
        if assets_dir:
            if args.dry_run:
                matches = list(DATA_URI_RE.finditer(text2))
                extracted_assets = [{"dry_run": True, "count_index": i + 1} for i, _ in enumerate(matches)]
            else:
                text2, extracted_assets = extract_data_uris(text2, assets_dir, assets_prefix, choice.index, asset_counter)

        anchor = f"page-{choice.index}"
        header_lines = [f'<a id="{anchor}"></a>', f"<!-- Page {choice.index} | source: {choice.source_path.name} | kind: {choice.source_kind} -->"]
        if printed and str(printed) != str(choice.index):
            header_lines.append(f"<!-- Printed page: {printed} -->")
        if args.dedupe_headers == "annotate":
            for h in rem_h:
                header_lines.append(f"<!-- Repeated header removed: {json.dumps(h, ensure_ascii=False)} -->")
            for f in rem_f:
                header_lines.append(f"<!-- Repeated footer removed: {json.dumps(f, ensure_ascii=False)} -->")
        block = "\n".join(header_lines) + "\n\n" + text2.strip()
        page_render_blocks.append(block)

        page_entry = {
            "index": choice.index,
            "printed_page": printed,
            "source": str(choice.source_path),
            "source_kind": choice.source_kind,
            "available": {k: str(v) for k, v in {
                "md": choice.md_path, "nohf": choice.nohf_path, "image": choice.image_path, "json": choice.json_path
            }.items() if v},
            "detected_headers": det_h,
            "detected_footers": det_f,
            "removed_headers": rem_h,
            "removed_footers": rem_f,
            "extracted_assets": extracted_assets,
            "anchor": anchor,
            "char_count": len(text2),
        }
        manifest["pages"].append(page_entry)
        entries_for_toc.append({"index": choice.index, "printed_page": printed})

    if args.toc:
        combined_parts.append(build_toc(entries_for_toc))
        combined_parts.append("")
    combined_parts.append(args.separator.lstrip("\n").join(page_render_blocks))
    out_text = "\n".join(combined_parts).rstrip() + "\n"

    manifest["output_bytes"] = len(out_text.encode("utf-8"))
    manifest["dry_run"] = args.dry_run

    manifest_path = None
    if args.manifest == "__AUTO__":
        manifest_path = output_path.with_suffix(".manifest.json")
    elif args.manifest:
        manifest_path = Path(args.manifest)

    if manifest_path:
        mpath = manifest_path.resolve()
        if not args.dry_run:
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(out_text, encoding="utf-8")

    summary = {
        "output": str(output_path),
        "pages": len(choices),
        "range": [choices[0].index, choices[-1].index],
        "gaps": gaps,
        "source_mode": args.source,
        "dedupe_headers": args.dedupe_headers,
        "extract_assets": str(assets_dir) if assets_dir else None,
        "manifest": str(args.manifest) if args.manifest else None,
        "output_bytes": manifest["output_bytes"],
        "dry_run": args.dry_run,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_cli(args: argparse.Namespace):
    """Execute with already-parsed arguments (for CLI mode)."""
    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        print(f"error: input_dir not found: {input_dir}", file=sys.stderr)
        return 2

    if args.recursive:
        subdirs = find_page_subdirs(input_dir)
        if not subdirs:
            print(f"error: no subdirectories with page files found in {input_dir}", file=sys.stderr)
            return 2

        # Interactive naming prompt when no explicit output path is given
        custom_names: List[str] = []
        if args.output is None:
            answer = input(
                f"\nFound {len(subdirs)} subfolder(s) with page files. Name each output? (y/n) [n]: "
            ).strip().lower()
            if answer == "y":
                print("Enter one document name per line (blank line to finish):")
                while True:
                    try:
                        name = input("> ").strip()
                    except EOFError:
                        break
                    if not name:
                        break
                    custom_names.append(name)

        exit_code = 0
        for i, subdir in enumerate(subdirs):
            name = custom_names[i] if i < len(custom_names) else None

            sub_args = argparse.Namespace(**vars(args))
            sub_args.input_dir = str(subdir)
            sub_args.recursive = False

            slug = slugify(name or subdir.name)
            sub_args.output = str((subdir / f"{slug}.md").resolve())

            if args.extract_assets:
                sub_args.extract_assets = str((subdir / slug).resolve())
                if args.assets_prefix is None:
                    sub_args.assets_prefix = slug

            print(f"\n=== Processing: {subdir.name} ===")
            result = process_single_directory(subdir, sub_args)
            if result != 0:
                exit_code = result

        return exit_code

    return process_single_directory(input_dir, args)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Combine dots.mocr per-page markdown files into one markdown document.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python combine_dots_ocr.py                    # Interactive mode
  python combine_dots_ocr.py ./pdf_out          # CLI with defaults
  python combine_dots_ocr.py ./pdf_out -o combined.md --source md --dedupe-headers collapse --extract-assets assets
        """)
    parser.add_argument("input_dir", nargs="?", default=".", help="Directory containing page_*.md files")
    parser.add_argument("-o", "--output", help="Output markdown file path")
    parser.add_argument("--source", choices=["auto", "md", "nohf"], default="auto")
    parser.add_argument("--dedupe-headers", choices=["off", "detect", "collapse", "annotate"], default="off")
    parser.add_argument("--edge-lines", type=int, default=2)
    parser.add_argument("--repeat-threshold", type=int, default=3)
    parser.add_argument("--extract-assets", help="Directory to extract base64/data-URI images into")
    parser.add_argument("--assets-prefix", help="Relative path/prefix used in markdown links for extracted assets")
    parser.add_argument("--toc", action="store_true", help="Include a table of contents")
    parser.add_argument("--front-matter", dest="front_matter", action="store_true", default=True, help="Include YAML front matter header (default: on)")
    parser.add_argument("--no-front-matter", dest="front_matter", action="store_false", help="Disable YAML front matter header")
    parser.add_argument("--title", help="Document title for front matter")
    parser.add_argument("--include-source-dir", action="store_true", help="Include source_dir in front matter")
    parser.add_argument("--manifest", nargs="?", const="__AUTO__", default=None, help="Write JSON manifest/report (default: <output>.manifest.json next to output file)")
    parser.add_argument("--separator", default="\n\n---\n\n")
    parser.add_argument("--strict", action="store_true", help="Fail on page index gaps or missing sources")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and report without writing files")
    parser.add_argument("--json", action="store_true", default=False, help="Print JSON summary to stdout (default: off)")
    parser.add_argument("--recursive", "-r", action="store_true", help="Scan subdirectories recursively for page files")
    parser.add_argument("--version", action="version", version=f"combine_dots_ocr {__version__}")

    # If no args beyond script name, run interactive
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        defaults = {"input_dir": Path.cwd(), "output": None}
        args = prompt_interactive(defaults)
    else:
        args = parser.parse_args(argv)

    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())