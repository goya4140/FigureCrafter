---
name: representation-split-math-source-identity-loss
description: Prevention strategy for split-math-source-identity-loss.
---

For a native math owner, validate the concatenation of all run source_text fragments against exact recognized text. Only when it forms an exact partition, normalize each op text to its own run-source fragment; retain geometry, glyphs and styles. Reject missing, duplicated or conflicting source claims with owner IDs for bounded repair, and never silently discard invalid runs. This structural fix does not prove font fidelity or correct hat rendering.

Downstreamed: integrated into the current pipeline and supported by measured proof.
