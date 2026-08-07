# Outline artifact contract

Use UTF-8 JSON with `schema_version: "1.0"`. Keep field names and evidence-status values stable so downstream skills can consume approved outlines without interpreting prose.

## `analysis/material-inventory.json`

Include `source_roots`, a path-sorted `files` list, `summary`, and `errors`. Each file entry contains `source_id`, absolute `path`, `sha256`, `size_bytes`, `modified_utc`, `extension`, `material_type`, `parser_hint`, `parsing_status`, `project_boundary`, `sensitivity`, and `duplicate_of`.

## `analysis/evidence-register.json`

```json
{
  "schema_version": "1.0",
  "claims": [
    {
      "claim_id": "C001",
      "text": "Atomic factual or proposed statement",
      "status": "source_stated",
      "source_refs": ["report.pdf#page=12"],
      "method": null,
      "notes": "",
      "allowed_wording": ""
    }
  ]
}
```

## `outline/deck-outline.json`

Required top-level fields:

- `schema_version`
- `deck_id`
- positive integer `outline_version`
- nonempty `source_snapshot` entries containing `path` and 64-character `sha256`
- `deck`
- nonempty `slides`
- `validation`

Required `deck` fields are `title`, `purpose`, `audience`, positive `duration_min`, positive `target_slide_count`, `language`, `aspect_ratio`, nullable `template_ref`, `cloud_policy`, and `assumptions`.

Use `cloud_policy` values `allowed`, `redact_required`, `prohibited`, or `unknown`.

Each slide contains:

```json
{
  "slide_id": "S001",
  "order": 1,
  "role": "context",
  "title": "Page title",
  "objective": "What the audience should understand",
  "takeaway": "The single primary message",
  "content_blocks": [
    {
      "kind": "bullet",
      "text": "Slide-ready text",
      "claim_ids": ["C001"],
      "evidence_status": "source_stated"
    }
  ],
  "source_refs": ["report.pdf#page=12"],
  "visual_brief": {
    "visual_type": "process",
    "message": "Relationship the visual must communicate",
    "required_elements": [],
    "prohibited_elements": [],
    "data_chart_required": false
  },
  "layout_constraints": {
    "template_layout_id": null,
    "max_bullets": 4,
    "max_chars": 110
  },
  "speaker_notes": "",
  "dependencies": [],
  "status": "draft"
}
```

Allowed roles are `cover`, `agenda`, `context`, `problem`, `objective`, `method`, `framework`, `evidence`, `result`, `value`, `plan`, `team`, `resources`, `risk`, and `closing`.

Allowed slide states are `draft`, `needs_input`, and `approved`. Use the evidence statuses defined in `evidence-policy.md`. Add `visible_label` to a content block when an approved slide intentionally displays unresolved or conflicting information.

The `validation` object contains list fields `unresolved_claims`, `overloaded_slides`, and `source_conflicts`.

## Approval and handoff

`outline/approval.json` contains `deck_id`, `outline_version`, `approved`, `approval_mode`, `approved_by`, `approved_at`, `source_snapshot`, and `exceptions`. A valid approval must match the canonical outline version and source snapshot.

Generate `generation/image-generation-brief.json` only after approval. Include the approved deck constraints and slide specifications, but exclude raw source excerpts and secrets. Preserve `slide_id`, `outline_version`, claim IDs, visual requirements, and prohibited elements.
