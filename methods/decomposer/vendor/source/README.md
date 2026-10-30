> Workspace merged on 2026-10-15. Run commands from this `source/` directory. Historical experiments and the evolved knowledge tree are siblings; see [branch guide](../README.md).

# Figure2PPTX final method framework v1

> Next-stage copy: run from this `method/` directory with `configs/mac-smoke.json`. See the parent `README.md` for the verified command and output. Historical commands and paths below document the original frozen framework.

This is the frozen, model-independent method extracted after the first-100 and Stage-5 experiments. It is the canonical folder for future GPT-Terra, GPT-Astra, DeepSeek, and other vision-model comparisons.

## What is fixed

- Three JSON-only model stages: Recognition, Representation, and Layout.
- Deterministic Evidence IR, asset policy, canonicalization, scene assembly, and native PPTX compiler.
- Real LibreOffice rendering and structural/visual/editability validation.
- Maximum three owner-attributed refinement rounds with measured acceptance and rollback.
- Strict knowledge boundary: Recognition, Representation, and Layout receive no skill/memory context; only Refinement may retrieve bounded candidate or promoted knowledge.
- Deterministic measured registration stages for text, shadow, fill, panel, style, and tracking.
- Frozen prompts, schemas, quality thresholds, and knowledge snapshot.

Read [FINAL_METHOD.md](FINAL_METHOD.md) for the method and evaluation contract. Read [MODEL_ADAPTER_PROTOCOL.md](MODEL_ADAPTER_PROTOCOL.md) before adding a provider.
Use [BENCHMARKING.md](BENCHMARKING.md) for the frozen cross-model test procedure.

## One-sample run

GPT-Terra through Codex CLI:

```bash
cd Figure2PPTX_FINAL_METHOD
python3 run.py \
  --input /absolute/path/to/figure.png \
  --config configs/base.json \
  --model-profile configs/models/gpt-terra.json \
  --out runs/gpt-terra/sample-001
```

DeepSeek V4.1 Flash through its OpenAI-compatible vision endpoint:

```bash
cd Figure2PPTX_FINAL_METHOD
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_MODEL="deepseek-flash"
python3 run.py \
  --input /absolute/path/to/figure.png \
  --config configs/base.json \
  --model-profile configs/models/deepseek-v4.1-flash.json \
  --out runs/deepseek/sample-001
```

The DeepSeek profile uses `json_object`, embeds the complete stage schema in the
system instruction, keeps the original image detail, and retries the provider's
documented empty-JSON response at most once. The retry is deliberately limited:
HTTP errors, non-empty invalid JSON, schema failures, and timeouts still fail
closed. `deepseek-v4.1-flash-max.json` is a diagnostic profile, not the default.

Never place the API key in a profile, command-line argument, run directory, or
report. Inject it only through the `DEEPSEEK_API_KEY` process environment.

Provider endpoints vary. Adjust the reference adapter response mode or replace it with another `external-json` adapter when the endpoint does not support the standard vision/structured-output payload.

## Environment setup

Copy `configs/base.json` to `config.local.json` and set the local detector, Node.js, LibreOffice, and Poppler paths. Do not commit credentials or machine-specific configuration.

On the project server, run from this framework directory with `--config config.server.json`. That configuration uses the server PP-OCRv5 det+rec runtime and a package-relative `../knowledge/` path, so the refinement library always comes from the uploaded framework snapshot rather than another model checkpoint.

## Tests

```bash
python3 -m pytest -q
```

The framework contains no historical run outputs and no machine-local configuration. The sibling `../knowledge/` tree is the final Stage-5 snapshot retained for audit and controlled future promotion.
