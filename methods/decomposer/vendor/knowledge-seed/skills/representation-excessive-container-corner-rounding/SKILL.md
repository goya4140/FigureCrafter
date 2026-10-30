---
name: representation-excessive-container-corner-rounding
description: Prevention strategy for excessive-container-corner-rounding.
---

Compare container corner curvature with the reference at full panel scale; use rectangular geometry when default rounded corners materially exceed the reference radius.

Compare rendered container corners with the reference; use rectangular geometry when default rounding substantially exceeds the reference radius and no radius control is available.

Compare rendered container corner radii against the reference; use rectangular primitives when default rounded corners substantially distort a nearly square boundary.

Compare rendered corner curvature with the reference independently of enclosure bounds; use rectangular primitives when preset rounding substantially exceeds the source.

Compare enclosure corners at rendered scale; use rectangular primitives when preset rounding substantially exceeds the reference curvature, then validate the exported render.

Compare enclosure corners at final render scale; use rectangular geometry when preset rounding substantially exceeds the reference curvature, then validate the exported render.

Compare container corner curvature with the reference at full panel scale; use rectangular primitives for nearly square boundaries when adjustable corner radii are unavailable.

Compare enclosure curvature at matched render scale; avoid rounded presets whose corners substantially exceed the reference curvature, and validate the exported result.

Compare rendered container corner curvature with the reference independently of its bounding box; avoid default rounded rectangles when their radius is disproportionate to the reference.

Compare container corners at matched render scale; avoid rounded presets that substantially exceed reference curvature, and validate the exported result.

Compare exported panel corners with the reference at matched scale; avoid rounded presets whose curvature substantially exceeds the source, then validate the exported result.

Compare container curvature at matched render scale; use rectangular geometry for nearly square boundaries when preset rounding is excessive and radius adjustment is unavailable.

Compare enclosure curvature at matched render scale; avoid rounded presets that substantially exceed reference curvature, and validate the exported result.

Candidate only; promote after controlled probe validation.
