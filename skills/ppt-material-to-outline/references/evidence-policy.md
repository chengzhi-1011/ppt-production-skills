# Evidence policy

Assign one status to every atomic claim and repeat that status on each content block that uses it.

| Status | Use when | Allowed presentation language |
|---|---|---|
| `confirmed` | Primary records or multiple consistent sources directly support the claim. | Direct factual wording. |
| `source_stated` | One supplied source states the claim, but it has not been independently verified. | Attribute to the source when the distinction matters. |
| `derived` | The claim is calculated or synthesized from cited inputs. | State the method or basis; do not imply direct measurement. |
| `recommended` | The content is a proposal, design choice, target, or future action. | Use “建议”“拟”“目标” or equivalent wording. |
| `pending_verification` | A user or source must still confirm the claim. | Use a visible placeholder; never present it as settled. |
| `data_insufficient` | Available material cannot support the requested conclusion. | State the limitation or omit the claim. |
| `conflicting` | Supplied sources disagree. | Show the conflict or omit the claim pending resolution. |

## Claim requirements

Store every claim in `analysis/evidence-register.json` with:

- unique `claim_id`
- atomic `text`
- `status`
- exact `source_refs`
- `method` for `derived` claims
- optional `notes` and `allowed_wording`

Require source references for `confirmed`, `source_stated`, `derived`, and `conflicting` claims. A file name alone is not an exact locator. Use forms such as `report.pdf#page=12`, `plan.docx#heading=3.2;paragraph=4`, `data.xlsx#sheet=测试;cell=B7`, or `photo.png#bbox=120,80,640,220`.

## Boundary rules

- A statement in a supplied document is `source_stated`, not automatically `confirmed`.
- A calculation is `derived`, even when every input is confirmed.
- Do not choose between conflicting versions without a documented user decision.
- Do not convert a recommendation, target, simulation, or prototype result into completed-work wording.
- Do not infer missing names, dates, quantities, performance, funding, acceptance, or business value.
- Keep extraction uncertainty in notes and mark the claim `pending_verification` when it can change meaning.
- An approved slide may contain `pending_verification`, `data_insufficient`, or `conflicting` content only when the block includes a nonempty `visible_label` that makes the limitation explicit.

## Cloud boundary

The outline stage remains local. A downstream web workflow may proceed only when `cloud_policy` is `allowed`. For `redact_required`, create and approve a sanitized handoff first. Treat `unknown` as blocked, not as consent.
