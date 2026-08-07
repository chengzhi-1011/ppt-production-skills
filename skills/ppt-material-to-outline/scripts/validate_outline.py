#!/usr/bin/env python3
"""Validate the canonical PPT outline and its optional claim ledger and approval."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


EVIDENCE_STATUSES = {
    "confirmed",
    "source_stated",
    "derived",
    "recommended",
    "pending_verification",
    "data_insufficient",
    "conflicting",
}
SOURCE_REQUIRED = {"confirmed", "source_stated", "derived", "conflicting"}
UNRESOLVED = {"pending_verification", "data_insufficient", "conflicting"}
CLOUD_POLICIES = {"allowed", "redact_required", "prohibited", "unknown"}
ROLES = {
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
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value: object, allow_empty: bool = True) -> bool:
    return isinstance(value, list) and (allow_empty or bool(value)) and all(nonempty_string(item) for item in value)


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, location: str, message: str) -> None:
        self.errors.append(f"{location}: {message}")

    def warn(self, location: str, message: str) -> None:
        self.warnings.append(f"{location}: {message}")


def validate_claims(data: object, report: Report) -> dict[str, dict]:
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        report.error("claims", "claim ledger must contain a claims list")
        return {}
    if data.get("schema_version") != "1.0":
        report.error("claims.schema_version", "must equal '1.0'")

    index: dict[str, dict] = {}
    for position, claim in enumerate(data["claims"]):
        location = f"claims[{position}]"
        if not isinstance(claim, dict):
            report.error(location, "must be an object")
            continue
        claim_id = claim.get("claim_id")
        if not nonempty_string(claim_id):
            report.error(location, "claim_id must be a nonempty string")
            continue
        if claim_id in index:
            report.error(location, f"duplicate claim_id {claim_id}")
            continue
        index[claim_id] = claim
        if not nonempty_string(claim.get("text")):
            report.error(location, "text must be a nonempty string")
        status = claim.get("status")
        if status not in EVIDENCE_STATUSES:
            report.error(location, f"invalid evidence status {status!r}")
        refs = claim.get("source_refs")
        if not string_list(refs):
            report.error(location, "source_refs must be a list of nonempty strings")
        elif status in SOURCE_REQUIRED and not refs:
            report.error(location, f"{status} requires at least one source reference")
        if status == "derived" and not nonempty_string(claim.get("method")):
            report.error(location, "derived claims require a nonempty method")
    return index


def validate_outline(data: object, claim_index: dict[str, dict] | None, report: Report) -> None:
    if not isinstance(data, dict):
        report.error("outline", "root must be an object")
        return
    if data.get("schema_version") != "1.0":
        report.error("schema_version", "must equal '1.0'")
    if not nonempty_string(data.get("deck_id")):
        report.error("deck_id", "must be a nonempty string")
    version = data.get("outline_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        report.error("outline_version", "must be a positive integer")

    snapshot = data.get("source_snapshot")
    if not isinstance(snapshot, list) or not snapshot:
        report.error("source_snapshot", "must be a nonempty list")
    else:
        for position, item in enumerate(snapshot):
            location = f"source_snapshot[{position}]"
            if not isinstance(item, dict):
                report.error(location, "must be an object")
                continue
            if not nonempty_string(item.get("path")):
                report.error(location, "path must be a nonempty string")
            if not isinstance(item.get("sha256"), str) or not SHA256_RE.fullmatch(item["sha256"]):
                report.error(location, "sha256 must contain 64 hexadecimal characters")

    deck = data.get("deck")
    if not isinstance(deck, dict):
        report.error("deck", "must be an object")
        deck = {}
    for field in ("title", "purpose", "audience", "language", "aspect_ratio"):
        if not nonempty_string(deck.get(field)):
            report.error(f"deck.{field}", "must be a nonempty string")
    if not isinstance(deck.get("duration_min"), (int, float)) or isinstance(deck.get("duration_min"), bool) or deck.get("duration_min", 0) <= 0:
        report.error("deck.duration_min", "must be a positive number")
    target_count = deck.get("target_slide_count")
    if not isinstance(target_count, int) or isinstance(target_count, bool) or target_count < 1:
        report.error("deck.target_slide_count", "must be a positive integer")
    if deck.get("template_ref") is not None and not nonempty_string(deck.get("template_ref")):
        report.error("deck.template_ref", "must be null or a nonempty string")
    if deck.get("cloud_policy") not in CLOUD_POLICIES:
        report.error("deck.cloud_policy", "must be allowed, redact_required, prohibited, or unknown")
    if not isinstance(deck.get("assumptions"), list) or not all(nonempty_string(item) for item in deck.get("assumptions", [])):
        report.error("deck.assumptions", "must be a list of nonempty strings")

    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        report.error("slides", "must be a nonempty list")
        slides = []
    ids: set[str] = set()
    orders: set[int] = set()
    for position, slide in enumerate(slides):
        location = f"slides[{position}]"
        if not isinstance(slide, dict):
            report.error(location, "must be an object")
            continue
        slide_id = slide.get("slide_id")
        expected_slide_id = f"S{position + 1:03d}"
        if not isinstance(slide_id, str) or not re.fullmatch(r"S\d{2,3}", slide_id):
            report.error(location, "slide_id must match S01 or S001 style")
        elif int(slide_id[1:]) != position + 1:
            report.error(location, f"slide_id must be sequential and match order {position + 1} (for example {expected_slide_id})")
        elif slide_id in ids:
            report.error(location, f"duplicate slide_id {slide_id}")
        else:
            ids.add(slide_id)
        order = slide.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            report.error(location, "order must be a positive integer")
        elif order in orders:
            report.error(location, f"duplicate order {order}")
        else:
            orders.add(order)
            if order != position + 1:
                report.error(location, f"order must equal list position {position + 1}")
        if slide.get("role") not in ROLES:
            report.error(location, f"invalid role {slide.get('role')!r}")
        for field in ("title", "objective", "takeaway"):
            if not nonempty_string(slide.get(field)):
                report.error(f"{location}.{field}", "must be a nonempty string")
        if slide.get("status") not in SLIDE_STATES:
            report.error(location, f"invalid slide status {slide.get('status')!r}")
        if not string_list(slide.get("source_refs")):
            report.error(location, "source_refs must be a list of nonempty strings")
        if not isinstance(slide.get("speaker_notes"), str):
            report.error(location, "speaker_notes must be a string")
        if not string_list(slide.get("dependencies")):
            report.error(location, "dependencies must be a list of nonempty strings")

        visual = slide.get("visual_brief")
        if not isinstance(visual, dict):
            report.error(f"{location}.visual_brief", "must be an object")
        else:
            for field in ("visual_type", "message"):
                if not nonempty_string(visual.get(field)):
                    report.error(f"{location}.visual_brief.{field}", "must be a nonempty string")
            for field in ("required_elements", "prohibited_elements"):
                if not string_list(visual.get(field)):
                    report.error(f"{location}.visual_brief.{field}", "must be a list of nonempty strings")
            if not isinstance(visual.get("data_chart_required"), bool):
                report.error(f"{location}.visual_brief.data_chart_required", "must be a boolean")

        layout = slide.get("layout_constraints")
        if not isinstance(layout, dict):
            report.error(f"{location}.layout_constraints", "must be an object")
            max_bullets = None
            max_chars = None
        else:
            template_layout_id = layout.get("template_layout_id")
            if template_layout_id is not None and not nonempty_string(template_layout_id):
                report.error(f"{location}.layout_constraints.template_layout_id", "must be null or a nonempty string")
            max_bullets = layout.get("max_bullets")
            max_chars = layout.get("max_chars")
            if not isinstance(max_bullets, int) or isinstance(max_bullets, bool) or max_bullets < 0:
                report.error(f"{location}.layout_constraints.max_bullets", "must be a nonnegative integer")
            if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
                report.error(f"{location}.layout_constraints.max_chars", "must be a positive integer")

        blocks = slide.get("content_blocks")
        if not isinstance(blocks, list):
            report.error(f"{location}.content_blocks", "must be a list")
            blocks = []
        elif not blocks and slide.get("role") not in {"cover", "agenda", "closing"}:
            report.warn(f"{location}.content_blocks", "non-structural slide has no content blocks")
        total_chars = 0
        bullet_count = 0
        for block_position, block in enumerate(blocks):
            block_location = f"{location}.content_blocks[{block_position}]"
            if not isinstance(block, dict):
                report.error(block_location, "must be an object")
                continue
            if not nonempty_string(block.get("kind")):
                report.error(block_location, "kind must be a nonempty string")
            text = block.get("text")
            if not nonempty_string(text):
                report.error(block_location, "text must be a nonempty string")
            else:
                total_chars += len(text)
            if block.get("kind") == "bullet":
                bullet_count += 1
            claim_ids = block.get("claim_ids")
            if not string_list(claim_ids, allow_empty=False):
                report.error(block_location, "claim_ids must be a nonempty list of strings")
                claim_ids = []
            status = block.get("evidence_status")
            if status not in EVIDENCE_STATUSES:
                report.error(block_location, f"invalid evidence status {status!r}")
            if status in UNRESOLVED and not nonempty_string(block.get("visible_label")):
                report.error(block_location, f"{status} content requires a visible_label")
            if claim_index is not None:
                for claim_id in claim_ids:
                    claim = claim_index.get(claim_id)
                    if claim is None:
                        report.error(block_location, f"unknown claim_id {claim_id}")
                    elif status in EVIDENCE_STATUSES and claim.get("status") != status:
                        report.error(block_location, f"status {status} does not match claim {claim_id} status {claim.get('status')}")
        if isinstance(max_bullets, int) and bullet_count > max_bullets:
            report.warn(location, f"contains {bullet_count} bullets; budget is {max_bullets}")
        if isinstance(max_chars, int) and total_chars > max_chars:
            report.warn(location, f"contains {total_chars} content characters; budget is {max_chars}")

    if isinstance(target_count, int) and slides and target_count != len(slides):
        report.error("deck.target_slide_count", f"target is {target_count}, but outline contains {len(slides)} slides")

    validation = data.get("validation")
    if not isinstance(validation, dict):
        report.error("validation", "must be an object")
    else:
        for field in ("unresolved_claims", "overloaded_slides", "source_conflicts"):
            if not string_list(validation.get(field)):
                report.error(f"validation.{field}", "must be a list of nonempty strings")


def validate_approval(data: object, outline: object, report: Report) -> None:
    if not isinstance(data, dict):
        report.error("approval", "root must be an object")
        return
    if not isinstance(outline, dict):
        return
    if data.get("approved") is not True:
        report.error("approval.approved", "must be true")
    if data.get("deck_id") != outline.get("deck_id"):
        report.error("approval.deck_id", "does not match outline")
    if data.get("outline_version") != outline.get("outline_version"):
        report.error("approval.outline_version", "does not match outline")
    if data.get("source_snapshot") != outline.get("source_snapshot"):
        report.error("approval.source_snapshot", "does not match outline")
    if data.get("approval_mode") not in {"explicit", "preauthorized"}:
        report.error("approval.approval_mode", "must be explicit or preauthorized")
    for field in ("approved_by", "approved_at"):
        if not nonempty_string(data.get(field)):
            report.error(f"approval.{field}", "must be a nonempty string")
    if not isinstance(data.get("exceptions"), list):
        report.error("approval.exceptions", "must be a list")
    for position, slide in enumerate(outline.get("slides", [])):
        if isinstance(slide, dict) and slide.get("status") != "approved":
            report.error(f"slides[{position}].status", "must be approved when approval.json is supplied")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outline", type=Path)
    parser.add_argument("--claims", type=Path, help="analysis/evidence-register.json")
    parser.add_argument("--approval", type=Path, help="approval.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = Report()
    try:
        outline = load_json(args.outline)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [f"outline: {exc}"], "warnings": []}, ensure_ascii=False, indent=2))
        return 1

    claim_index: dict[str, dict] | None = None
    if args.claims:
        try:
            claim_index = validate_claims(load_json(args.claims), report)
        except (OSError, json.JSONDecodeError) as exc:
            report.error("claims", str(exc))
            claim_index = {}
    validate_outline(outline, claim_index, report)

    if args.approval:
        try:
            validate_approval(load_json(args.approval), outline, report)
        except (OSError, json.JSONDecodeError) as exc:
            report.error("approval", str(exc))

    result = {
        "valid": not report.errors,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "errors": report.errors,
        "warnings": report.warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
