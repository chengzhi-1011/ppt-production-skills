---
name: chatgpt-template-slide-images
license: Apache-2.0
description: Generate PowerPoint slide-design images one page at a time in a dedicated visible agent-browser profile that the user has already logged into ChatGPT, using a pptflow-approved outline and original template references. Use when the PPT production pipeline has reached its approved web-generation stage and needs template-faithful 16:9 page images, bounded retries, deterministic downloads, and provenance logging before editable PPT reconstruction. Not for material analysis, outline approval, login automation, bulk deck generation, or direct PPTX authoring.
---

# ChatGPT Template Slide Images

Generate slide images through the official `https://chatgpt.com/` web interface. Treat each image as an intermediate visual specification. The approved outline remains authoritative for wording, facts, values, formulas, citations, and evidence status.

## Required pipeline context

Use this skill only inside a run owned by `$ppt-production-pipeline` and `pptflow`.

Before acting:

1. Read the sibling [run contract](../ppt-production-pipeline/references/run-contract.md) and [quality gates](../ppt-production-pipeline/references/quality-gates.md).
2. Run `pptflow <command> --help` before using a command whose installed syntax is not already known.
3. Run `pptflow doctor`, then `pptflow status <run>`.
4. Require the run to have an approved outline whose approval hash still matches `outline/deck-outline.json`.
5. Require cloud policy to permit ChatGPT upload. Stop for `unknown` or `prohibited`; when it is `redact_required`, use only the recorded redacted artifacts. The CLI accepts legacy `redact-required` input but stores the canonical underscore form.
6. Render immutable template references with `scripts/render-template-references.ps1 -Run <run>`, classify their archetypes in `generation/style-lock.md`, and verify that every style-lock reference hash matches `generation/template-reference-manifest.json`.
7. Create or refresh the prompt pack with `pptflow prompts <run> --stage images`. Do not hand-write a replacement content authority.
8. Run `scripts/browser-doctor.ps1`. Fix every blocking result before opening ChatGPT.

Read [prompt-contract.md](references/prompt-contract.md), [browser-state-machine.md](references/browser-state-machine.md), and [style-lock-schema.md](references/style-lock-schema.md) before the first page.

## Browser boundary

- Require `PPTFLOW_CHATGPT_PROFILE` to point to a dedicated custom Chrome profile directory created for this workflow.
- Launch `agent-browser` with that profile and `--headed` on every command. Use one run-scoped `--session` value consistently.
- Do not use `--restore`, `--state`, `auth`, `cookies`, `storage`, `--auto-connect`, or the user's normal Chrome/Edge profile.
- Never store, read, export, or log credentials, cookies, local storage, session tokens, OTPs, or account identifiers.
- Never automate login, CAPTCHA, password-manager, account settings, sharing, or public-link creation. If authentication is required, stop and ask the user to finish it in the visible dedicated browser, then resume from `pptflow status`.
- Navigate only to HTTPS URLs whose host is exactly `chatgpt.com`. After any action that may navigate, read the current URL and stop if the host differs.
- Use fresh accessibility snapshots and semantic refs. Refs are invalid after navigation, upload, modal changes, generation updates, or other DOM changes.

The dedicated profile may retain the user's login because the user manages that profile. This skill must not create a second credential store or copy authentication state elsewhere.

## External-transmission gate

Outline approval and cloud-policy approval do not by themselves authorize the immediate browser send action.

Before the first upload or prompt submission, establish one concrete action-time gate containing:

- destination: the logged-in account at `https://chatgpt.com/`;
- exact sanitized filenames to upload;
- number of initial slide prompts;
- retry allowance, normally one corrective prompt per slide;
- the fact that the canonical slide text and selected reference images will be transmitted.

The user's current request satisfies this gate when it explicitly authorizes ChatGPT web for the identified task-local files and retry scope; record that scope and proceed without asking again. Otherwise request explicit confirmation. Keep the approved batch narrow. Added files, newly sensitive content, a new destination, or a retry beyond the approved bound requires another gate. Do not infer approval from silence.

Record the action-time gate before opening the browser:

```powershell
pptflow authorize-web <run> --slide S001 --file <run>\generation\template-references\STYLE_REF_001.png --retry-limit 1 --authorized-by USER
```

Repeat `--slide` and `--file` for the exact approved batch. For `redact_required`, include every exact prompt file and transmitted reference in the separately approved redacted set.

## Style anchor

