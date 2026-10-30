---
name: compiler-path-local-offset-dropped
description: Prevention strategy for path-local-offset-dropped.
---

Validate exported path origins against scene bounds, applying both component placement and primitive-local translation exactly once; compare paths with neighboring native rectangles in the rendered output.

Candidate only; promote after controlled probe validation.
