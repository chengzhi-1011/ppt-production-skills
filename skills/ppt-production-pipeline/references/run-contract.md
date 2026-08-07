# Run contract

`pptflow` creates and advances this directory. Use relative artifact paths inside JSON and stable slide IDs such as `S001`.

```text
<run>/
├─ run.json
├─ input/
│  ├─ input-manifest.json
│  ├─ materials/
│  └─ template/
├─ analysis/
│  ├─ material-inventory.json
│  ├─ evidence-register.json
│  ├─ content-summary.md
│  └─ open-questions.md
├─ outline/
│  ├─ deck-outline.json
│  ├─ outline-review.md
│  ├─ approval.json
│  └─ slide-briefs/
├─ generation/
│  ├─ style-lock.md
│  ├─ prompts/
│  ├─ images/
│  └─ generation-log.json
├─ editable/
│  ├─ final.pptx
│  ├─ validation.json
│  └─ source.json
└─ qa/
   ├─ powerpoint-qa.json
   ├─ qa-report.json
   └─ powerpoint-render/
```

Companion skills also write `analysis/source-map.json`, `outline/deck-brief.json`, `generation/image-generation-brief.json`, `generation/template-reference-manifest.json`, original template renders under `generation/template-references/`, `generation/prompt-pack.json`, `generation/web-transmission-approval.json`, per-slide candidates under `generation/candidates/<slide_id>/`, and editable reconstruction prompts under `generation/prompts/editable/`.

## Stage facts

Allowed ordered states are `initialized`, `outline`, `approved`, `images`, `editable`, `qa`, and `complete`. A state transition must run its stage check first. `run.json` is updated atomically and stores input hashes, settings, current state, and event history references.

## Outline minimum

`outline/deck-outline.json` is the content authority. It must contain deck metadata and an ordered `slides` array. Each slide needs:

- stable `slide_id` and integer `order`;
- `role`, `title`, `objective`, and one `takeaway`;
- exact `content_blocks` with claim IDs and evidence status;
- source references;
- a `visual_brief` with required and prohibited elements;
- layout and text-budget constraints;
- speaker notes and slide status.

Approval is stored separately and must bind to the SHA-256 of this file.

Evidence status is one of `confirmed`, `source_stated`, `derived`, `recommended`, `pending_verification`, `data_insufficient`, or `conflicting`. Never silently promote a weaker status. Cloud policy is one of `allowed`, `redact_required`, `prohibited`, or `unknown`.

For `redact_required`, `generation/redaction-approval.json` must contain `approved: true`, a nonempty `approved_by`, and `sanitized_files` entries with task-relative `path` and exact `sha256`. Any content or hash change invalidates the approval.

## Generation minimum

`generation/generation-log.json` records one accepted image per slide with:

- slide ID;
- template, outline, prompt, and image SHA-256;
- image dimensions;
- ChatGPT web conversation URL when available;
- retry count, reviewer decision, and unresolved issues.

Generated images never modify the approved text authority.

## Editable and QA minimum

`editable/validation.json` must contain top-level `passed: true`, `pptx_sha256`, and ordered `source_images` entries with `slide_id` and `sha256`. `editable/source.json` repeats the ordered image provenance used by reconstruction. `qa/powerpoint-qa.json` proves actual PowerPoint open/export. `qa/qa-report.json` records the independent comparison and must contain top-level `passed: true`, `reviewer`, `reviewed_at`, reviewed slide IDs, `pptx_sha256`, `powerpoint_qa_sha256`, per-export `render_sha256`, per-slide `accepted_image_sha256`, findings, and warnings.
