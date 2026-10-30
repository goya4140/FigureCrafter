---
name: representation-text-box-font-metric-overflow
description: Prevention strategy for text-box-font-metric-overflow.
---

Size multiline editable text boxes to accommodate the specified font's line metrics before export, preserving the intended text center and checking for automatic shrinkage.

Size single-symbol text boxes to accommodate font line metrics and internal margins, preserving the intended glyph center and checking for clipping after export.

Size native text boxes to accommodate rendered font metrics and verify that every legend caption remains visible after export.

Size text boxes to accommodate the font’s full line metrics before export, preventing automatic shrinkage in short legend labels.

Size editable text boxes to accommodate the font's full line metrics, including ascenders and descenders, before rendering.

Size multiline text primitives for the full font and line-spacing metrics before export, preserving the reference font size and keeping text within its container.

Allow sufficient text-box height for mixed-font mathematical runs and subscript descenders before applying automatic font fitting.

Size multiline scientific captions using full line and subscript extents before applying text fitting.

Size equation text boxes using glyph and line metrics, preserving their centers and checking that complete symbols remain visible after export.

Size mathematical text boxes for the full font ascent, descent, and script offsets before export to avoid automatic shrinking.

Size editable text boxes using the font's full line metrics, preserving the intended text center and checking that autofit does not shrink prominent headings.

Size editable text boxes using font metrics and sufficient vertical clearance to prevent unintended automatic shrinking.

Give editable tick labels sufficient text-box padding for font metrics and verify their rendered size against the reference.

Size text primitive boxes to accommodate the font's full line metrics before export; verify that fitting does not shrink legend headings below neighboring labels.

Provide text boxes with sufficient vertical font-metric clearance before increasing font scale; verify that automatic fitting does not shrink otherwise correctly sized captions.

Size multiline text boxes to accommodate the specified line spacing and font metrics before export, avoiding automatic font shrinkage.

Size multiline text primitives for the full font metrics and explicit line spacing, preventing unintended wrapping and automatic shrinking.

Size editable text boxes for full font line metrics, with sufficient clearance to prevent automatic shrinking; preserve the intended visual center.

Allow sufficient vertical headroom for explicit line spacing and font metrics so multiline text does not shrink during export; verify rendered glyph size against the reference.

Size multiline text primitives for the full font metrics and configured line spacing before export, preventing automatic shrinkage from making condition labels illegible.

Reserve full font and script vertical extents in mathematical annotation boxes, then verify every vertex label in the exported render.

Check text-box capacity against effective font metrics before increasing text scale; verify rendered glyph size and wrapping after adjusting local bounds.

Allow text boxes sufficient height for font metrics, then verify rendered caption ink dimensions before increasing font scale.

Candidate only; promote after controlled probe validation.
