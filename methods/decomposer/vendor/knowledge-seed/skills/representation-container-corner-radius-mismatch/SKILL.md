---
name: representation-container-corner-radius-mismatch
description: Prevention strategy for container-corner-radius-mismatch.
---

Compare container corner curvature against the reference at final render size; avoid default rounded rectangles when their radius substantially cuts into the enclosed content.

Check rendered container corners against the reference; use rectangular geometry when preset rounding substantially intrudes into the content area.

Compare enclosure corners at final render scale and use rectangular geometry when preset rounding materially exceeds the reference curvature.

Compare exported container corner curvature with the reference; use rectangular geometry when native rounded rectangles introduce disproportionately large corner radii.

Compare container corners at final render scale; when preset rounding substantially exceeds the reference and radius control is unavailable, use rectangular geometry and validate the exported result.

Check rendered container corner curvature against the reference; use rectangular geometry for nearly square boundaries when native rounding produces oversized corners.

Compare enclosure curvature at final render scale; use rectangular geometry when preset rounding substantially exceeds the reference, then validate the exported render.

Compare exported container corner curvature against the reference; use rectangular geometry when default rounded-rectangle radii substantially intrude into the content area.

Compare enclosure corners at final render scale; when preset rounding materially exceeds the reference and radius control is unavailable, use rectangular geometry and validate the exported result.

Compare container corner curvature against the reference at full-panel scale; avoid default rounded rectangles when their radius materially cuts into the content area.

Compare rendered container corners with the reference; use rectangular geometry when preset rounded corners substantially intrude into the panel.

Compare container corners at final render scale; use rectangular geometry for nearly square boundaries when preset rounding substantially intrudes into the content area, then validate the exported render.

Candidate only; promote after controlled probe validation.