Use `generation/style-lock.md` conforming to [style-lock-schema.md](references/style-lock-schema.md). Bind it to the immutable template snapshot hash.

- Render the original template to reference images outside ChatGPT.
- For every page, attach one to three original template references selected by layout archetype.
- Label template images as style references only; their text and subject matter must not leak into the new slide.
- Never promote a generated page, rejected candidate, or screenshot to the canonical style anchor.
- A template or style-lock hash change invalidates the corresponding prompts and accepted images. Return control to `pptflow`; do not silently reuse them.

## One-slide workflow

Process slides in stable approved order and complete one before sending the next.

1. Read the slide's approved brief and its generated prompt pack entry.
2. Confirm the prompt uses exact canonical text from the approved outline. OCR or ChatGPT rewriting is never content authority.
3. Select the original template references named by the style lock. Add only slide-local content images explicitly approved by the brief.
4. Stage uploads under sanitized filenames that reveal no local path or unrelated project information.
5. Open the run's dedicated ChatGPT conversation, or create one dedicated conversation for the run after the transmission gate.
6. Upload only the staged references for the current page.
7. Send one request for one complete 16:9 slide image, following [prompt-contract.md](references/prompt-contract.md).
8. Wait for the matching response to finish. Do not send another page while generation is running.
9. Download the generated image directly to a unique candidate path under `generation/candidates/<slide_id>/`. Never overwrite another attempt.
10. Verify file signature, dimensions, 16:9 ratio, title, major labels, content coverage, template grammar, and absence of invented claims, values, logos, or watermarks.
11. Accept the candidate or create the one allowed corrective prompt with `pptflow prompts <run> --stage images --slide <slide_id> --attempt 2 --correction <observed-defect>` (repeat `--correction` as needed), then send that exact UTF-8 prompt with the original template references. For `redact_required`, refresh the redaction receipt and `pptflow authorize-web` so the exact corrective-prompt hash is approved before sending it.
12. Default to at most two total attempts per slide: one initial attempt and one correction. A third attempt needs explicit user approval and a recorded reason.
13. Register only the accepted file using `pptflow ingest-image`. Consult `pptflow ingest-image --help` for installed syntax; the web form is `pptflow ingest-image <run> --slide <slide_id> --file <image> --source chatgpt-web --attempt <n> --conversation-url <https-chatgpt-url>`.
14. Verify through `pptflow status <run>` and the canonical `generation/generation-log.json` that template, outline, prompt, and image hashes, dimensions, conversation URL when available, retry count, decision, and unresolved issues were recorded.

Do not hand-edit `run.json` or `generation/generation-log.json`, and do not create a parallel run-state ledger.

## Download rules

- Prefer `agent-browser download <fresh-download-ref> <absolute-candidate-path>` so the file lands at a deterministic task-local path.
- Re-snapshot immediately before using the download ref.
- Reject partial, zero-byte, non-image, wrong-slide, or ambiguous downloads.
- Preserve rejected candidates until the run completes; only the accepted candidate enters `generation/images/` through `pptflow ingest-image`.
- Do not scrape authenticated blob URLs, inspect cookies, save HAR files, or call undocumented ChatGPT endpoints.
- Do not substitute a browser screenshot for the original download. If the download control cannot be recovered after one fresh snapshot and one reopened image view, pause for manual download.

## Failure and resume

Always resume with `pptflow status <run>` and file facts.

- **Login or CAPTCHA:** stop for user handoff; do not type credentials or solve it.
- **Unexpected host:** stop immediately without interacting.
- **Rate limit or refusal:** record the concrete issue through the supported `pptflow` path and ask for direction; do not switch accounts or evade safeguards.
- **UI change:** take a fresh snapshot, rediscover semantic controls, and retry once; never reuse stale refs or blind coordinates.
- **Unknown send state:** inspect the last visible user request and following response. Never resend merely because a local receipt is absent.
- **Generation timeout:** poll with fresh state for a bounded interval and keep the user updated. Do not start another page concurrently.
- **Style drift:** start a continuation conversation only after reapplying the full style lock and original template references.
- **Changed outline/template/prompt hash:** stop and return to the earliest failed `pptflow` gate.

## Completion gate

This stage is complete only when every approved slide has exactly one accepted 16:9 image registered by `pptflow`, hashes match the approved outline and template, and no send/download ambiguity remains. Hand control back to `$ppt-production-pipeline`; do not call the deck complete and do not author the PPTX in this skill.
