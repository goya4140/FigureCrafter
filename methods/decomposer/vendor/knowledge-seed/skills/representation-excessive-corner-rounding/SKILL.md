---
name: representation-excessive-corner-rounding
description: Prevention strategy for excessive-corner-rounding.
---

Match enclosure corner geometry to the reference instead of defaulting containers to rounded rectangles.

Match enclosure corner geometry to the reference; use a rectangle when default rounded-rectangle curvature substantially exceeds the visible corner radius.

Match enclosure corner geometry to the reference instead of applying default rounded rectangles to all modules.

Match container corner geometry to the reference; use rectangular primitives when corner rounding is negligible at output scale.

Match enclosure corner geometry to the reference; use a rectangle when default rounded-rectangle curvature substantially exceeds the visible corner radius, then validate the exported render.

Downstreamed: integrated into the current pipeline and supported by measured proof.
