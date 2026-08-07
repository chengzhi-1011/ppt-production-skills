#!/usr/bin/env python3
"""Deterministic state and artifact gates for the PPT production pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


RUN_SCHEMA = "pptflow.run/1"
OUTLINE_SCHEMA = "1.0"
PHASES = ("initialized", "outline", "approved", "images", "editable", "qa", "complete")
CLOUD_POLICIES = ("allowed", "redact_required", "prohibited", "unknown")
CLOUD_POLICY_CHOICES = CLOUD_POLICIES + ("redact-required",)
EVIDENCE_STATUSES = {
    "confirmed",
    "source_stated",
    "derived",
    "recommended",
    "pending_verification",
    "data_insufficient",
    "conflicting",
}
SLIDE_ROLES = {
    "cover",
    "agenda",
    "context",
    "problem",
    "objective",
    "method",
    "framework",
    "evidence",
    "result",
    "value",
    "plan",
    "team",
    "resources",
    "risk",
    "closing",
}
SLIDE_STATES = {"draft", "needs_input", "approved"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PptflowError(Exception):
    def __init__(self, code: str, message: str, details: Any = None, exit_code: int = 4):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.exit_code = exit_code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise PptflowError("FILE_NOT_FOUND", f"Required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PptflowError("INVALID_JSON", f"Invalid JSON in {path}: {exc}") from exc


def append_event(run_dir: Path, command: str, details: dict[str, Any]) -> None:
    event = {"at": utc_now(), "command": command, "details": details}
    event_path = run_dir / "logs" / "events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def normalize_cloud_policy(value: Any) -> str:
    policy = normalize_token(value)
    if policy not in CLOUD_POLICIES:
        raise PptflowError(
            "INVALID_CLOUD_POLICY",
            f"Cloud policy must be one of: {', '.join(CLOUD_POLICIES)}",
        )
    return policy


def relative_path(run_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(run_dir.resolve()).as_posix()


def run_path(run_value: str) -> Path:
    path = Path(run_value).expanduser().resolve()
    if path.is_file() and path.name == "run.json":
        path = path.parent
    if not (path / "run.json").is_file():
        raise PptflowError("RUN_NOT_FOUND", f"No run.json found under: {path}", exit_code=3)
    return path


def load_state(run_dir: Path) -> dict[str, Any]:
    state = read_json(run_dir / "run.json")
    if not isinstance(state, dict) or state.get("schema_version") != RUN_SCHEMA:
        raise PptflowError("RUN_SCHEMA_MISMATCH", f"Unsupported run schema in {run_dir / 'run.json'}")
    if state.get("phase") not in PHASES:
        raise PptflowError("RUN_PHASE_INVALID", f"Invalid phase in {run_dir / 'run.json'}")
    return state


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(run_dir / "run.json", state)


def slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return clean[:40] or "ppt-run"


def snapshot_file(source: Path, target: Path) -> dict[str, Any]:
    if not source.is_file():
        raise PptflowError("INPUT_NOT_FOUND", f"Input is not a file: {source}", exit_code=3)
    source_hash = sha256_file(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target_hash = sha256_file(target)
    if source_hash != target_hash:
        raise PptflowError("SNAPSHOT_HASH_MISMATCH", f"Snapshot hash mismatch for {source}")
    return {
        "source_path": str(source.resolve()),
        "snapshot_path": target.as_posix(),
        "sha256": target_hash,
        "size_bytes": target.stat().st_size,
    }


def png_dimensions(path: Path) -> tuple[int, int]:
    bit_depth = color_type = interlace = 0
    channels_by_color_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    valid_bit_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    try:
        with path.open("rb") as handle:
            if handle.read(8) != PNG_SIGNATURE:
                raise PptflowError("INVALID_PNG", f"Invalid PNG signature: {path}")
            width = height = 0
            seen_ihdr = seen_idat = seen_iend = False
            compressed = bytearray()
            while not seen_iend:
                length_bytes = handle.read(4)
                if len(length_bytes) != 4:
                    raise PptflowError("INVALID_PNG", f"PNG ended before IEND: {path}")
                length = struct.unpack(">I", length_bytes)[0]
                chunk_type = handle.read(4)
                payload = handle.read(length)
                crc_bytes = handle.read(4)
                if len(chunk_type) != 4 or len(payload) != length or len(crc_bytes) != 4:
                    raise PptflowError("INVALID_PNG", f"PNG chunk is truncated: {path}")
                expected_crc = struct.unpack(">I", crc_bytes)[0]
                actual_crc = zlib.crc32(chunk_type)
                actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
                if actual_crc != expected_crc:
                    raise PptflowError("INVALID_PNG", f"PNG chunk CRC mismatch: {path}")
                if chunk_type == b"IHDR":
                    if seen_ihdr or length != 13:
                        raise PptflowError("INVALID_PNG", f"PNG IHDR is invalid: {path}")
                    width, height = struct.unpack(">II", payload[:8])
                    bit_depth, color_type, compression_method, filter_method, interlace = payload[8:13]
                    if width < 1 or height < 1 or width > 10000 or height > 10000 or width * height > 100_000_000:
                        raise PptflowError("INVALID_PNG", f"PNG dimensions are invalid or unsafe: {path}")
                    if color_type not in valid_bit_depths or bit_depth not in valid_bit_depths[color_type]:
                        raise PptflowError("INVALID_PNG", f"PNG color type and bit depth are incompatible: {path}")
                    if compression_method != 0 or filter_method != 0 or interlace not in (0, 1):
                        raise PptflowError("INVALID_PNG", f"PNG IHDR methods are unsupported or invalid: {path}")
                    seen_ihdr = True
                elif chunk_type == b"IDAT":
                    if not seen_ihdr:
                        raise PptflowError("INVALID_PNG", f"PNG IDAT precedes IHDR: {path}")
                    seen_idat = True
                    compressed.extend(payload)
                elif chunk_type == b"IEND":
                    if length != 0:
                        raise PptflowError("INVALID_PNG", f"PNG IEND is invalid: {path}")
                    seen_iend = True
            if handle.read(1):
                raise PptflowError("INVALID_PNG", f"PNG has trailing data after IEND: {path}")
    except FileNotFoundError as exc:
        raise PptflowError("IMAGE_NOT_FOUND", f"Image is missing: {path}") from exc
    if not (seen_ihdr and seen_idat and seen_iend):
        raise PptflowError("INVALID_PNG", f"PNG lacks required IHDR, IDAT, or IEND chunks: {path}")

    channels = channels_by_color_type[color_type]
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    )
    scanlines: list[tuple[int, int]] = []
    expected_decoded = 0
    for start_x, start_y, step_x, step_y in passes:
        pass_width = 0 if width <= start_x else (width - start_x + step_x - 1) // step_x
        pass_height = 0 if height <= start_y else (height - start_y + step_y - 1) // step_y
        if pass_width == 0 or pass_height == 0:
            continue
        row_bytes = (pass_width * channels * bit_depth + 7) // 8
        scanlines.append((pass_height, row_bytes))
        expected_decoded += pass_height * (row_bytes + 1)
    if expected_decoded < 1 or expected_decoded > 256_000_000:
        raise PptflowError("INVALID_PNG", f"PNG decoded data size is invalid or unsafe: {path}")
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(bytes(compressed), expected_decoded + 1)
    except zlib.error as exc:
        raise PptflowError("INVALID_PNG", f"PNG image data cannot be decoded: {path}") from exc
    if (
        len(decoded) != expected_decoded
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise PptflowError("INVALID_PNG", f"PNG decoded scanline length is invalid: {path}")
    offset = 0
    for pass_height, row_bytes in scanlines:
        for _ in range(pass_height):
            if decoded[offset] > 4:
                raise PptflowError("INVALID_PNG", f"PNG contains an invalid scanline filter: {path}")
            offset += row_bytes + 1
    return width, height


def normalize_outline(raw: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["Outline root must be an object."]
    outline = json.loads(json.dumps(raw, ensure_ascii=False))
    if str(outline.get("schema_version")) != OUTLINE_SCHEMA:
        errors.append("schema_version must be '1.0'.")
    if not isinstance(outline.get("deck_id"), str) or not outline["deck_id"].strip():
        errors.append("deck_id must be a nonempty string.")
    if not isinstance(outline.get("outline_version"), int) or isinstance(outline.get("outline_version"), bool) or outline.get("outline_version", 0) < 1:
        errors.append("outline_version must be a positive integer.")

    snapshots = outline.get("source_snapshot")
    if not isinstance(snapshots, list) or not snapshots:
        errors.append("source_snapshot must be a nonempty list.")
    else:
        for index, item in enumerate(snapshots):
            if not isinstance(item, dict):
                errors.append(f"source_snapshot[{index}] must be an object.")
                continue
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                errors.append(f"source_snapshot[{index}].path is required.")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get("sha256", ""))):
                errors.append(f"source_snapshot[{index}].sha256 must be 64 hex characters.")

    deck = outline.get("deck")
    if not isinstance(deck, dict):
        errors.append("deck must be an object.")
        deck = {}
    for field in ("title", "purpose", "audience", "language", "aspect_ratio"):
        if not isinstance(deck.get(field), str) or not deck[field].strip():
            errors.append(f"deck.{field} must be a nonempty string.")
    duration = deck.get("duration_min")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
        errors.append("deck.duration_min must be a positive number.")
    target_count = deck.get("target_slide_count")
    if not isinstance(target_count, int) or isinstance(target_count, bool) or target_count < 1:
        errors.append("deck.target_slide_count must be a positive integer.")
    raw_cloud_policy = deck.get("cloud_policy")
    try:
        canonical_cloud_policy = normalize_cloud_policy(raw_cloud_policy)
        if raw_cloud_policy != canonical_cloud_policy:
            errors.append("deck.cloud_policy must use the canonical redact_required spelling.")
        deck["cloud_policy"] = canonical_cloud_policy
    except PptflowError as exc:
        errors.append(f"deck.cloud_policy: {exc.message}")
    if not isinstance(deck.get("assumptions"), list) or not all(isinstance(item, str) and item.strip() for item in deck.get("assumptions", [])):
        errors.append("deck.assumptions must be a list of nonempty strings.")
    if deck.get("template_ref") is not None and (not isinstance(deck.get("template_ref"), str) or not deck["template_ref"].strip()):
        errors.append("deck.template_ref must be null or a nonempty string.")

    slides = outline.get("slides")
    slide_ids: list[str] = []
    if not isinstance(slides, list) or not slides:
        errors.append("slides must be a nonempty list.")
        slides = []
    for index, slide in enumerate(slides, start=1):
        prefix = f"slides[{index - 1}]"
        if not isinstance(slide, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        slide_id = str(slide.get("slide_id", ""))
        if not re.fullmatch(r"S\d{2,3}", slide_id):
            errors.append(f"{prefix}.slide_id must match S01 or S001 style.")
        elif int(slide_id[1:]) != index:
            errors.append(f"{prefix}.slide_id must be sequential and match order {index}.")
        elif slide_id in slide_ids:
            errors.append(f"Duplicate slide_id: {slide_id}.")
        else:
            slide_ids.append(slide_id)
        if not isinstance(slide.get("order"), int) or isinstance(slide.get("order"), bool) or slide.get("order") != index:
            errors.append(f"{prefix}.order must equal {index}.")
        if slide.get("role") not in SLIDE_ROLES:
            errors.append(f"{prefix}.role is not an allowed role.")
        for field in ("title", "objective", "takeaway"):
            if not isinstance(slide.get(field), str) or not slide[field].strip():
                errors.append(f"{prefix}.{field} must be a nonempty string.")
        blocks = slide.get("content_blocks")
        if not isinstance(blocks, list):
            errors.append(f"{prefix}.content_blocks must be a list.")
            blocks = []
        for block_index, block in enumerate(blocks):
            block_prefix = f"{prefix}.content_blocks[{block_index}]"
            if not isinstance(block, dict):
                errors.append(f"{block_prefix} must be an object.")
                continue
            status = block.get("evidence_status")
            if status not in EVIDENCE_STATUSES:
                errors.append(f"{block_prefix}.evidence_status is invalid.")
            if not isinstance(block.get("kind"), str) or not block["kind"].strip():
                errors.append(f"{block_prefix}.kind must be a nonempty string.")
            if not isinstance(block.get("text"), str) or not block["text"].strip():
                errors.append(f"{block_prefix}.text must be a nonempty string.")
            claim_ids = block.get("claim_ids")
            if not isinstance(claim_ids, list) or not claim_ids or not all(isinstance(item, str) and item.strip() for item in claim_ids):
                errors.append(f"{block_prefix}.claim_ids must be a nonempty list of strings.")
            if status in {"pending_verification", "data_insufficient", "conflicting"}:
                if not isinstance(block.get("visible_label"), str) or not block["visible_label"].strip():
                    errors.append(f"{block_prefix}.visible_label is required for unresolved evidence.")
        if not isinstance(slide.get("source_refs"), list) or not all(isinstance(item, str) and item.strip() for item in slide.get("source_refs", [])):
            errors.append(f"{prefix}.source_refs must be a list of nonempty strings.")
        if not isinstance(slide.get("speaker_notes"), str):
            errors.append(f"{prefix}.speaker_notes must be a string.")
        if not isinstance(slide.get("dependencies"), list) or not all(isinstance(item, str) and item.strip() for item in slide.get("dependencies", [])):
            errors.append(f"{prefix}.dependencies must be a list of nonempty strings.")
        visual = slide.get("visual_brief")
        if not isinstance(visual, dict):
            errors.append(f"{prefix}.visual_brief must be an object.")
        else:
            for field in ("visual_type", "message"):
                if not isinstance(visual.get(field), str) or not visual[field].strip():
                    errors.append(f"{prefix}.visual_brief.{field} must be a nonempty string.")
            for field in ("required_elements", "prohibited_elements"):
                if not isinstance(visual.get(field), list) or not all(isinstance(item, str) and item.strip() for item in visual.get(field, [])):
                    errors.append(f"{prefix}.visual_brief.{field} must be a list of nonempty strings.")
            if not isinstance(visual.get("data_chart_required"), bool):
                errors.append(f"{prefix}.visual_brief.data_chart_required must be a boolean.")
        layout = slide.get("layout_constraints")
        if not isinstance(layout, dict):
            errors.append(f"{prefix}.layout_constraints must be an object.")
        else:
            template_layout_id = layout.get("template_layout_id")
            if template_layout_id is not None and (not isinstance(template_layout_id, str) or not template_layout_id.strip()):
                errors.append(f"{prefix}.layout_constraints.template_layout_id must be null or a nonempty string.")
            max_bullets = layout.get("max_bullets")
            if not isinstance(max_bullets, int) or isinstance(max_bullets, bool) or max_bullets < 0:
                errors.append(f"{prefix}.layout_constraints.max_bullets must be a nonnegative integer.")
            max_chars = layout.get("max_chars")
            if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
                errors.append(f"{prefix}.layout_constraints.max_chars must be a positive integer.")
        if slide.get("status") not in SLIDE_STATES:
            errors.append(f"{prefix}.status is not an allowed slide state.")

    if isinstance(deck.get("target_slide_count"), int) and deck.get("target_slide_count") != len(slides):
        errors.append("deck.target_slide_count must equal the number of slides.")
    validation = outline.get("validation")
    if not isinstance(validation, dict):
        errors.append("validation must be an object.")
    else:
        for field in ("unresolved_claims", "overloaded_slides", "source_conflicts"):
            if not isinstance(validation.get(field), list) or not all(isinstance(item, str) and item.strip() for item in validation.get(field, [])):
                errors.append(f"validation.{field} must be a list of nonempty strings.")
    return outline, errors


def outline_path(run_dir: Path) -> Path:
    return run_dir / "outline" / "deck-outline.json"


def approval_path(run_dir: Path) -> Path:
    return run_dir / "outline" / "approval.json"


def expected_slide_ids(run_dir: Path) -> list[str]:
    outline, errors = normalize_outline(read_json(outline_path(run_dir)))
    if errors:
        raise PptflowError("OUTLINE_INVALID", "The canonical outline is invalid.", errors)
    return [slide["slide_id"] for slide in outline["slides"]]


def cloud_gate(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    policy = normalize_cloud_policy(state.get("cloud_policy"))
    if policy == "allowed":
        return {"passed": True, "policy": policy, "attachments": []}
    if policy == "redact_required":
        receipt_path = run_dir / "generation" / "redaction-approval.json"
        if not receipt_path.is_file():
            return {"passed": False, "policy": policy, "reason": "generation/redaction-approval.json is missing"}
        receipt = read_json(receipt_path)
        files = receipt.get("sanitized_files") if isinstance(receipt, dict) else None
        if receipt.get("approved") is not True or not receipt.get("approved_by") or not isinstance(files, list) or not files:
            return {"passed": False, "policy": policy, "reason": "redaction approval is invalid"}
        resolved: list[str] = []
        for value in files:
            if not isinstance(value, dict) or not value.get("path") or not value.get("sha256"):
                return {"passed": False, "policy": policy, "reason": "each sanitized file needs path and sha256"}
            candidate = (run_dir / str(value["path"])).resolve()
            try:
                candidate.relative_to(run_dir.resolve())
            except ValueError:
                return {"passed": False, "policy": policy, "reason": "sanitized file escapes the run directory"}
            if not candidate.is_file():
                return {"passed": False, "policy": policy, "reason": f"sanitized file is missing: {value['path']}"}
            if sha256_file(candidate) != str(value["sha256"]).lower():
                return {"passed": False, "policy": policy, "reason": f"sanitized file hash is stale: {value['path']}"}
            resolved.append(relative_path(run_dir, candidate))
        return {"passed": True, "policy": policy, "attachments": resolved}
    return {"passed": False, "policy": policy, "reason": "cloud transmission is not approved"}


def validate_style_lock(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    style_path = run_dir / "generation" / "style-lock.md"
    manifest_path = run_dir / "generation" / "template-reference-manifest.json"
    if not style_path.is_file():
        raise PptflowError("STYLE_LOCK_REQUIRED", "generation/style-lock.md is required.", exit_code=3)
    if not manifest_path.is_file():
        raise PptflowError(
            "TEMPLATE_REFERENCES_REQUIRED",
            "Render the immutable template with render-template-references.ps1 before generating image prompts.",
            exit_code=3,
        )

    style_text = style_path.read_text(encoding="utf-8-sig")
    header_match = re.match(r"\A---\s*\r?\n(?P<header>.*?)\r?\n---(?:\s*\r?\n|\s*\Z)", style_text, re.DOTALL)
    if not header_match:
        raise PptflowError("STYLE_LOCK_INVALID", "generation/style-lock.md needs a YAML-style header bounded by ---." )
    header: dict[str, str] = {}
    for line in header_match.group("header").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            header[key.strip()] = value.strip().strip('"\'')

    errors: list[str] = []
    template_hash = state["inputs"]["template"]["sha256"]
    if header.get("schema_version") != "1":
        errors.append("style-lock schema_version must be 1")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,80}", header.get("style_lock_id", "")):
        errors.append("style-lock style_lock_id must be a stable nonempty identifier")
    if header.get("template_sha256", "").lower() != template_hash:
        errors.append("style-lock template_sha256 does not match the immutable template snapshot")
    if header.get("slide_ratio") != state["settings"]["aspect_ratio"]:
        errors.append("style-lock slide_ratio does not match the run")
    try:
        declared_count = int(header.get("reference_count", ""))
    except ValueError:
        declared_count = -1
        errors.append("style-lock reference_count must be an integer")

    manifest = read_json(manifest_path)
    references = manifest.get("references") if isinstance(manifest, dict) else None
    if not isinstance(references, list) or not references:
        errors.append("template-reference-manifest.json needs a nonempty references list")
        references = []
    if manifest.get("template_sha256") != template_hash:
        errors.append("template-reference manifest hash does not match the immutable template snapshot")
    if declared_count != len(references) or manifest.get("reference_count") != len(references):
        errors.append("style-lock and template-reference counts do not match the manifest")

    required_sections = (
        "Canvas and fixed regions",
        "Palette",
        "Typography hierarchy",
        "Grid and layout grammar",
        "Containers and strokes",
        "Visual language",
        "Brand assets",
        "Must keep",
        "Must avoid",
    )
    for section in required_sections:
        match = re.search(
            rf"(?im)^##+\s+{re.escape(section)}\s*$\r?\n(?P<body>.*?)(?=^##+\s+|\Z)",
            style_text,
            re.DOTALL,
        )
        if not match or not match.group("body").strip():
            errors.append(f"style-lock section is missing or empty: {section}")

    for item in references:
        if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
            errors.append("each template reference needs path and sha256")
            continue
        slide_number = item.get("slide_number")
        if not isinstance(slide_number, int) or slide_number < 1:
            errors.append("each template reference needs a positive integer slide_number")
            continue
        candidate = (run_dir / str(item["path"])).resolve()
        try:
            candidate.relative_to(run_dir.resolve())
        except ValueError:
            errors.append(f"template reference escapes the run directory: {item.get('path')}")
            continue
        if not candidate.is_file():
            errors.append(f"template reference is missing: {item.get('path')}")
            continue
        actual_hash = sha256_file(candidate)
        if actual_hash != str(item["sha256"]).lower():
            errors.append(f"template reference hash is stale: {item.get('path')}")
        reference_line = next((line for line in style_text.splitlines() if candidate.name in line and actual_hash in line), "")
        if not reference_line:
            errors.append(f"style-lock must list the filename and SHA-256 for {candidate.name}")
        else:
            if not re.search(rf"(?i)source\s+template\s+slide\s+{slide_number}\b", reference_line):
                errors.append(f"style-lock reference must name source template slide for {candidate.name}")
            if not re.search(r"(?i)\b(cover|section|content|process|data|closing)\b", reference_line):
                errors.append(f"style-lock reference must name a supported archetype for {candidate.name}")

    if errors:
        raise PptflowError("STYLE_LOCK_INVALID", "Template style-lock validation failed.", errors)
    return {
        "style_path": relative_path(run_dir, style_path),
        "style_sha256": sha256_file(style_path),
        "style_text": style_text.strip(),
        "manifest_path": relative_path(run_dir, manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "references": references,
    }


def web_transmission_gate(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    approval_path = run_dir / "generation" / "web-transmission-approval.json"
    if not approval_path.is_file():
        return {"passed": False, "reason": "generation/web-transmission-approval.json is missing"}
    try:
        approval = read_json(approval_path)
    except PptflowError as exc:
        return {"passed": False, "reason": exc.message}
    if not isinstance(approval, dict) or approval.get("approved") is not True:
        return {"passed": False, "reason": "web transmission approval is invalid"}
    if approval.get("destination") != "https://chatgpt.com/":
        return {"passed": False, "reason": "destination is not exactly https://chatgpt.com/"}
    if not outline_path(run_dir).is_file() or approval.get("outline_sha256") != sha256_file(outline_path(run_dir)):
        return {"passed": False, "reason": "web transmission approval is stale for the outline"}
    style_path = run_dir / "generation" / "style-lock.md"
    if not style_path.is_file() or approval.get("style_lock_sha256") != sha256_file(style_path):
        return {"passed": False, "reason": "web transmission approval is stale for the style lock"}
    if approval.get("template_sha256") != state["inputs"]["template"]["sha256"]:
        return {"passed": False, "reason": "web transmission approval is stale for the template"}
    manifest_path = run_dir / "generation" / "template-reference-manifest.json"
    if not manifest_path.is_file() or approval.get("template_reference_manifest_sha256") != sha256_file(manifest_path):
        return {"passed": False, "reason": "web transmission approval is stale for the template-reference manifest"}
    slides = approval.get("slides")
    if not isinstance(slides, list) or not slides or len(set(slides)) != len(slides) or any(slide not in expected_slide_ids(run_dir) for slide in slides):
        return {"passed": False, "reason": "web transmission approval has invalid slide scope"}
    if approval.get("initial_prompt_count") != len(slides):
        return {"passed": False, "reason": "web transmission prompt count does not match the slide scope"}
    prompts = approval.get("prompts")
    if not isinstance(prompts, list) or [item.get("slide_id") for item in prompts if isinstance(item, dict)] != slides:
        return {"passed": False, "reason": "web transmission approval has an invalid prompt scope"}
    for item in prompts:
        current = state.get("prompts", {}).get("images", {}).get(item["slide_id"])
        history = current.get("history") if isinstance(current, dict) else None
        initial = history[0] if isinstance(history, list) and history else current
        prompt_path = (run_dir / str(item.get("path", ""))).resolve()
        try:
            prompt_path.relative_to(run_dir.resolve())
        except ValueError:
            return {"passed": False, "reason": f"approved prompt escapes the run directory: {item['slide_id']}"}
        if not isinstance(initial, dict) or initial.get("path") != item.get("path") or initial.get("sha256") != item.get("sha256"):
            return {"passed": False, "reason": f"web transmission approval is stale for prompt {item['slide_id']}"}
        if not prompt_path.is_file() or sha256_file(prompt_path) != item.get("sha256"):
            return {"passed": False, "reason": f"approved prompt is missing or stale: {item['slide_id']}"}
    retry_limit = approval.get("corrective_retries_per_slide")
    if not isinstance(retry_limit, int) or retry_limit < 0:
        return {"passed": False, "reason": "web transmission retry allowance is invalid"}
    files = approval.get("files")
    if not isinstance(files, list) or not files:
        return {"passed": False, "reason": "web transmission approval has no files"}
    for item in files:
        if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
            return {"passed": False, "reason": "web transmission approval has an invalid file entry"}
        candidate = (run_dir / str(item["path"])).resolve()
        try:
            candidate.relative_to(run_dir.resolve())
        except ValueError:
            return {"passed": False, "reason": f"approved file escapes the run directory: {item.get('path')}"}
        if not candidate.is_file() or sha256_file(candidate) != item["sha256"]:
            return {"passed": False, "reason": f"approved file is missing or stale: {item.get('path')}"}
    cloud = cloud_gate(run_dir, state)
    if not cloud["passed"]:
        return {"passed": False, "reason": f"cloud policy gate failed: {cloud.get('reason')}"}
    if cloud["policy"] == "redact_required":
        allowed = set(cloud.get("attachments", []))
        if any(item["path"] not in allowed for item in files):
            return {"passed": False, "reason": "web approval includes a file outside the approved redacted set"}
        approved_files = {item["path"] for item in files}
        current_prompt_paths = {
            state.get("prompts", {}).get("images", {}).get(slide, {}).get("path")
            for slide in slides
        }
        if None in current_prompt_paths or not current_prompt_paths.issubset(approved_files):
            return {"passed": False, "reason": "current corrective prompt is outside the approved redacted transmission set"}
    return {
        "passed": True,
        "destination": approval["destination"],
        "slides": slides,
        "initial_prompt_count": approval.get("initial_prompt_count"),
        "corrective_retries_per_slide": retry_limit,
        "policy": cloud["policy"],
        "files": [item["path"] for item in files],
        "approved_by": approval.get("approved_by"),
        "approved_at": approval.get("approved_at"),
    }


def check_initialized(run_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    errors: list[dict[str, str]] = []
    settings = state.get("settings", {})
    width = settings.get("image_width")
    height = settings.get("image_height")
    if settings.get("aspect_ratio") != "16:9" or not isinstance(width, int) or not isinstance(height, int) or width < 1280 or height < 720 or width * 9 != height * 16:
        errors.append({"code": "CANVAS_INVALID", "path": "run.json", "message": "Canvas settings must be at least 1280x720 with an exact 16:9 ratio."})
    checked = 0
    for entry in state["inputs"]["materials"] + [state["inputs"]["template"]]:
        path = run_dir / entry["path"]
        if not path.is_file():
            errors.append({"code": "SNAPSHOT_MISSING", "path": entry["path"], "message": "Snapshot is missing."})
            continue
        checked += 1
        if sha256_file(path) != entry["sha256"]:
            errors.append({"code": "SNAPSHOT_CHANGED", "path": entry["path"], "message": "Snapshot hash changed."})
    return errors, [], {"snapshot_count": checked}


def check_outline_stage(run_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    errors, warnings, details = check_initialized(run_dir, state)
    if errors:
        return errors, warnings, details
    path = outline_path(run_dir)
    if not path.is_file():
        errors.append({"code": "OUTLINE_MISSING", "path": relative_path(run_dir, path), "message": "Canonical outline is missing."})
        return errors, warnings, details
    outline, outline_errors = normalize_outline(read_json(path))
    errors.extend({"code": "OUTLINE_INVALID", "path": relative_path(run_dir, path), "message": message} for message in outline_errors)
    if not errors:
        policy = normalize_cloud_policy(outline["deck"]["cloud_policy"])
        if policy != normalize_cloud_policy(state["cloud_policy"]):
            errors.append({"code": "CLOUD_POLICY_MISMATCH", "path": relative_path(run_dir, path), "message": "Outline cloud policy does not match the run."})
        known_hashes = {item["sha256"] for item in state["inputs"]["materials"]}
        for item in outline["source_snapshot"]:
            if item["sha256"].lower() not in known_hashes:
                errors.append({"code": "SOURCE_SNAPSHOT_MISMATCH", "path": relative_path(run_dir, path), "message": f"Unknown source snapshot hash: {item['sha256']}"})
    details.update({"slide_count": len(outline.get("slides", [])), "outline_sha256": sha256_file(path)})
    return errors, warnings, details


def check_approved(run_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    errors, warnings, details = check_outline_stage(run_dir, state)
    if errors:
        return errors, warnings, details
    path = approval_path(run_dir)
    if not path.is_file():
        errors.append({"code": "APPROVAL_MISSING", "path": relative_path(run_dir, path), "message": "Explicit outline approval is missing."})
        return errors, warnings, details
    approval = read_json(path)
    outline = read_json(outline_path(run_dir))
    if approval.get("approved") is not True:
        errors.append({"code": "APPROVAL_INVALID", "path": relative_path(run_dir, path), "message": "approved must be true."})
    if approval.get("outline_sha256") != sha256_file(outline_path(run_dir)):
        errors.append({"code": "APPROVAL_STALE", "path": relative_path(run_dir, path), "message": "Approval does not match the current outline hash."})
    if approval.get("deck_id") != outline.get("deck_id") or approval.get("outline_version") != outline.get("outline_version"):
        errors.append({"code": "APPROVAL_VERSION_MISMATCH", "path": relative_path(run_dir, path), "message": "Approval deck/version does not match the outline."})
    if approval.get("source_snapshot") != outline.get("source_snapshot"):
        errors.append({"code": "APPROVAL_SOURCE_MISMATCH", "path": relative_path(run_dir, path), "message": "Approval source snapshot does not match the outline."})
    details["approved_by"] = approval.get("approved_by")
    return errors, warnings, details


def check_images(run_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    errors, warnings, details = check_approved(run_dir, state)
    if errors:
        return errors, warnings, details
    try:
        style = validate_style_lock(run_dir, state)
    except PptflowError as exc:
        errors.append({"code": exc.code, "path": "generation/style-lock.md", "message": exc.message})
        if exc.details:
            warnings.extend(str(item) for item in exc.details)
        return errors, warnings, details
    expected = expected_slide_ids(run_dir)
    log_path = run_dir / "generation" / "generation-log.json"
    if not log_path.is_file():
        errors.append({"code": "GENERATION_LOG_MISSING", "path": relative_path(run_dir, log_path), "message": "Generation log is missing."})
        return errors, warnings, details
    log = read_json(log_path)
    records = log.get("slides", {}) if isinstance(log, dict) else {}
    seen_hashes: dict[str, str] = {}
    accepted_count = 0
    for slide_id in expected:
        record = records.get(slide_id, {}).get("accepted") if isinstance(records.get(slide_id), dict) else None
        if not isinstance(record, dict):
            errors.append({"code": "IMAGE_MISSING", "path": slide_id, "message": "No accepted image is recorded."})
            continue
        image = run_dir / record.get("image_path", "")
        if not image.is_file():
            errors.append({"code": "IMAGE_MISSING", "path": record.get("image_path", ""), "message": "Accepted image file is missing."})
            continue
        image_hash = sha256_file(image)
        if image_hash != record.get("image_sha256"):
            errors.append({"code": "IMAGE_HASH_MISMATCH", "path": record.get("image_path", ""), "message": "Accepted image hash changed."})
        try:
            width, height = png_dimensions(image)
        except PptflowError as exc:
            errors.append({"code": exc.code, "path": record.get("image_path", ""), "message": exc.message})
            continue
        if (width, height) != (state["settings"]["image_width"], state["settings"]["image_height"]):
            errors.append({"code": "IMAGE_DIMENSION_MISMATCH", "path": record.get("image_path", ""), "message": f"Expected {state['settings']['image_width']}x{state['settings']['image_height']}, got {width}x{height}."})
        if record.get("outline_sha256") != sha256_file(outline_path(run_dir)):
            errors.append({"code": "IMAGE_OUTLINE_STALE", "path": slide_id, "message": "Image is bound to a stale outline."})
        if record.get("template_sha256") != state["inputs"]["template"]["sha256"]:
            errors.append({"code": "IMAGE_TEMPLATE_STALE", "path": slide_id, "message": "Image is bound to a stale template."})
        if record.get("style_lock_sha256") != style["style_sha256"]:
            errors.append({"code": "IMAGE_STYLE_LOCK_STALE", "path": slide_id, "message": "Image is bound to a stale style lock."})
        if record.get("template_reference_manifest_sha256") != style["manifest_sha256"]:
            errors.append({"code": "IMAGE_STYLE_REFERENCE_STALE", "path": slide_id, "message": "Image is bound to a stale template-reference manifest."})
        prompt_path = run_dir / record.get("prompt_path", "")
        if not prompt_path.is_file() or sha256_file(prompt_path) != record.get("prompt_sha256"):
            errors.append({"code": "IMAGE_PROMPT_STALE", "path": slide_id, "message": "Image prompt is missing or changed."})
        current_prompt = state.get("prompts", {}).get("images", {}).get(slide_id)
        if not isinstance(current_prompt, dict) or current_prompt.get("sha256") != record.get("prompt_sha256"):
            errors.append({"code": "IMAGE_PROMPT_RECORD_STALE", "path": slide_id, "message": "Image is not bound to the current prompt record."})
        elif current_prompt.get("attempt_id") != record.get("attempt_id") or record.get("attempt_id") != record.get("retry_count", 0) + 1:
            errors.append({"code": "IMAGE_PROMPT_ATTEMPT_MISMATCH", "path": slide_id, "message": "Accepted image attempt does not match the current prompt attempt."})
        if record.get("source") == "chatgpt-web":
            web_gate = web_transmission_gate(run_dir, state)
            if not web_gate["passed"] or slide_id not in web_gate.get("slides", []):
                errors.append({"code": "IMAGE_WEB_AUTH_STALE", "path": slide_id, "message": "ChatGPT image lacks a current matching web-transmission approval."})
            elif record.get("retry_count", 0) > web_gate["corrective_retries_per_slide"]:
                errors.append({"code": "IMAGE_WEB_RETRY_UNAUTHORIZED", "path": slide_id, "message": "Recorded ChatGPT retry count exceeds approval."})
            elif web_gate.get("policy") == "redact_required" and record.get("prompt_path") not in web_gate.get("files", []):
                errors.append({"code": "IMAGE_REDACTED_PROMPT_UNAUTHORIZED", "path": slide_id, "message": "Accepted ChatGPT image used a prompt outside the redacted transmission set."})
        if image_hash in seen_hashes and seen_hashes[image_hash] != slide_id:
            errors.append({"code": "DUPLICATE_IMAGE_HASH", "path": slide_id, "message": f"Image duplicates {seen_hashes[image_hash]}."})
        seen_hashes[image_hash] = slide_id
        accepted_count += 1
    extra = sorted(set(records) - set(expected))
    if extra:
        errors.append({"code": "UNKNOWN_IMAGE_SLIDES", "path": "generation/generation-log.json", "message": f"Unknown slide IDs: {', '.join(extra)}"})
    details.update({"expected_images": len(expected), "accepted_images": accepted_count, "style_lock_sha256": style["style_sha256"], "template_reference_manifest_sha256": style["manifest_sha256"]})
    return errors, warnings, details


def inspect_pptx(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "ppt/presentation.xml"}
            missing = sorted(required - names)
            slide_names = sorted(
                (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda value: int(re.search(r"(\d+)", Path(value).stem).group(1)),
            )
            namespaces = {
                "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            }
            presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
            slide_size = presentation.find("p:sldSz", namespaces)
            slide_width = int(slide_size.get("cx", "0")) if slide_size is not None else 0
            slide_height = int(slide_size.get("cy", "0")) if slide_size is not None else 0
            if slide_width < 1 or slide_height < 1:
                missing.append("ppt/presentation.xml#p:sldSz")
            slide_area = max(slide_width * slide_height, 1)
            slides: list[dict[str, Any]] = []
            for name in slide_names:
                root = ET.fromstring(archive.read(name))
                shapes = root.findall(".//p:sp", namespaces)
                pictures = root.findall(".//p:pic", namespaces)
                frames = root.findall(".//p:graphicFrame", namespaces)
                groups = root.findall(".//p:grpSp", namespaces)
                text_runs = [node.text or "" for node in root.findall(".//a:t", namespaces)]
                meaningful_text_shapes = 0
                text_chars = 0
                for shape in shapes:
                    text = "".join(node.text or "" for node in shape.findall(".//a:t", namespaces)).strip()
                    text_chars += len(text)
                    transform = shape.find("./p:spPr/a:xfrm", namespaces)
                    extent = transform.find("a:ext", namespaces) if transform is not None else None
                    area_ratio = 0.0
                    if extent is not None:
                        area_ratio = int(extent.get("cx", "0")) * int(extent.get("cy", "0")) / slide_area
                    if len(text) >= 2 and area_ratio >= 0.00001:
                        meaningful_text_shapes += 1
                full_slide_pictures = 0
                maximum_picture_coverage = 0.0
                for picture in pictures:
                    transform = picture.find("./p:spPr/a:xfrm", namespaces)
                    offset = transform.find("a:off", namespaces) if transform is not None else None
                    extent = transform.find("a:ext", namespaces) if transform is not None else None
                    if offset is None or extent is None:
                        continue
                    x = int(offset.get("x", "0"))
                    y = int(offset.get("y", "0"))
                    cx = int(extent.get("cx", "0"))
                    cy = int(extent.get("cy", "0"))
                    coverage = cx * cy / slide_area
                    maximum_picture_coverage = max(maximum_picture_coverage, coverage)
                    if coverage >= 0.95 and abs(x) <= slide_width * 0.01 and abs(y) <= slide_height * 0.01:
                        full_slide_pictures += 1
                slides.append(
                    {
                        "text_runs": sum(1 for value in text_runs if value.strip()),
                        "text_chars": text_chars,
                        "meaningful_text_shapes": meaningful_text_shapes,
                        "objects": len(shapes) + len(pictures) + len(frames) + len(groups),
                        "pictures": len(pictures),
                        "full_slide_pictures": full_slide_pictures,
                        "maximum_picture_coverage": round(maximum_picture_coverage, 6),
                    }
                )
            return {
                "broken_member": broken,
                "missing_parts": missing,
                "slide_count": len(slide_names),
                "slide_width": slide_width,
                "slide_height": slide_height,
                "slides": slides,
            }
    except (FileNotFoundError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        raise PptflowError("INVALID_PPTX", f"PPTX is missing or not a valid ZIP package: {path}") from exc


def check_editable(run_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    errors, warnings, details = check_images(run_dir, state)
    if errors:
        return errors, warnings, details
    deck_path = run_dir / "editable" / "final.pptx"
    validation_path = run_dir / "editable" / "validation.json"
    source_path = run_dir / "editable" / "source.json"
    for path, code in ((deck_path, "EDITABLE_DECK_MISSING"), (validation_path, "EDITABLE_VALIDATION_MISSING"), (source_path, "EDITABLE_SOURCE_MISSING")):
        if not path.is_file():
            errors.append({"code": code, "path": relative_path(run_dir, path), "message": "Required editable artifact is missing."})
    if errors:
        return errors, warnings, details
    validation = read_json(validation_path)
    if validation.get("passed") is not True:
        errors.append({"code": "EDITABLE_VALIDATION_FAILED", "path": relative_path(run_dir, validation_path), "message": "Editable validation did not pass."})
    deck_hash = sha256_file(deck_path)
    if validation.get("pptx_sha256") != deck_hash:
        errors.append({"code": "EDITABLE_VALIDATION_STALE", "path": relative_path(run_dir, validation_path), "message": "Editable validation is not bound to the current PPTX hash."})
    source = read_json(source_path)
    source_slides = source.get("slides") if isinstance(source, dict) else None
    expected = expected_slide_ids(run_dir)
    if not isinstance(source_slides, list) or [item.get("slide_id") for item in source_slides if isinstance(item, dict)] != expected:
        errors.append({"code": "EDITABLE_SOURCE_INVALID", "path": relative_path(run_dir, source_path), "message": "Editable source map does not match slide order."})
    else:
        log = read_json(run_dir / "generation" / "generation-log.json")
        for item in source_slides:
            accepted = log["slides"][item["slide_id"]]["accepted"]
            if item.get("source_image_sha256") != accepted["image_sha256"]:
                errors.append({"code": "EDITABLE_SOURCE_STALE", "path": item["slide_id"], "message": "Editable source image hash is stale."})
        validation_sources = validation.get("source_images")
        expected_sources = [{"slide_id": item["slide_id"], "sha256": item["source_image_sha256"]} for item in source_slides]
        if validation_sources != expected_sources:
            errors.append({"code": "EDITABLE_VALIDATION_SOURCE_STALE", "path": relative_path(run_dir, validation_path), "message": "Editable validation is not bound to the ordered source-image hashes."})
    try:
        inspection = inspect_pptx(deck_path)
    except PptflowError as exc:
        errors.append({"code": exc.code, "path": relative_path(run_dir, deck_path), "message": exc.message})
        return errors, warnings, details
    if inspection["broken_member"] or inspection["missing_parts"]:
        errors.append({"code": "PPTX_PACKAGE_BROKEN", "path": relative_path(run_dir, deck_path), "message": "PPTX package integrity failed."})
    if inspection["slide_count"] != len(expected):
        errors.append({"code": "PPTX_SLIDE_COUNT_MISMATCH", "path": relative_path(run_dir, deck_path), "message": f"Expected {len(expected)} slides, got {inspection['slide_count']}."})
    for index, slide in enumerate(inspection["slides"]):
        if slide["text_runs"] < 1 or slide["objects"] < 2 or slide["meaningful_text_shapes"] < 1:
            errors.append({"code": "PPTX_PAGE_NOT_EDITABLE", "path": expected[index] if index < len(expected) else str(index + 1), "message": "Page lacks editable text or multiple objects."})
        if slide["full_slide_pictures"] > 0 and (slide["meaningful_text_shapes"] < 2 or slide["objects"] < 5 or slide["text_chars"] < 20):
            errors.append({"code": "PPTX_SCREENSHOT_MASQUERADE", "path": expected[index] if index < len(expected) else str(index + 1), "message": "Near-full-slide raster content lacks substantial editable overlays."})
    details.update({"pptx_sha256": deck_hash, "pptx_slide_count": inspection["slide_count"], "object_metrics": inspection["slides"]})
    return errors, warnings, details


def check_qa(run_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    errors, warnings, details = check_editable(run_dir, state)
    if errors:
        return errors, warnings, details
    power_path = run_dir / "qa" / "powerpoint-qa.json"
    report_path = run_dir / "qa" / "qa-report.json"
    for path, code in ((power_path, "POWERPOINT_QA_MISSING"), (report_path, "VISUAL_QA_MISSING")):
        if not path.is_file():
            errors.append({"code": code, "path": relative_path(run_dir, path), "message": "Required QA report is missing."})
    if errors:
        return errors, warnings, details
    power = read_json(power_path)
    report = read_json(report_path)
    expected = expected_slide_ids(run_dir)
    if power.get("passed") is not True:
        errors.append({"code": "POWERPOINT_QA_FAILED", "path": relative_path(run_dir, power_path), "message": "PowerPoint open/export did not pass."})
    if power.get("pptx_sha256") != sha256_file(run_dir / "editable" / "final.pptx"):
        errors.append({"code": "POWERPOINT_QA_STALE", "path": relative_path(run_dir, power_path), "message": "PowerPoint QA is bound to a different deck."})
    if power.get("slide_count") != len(expected):
        errors.append({"code": "POWERPOINT_EXPORT_COUNT_MISMATCH", "path": relative_path(run_dir, power_path), "message": "PowerPoint export count does not match the outline."})
    for value in power.get("exports", []):
        export_path = run_dir / "qa" / str(value)
        if not export_path.is_file() or export_path.stat().st_size == 0:
            errors.append({"code": "POWERPOINT_EXPORT_MISSING", "path": str(value), "message": "PowerPoint export is missing or empty."})
    if report.get("passed") is not True:
        errors.append({"code": "VISUAL_QA_FAILED", "path": relative_path(run_dir, report_path), "message": "Independent visual review did not pass."})
    current_deck_hash = sha256_file(run_dir / "editable" / "final.pptx")
    if report.get("pptx_sha256") != current_deck_hash:
        errors.append({"code": "VISUAL_QA_DECK_STALE", "path": relative_path(run_dir, report_path), "message": "Visual review is not bound to the current PPTX."})
    if report.get("powerpoint_qa_sha256") != sha256_file(power_path):
        errors.append({"code": "VISUAL_QA_EXPORT_REPORT_STALE", "path": relative_path(run_dir, report_path), "message": "Visual review is not bound to the current PowerPoint export report."})
    if report.get("reviewed_slide_ids") != expected:
        errors.append({"code": "VISUAL_QA_INCOMPLETE", "path": relative_path(run_dir, report_path), "message": "Reviewed slide IDs do not match the approved outline."})
    render_hashes = report.get("render_sha256")
    expected_render_hashes = {
        str(value): sha256_file(run_dir / "qa" / str(value))
        for value in power.get("exports", [])
        if (run_dir / "qa" / str(value)).is_file()
    }
    if render_hashes != expected_render_hashes:
        errors.append({"code": "VISUAL_QA_RENDER_STALE", "path": relative_path(run_dir, report_path), "message": "Visual review is not bound to the current exported-slide hashes."})
    generation_log = read_json(run_dir / "generation" / "generation-log.json")
    expected_image_hashes = {
        slide_id: generation_log["slides"][slide_id]["accepted"]["image_sha256"]
        for slide_id in expected
    }
    if report.get("accepted_image_sha256") != expected_image_hashes:
        errors.append({"code": "VISUAL_QA_SOURCE_STALE", "path": relative_path(run_dir, report_path), "message": "Visual review is not bound to the accepted slide-image hashes."})
    for field in ("reviewer", "reviewed_at"):
        if not isinstance(report.get(field), str) or not report[field].strip():
            errors.append({"code": "VISUAL_QA_SCHEMA_INVALID", "path": relative_path(run_dir, report_path), "message": f"{field} must be a nonempty string."})
    for field in ("findings", "warnings"):
        if not isinstance(report.get(field), list):
            errors.append({"code": "VISUAL_QA_SCHEMA_INVALID", "path": relative_path(run_dir, report_path), "message": f"{field} must be a list."})
    details.update({"powerpoint_open_export": power.get("passed") is True, "visual_review": report.get("passed") is True})
    return errors, warnings, details


CHECKERS = {
    "initialized": check_initialized,
    "outline": check_outline_stage,
    "approved": check_approved,
    "images": check_images,
    "editable": check_editable,
    "qa": check_qa,
}


def run_check(run_dir: Path, state: dict[str, Any], stage: str, write_receipt: bool = True) -> dict[str, Any]:
    errors, warnings, details = CHECKERS[stage](run_dir, state)
    report = {
        "schema_version": "pptflow.check/1",
        "run_id": state["run_id"],
        "stage": stage,
        "passed": not errors,
        "checked_at": utc_now(),
        "errors": errors,
        "warnings": warnings,
        "details": details,
    }
    if write_receipt:
        atomic_write_json(run_dir / "qa" / "checks" / f"{stage}.json", report)
    return report


def require_pass(report: dict[str, Any]) -> None:
    if not report["passed"]:
        raise PptflowError("STAGE_CHECK_FAILED", f"Stage check failed: {report['stage']}", report)


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    skill_home = Path(os.environ.get("PPTFLOW_SKILL_HOME", Path(__file__).resolve().parent.parent)).resolve()
    profile_value = os.environ.get("PPTFLOW_CHATGPT_PROFILE")
    profile_path = Path(profile_value).expanduser().resolve() if profile_value else None
    profile_safe = bool(profile_path and profile_path.is_dir())
    profile_reason = None
    local_app_data = os.environ.get("LOCALAPPDATA")
    if profile_safe and local_app_data:
        browser_roots = (
            Path(local_app_data).resolve() / "Google" / "Chrome" / "User Data",
            Path(local_app_data).resolve() / "Microsoft" / "Edge" / "User Data",
        )
        for root in browser_roots:
            try:
                profile_path.relative_to(root)
                profile_safe = False
                profile_reason = "Profile points into a normal Chrome or Edge user-data directory."
                break
            except ValueError:
                pass
    checks = {
        "python": {"ok": sys.version_info >= (3, 10), "version": sys.version.split()[0], "executable": sys.executable},
        "skill_home": {"ok": skill_home.is_dir(), "path": str(skill_home)},
        "powerpoint_qa_script": {"ok": (skill_home / "scripts" / "powerpoint-qa.ps1").is_file(), "path": str(skill_home / "scripts" / "powerpoint-qa.ps1")},
        "powershell": {"ok": shutil.which("powershell.exe") is not None, "path": shutil.which("powershell.exe")},
        "editppt": {"ok": shutil.which("editppt") is not None, "path": shutil.which("editppt")},
        "agent_browser": {"ok": shutil.which("agent-browser") is not None, "path": shutil.which("agent-browser")},
        "chatgpt_profile": {"ok": profile_safe, "configured": bool(profile_value), "path": str(profile_path) if profile_path else profile_value, "reason": profile_reason},
        "chatgpt_web": {"mode": "manual-visible-browser", "auth_checked": False, "credentials_stored": False},
    }
    skill_root = skill_home.parent
    companions = {
        "outline_skill": skill_root / "ppt-material-to-outline" / "SKILL.md",
        "browser_skill": skill_root / "chatgpt-template-slide-images" / "SKILL.md",
        "editable_skill": skill_root / "image-to-editable-ppt" / "SKILL.md",
        "template_renderer": skill_root / "chatgpt-template-slide-images" / "scripts" / "render-template-references.ps1",
    }
    for name, path in companions.items():
        checks[name] = {"ok": path.is_file(), "path": str(path)}
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application\CLSID"):
                checks["powerpoint_registered"] = {"ok": True}
        except OSError:
            checks["powerpoint_registered"] = {"ok": False}
    else:
        checks["powerpoint_registered"] = {"ok": False, "reason": "Windows is required"}
    required = (
        "python",
        "skill_home",
        "powerpoint_qa_script",
        "powershell",
        "editppt",
        "agent_browser",
        "chatgpt_profile",
        "powerpoint_registered",
        "outline_skill",
        "browser_skill",
        "editable_skill",
        "template_renderer",
    )
    core_ok = all(checks[name]["ok"] for name in required)
    result = {"ok": core_ok, "checks": checks, "deep_requested": bool(args.deep)}
    if not core_ok:
        raise PptflowError("DOCTOR_FAILED", "One or more required PPT pipeline capabilities are unavailable.", result, exit_code=3)
    return result


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    policy = normalize_cloud_policy(args.cloud_policy)
    if args.width < 1280 or args.height < 720 or args.width * 9 != args.height * 16:
        raise PptflowError("CANVAS_INVALID", "--width and --height must be at least 1280x720 with an exact 16:9 ratio.", exit_code=3)
    output_root = Path(args.out).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = output_root / f"{stamp}-{slug(args.title)}"
    run_dir.mkdir()
    for directory in (
        "input/materials",
        "input/template",
        "analysis",
        "outline/slide-briefs",
        "generation/prompts",
        "generation/images",
        "generation/candidates",
        "editable",
        "qa/checks",
        "qa/powerpoint-render",
        "logs",
    ):
        (run_dir / directory).mkdir(parents=True, exist_ok=True)

    materials: list[dict[str, Any]] = []
    for index, value in enumerate(args.material, start=1):
        source = Path(value).expanduser().resolve()
        target = run_dir / "input" / "materials" / f"M{index:03d}{source.suffix.lower()}"
        record = snapshot_file(source, target)
        record.update({"source_id": f"M{index:03d}", "path": relative_path(run_dir, target)})
        record.pop("snapshot_path", None)
        materials.append(record)

    template_source = Path(args.template).expanduser().resolve()
    template_target = run_dir / "input" / "template" / f"T001{template_source.suffix.lower()}"
    template = snapshot_file(template_source, template_target)
    template.update({"template_id": "T001", "path": relative_path(run_dir, template_target)})
    template.pop("snapshot_path", None)

    created = utc_now()
    state = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_dir.name,
        "title": args.title,
        "created_at": created,
        "updated_at": created,
        "phase": "initialized",
        "cloud_policy": policy,
        "cloud_policy_recorded_at": created,
        "settings": {"aspect_ratio": "16:9", "image_width": args.width, "image_height": args.height},
        "inputs": {"materials": materials, "template": template},
        "outline": None,
        "prompts": {"outline": None, "images": {}, "editable": {}},
        "images": {},
    }
    inventory = {
        "schema_version": "1.0",
        "run_id": state["run_id"],
        "materials": materials,
        "template": template,
        "created_at": created,
    }
    atomic_write_json(run_dir / "input" / "input-manifest.json", inventory)
    atomic_write_json(run_dir / "generation" / "generation-log.json", {"schema_version": "1.0", "run_id": state["run_id"], "slides": {}})
    save_state(run_dir, state)
    append_event(run_dir, "init", {"phase": "initialized", "cloud_policy": policy, "material_count": len(materials)})
    return {"run": str(run_dir), "run_id": state["run_id"], "phase": state["phase"], "cloud_policy": policy}


def next_actions(run_dir: Path, state: dict[str, Any]) -> list[str]:
    quoted = f'"{run_dir}"'
    phase = state["phase"]
    if phase == "initialized":
        return [f"pptflow prompts {quoted} --stage outline", f"pptflow advance {quoted}"]
    if phase == "outline":
        return [f"pptflow approve-outline {quoted} --file outline.json --reviewer USER"]
    if phase == "approved":
        return [f"pptflow prompts {quoted}", f"pptflow authorize-web {quoted} --file STYLE_REF.png --authorized-by USER", f"pptflow advance {quoted}"]
    if phase == "images":
        missing = sorted(set(expected_slide_ids(run_dir)) - set(state.get("images", {})))
        if missing:
            return [f"pptflow ingest-image {quoted} --slide {missing[0]} --file IMAGE.png"]
        return [f"pptflow advance {quoted}"]
    if phase == "editable":
        return [f"pptflow prompts {quoted} --stage editable", f"pptflow advance {quoted}"]
    if phase == "qa":
        qa_script = Path(__file__).resolve().parent / "powerpoint-qa.ps1"
        pptx_path = run_dir / "editable" / "final.pptx"
        qa_path = run_dir / "qa"
        return [f'powershell -NoProfile -File "{qa_script}" -Pptx "{pptx_path}" -QaDir "{qa_path}"', f"pptflow advance {quoted}"]
    return []


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    slide_ids: list[str] = []
    if outline_path(run_dir).is_file():
        outline, errors = normalize_outline(read_json(outline_path(run_dir)))
        if not errors:
            slide_ids = [slide["slide_id"] for slide in outline["slides"]]
    gate = cloud_gate(run_dir, state)
    web_gate = web_transmission_gate(run_dir, state)
    current_stage = "qa" if state["phase"] == "complete" else state["phase"]
    current_gate = run_check(run_dir, state, current_stage, write_receipt=False)
    actions = next_actions(run_dir, state)
    if state["phase"] == "complete" and not current_gate["passed"]:
        actions = [f'pptflow check "{run_dir}" --stage qa']
    approved_valid = False
    if outline_path(run_dir).is_file() and approval_path(run_dir).is_file():
        approved_valid = run_check(run_dir, state, "approved", write_receipt=False)["passed"]
    return {
        "run": str(run_dir),
        "run_id": state["run_id"],
        "phase": state["phase"],
        "effective_status": state["phase"] if current_gate["passed"] else f"{state['phase']}-stale",
        "current_gate": current_gate,
        "cloud_policy": state["cloud_policy"],
        "cloud_gate": gate,
        "web_transmission_gate": web_gate,
        "outline_present": outline_path(run_dir).is_file(),
        "outline_approved": approved_valid,
        "slide_ids": slide_ids,
        "accepted_images": sorted(state.get("images", {})),
        "missing_images": sorted(set(slide_ids) - set(state.get("images", {}))),
        "next_actions": actions,
    }


def archive_downstream(run_dir: Path, state: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-%f")
    archive_root = run_dir / "logs" / "invalidated" / stamp
    direct_files = (
        "generation/generation-log.json",
        "generation/prompt-pack.json",
        "generation/web-transmission-approval.json",
        "generation/redaction-approval.json",
        "generation/image-generation-brief.json",
        "editable/final.pptx",
        "editable/validation.json",
        "editable/source.json",
        "qa/powerpoint-qa.json",
        "qa/qa-report.json",
    )
    candidates: list[Path] = [run_dir / value for value in direct_files]
    for pattern in (
        "generation/images/*.png",
        "generation/prompts/S*.md",
        "generation/prompts/editable/*.md",
        "generation/candidates/**/*",
        "qa/powerpoint-render/*",
        "qa/checks/*.json",
    ):
        candidates.extend(path for path in run_dir.glob(pattern) if path.is_file())
    moved: list[str] = []
    for source in sorted(set(candidates), key=lambda path: path.as_posix()):
        if not source.is_file():
            continue
        relative = relative_path(run_dir, source)
        destination = archive_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append(relative)
    atomic_write_json(
        archive_root / "invalidation.json",
        {"schema_version": "1.0", "invalidated_at": utc_now(), "previous_phase": state["phase"], "moved": moved},
    )
    atomic_write_json(run_dir / "generation" / "generation-log.json", {"schema_version": "1.0", "run_id": state["run_id"], "slides": {}})
    return relative_path(run_dir, archive_root)


def cmd_approve_outline(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    source = Path(args.file).expanduser().resolve()
    outline, errors = normalize_outline(read_json(source))
    if errors:
        raise PptflowError("OUTLINE_INVALID", "Outline validation failed.", errors)
    if normalize_cloud_policy(outline["deck"]["cloud_policy"]) != normalize_cloud_policy(state["cloud_policy"]):
        raise PptflowError("CLOUD_POLICY_MISMATCH", "Outline cloud policy does not match the run policy.")
    input_hashes = {item["sha256"] for item in state["inputs"]["materials"]}
    unknown_hashes = [item["sha256"] for item in outline["source_snapshot"] if item["sha256"].lower() not in input_hashes]
    if unknown_hashes:
        raise PptflowError("SOURCE_SNAPSHOT_MISMATCH", "Outline references material snapshots outside this run.", unknown_hashes)
    validation = outline["validation"]
    unresolved = validation["unresolved_claims"] + validation["overloaded_slides"] + validation["source_conflicts"]
    if unresolved and not args.note:
        raise PptflowError("OUTLINE_EXCEPTIONS_UNACKNOWLEDGED", "Outline has unresolved validation items; provide --note for explicit approval.", unresolved)
    for slide in outline["slides"]:
        slide["status"] = "approved"
    canonical_text = json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
    outline_hash = sha256_text(canonical_text)
    target = outline_path(run_dir)
    old_outline = read_json(target) if target.is_file() else None
    old_hash = sha256_file(target) if target.is_file() else None
    changed = old_hash != outline_hash
    has_prior_approval = approval_path(run_dir).is_file()
    downstream_exists = state["phase"] in {"images", "editable", "qa", "complete"} or bool(state.get("prompts", {}).get("images")) or bool(state.get("images"))
    archive_path = None
    if changed and has_prior_approval:
        if not args.replace:
            raise PptflowError("DOWNSTREAM_ARTIFACTS_EXIST", "A changed approved outline requires --replace to invalidate downstream artifacts.")
        if not isinstance(old_outline, dict) or outline["outline_version"] <= int(old_outline.get("outline_version", 0)):
            raise PptflowError("OUTLINE_VERSION_NOT_INCREMENTED", "A replacement outline must increment outline_version.")
        archive_path = archive_downstream(run_dir, state)
    elif changed and downstream_exists and not args.replace:
        raise PptflowError("DOWNSTREAM_ARTIFACTS_EXIST", "A changed outline requires --replace to invalidate downstream artifacts.")

    if changed:
        atomic_write_text(target, canonical_text)
    approval = {
        "schema_version": "1.0",
        "deck_id": outline["deck_id"],
        "outline_version": outline["outline_version"],
        "approved": True,
        "approval_mode": args.approval_mode,
        "approved_by": args.reviewer,
        "approved_at": utc_now(),
        "outline_sha256": outline_hash,
        "source_snapshot": outline["source_snapshot"],
        "exceptions": [args.note] if args.note else [],
    }
    atomic_write_json(approval_path(run_dir), approval)
    state["outline"] = {"path": "outline/deck-outline.json", "sha256": outline_hash, "approval_path": "outline/approval.json"}
    if changed or not has_prior_approval:
        state["phase"] = "approved"
        state["prompts"]["images"] = {}
        state["prompts"]["editable"] = {}
        state["images"] = {}
    save_state(run_dir, state)
    append_event(run_dir, "approve-outline", {"outline_sha256": outline_hash, "approved_by": args.reviewer, "slide_count": len(outline["slides"]), "changed": changed, "archive": archive_path})
    return {"run": str(run_dir), "phase": state["phase"], "outline_sha256": outline_hash, "slide_ids": [slide["slide_id"] for slide in outline["slides"]], "changed": changed, "archive": archive_path}


def cmd_authorize_web(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    if args.retry_limit < 0:
        raise PptflowError("INVALID_RETRY_LIMIT", "--retry-limit cannot be negative.")
    require_pass(run_check(run_dir, state, "approved"))
    cloud = cloud_gate(run_dir, state)
    if not cloud["passed"]:
        raise PptflowError("CLOUD_POLICY_BLOCKED", f"ChatGPT web transmission is blocked: {cloud.get('reason')}", cloud, exit_code=3)
    style = validate_style_lock(run_dir, state)
    expected = expected_slide_ids(run_dir)
    slides = args.slide or expected
    if len(set(slides)) != len(slides) or any(slide not in expected for slide in slides):
        raise PptflowError("WEB_SCOPE_INVALID", "Every --slide must be a unique approved slide ID.")
    missing_prompts = [slide for slide in slides if slide not in state["prompts"]["images"]]
    if missing_prompts:
        raise PptflowError("PROMPT_REQUIRED", "Generate current image prompts before recording web authorization.", missing_prompts, exit_code=3)

    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in args.file:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        candidate = candidate.resolve()
        try:
            relative = relative_path(run_dir, candidate)
        except ValueError as exc:
            raise PptflowError("WEB_FILE_OUTSIDE_RUN", f"Approved web file escapes the run directory: {candidate}") from exc
        if not candidate.is_file():
            raise PptflowError("WEB_FILE_MISSING", f"Approved web file is missing: {candidate}")
        if relative not in seen:
            files.append({"path": relative, "sha256": sha256_file(candidate)})
            seen.add(relative)

    style_reference_paths = {str(item["path"]) for item in style["references"]}
    if not style_reference_paths.intersection(seen):
        raise PptflowError("STYLE_REFERENCE_NOT_AUTHORIZED", "Authorize at least one immutable STYLE_REF file for upload.")
    if cloud["policy"] == "redact_required":
        allowed = set(cloud.get("attachments", []))
        if any(item["path"] not in allowed for item in files):
            raise PptflowError("UNREDACTED_FILE_BLOCKED", "Every transmitted file must appear in the approved redacted file set.")
        prompt_paths = {state["prompts"]["images"][slide]["path"] for slide in slides}
        if not prompt_paths.issubset(seen):
            raise PptflowError("REDACTED_PROMPT_NOT_AUTHORIZED", "For redact_required runs, authorize every exact prompt file as sanitized content.")

    authorized_prompts: list[dict[str, str]] = []
    for slide in slides:
        current = state["prompts"]["images"][slide]
        history = current.get("history") if isinstance(current, dict) else None
        initial = history[0] if isinstance(history, list) and history else current
        authorized_prompts.append({"slide_id": slide, "path": initial["path"], "sha256": initial["sha256"]})

    approval = {
        "schema_version": "1.0",
        "approved": True,
        "destination": "https://chatgpt.com/",
        "approved_by": args.authorized_by,
        "approved_at": utc_now(),
        "note": args.note,
        "slides": slides,
        "initial_prompt_count": len(slides),
        "corrective_retries_per_slide": args.retry_limit,
        "canonical_slide_text_transmitted": True,
        "files": files,
        "prompts": authorized_prompts,
        "outline_sha256": sha256_file(outline_path(run_dir)),
        "template_sha256": state["inputs"]["template"]["sha256"],
        "style_lock_sha256": style["style_sha256"],
        "template_reference_manifest_sha256": style["manifest_sha256"],
    }
    target = run_dir / "generation" / "web-transmission-approval.json"
    atomic_write_json(target, approval)
    append_event(
        run_dir,
        "authorize-web",
        {
            "destination": approval["destination"],
            "slides": slides,
            "initial_prompt_count": len(slides),
            "corrective_retries_per_slide": args.retry_limit,
            "file_count": len(files),
            "approved_by": args.authorized_by,
        },
    )
    return {"run": str(run_dir), "approval": relative_path(run_dir, target), "web_gate": web_transmission_gate(run_dir, state)}


def selected_slides(outline: dict[str, Any], slide_id: str | None) -> list[dict[str, Any]]:
    slides = outline["slides"]
    if slide_id is None:
        return slides
    selected = [slide for slide in slides if slide["slide_id"] == slide_id]
    if not selected:
        raise PptflowError("UNKNOWN_SLIDE", f"Slide is not in the approved outline: {slide_id}")
    return selected


def cmd_prompts(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    stage = args.stage
    if args.attempt < 1:
        raise PptflowError("INVALID_ATTEMPT", "--attempt must be a positive integer.")
    if stage != "images" and (args.attempt != 1 or args.correction):
        raise PptflowError("INVALID_PROMPT_OPTIONS", "--attempt and --correction apply only to image prompts.")
    outputs: list[dict[str, Any]] = []
    if stage == "outline":
        manifest_path = run_dir / "input" / "input-manifest.json"
        prompt = (
            "Use the ppt-material-to-outline skill to analyze only the immutable snapshots listed in "
            "input/input-manifest.json. Preserve evidence status and exact source locators. Write the canonical "
            "schema_version 1.0 artifact to outline/deck-outline.json, but do not approve it.\n"
        )
        target = run_dir / "generation" / "prompts" / "outline.md"
        atomic_write_text(target, prompt)
        record = {"path": relative_path(run_dir, target), "sha256": sha256_file(target), "input_manifest_sha256": sha256_file(manifest_path)}
        state["prompts"]["outline"] = record
        outputs.append(record)
    elif stage == "images":
        require_pass(run_check(run_dir, state, "approved"))
        gate = cloud_gate(run_dir, state)
        if not gate["passed"]:
            raise PptflowError("CLOUD_POLICY_BLOCKED", f"ChatGPT web generation is blocked: {gate.get('reason')}", gate, exit_code=3)
        style = validate_style_lock(run_dir, state)
        outline = read_json(outline_path(run_dir))
        outline_hash = sha256_file(outline_path(run_dir))
        style_hash = style["style_sha256"]
        style_text = style["style_text"]
        style_references = style["references"]
        reference_lines = "\n".join(
            f"- {Path(item['path']).name} | source slide {item.get('slide_number')} | sha256 {item['sha256']}"
            for item in style_references
        )
        template_hash = state["inputs"]["template"]["sha256"]
        if args.attempt == 1 and args.correction:
            raise PptflowError("INVALID_PROMPT_OPTIONS", "Initial image prompts cannot contain --correction.")
        if args.attempt > 1:
            if not args.slide or not args.correction:
                raise PptflowError("CORRECTION_SCOPE_REQUIRED", "A corrective prompt needs one --slide and at least one --correction.")
            web_gate = web_transmission_gate(run_dir, state)
            if not web_gate["passed"] or args.slide not in web_gate.get("slides", []):
                raise PptflowError("WEB_TRANSMISSION_NOT_AUTHORIZED", f"Corrective prompt is blocked: {web_gate.get('reason')}", web_gate, exit_code=3)
            if args.attempt > 1 + web_gate["corrective_retries_per_slide"]:
                raise PptflowError("WEB_RETRY_LIMIT_EXCEEDED", f"Attempt {args.attempt} exceeds the recorded web retry allowance.", exit_code=3)
        for slide in selected_slides(outline, args.slide):
            slide_id = slide["slide_id"]
            request_seed = f"{state['run_id']}|{slide_id}|{args.attempt}|{outline_hash}|{template_hash}|{style_hash}|{args.correction}"
            request_id = hashlib.sha256(request_seed.encode("utf-8")).hexdigest()[:16]
            metadata = (
                "[REQUEST METADATA - DO NOT RENDER]\n"
                f"run_id: {state['run_id']}\nslide_id: {slide_id}\nattempt_id: {args.attempt}\nrequest_id: {request_id}\n"
                f"style_lock_sha256: {style_hash}\ntemplate_sha256: {template_hash}\n\n"
            )
            authority = (
                "[AVAILABLE IMMUTABLE STYLE REFERENCES]\n"
                + reference_lines
                + "\nAttach one to three references named by the style lock for this page.\n\n"
                "[STYLE LOCK - DO NOT RENDER]\n"
                + style_text
                + "\n\n[APPROVED SLIDE OBJECT - DO NOT RENDER AS METADATA]\n"
                + json.dumps(slide, ensure_ascii=False, indent=2)
                + "\n"
            )
            if args.attempt == 1:
                instruction = (
                f"Create exactly one complete {state['settings']['aspect_ratio']} PowerPoint slide image at "
                f"{state['settings']['image_width']}x{state['settings']['image_height']}. Use the supplied original "
                "template references only for visual grammar; do not copy their subject matter or text. Use the style "
                "lock and approved slide object below as exact authorities. Do not rewrite, translate, omit, or invent "
                "facts, numbers, formulas, labels, logos, or claims. Do not fabricate a brand mark: reserve its defined "
                "region for downstream insertion of the supplied original asset. Do not render this metadata or any "
                "explanation. Generate a flat slide canvas only, not a monitor, projector, paper, frame, mockup, or "
                "multi-slide montage. Add no placeholder text, gibberish, decorative microtext, watermark, or model "
                "signature. Keep titles, major labels, numbers, units, and formulas clear; resolve density through "
                "hierarchy and whitespace rather than omission. Output only the full-bleed slide image.\n\n"
                )
            else:
                deviations = "\n".join(f"- {item}" for item in args.correction)
                instruction = (
                    "Regenerate the same slide. Preserve the complete original STYLE LOCK, layout grammar, and every "
                    "approved content item. Reattach the original STYLE_REF files; do not use a rejected candidate as "
                    "a style anchor. Correct only these observed defects:\n"
                    + deviations
                    + "\nDo not add new text, numbers, visual categories, colors, logos, or layouts. Output only one "
                    "complete full-bleed slide image and do not render REQUEST METADATA.\n\n"
                )
            prompt = metadata + instruction + authority
            target_name = f"{slide_id}.md" if args.attempt == 1 else f"{slide_id}-attempt-{args.attempt:02d}.md"
            target = run_dir / "generation" / "prompts" / target_name
            atomic_write_text(target, prompt)
            base_record = {
                "slide_id": slide_id,
                "attempt_id": args.attempt,
                "path": relative_path(run_dir, target),
                "sha256": sha256_file(target),
                "request_id": request_id,
                "outline_sha256": outline_hash,
                "template_sha256": template_hash,
                "style_lock_sha256": style_hash,
                "template_reference_manifest_sha256": style["manifest_sha256"],
                "available_style_references": [item["path"] for item in style_references],
                "observed_corrections": args.correction,
            }
            previous = state["prompts"]["images"].get(slide_id)
            if args.attempt == 1:
                history = [base_record.copy()]
            else:
                if not isinstance(previous, dict):
                    raise PptflowError("INITIAL_PROMPT_MISSING", f"Generate attempt 1 before attempt {args.attempt} for {slide_id}.")
                history = list(previous.get("history") or [{key: value for key, value in previous.items() if key != "history"}])
                previous_attempt = max(int(item.get("attempt_id", 1)) for item in history)
                if args.attempt != previous_attempt + 1:
                    raise PptflowError("PROMPT_ATTEMPT_SEQUENCE", f"Expected attempt {previous_attempt + 1} for {slide_id}, got {args.attempt}.")
                history.append(base_record.copy())
            record = dict(base_record)
            record["history"] = history
            state["prompts"]["images"][slide_id] = record
            outputs.append(base_record)
        pack = {
            "schema_version": "1.0",
            "run_id": state["run_id"],
            "cloud_policy": state["cloud_policy"],
            "cloud_policy_attachments": gate.get("attachments", []),
            "style_references": style_references,
            "attachments": sorted(set(gate.get("attachments", [])) | {item["path"] for item in style_references}),
            "slides": [state["prompts"]["images"][slide["slide_id"]] for slide in outline["slides"] if slide["slide_id"] in state["prompts"]["images"]],
        }
        atomic_write_json(run_dir / "generation" / "prompt-pack.json", pack)
    else:
        require_pass(run_check(run_dir, state, "images"))
        outline = read_json(outline_path(run_dir))
        for slide in selected_slides(outline, args.slide):
            slide_id = slide["slide_id"]
            prompt = (
                "Use the image-to-editable-ppt skill to reconstruct generation/images/"
                f"{slide_id}.png as object-level editable PowerPoint content. The approved text in "
                "outline/deck-outline.json overrides raster OCR. Preserve formulas, labels, curves, template grammar, "
                "and provenance. Do not use a single full-slide screenshot as the editable result. Build, validate, "
                "render, and visually compare the page before recording it.\n"
            )
            target = run_dir / "generation" / "prompts" / "editable" / f"{slide_id}.md"
            atomic_write_text(target, prompt)
            record = {"slide_id": slide_id, "path": relative_path(run_dir, target), "sha256": sha256_file(target)}
            state["prompts"]["editable"][slide_id] = record
            outputs.append(record)
    save_state(run_dir, state)
    append_event(run_dir, "prompts", {"stage": stage, "outputs": [item["path"] for item in outputs]})
    return {"run": str(run_dir), "stage": stage, "outputs": outputs}


def cmd_ingest_image(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    if args.source == "chatgpt-web":
        gate = cloud_gate(run_dir, state)
        if not gate["passed"]:
            raise PptflowError("CLOUD_POLICY_BLOCKED", f"ChatGPT image ingest is blocked: {gate.get('reason')}", gate, exit_code=3)
        web_gate = web_transmission_gate(run_dir, state)
        if not web_gate["passed"]:
            raise PptflowError("WEB_TRANSMISSION_NOT_AUTHORIZED", f"ChatGPT image ingest is blocked: {web_gate.get('reason')}", web_gate, exit_code=3)
        if args.slide not in web_gate["slides"]:
            raise PptflowError("WEB_SLIDE_NOT_AUTHORIZED", f"Slide is outside the recorded web scope: {args.slide}", exit_code=3)
        if args.attempt > 1 + web_gate["corrective_retries_per_slide"]:
            raise PptflowError("WEB_RETRY_LIMIT_EXCEEDED", f"Attempt {args.attempt} exceeds the recorded web retry allowance.", exit_code=3)
        if args.conversation_url:
            parsed = urlparse(args.conversation_url)
            if parsed.scheme != "https" or parsed.hostname != "chatgpt.com":
                raise PptflowError("INVALID_CONVERSATION_URL", "Conversation URL must use HTTPS host chatgpt.com.")
    if args.attempt < 1:
        raise PptflowError("INVALID_ATTEMPT", "--attempt must be a positive integer.")
    require_pass(run_check(run_dir, state, "approved"))
    slide_ids = expected_slide_ids(run_dir)
    if args.slide not in slide_ids:
        raise PptflowError("UNKNOWN_SLIDE", f"Slide is not in the approved outline: {args.slide}")
    prompt_record = state["prompts"]["images"].get(args.slide)
    if not prompt_record:
        raise PptflowError("PROMPT_REQUIRED", f"Generate the image prompt before ingesting {args.slide}.", exit_code=3)
    if prompt_record.get("attempt_id") != args.attempt:
        raise PptflowError(
            "PROMPT_ATTEMPT_MISMATCH",
            f"Current prompt for {args.slide} is attempt {prompt_record.get('attempt_id')}; cannot ingest as attempt {args.attempt}.",
            exit_code=3,
        )
    if args.attempt > 1 and not prompt_record.get("observed_corrections"):
        raise PptflowError("CORRECTIVE_PROMPT_REQUIRED", "A retry must be bound to an observed corrective prompt.", exit_code=3)
    if args.source == "chatgpt-web" and web_gate.get("policy") == "redact_required" and prompt_record["path"] not in web_gate.get("files", []):
        raise PptflowError("REDACTED_PROMPT_NOT_AUTHORIZED", "The exact current prompt is outside the approved redacted transmission set.", exit_code=3)
    source = Path(args.file).expanduser().resolve()
    width, height = png_dimensions(source)
    expected_size = (state["settings"]["image_width"], state["settings"]["image_height"])
    if (width, height) != expected_size:
        raise PptflowError("IMAGE_DIMENSION_MISMATCH", f"Expected {expected_size[0]}x{expected_size[1]}, got {width}x{height}.")
    target = run_dir / "generation" / "images" / f"{args.slide}.png"
    if source != target.resolve():
        shutil.copy2(source, target)
    image_hash = sha256_file(target)
    style_path = run_dir / "generation" / "style-lock.md"
    accepted = {
        "slide_id": args.slide,
        "attempt_id": args.attempt,
        "image_path": relative_path(run_dir, target),
        "image_sha256": image_hash,
        "width": width,
        "height": height,
        "source": args.source,
        "accepted_at": utc_now(),
        "outline_sha256": sha256_file(outline_path(run_dir)),
        "template_sha256": state["inputs"]["template"]["sha256"],
        "style_lock_sha256": sha256_file(style_path) if style_path.is_file() else None,
        "template_reference_manifest_sha256": prompt_record.get("template_reference_manifest_sha256"),
        "prompt_path": prompt_record["path"],
        "prompt_sha256": prompt_record["sha256"],
        "conversation_url": args.conversation_url,
        "retry_count": max(args.attempt - 1, 0),
        "reviewer_decision": "accepted",
        "unresolved_issues": args.unresolved,
        "note": args.note,
    }
    log_path = run_dir / "generation" / "generation-log.json"
    log = read_json(log_path)
    slide_log = log.setdefault("slides", {}).setdefault(args.slide, {"history": [], "accepted": None})
    slide_log.setdefault("history", []).append(accepted)
    slide_log["accepted"] = accepted
    atomic_write_json(log_path, log)
    state["images"][args.slide] = {"path": accepted["image_path"], "sha256": image_hash}
    if state["phase"] in {"approved", "outline", "initialized"}:
        state["phase"] = "images"
    save_state(run_dir, state)
    append_event(run_dir, "ingest-image", {"slide_id": args.slide, "image_sha256": image_hash, "source": args.source})
    return {"run": str(run_dir), "phase": state["phase"], "slide_id": args.slide, "image": accepted}


def cmd_check(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    report = run_check(run_dir, state, args.stage)
    if not report["passed"]:
        raise PptflowError("STAGE_CHECK_FAILED", f"Stage check failed: {args.stage}", report)
    return report


def cmd_advance(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = run_path(args.run)
    state = load_state(run_dir)
    phase = state["phase"]
    if phase == "complete":
        require_pass(run_check(run_dir, state, "qa"))
        return {"run": str(run_dir), "phase": "complete", "changed": False}
    if phase == "initialized":
        require_pass(run_check(run_dir, state, "initialized"))
        next_phase = "outline"
    elif phase == "outline":
        require_pass(run_check(run_dir, state, "approved"))
        next_phase = "approved"
    elif phase == "approved":
        require_pass(run_check(run_dir, state, "approved"))
        gate = cloud_gate(run_dir, state)
        if not gate["passed"]:
            raise PptflowError("CLOUD_POLICY_BLOCKED", f"Cannot advance to web image generation: {gate.get('reason')}", gate, exit_code=3)
        validate_style_lock(run_dir, state)
        expected = expected_slide_ids(run_dir)
        missing_prompts = sorted(set(expected) - set(state["prompts"]["images"]))
        if missing_prompts:
            raise PptflowError("IMAGE_PROMPTS_MISSING", "Generate all image prompts before advancing.", missing_prompts, exit_code=3)
        web_gate = web_transmission_gate(run_dir, state)
        if not web_gate["passed"]:
            raise PptflowError("WEB_TRANSMISSION_NOT_AUTHORIZED", f"Cannot advance to web image generation: {web_gate.get('reason')}", web_gate, exit_code=3)
        next_phase = "images"
    elif phase == "images":
        require_pass(run_check(run_dir, state, "images"))
        next_phase = "editable"
    elif phase == "editable":
        require_pass(run_check(run_dir, state, "editable"))
        next_phase = "qa"
    else:
        require_pass(run_check(run_dir, state, "qa"))
        next_phase = "complete"
    state["phase"] = next_phase
    save_state(run_dir, state)
    append_event(run_dir, "advance", {"from": phase, "to": next_phase})
    return {"run": str(run_dir), "from": phase, "phase": next_phase, "changed": True}


def emit(command: str, result: dict[str, Any], json_mode: bool) -> None:
    envelope = {"ok": True, "command": command, "result": result}
    if json_mode:
        print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pptflow", description="Deterministic gates for the PPT production pipeline.")
    parser.add_argument("--json", action="store_true", help="Emit stable machine-readable JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check the local pipeline environment without reading credentials.")
    doctor.add_argument("--deep", action="store_true", help="Report that a deeper external-tool check was requested.")
    doctor.set_defaults(handler=cmd_doctor)

    init = subparsers.add_parser("init", help="Create a run and snapshot all supplied inputs.")
    init.add_argument("--title", required=True)
    init.add_argument("--material", action="append", required=True, help="Material file; repeat for multiple files.")
    init.add_argument("--template", required=True)
    init.add_argument("--out", required=True, help="Parent directory for the new run.")
    init.add_argument("--cloud-policy", choices=CLOUD_POLICY_CHOICES, default="unknown")
    init.add_argument("--width", type=int, default=1920)
    init.add_argument("--height", type=int, default=1080)
    init.set_defaults(handler=cmd_init)

    status = subparsers.add_parser("status", help="Show run facts and deterministic next actions.")
    status.add_argument("run")
    status.set_defaults(handler=cmd_status)

    approve = subparsers.add_parser("approve-outline", help="Validate, normalize, snapshot, and explicitly approve an outline.")
    approve.add_argument("run")
    approve.add_argument("--file", required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--approval-mode", choices=("explicit", "preauthorized"), default="explicit")
    approve.add_argument("--note")
    approve.add_argument("--replace", action="store_true", help="Invalidate downstream state when approving a changed outline.")
    approve.set_defaults(handler=cmd_approve_outline)

    authorize = subparsers.add_parser("authorize-web", help="Record the exact user-approved ChatGPT transmission scope.")
    authorize.add_argument("run")
    authorize.add_argument("--slide", action="append", help="Approved slide ID; repeat as needed. Defaults to all slides.")
    authorize.add_argument("--file", action="append", required=True, help="Task-local file authorized for transmission; repeat as needed.")
    authorize.add_argument("--retry-limit", type=int, default=1, help="Corrective retries allowed per slide after the initial prompt.")
    authorize.add_argument("--authorized-by", required=True)
    authorize.add_argument("--note")
    authorize.set_defaults(handler=cmd_authorize_web)

    prompts = subparsers.add_parser("prompts", help="Generate deterministic prompt artifacts; never calls an LLM.")
    prompts.add_argument("run")
    prompts.add_argument("--stage", choices=("outline", "images", "editable"), default="images")
    prompts.add_argument("--slide", help="Generate only one approved slide prompt.")
    prompts.add_argument("--attempt", type=int, default=1, help="Image-generation attempt number; attempt 2+ requires one slide and observed corrections.")
    prompts.add_argument("--correction", action="append", default=[], help="Observed defect to correct; repeat for multiple defects.")
    prompts.set_defaults(handler=cmd_prompts)

    ingest = subparsers.add_parser("ingest-image", help="Validate and register one accepted slide PNG.")
    ingest.add_argument("run")
    ingest.add_argument("--slide", required=True)
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--source", choices=("chatgpt-web", "local"), default="chatgpt-web")
    ingest.add_argument("--attempt", type=int, default=1)
    ingest.add_argument("--conversation-url")
    ingest.add_argument("--unresolved", action="append", default=[])
    ingest.add_argument("--note")
    ingest.set_defaults(handler=cmd_ingest_image)

    check = subparsers.add_parser("check", help="Run one deterministic stage gate.")
    check.add_argument("run")
    check.add_argument("--stage", choices=tuple(CHECKERS), required=True)
    check.set_defaults(handler=cmd_check)

    advance = subparsers.add_parser("advance", help="Advance exactly one phase after its gate passes.")
    advance.add_argument("run")
    advance.set_defaults(handler=cmd_advance)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
        emit(args.command, result, args.json)
        return 0
    except PptflowError as exc:
        envelope = {
            "ok": False,
            "command": getattr(args, "command", None),
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
        }
        if args.json:
            print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"ERROR [{exc.code}] {exc.message}", file=sys.stderr)
            if exc.details is not None:
                print(json.dumps(exc.details, ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        envelope = {
            "ok": False,
            "command": getattr(args, "command", None),
            "error": {"code": "UNEXPECTED_ERROR", "message": str(exc), "details": None},
        }
        if args.json:
            print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"ERROR [UNEXPECTED_ERROR] {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
