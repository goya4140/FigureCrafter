---
name: representation-container-corner-rounding-excess
description: Prevention strategy for container-corner-rounding-excess.
---

Compare exported container corner curvature with the reference; use rectangular geometry when native rounding intrudes substantially into a nearly square boundary.

Compare rendered container corners with the reference at full panel scale; avoid rounded presets whose radius grows excessively with container dimensions.

Compare exported enclosure curvature with the reference at final scale; use rectangular geometry when preset rounding substantially exceeds a nearly square source boundary, then validate the render.

Candidate only; promote after controlled probe validation.
