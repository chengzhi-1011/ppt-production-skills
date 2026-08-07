---
name: ppt-production-pipeline
license: Apache-2.0
description: Orchestrate a complete presentation workflow from mixed source materials and a supplied PPT template through evidence-bounded analysis, an approved page-by-page outline, ChatGPT web slide-image generation, object-level editable PowerPoint reconstruction, and real Microsoft PowerPoint QA. Use when the user asks to create a full deck, reporting presentation, proposal deck, defense deck, or a reusable end-to-end PPT production workflow rather than converting an already finished slide image alone.
---

# PPT Production Pipeline

Build a deck through explicit stage gates. Treat generated slide images as an intermediate design specification, never as the final PowerPoint.

## Required companions

- Use `$ppt-material-to-outline` for material analysis, evidence tracking, and the approved slide plan.
- Use `$chatgpt-template-slide-images` for visible-browser generation from the approved plan and template.
- Use `$image-to-editable-ppt` for object-level reconstruction of the accepted slide images.
- Use `pptflow` for run state, hashes, prompt packs, approvals, and deterministic checks.

Read [run-contract.md](references/run-contract.md) before starting or resuming a run. Read [quality-gates.md](references/quality-gates.md) before advancing a stage or claiming completion.

## Command skeleton

```powershell
pptflow doctor
pptflow init --title "<deck>" --material "<file>" --template "<template.pptx>" --out "<output-root>" --cloud-policy allowed
pptflow status "<run>"
```

Repeat `--material` for multiple files. Use `pptflow <command> --help` for the exact approval, prompt, web-authorization, image-ingest, check, and advance options installed on the machine.

## Workflow

1. Run `pptflow doctor`. Fix a missing required tool before starting.
2. Initialize a run with the materials, template, and cloud policy, using absolute input paths. Record audience, purpose, page target, duration, language, and ratio in `outline/deck-brief.json` before writing the outline.
3. Invoke `$ppt-material-to-outline`. Write the material inventory, claim ledger, content summary, machine-readable outline, and reviewable outline.
4. Present the page list and unresolved evidence issues to the user. Do not approve the outline without explicit approval, unless the user explicitly preauthorized an uninterrupted end-to-end run.
5. Record approval with `pptflow approve-outline`, render immutable template references, then create the per-page web prompt pack with `pptflow prompts`.
6. Record the exact ChatGPT destination, slide batch, reference files, and retry allowance with `pptflow authorize-web`. Invoke `$chatgpt-template-slide-images`, generate and review one slide at a time, then register each accepted image with `pptflow ingest-image`.
7. Check that every approved slide has exactly one image and that hashes still match the approved outline and template.
8. Invoke `$image-to-editable-ppt` on the accepted images in stable slide-ID order. Give each page worker the corresponding approved slide brief as canonical text; OCR is geometry help, not content authority.
9. Copy the finalized PPTX into `editable/final.pptx`. Wrap the reconstruction engine's result in a run-scoped `editable/validation.json` containing `passed: true`, the current PPTX SHA-256, and the ordered accepted source-image hashes; write the same ordered mapping in `editable/source.json`. Run the editable-stage check.
10. Run `scripts/powerpoint-qa.ps1`, inspect every exported slide against both the accepted image and approved brief, and write `qa/qa-report.json` only after the visual review. Bind that report to the current PPTX, PowerPoint report, every exported render, and every accepted source image by SHA-256.
11. Advance to `complete` only when all gates pass. Deliver the `.pptx`, validation summary, remaining warnings, and exact path.

## Non-negotiable rules

- Keep `confirmed`, `source_stated`, `derived`, `recommended`, `pending_verification`, `data_insufficient`, and `conflicting` content distinct.
- Never invent people, funding, parameters, results, patents, detection rates, or acceptance conclusions.
- Do not upload material when cloud policy is `unknown` or `prohibited`. Apply and record redaction before upload when policy is `redact_required`.
- Never generate slide images before the outline is approved.
- Never silently reuse an image after the template hash, outline hash, slide brief, or source snapshot changes.
- Keep one stable slide ID across outline, prompt, generated image, editable page, QA render, and final slide.
- Reuse exact supplied logos and brand marks in the final deck. The image generator may reserve their positions but must not invent or redraw them.
- Do not deliver a deck made of full-slide screenshots. Complex visuals may remain independent image objects, but text, containers, arrows, and regular diagrams must be rebuilt as editable objects.
- Rebuild charts from authoritative data where practical; do not accept a visually plausible generated chart with changed values or relationships.
- Structural validation does not prove scientific or factual correctness. Compare formulas, numbers, curves, labels, and claims to the approved brief and evidence ledger.
- A successful build is not completion. Require real Microsoft PowerPoint open/export plus independent visual review.

## Resume behavior

Run `pptflow status <run>` and continue from file facts. Do not infer state from chat history. If an approved outline changes, increment `outline_version` and use `pptflow approve-outline --replace`; the CLI archives downstream artifacts under `logs/invalidated/` before returning to the approved gate. Other upstream hash changes must return to the earliest failed gate.
