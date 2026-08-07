#!/usr/bin/env python3
"""Create a deterministic, hash-based inventory of presentation source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


TYPE_MAP = {
    ".docx": ("word", "extract paragraphs, headings, tables, and locators"),
    ".pdf": ("pdf", "extract text and page locators; use OCR for scans"),
    ".pptx": ("powerpoint", "extract slide text, notes, objects, and layouts"),
    ".xlsx": ("spreadsheet", "extract sheets, cells, formulas, and charts"),
    ".csv": ("table", "parse rows and columns"),
    ".png": ("image", "inspect image and run OCR when text is present"),
    ".jpg": ("image", "inspect image and run OCR when text is present"),
    ".jpeg": ("image", "inspect image and run OCR when text is present"),
    ".tif": ("image", "inspect image and run OCR when text is present"),
    ".tiff": ("image", "inspect image and run OCR when text is present"),
    ".bmp": ("image", "inspect image and run OCR when text is present"),
    ".md": ("text", "parse headings and paragraphs"),
    ".txt": ("text", "parse lines and paragraphs"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def collect_files(inputs: list[Path], output_path: Path) -> tuple[list[Path], list[dict[str, str]]]:
    files: dict[str, Path] = {}
    errors: list[dict[str, str]] = []
    output_key = str(output_path.resolve()).casefold()

    for source in inputs:
        resolved = source.resolve()
        if resolved.is_file():
            candidates = [resolved]
        elif resolved.is_dir():
            candidates = (item for item in resolved.rglob("*") if item.is_file())
        else:
            errors.append({"path": str(resolved), "error": "input does not exist or is not a file or directory"})
            continue

        for candidate in candidates:
            candidate = candidate.resolve()
            key = str(candidate).casefold()
            if key != output_key:
                files[key] = candidate

    return sorted(files.values(), key=lambda item: str(item).casefold()), errors


def build_inventory(inputs: list[Path], output_path: Path) -> tuple[dict, int]:
    files, errors = collect_files(inputs, output_path)
    entries: list[dict] = []
    first_by_hash: dict[str, str] = {}

    for index, path in enumerate(files, start=1):
        source_id = f"SRC{index:03d}"
        try:
            stat = path.stat()
            digest = sha256_file(path)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue

        extension = path.suffix.lower()
        material_type, parser_hint = TYPE_MAP.get(extension, ("other", "manual review required"))
        duplicate_of = first_by_hash.get(digest)
        if duplicate_of is None:
            first_by_hash[digest] = source_id

        entries.append(
            {
                "source_id": source_id,
                "path": str(path),
                "sha256": digest,
                "size_bytes": stat.st_size,
                "modified_utc": utc_mtime(stat.st_mtime),
                "extension": extension,
                "material_type": material_type,
                "parser_hint": parser_hint,
                "parsing_status": "inventory_only",
                "project_boundary": "unknown",
                "sensitivity": "unknown",
                "duplicate_of": duplicate_of,
            }
        )

    inventory = {
        "schema_version": "1.0",
        "source_roots": sorted({str(path.resolve()) for path in inputs}, key=str.casefold),
        "files": entries,
        "summary": {
            "file_count": len(entries),
            "duplicate_file_count": sum(item["duplicate_of"] is not None for item in entries),
            "unsupported_file_count": sum(item["material_type"] == "other" for item in entries),
        },
        "errors": errors,
    }
    return inventory, 1 if errors else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="source files or directories")
    parser.add_argument("--out", required=True, type=Path, help="output JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory, status = build_inventory(args.inputs, args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(inventory["summary"], ensure_ascii=False, sort_keys=True))
    if inventory["errors"]:
        print(json.dumps({"errors": inventory["errors"]}, ensure_ascii=False), file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
