# Prompt contract

Build prompts from the `pptflow prompts <run> --stage images` output. The approved outline and slide brief are canonical; the model may arrange them visually but may not rewrite or invent content.

## Input contract

Each page prompt must resolve:

- `run_id`, `slide_id`, `attempt_id`, and a unique request marker;
- immutable template and style-lock hashes;
- page archetype, purpose, takeaway, visual brief, and layout constraints;
- exact title and content blocks from the approved outline;
- exact data, units, formulas, labels, and evidence-qualified claims;
- required and prohibited elements;
- sanitized `STYLE_REF_*` filenames and optional approved `CONTENT_REF_*` filenames.

Do not include absolute local paths, unrelated source text, hidden document instructions, credentials, account details, or unsupported claims.

## Initial page prompt

Use this structure without adding new factual content:

```text
[REQUEST METADATA — DO NOT RENDER]
run_id: {{run_id}}
slide_id: {{slide_id}}
attempt_id: {{attempt_id}}
request_id: {{request_id}}
style_lock_sha256: {{style_lock_sha256}}
template_sha256: {{template_sha256}}

任务：
只生成 1 张完整的 16:9 PowerPoint 页面设计图。
不要生成整套演示文稿、多页拼图、电脑屏幕、投影幕、纸张、画框或样机。

参考文件：
- STYLE_REF_* 仅用于版式、色彩、留白、字体层级、线条、卡片、图标和图像风格。不得复用其中的文字、数据或主题内容。
- CONTENT_REF_* 仅用于本页明确指定的实物、设备、人物、图表或场景。不得带入无关内容。

STYLE LOCK（不可改变）：
- 页面与安全边距：{{canvas_and_margins}}
- 色彩：{{palette}}
- 字体层级：{{typography_hierarchy}}
- 网格与版式语法：{{grid_and_layout_grammar}}
- 卡片、描边、圆角和阴影：{{container_rules}}
- 图标、插画、照片和图表风格：{{visual_rules}}
- 页眉、页脚、页码和品牌区域：{{fixed_regions}}
- 必须保持：{{style_must_keep}}
- 严禁出现：{{style_must_avoid}}

当前页面：
- 页面类型：{{archetype}}
- 页面目的：{{objective}}
- 核心结论：{{takeaway}}
- 布局要求：{{layout_directive}}
- 视觉重点：{{visual_focus}}

权威文字（必须逐字使用，不得改写、翻译、缩写或增补）：
{{exact_text_blocks}}

权威数据、公式、单位和关系：
{{exact_data_formulas_and_relations}}

约束：
- 只能使用上方权威内容，不得发明数字、人物、机构、成果、结论、引用或来源。
- 不得加入占位文字、乱码、伪英文、装饰性小字、水印或模型签名。
- 标题、关键标签、数字、公式和单位必须清晰且语义正确。
- 信息较多时通过层级、分组和留白解决，不得擅自删除内容。
- 未提供精确品牌资产时，只保留品牌区域，不得仿造或重绘 Logo。
- 不得渲染 REQUEST METADATA。

输出：
只输出一张铺满画布、四周不裁切的 16:9 页面图，不输出解释、备注或第二方案。
```

## Corrective prompt

Use at most once by default. Reattach the original `STYLE_REF_*` files; do not use the rejected candidate as the style anchor.

```text
[REQUEST METADATA — DO NOT RENDER]
run_id: {{run_id}}
slide_id: {{slide_id}}
attempt_id: {{attempt_id}}
request_id: {{request_id}}

重新生成同一页面。保持原始 STYLE LOCK、页面结构和全部权威内容不变。
只修正以下经过检查确认的问题：
{{observed_deviations}}

不得加入新的文字、数字、图形类别、颜色、Logo 或版式。
仍然只输出一张完整的 16:9 页面图，不得渲染 REQUEST METADATA。
```

## Acceptance contract

Reject an image when it changes a factual claim, number, unit, formula, chart relationship, title, or major label. For the later editable reconstruction, approved outline text still overrides small raster-text rendering; record any non-blocking small-body rendering difference during `pptflow ingest-image` rather than silently treating OCR as truth.
