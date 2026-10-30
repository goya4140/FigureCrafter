# Qwen3-VL-32B-Instruct evaluation target

## Scope

Evaluate `Qwen/Qwen3-VL-32B-Instruct` with the frozen Figure2PPTX final-method
framework. The server already owns the benchmark dataset; this framework
snapshot intentionally contains no evaluation images, annotations, prior model
outputs, or reference PPTX files.

## Frozen method

- Run the same three JSON-only stages: Recognition, Representation, and Layout.
- Keep prompts, schemas, deterministic Evidence IR, asset policy, canonicalizer,
  native PPTX compiler, renderer, validation thresholds, refinement budget, and
  knowledge snapshot unchanged.
- Use the server's existing SAM3 and PP-OCRv5 detector runtime. In particular,
  OCR must use `experiments/v4-runtime/recognition/ocr.py` and the frozen
  `PP-OCRv5_server_det` / `PP-OCRv5_server_rec` checkpoints referenced by
  `config.server.json`.
- Treat the Qwen deployment as the only model-variable change. Do not mix model
  outputs or resumed stage state from GPT, Gemini, or DeepSeek runs.

## Qwen deployment contract

The model is not deployed by this snapshot. After deployment, expose Qwen through
an OpenAI-compatible Chat Completions endpoint and set these process variables:

```bash
export QWEN_BASE_URL='http://127.0.0.1:<port>/v1'
export QWEN_API_KEY='<server-configured-key-or-local-placeholder>'
export QWEN_MODEL='Qwen/Qwen3-VL-32B-Instruct'
```

Use `configs/models/qwen3-vl-32b-instruct.json`. The profile requests strict JSON
Schema output, temperature zero, original image detail, and at most 32,768 output
tokens. It deliberately does not send a provider-specific `reasoning_effort`.

## Evaluation sequence

1. Capability probe: one direct multimodal request and one strict-schema request.
2. Two-sample smoke: require all three generation calls to complete without
   fallback and require the editable PPTX and validation report to exist.
3. Apply the existing frozen quality gate. Do not start the larger batch unless
   the smoke run passes or an override is explicitly recorded.
4. If authorized, run the same sample order and fresh output root used by the
   cross-model protocol in `BENCHMARKING.md`.

## Required reporting

Report first-pass and final post-refinement results separately, including:

- model-call completion, fallback, invalid-JSON, and schema-failure rates;
- pixel similarity, edge IoU, structural/editability validation, and production
  pass rate;
- accepted refinement patches and repair rate;
- stage-level latency and token accounting when exposed by the server;
- exact model revision, quantization, serving engine/version, GPU topology,
  decoding settings, profile hash, framework hash, and detector hashes.

Quantization and serving-engine choices are part of the tested model condition
and must not change inside one reported run.
