---
name: representation-math-markup-rendered-as-plain-text
description: Prevention strategy for math-markup-rendered-as-plain-text.
---

Observed failure: a single formula run patch can duplicate a separately owned superscript and alter neighboring script ink. Inspect every text fragment in the formula owner, preserve its exact source partition, and verify the exported render and neighboring ink; retain rollback when visual acceptance fails.

Observed narrow success: source-preserving native runs repaired loss_stage2; do not infer that other formulas or combining hats are fixed. Inspect every formula and its exported baseline separately.

Candidate only; promote after controlled probe validation.
