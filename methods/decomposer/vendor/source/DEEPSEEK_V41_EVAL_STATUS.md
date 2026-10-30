# DeepSeek V4.1 Flash evaluation status

Date: 2026-09-23  
Method: `1.1.0-refinement-memory`  
Provider model: `deepseek-flash`

This is an interim gate report, not a completed 100-sample benchmark.

## Frozen invariants

- Recognition, Representation, and Layout receive no skill or memory entries.
- Skill and memory retrieval occurs only during refinement.
- Prompts, stage schemas, renderer, validation thresholds, source images, sample
  order, and initial knowledge snapshot are unchanged from the Terra comparison.
- Provider credentials are process-environment-only and are absent from run
  artifacts.

## Capability probes

- `/models` exposed `deepseek-flash` and `deepseek-v4-pro`.
- A real multimodal recognition request returned valid JSON with 33 objects.
- The adapter uses Chat Completions `image_url` and `json_object`.
- Local adapter/model/pipeline regression suite: 49 tests passed.

## Two-sample smoke (`reasoning_effort=high`)

| Sample | DeepSeek pixel | DeepSeek edge | Terra pixel | Terra edge | DeepSeek production pass |
| --- | ---: | ---: | ---: | ---: | --- |
| 001 | 0.944335 | 0.165380 | 0.953643 | 0.268363 | no |
| 002 | 0.939513 | 0.211062 | 0.954658 | 0.295382 | no |

All six DeepSeek first-pass stage calls completed with no fallback. First-pass
`applicable_memories` was empty in every stage. Both samples reached refinement;
neither produced an accepted patch. The run stopped at its quality checkpoint
with 0/2 production passes.

## Max-reasoning diagnostic on sample 001

Two independent max-reasoning executions produced:

| Run | Pixel | Edge | Refinement result | Total recorded tokens |
| --- | ---: | ---: | --- | ---: |
| r2 | 0.948928 | 0.180211 | provider empty content; diagnosis failed | 148,443 before failed response |
| r3 | 0.950550 | 0.180199 | 3 proposed patches, all rejected | 644,163 |

The bounded empty-content retry recovered r3. Refinement retrieved knowledge in
all three rounds, referenced 23 memory entries in total, and produced two
knowledge-guided patches sourced from
`representation-connector-endpoint-misattribution`. Real rendering rejected all
three patches; accepted patch count and repair rate remain zero. Round 3 required
two provider attempts and dominated latency and token use.

## Gate decision

The planned 10-sample A/B and 100-sample batch are not authorized by the frozen
quality gate yet:

- the high-reasoning smoke achieved 0/2 production passes;
- max reasoning remained below the edge-IoU threshold (`0.1802 < 0.20`);
- max reasoning substantially increased token use and refinement latency;
- no accepted refinement repair has been observed.

Scaling this exact configuration would measure a known under-threshold and costly
setting, rather than validate the intended method. Keep the 10/100 stages stopped
until the gate is explicitly overridden or a provider configuration passes the
same two-sample protocol.
