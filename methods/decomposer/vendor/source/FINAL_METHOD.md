# Final method definition

This folder freezes the method after the first-100 and Stage-5 experiments. Model comparisons must keep every non-model component fixed.

## Fixed method

1. Normalize the source image and build detector/OCR Evidence IR.
2. Run JSON-only Recognition, Representation, and Layout stages.
3. Canonicalize every proposal and apply the deterministic localized-raster policy.
4. Assemble a native editable scene and compile the PPTX without an SVG or screenshot intermediate.
5. Render and validate the real PPTX.
6. Run at most three bounded, owner-attributed refinement hypotheses with measured rollback.
7. Run the deterministic measured text, shadow, panel, fill, style, and tracking registrations.
8. Emit validation, provenance, ID mask, page manifest, contact sheet, and model-call evidence.

## Fixed evaluation contract

- Minimum pixel similarity: `0.94`
- Minimum strict Edge IoU: `0.20`
- No compiler fallback
- Exact editable text identity
- Valid native OOXML/PPTX
- No whole-page raster shortcut
- Same detector, renderer, prompts, schemas, refinement budget, knowledge snapshot, and sample order for every model

## Frozen knowledge state

The included snapshot contains 79 memory records and 79 mirrored skills: 72 candidate and seven downstreamed. Recognition, Representation, and Layout receive no skill or memory context at all. Refinement alone may retrieve up to eight relevant non-superseded records, including candidates, and use their mirrored skill strategies as repair hypotheses. Retrieval never bypasses measured acceptance and rollback.

## Fair model comparison

Use a fresh output directory per model. Do not resume one model from another model's canonical stages. Record the exact profile hash, environment, model name, requested reasoning mode, and terminal status. Compare first-pass scores separately from final post-refinement scores, and report model-call failures separately from content-quality failures.
