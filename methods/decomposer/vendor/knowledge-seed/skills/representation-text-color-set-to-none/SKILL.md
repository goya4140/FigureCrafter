---
name: representation-text-color-set-to-none
description: Prevention strategy for text-color-set-to-none.
---

Require an explicit visible foreground color for editable text and verify caption visibility in the exported render before adjusting text metrics.

Preserve explicit visible foreground colors when converting recognized text into editable primitives, and verify text visibility after export.

Preserve explicit visible foreground colors when constructing editable text primitives, then verify caption visibility in the exported render.

Candidate only; promote after controlled probe validation.
