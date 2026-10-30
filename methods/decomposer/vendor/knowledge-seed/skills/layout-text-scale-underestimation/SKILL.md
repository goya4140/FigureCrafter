---
name: layout-text-scale-underestimation
description: Prevention strategy for text-scale-underestimation.
---

Compare rendered heading ink dimensions with the reference and calibrate text scale independently of container placement.

Match rendered text size to reference ink bounds, preserving styled runs; use text-scale adjustments when changing local bounds does not affect rendered size.

Calibrate equation text scale against reference ink height independently of enclosure dimensions, preserving mathematical runs and source text.

Compare annotation text extents against nearby fixed-size modules and adjust text scale independently of object placement.

Compare rendered glyph height and text width against the reference independently of text-box bounds, then adjust the label's text scale while preserving alignment.

Compare rendered glyph size against the reference independently of text-box bounds, and calibrate layout text scale while preserving run formatting.

Validate rendered caption glyph size against the reference independently of its bounding box, and adjust layout text scale when placement already matches.

Compare rendered title glyph height and width against the reference independently of textbox bounds, and calibrate text scale while preserving alignment.

Compare rendered heading size with the reference and calibrate each heading's layout font scale while checking for automatic shrinking or wrapping.

Validate multiline text scale against both glyph height and first-to-last baseline span, including alignment with background highlights.

Calibrate multiline text scale against both glyph size and cumulative baseline spacing, checking alignment with fixed highlights across the entire paragraph.

Compare rendered text width and multiline height against the reference independently of card geometry; correct text scale when both are proportionally undersized.

Calibrate paragraph text scale against reference glyph height and baseline spacing, preserving existing line breaks and panel margins.

Compare multiline text height and line widths against the reference within matching panel bounds, and calibrate text scale before accepting the layout.

Compare rendered caption glyph height and text width against the reference, and calibrate text scale while preserving the caption's anchor.

Compare rendered text size against the reference independently of its enclosing shape, and calibrate text scale without resizing the container.

Calibrate text scale against reference glyph height and line width while preserving correctly aligned container geometry.

Compare rendered paragraph glyph size and baseline spacing with the reference before accepting the final text scale.

Compare rendered text height and line extent against the reference independently of enclosure geometry, and adjust per-label text scale when both are undersized.

Compare multiline rendered glyph heights and line widths against the reference independently of card geometry, then calibrate text scale while preserving line breaks.

Compare rendered multiline annotation width and glyph height with the reference independently of placement, then calibrate text scale while preserving line breaks.

Compare multiline text ink height and line widths against the reference independently of card geometry; calibrate text scale while preserving line breaks.

Compare caption glyph heights and line widths against the reference independently of panel geometry, then calibrate per-label text scale while preserving styled runs and checking wrapping.

Compare caption ink width and glyph height against the reference independently of enclosure size; calibrate text scale and verify that the caption remains within its box.

Compare caption ink width and glyph height with the reference independently of placement, then calibrate text scale and verify that neither wrapping nor overlap occurs.

Compare caption ink widths and glyph heights against the reference independently of container geometry; calibrate text scale and verify that captions remain unwrapped and separated.

Compare rendered title ink width and height against the reference, then calibrate text scale while preserving alignment and checking for wrapping.

Compare rendered caption ink width and glyph height against the reference independently of panel geometry; adjust text scale while preserving the anchor and checking for wrapping.

Candidate only; promote after controlled probe validation.
