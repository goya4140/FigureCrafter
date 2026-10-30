# Figure2PPTX first-100 evolution protocol

This protocol accelerates framework-level learning without weakening any existing deliverability gate or iterating indefinitely on one sample.

## Formal stages

| Stage | Samples | Primary focus | Frozen output |
| --- | ---: | --- | --- |
| 1 | 1–25 | Problem map and observability | checkpoint 1 |
| 2 | 26–50 | Mechanism convergence and generalization | checkpoint 2 |
| 3 | 51–75 | Long-tail/composed failures and memory reuse | checkpoint 3 |
| 4 | 76–100 | Freeze, cumulative regression and final acceptance | checkpoint 4 |

Every five processed samples produce `observations/after-NNN-samples.json`. These reports are explicitly read-only: they collect metrics and cluster evidence but cannot mutate framework code or promote knowledge.

## Collection continuity and checkpoint blockers

A sample-level quality failure does not stop collection. Validation failures, independent-audit failures, rejected refinement, missing refinement lift, and repeated qualified mechanisms are recorded as checkpoint blockers and the runner continues to the next sample. These samples remain failed or quarantined and cannot support knowledge promotion.

A stage stops before its 25-sample boundary only when an execution-integrity event makes safe continuation impossible:

- compiler or runtime execution fails without an auditable terminal artifact;
- an input is missing or its frozen manifest hash changes;
- training identity, configuration, filesystem, lock, or artifact integrity is invalid;
- the interrupted sample cannot be resumed from a validated canonical stage.

The qualified clustering key remains `(owner_stage, mechanism, affected_label_ids, affected_primitive_ids, evaluation_region)`. Quality events are stored in `checkpoint_blockers`; execution interruptions are stored as `stop_event`. Resume skips successful and quarantined samples, retries the interrupted sample only, and preserves the previous attempt in `attempt_history`.

## Upgrade and regression gate

At a stage boundary, errors are clustered and one principal framework upgrade is performed. A new version may enter the next stage only after it reprocesses and passes the accumulated historical samples. Audit-only checks do not substitute for reprocessing when the compiler, canonical IR, prompts, or refinement behavior changed.

`regress_checkpoints.py` is the executable cumulative-regression gate. It verifies checkpoint eligibility, freezes checkpoint/code/config identities, reprocesses all prior sample indices, runs the independent audit for each result, and stops on the first failure.

Knowledge starts as candidate evidence. Promotion requires measured cross-sample support with applicability conditions, counterexamples, stage and evidence IDs. `superseded` knowledge remains auditable but is excluded from retrieval.

## Immutable quality gates

All stages retain the 14 top-level checks and the independent audit: no SVG intermediate, no single-slide screenshot shortcut, exact native editable text, complete provenance, styled-run text/baseline export, arrow endpoints, CJK font environment, source-grounded localized rasters, and owner-bound refinement localization.

## Efficiency metrics

Each observation report tracks mean/median seconds per sample, versions per sample, first-pass success rate, refinement attempts/acceptances and net deltas, recurrence rate, memory injection rate, artifact-backed memory reuse rate, first-pass reuse rate, upgrade coverage and cumulative regression pass rate. Injection alone is not reuse. Each 25-sample `checkpoint.json` hashes observation reports, independent audits, deliverables, frozen configuration and the complete knowledge snapshot.

## Reset-credit constraint

The only authorized reset credit is `RateLimitResetCredit_e9d0c2abd1a481919f249553cac36529`, the Full reset expiring on 2026-09-21. It may be consumed only after a fresh usage read proves remaining Codex capacity is below 3% and the credit is still available and uniquely identified. Any per-use confirmation requirement or ambiguity stops execution for user confirmation. The other credits are not authorized.
