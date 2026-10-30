# Cross-model benchmarking

Use the same base config, sample manifest, images, prompts, knowledge snapshot, renderer, and validation thresholds for every model. Each model must have a fresh output root.

## Smoke test

Run one representative image per model before starting a batch. Confirm that all three generation calls report `status: complete`, `fallback_used: false`, the configured backend/model name, and that `output/editable.pptx` plus `output/validation.json` exist.

## Batch example

```bash
python3 train_first100.py \
  --manifest /absolute/path/to/manifest.json \
  --images /absolute/path/to/images \
  --config configs/base.json \
  --model-profile configs/models/gpt-terra.json \
  --out benchmark-results/gpt-terra \
  --limit 100 \
  --resume
```

Repeat with another profile and a different output root. Never resume a run with a changed model profile; the frozen-config identity check will reject it.

## Minimum comparison table

Report these separately:

- model execution completion and fallback rate;
- first-pass pixel similarity and strict Edge IoU;
- first-pass production pass rate;
- refinement attempts, accepted patches, and score lift;
- final production pass rate;
- exact editable-text and independent-audit pass rates;
- mean/median model time by Recognition, Representation, Layout, and Refinement;
- token/cost metrics when the provider exposes comparable accounting.

Do not use only the final best-known artifact set for model comparison: it mixes model versions and recovery experiments. A fair comparison requires a fresh full-chain run under one frozen profile per model.
