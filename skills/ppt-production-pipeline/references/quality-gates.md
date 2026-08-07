# Quality gates

Use these gates in order. A later artifact never waives an earlier failure.

| Gate | Required evidence | Blocking failures |
|---|---|---|
| G0 Inputs | Immutable material and template snapshots with SHA-256; audience, purpose, duration, page target, language, ratio, cloud policy | Missing template, mixed-project sources, unknown upload policy before web use |
| G1 Analysis | Material inventory, source locators, claim ledger, conflicts and open questions | Unsupported factual claims, unresolved source conflicts presented as facts |
| G2 Outline | Stable slide IDs, one takeaway per page, exact text, visual brief, source links, text budget | Chapter names without page messages, overloaded pages, missing evidence links |
| G3 Approval | Approval record bound to the outline hash and source snapshot | Implicit approval, stale approval, changed outline after approval |
| G4 Images | Current template-reference manifest and style lock; exact ChatGPT transmission approval; one accepted 16:9 image per approved slide; template, outline, prompt and image hashes recorded | Missing/duplicate page, wrong aspect ratio, stale or unapproved web scope, semantic formula/chart error, style drift |
| G5 Editable pages | Page validation is hash-bound to the PPTX and ordered source images; canonical text matches approved brief; multiple substantial editable objects | Near-full-slide screenshot with token or invisible overlays, missing text, stale validation, or missing provenance |
| G6 Deck | Finalization validation passes; slide order and count match the outline | Broken OOXML, wrong page order, missing media or notes |
| G7 PowerPoint QA | Microsoft PowerPoint opens the deck and exports every slide; independent visual review is hash-bound to the current deck, export report, renders, and accepted images | Overflow, occlusion, wrong formula/number/curve, stale review, unreviewed export |

## Visual review checklist

- Compare title, body text, numbers, units, formulas, chart semantics, logos, and page number.
- Check alignment, margins, whitespace, line breaks, color, typography hierarchy, and template grammar.
- Confirm each image object is independently selectable and each ordinary text/shape remains editable.
- Record warnings explicitly. A warning cannot replace a missing required stage.

## Completion statement

Report separately:

- deterministic validation result;
- actual PowerPoint open/export result;
- independent visual-review result;
- editable object counts where available;
- remaining evidence or rendering warnings.
