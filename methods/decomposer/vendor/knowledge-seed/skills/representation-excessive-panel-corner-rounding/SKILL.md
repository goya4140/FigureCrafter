---
name: representation-excessive-panel-corner-rounding
description: Prevention strategy for excessive-panel-corner-rounding.
---

Match panel corner geometry to the reference; avoid default rounded rectangles when their corner radius visibly exceeds the source.

Match panel corner geometry to the reference; avoid default rounded rectangles when their radius visibly removes background area.

Compare rendered panel corner curvature with the reference; use rectangular geometry when default rounding materially exceeds the source radius.

Match enclosure corner geometry to the reference at rendered scale; use rectangular geometry when default rounded-rectangle curvature materially exceeds the visible radius.

Compare enclosure corner curvature at matched render scale and avoid default rounded rectangles when their radius materially exceeds the reference.

Compare nested enclosure corners at matched render scale; use rectangular primitives when preset rounding substantially exceeds the reference curvature, then validate the exported render.

Compare enclosure corners at matched render scale; replace preset rounding with rectangular geometry when the reference curvature is negligible, then validate the exported render.

Candidate only; promote after controlled probe validation.
