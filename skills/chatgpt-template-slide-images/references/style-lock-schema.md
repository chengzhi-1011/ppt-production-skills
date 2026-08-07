# Style-lock schema

`generation/style-lock.md` is the human-readable, hash-bound style authority for ChatGPT page generation. It describes the supplied template; it must not contain new slide facts or replace the approved outline.

## Required header

```yaml
schema_version: 1
template_sha256: <sha256>
style_lock_id: <stable-id>
slide_ratio: 16:9
reference_count: <integer>
```

List every original reference with:

- sanitized filename;
- source template slide number;
- archetype such as `cover`, `section`, `content`, `process`, `data`, or `closing`;
- SHA-256;
- what visual grammar it demonstrates.

## Required sections

### Canvas and fixed regions

Record slide ratio, safe margins, title zone, content zone, footer, page number, logo/brand reserve, and persistent rules or ornaments.

### Palette

Record exact or measured colors, their roles, permitted gradients, background treatment, contrast behavior, and prohibited colors. Do not infer unsupported corporate colors from generated pages.

### Typography hierarchy

Record font families when evidenced, Chinese/Latin fallback families, title/body/caption hierarchy, weight, alignment, line spacing, and typical line count. The prompt uses hierarchy; the approved outline supplies the text.

### Grid and layout grammar

Record column system, alignment anchors, recurring cards, process lanes, diagram conventions, chart placement, image-to-text ratios, and whitespace rules. Map each layout archetype to one or more original reference images.

### Containers and strokes

Record corner radii, borders, line weights, shadows, fills, separators, arrowheads, bullets, and icon treatment.

### Visual language

Record photography, illustration, scientific diagram, chart, data visualization, texture, and depth rules. Distinguish visual style from factual chart semantics.

### Brand assets

Identify exact user-supplied logos and fixed marks separately. If an exact asset is not supplied for generation, reserve its placement and require downstream editable reconstruction to insert the original; never ask ChatGPT to fabricate it.

### Must keep

List immutable template traits, including aspect ratio, margins, palette roles, hierarchy, fixed regions, and recognized visual motifs.

### Must avoid

List style drift patterns: unrelated color systems, invented logos, extra watermarks, new type styles, decorative text, UI mockups, page frames, excessive 3D effects, or content copied from template references.

## Reference selection rules

- Attach one to three original template references for every page.
- Prefer the closest archetype; use a general content reference when no exact archetype exists.
- Original template renders remain the anchor across all attempts and continuation conversations.
- Generated pages, rejected candidates, screenshots, and third-party deck examples cannot become canonical references.

## Invalidation

Changing the template snapshot or any required style-lock rule changes the style-lock hash. Regenerate affected prompt packs and invalidate accepted images through the `pptflow` workflow. Never edit hashes or generation-log records by hand.
