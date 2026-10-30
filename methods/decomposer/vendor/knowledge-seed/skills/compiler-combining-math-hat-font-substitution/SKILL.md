---
name: compiler-combining-math-hat-font-substitution
description: Prevention strategy for combining-math-hat-font-substitution.
---

Do not treat a font-family substitution or retained combining mark in XML as proof of visible mathematical accents. Render a tight crop and check each hat. If the renderer loses a combining hat, test native caret geometry with the underlying glyph run retaining the full recognized source identity; keep letters as editable text and avoid formula screenshots.

Candidate only; promote after controlled probe validation.
