# Decomposer v0.6.18 / PPTX-native architecture

## Invariants

1. SVG is not a generation, validation, or export intermediate.
2. Models return strict JSON proposals; they never author JavaScript or OOXML.
3. Canonicalization produces a valid editable fallback even for incomplete model content.
4. The assembler alone combines component-local representation with global layout.
5. The deterministic compiler alone writes PPTX.
6. Every final shape traces to `label_id -> primitive_id -> scene element -> OOXML shape`.
7. A refinement round diagnoses and patches one primary issue only.
8. A patch may change only declared labels and fields owned by one source stage.
9. Acceptance requires frozen-region improvement and no material global regression.
10. A successful repair creates evidence, not an automatically verified universal skill.
11. Two bounded post-render text-registration passes may move only native text x/y by at most three source pixels per pass. Each pass requires a fresh PPTX render, strict global pixel gain, nondecreasing edge IoU, unchanged compiler fallbacks, and an independently verifiable baseline-to-final hash chain. This is measured layout repair, not text replacement or rasterization.
12. Non-equation Recognition text has exact source identity, including whitespace and case, through Representation, native shapes, validation, and independent audit. Visual refinement cannot trade away editability or exact text for a score increase.
13. Source-row-measured multiline text registration may split a paragraph into native editable line shapes only with exact text/run identity. Every candidate receives an isolated PPTX compile/render, an independent scene/PPTX hash chain, and rollback unless global pixel similarity improves without edge IoU loss or new compiler fallback.
14. A flat offset shadow proxy may be replaced by a native editable outer shadow only when it is a neutral-gray same-size rectangle behind a same-owner panel, and a separately compiled, rendered candidate improves both global quality metrics without a new fallback. The independent audit rederives eligibility, variants, hashes, and selection.
15. A short native text box may grow to 1.5 font height and receive a bounded source-ink-aligned x/y shift only when measured rendered ink is severely collapsed; every individual candidate must pass a fresh PPTX render and exact source-text/rollback audit.
16. A separate post-short-box registration pass may move native editable text by at most six source pixels per axis when source-ink measurement clears both absolute and relative thresholds. It has its own compiled candidate, independent audit, strict pixel/edge acceptance, and scene/PPTX hash chain; it cannot change text identity or replace a rejected output.
17. Large flat native rectangles with a prior audited outer shadow may receive source-measured fill/extent recovery. Proposals must remain within a bounded displacement and preserve native shape identity. Every candidate is independently compiled/rendered, the best jointly improving variant is selected per owner, and the independent audit recomputes proposals, pixel/edge assessment, selection, rollback, and hashes.
18. Native rectangle fill correction is limited to a source-dominant RGB mode observed where the previous render still shows that rectangle's declared color, with bounded channel drift and measured support. It cannot alter geometry or text. A new compile/render and independent audit must show strict pixel gain without edge loss or new fallback before the joint candidate replaces the prior output.
19. Native styled multiline text may receive a bounded source-color position alignment only when non-neutral run-color ink has enough support and a measurable IoU gain. Source text, run content/style, provenance, and editability remain unchanged. The proposed scene is freshly compiled/rendered, and independent audit rederives the color-mask shift and strict pixel/edge acceptance before updating the hash chain.
20. Long dark unstyled multiline native text may receive 2–5-point character spacing only after isolated PPTX compile/render proves strict pixel gain without edge IoU loss or a new fallback. Source text, owner, and editability remain unchanged; the independent audit recomputes eligibility, candidate scenes, measured selection, rollback, and hashes. The export is OOXML native run spacing, not raster text.
21. After character-spacing calibration, source-measured native text may receive one bounded ±6-pixel alignment pass when its crop-MAE improvement clears both absolute and relative thresholds. The full joint candidate must improve global pixel similarity without edge IoU loss or a new compiler fallback. The independent auditor recomputes proposals, exact bbox-only edits, pixel/edge assessment, acceptance or rollback, and final hashes.
22. From v0.6.18, the post-tracking alignment and a source-darkness-gated native-shadow calibration are evaluated as atomic alternatives against one baseline. Shadow-only intermediate renders are evidence, never accepted output. Each combined candidate retains native shapes and exact editable text and requires strict whole-slide pixel gain, nondecreasing edge IoU, unchanged compiler fallbacks, and an independently audited scene/PPTX hash chain.

## Runtime data flow

```text
source.png
  -> detector candidates (SAM/OCR)
  -> immutable Evidence IR
  -> canonical recognition objects
  -> deterministic raster-asset authorization gate
  -> canonical per-label component packages
  -> canonical layout placements
  -> PPTX-native scene IR
  -> deterministic compiler
  -> editable.pptx + preview.png + provenance.json + id-mask.png
  -> discover -> diagnose -> declarative patch -> compile -> verify -> commit/rollback
  -> page-manifest.json + validation.json + contact-sheet.png
  -> evidence -> mechanism memory -> candidate skill
```

## Ownership

| Owner | Mutable fields |
| --- | --- |
| recognition | object identity, category, source bbox, text content, connector relationship |
| representation | component-local primitives, local geometry, style, primitive text and styled runs, and safe bbox-shape kind (`rect`, `round_rect`, `ellipse`) |
| layout | global bbox, rotation, text scale, z-order |
| compiler | PowerPoint serialization only; never model-patched |
| infrastructure | model transport, detector, renderer and filesystem; never visually patched |

`detected_in` and `owner_stage` are distinct. A bad connector discovered in the rendered PPTX may belong to recognition (wrong relation), representation (wrong local path), layout (wrong global placement), or compiler (serialization error).

## Refinement acceptance

For a fixed focus region, the controller compares reference/before/after pixel similarity and edge IoU. A candidate must improve local pixel similarity or edge IoU and may not reduce global pixel similarity beyond the configured tolerance. Contract, raster-safety, PPTX structure, rendering, and provenance gates must also pass. Rejected candidates remain in their immutable round directory and never replace the last accepted output.

The focus region is itself provenance-bound. A selected difference tile is used only when it overlaps the diagnosed labels; otherwise the controller derives a clipped, padded union from `affected_label_ids` and `affected_primitive_ids`. The decision, assessment, and summary record that region source, preventing a correct patch from being scored against an unrelated high-difference tile.

Primitive-kind repair is deliberately constrained to bbox-native shapes that share the same geometry contract. Cross-family conversions involving text, images, lines, polygons, or paths are rejected rather than guessed. The controlled `excessive-corner-rounding` probe improved local pixel similarity by `0.002728`, local edge IoU by `0.022495`, and global pixel similarity by `0.000303` before being marked downstreamed.

Path arrowhead metadata survives the full model-to-OOXML chain. Refinement may change `arrow_start` or `arrow_end` only on line-like primitives (`line`, `polyline`, and `path`), and the resulting custom-geometry shape retains native DrawingML arrowhead properties.
Provenance records each serialized DrawingML `head_end` and `tail_end`. Validation compares requested scene endpoints against those facts, and refinement must consult them before assigning an arrowhead defect to the compiler.

For East Asian text, the compiler records an explicit CJK font route per primitive. The bundled macOS LibreOffice renderer also receives its packaged Fontconfig file plus an isolated writable cache; validation attributes a missing render environment to the compiler before visual refinement is allowed to guess at Recognition or Layout.

Raster authorization prefers detector-grounded SAM/SAM3 leaves. A detector miss does not permit vector caricatures of photographs: a constrained fallback may crop a Recognition bbox only for a high-confidence `image` leaf with bounded area, empty recognized text, and low measured OCR overlap. The asset manifest records `source_basis` as either `sam_candidate` or `recognition_bbox`; containers, whole-slide regions, low-confidence objects, and OCR-heavy regions remain native-only.
Once a crop is approved, its decision is authoritative over a model-authored vector surrogate. Canonicalization replaces the surrogate with one localized image primitive and records a non-degraded policy override, keeping the surrounding diagram native and editable.

Recognized text has a category-independent canonical editability invariant: every non-equation object with non-empty text must preserve its complete content in editable text primitives. Pure text glyph-outline proposals are deterministically replaced; composite objects keep their non-text primitives and receive a repaired text layer. Explicit numeric and directional clockwise/counterclockwise rotation hints are converted into native 90/270-degree layout rotation around the source bbox center rather than simulated with path glyphs.

For mixed prose and mathematical identifiers, Representation may attach editable styled runs to the same text primitive. Concatenated run `source_text` must equal the Recognition string exactly, while the visible run text may remove notation-only separators and express script using native subscript/superscript properties. The compiler emits native PowerPoint runs; provenance records exported OOXML text and baseline values, and validation rejects a mismatch. A controlled v0.5.6 repair converted `I_Source`, `I_Bald`, and `I_Target` without rasterization, preserved source identity, exported `2/2` expected styled-text shapes, and improved local pixel similarity by `0.002272`.

The v0.5.7 compiler splits newline-bearing styled runs into explicit `breakLine` payload runs before PptxGenJS serialization. This preserves empty lines while keeping the next differently styled run in the intended final paragraph. Both provenance and the independent batch auditor reconstruct text from DrawingML paragraphs and explicit breaks. A controlled replay exported `MMOs:\n\nfluents +- δ` as exactly three paragraphs, passed all 14 validation checks and the independent audit, and retained `82/82` provenance coverage.

The v0.5.8 compiler extends native custom geometry to SVG elliptical arcs (`A`/`a`). It converts endpoint-form arcs to one or more cubic DrawingML Bezier segments while preserving absolute/relative coordinates, rotation, large-arc/sweep flags, and multiple closed subpaths. The Stage-1 hard-failure replay for sample 7 removed all 13 rectangle fallbacks and improved global pixel similarity from `0.892000` to `0.953646` and edge IoU from `0.184486` to `0.219407`, with `214` editable shapes, `0` pictures, and no screenshot-like output.

v0.5.9 strengthens the evolution evidence plane without weakening generation gates. Observation reports sum accepted refinement lift from persisted `assessment.json` files, retain exact evaluation regions and measured deltas in progress history, report memory injection separately from artifact-backed reuse, and identify first-pass reuse. The initial reuse detector proves native styled-run application from Representation source identity plus script baselines and the exported validation gate. Frozen checkpoints include independent-audit hashes, frozen-config identity, deliverable hashes, and a deterministic hash manifest of the full knowledge snapshot.

Primitive patch targets accept both component-local IDs (`frame`) and provenance-qualified IDs (`heuristics/frame`). A qualified target is resolved only when its owner prefix matches the declared operation label; mismatched owners fail before any mutation. This keeps model diagnostics and executor identity contracts aligned without weakening label-local isolation.

## Knowledge lifecycle

```text
event evidence
  -> stage + mechanism memory
  -> candidate skill
  -> repeated evidence
  -> controlled probe validation
  -> active policy
  -> deterministic code rule when possible
```

Historical v6.1 mechanisms are imported as `candidate_imported`; they do not become active simply because an older pipeline observed them. v0.4 refinement evidence records label IDs, primitive IDs, field, region, hashes, patch outcome, and before/after metrics.

Recognition, Representation, and Layout are deliberately knowledge-blind: their model payloads always contain an empty `applicable_memories` list. No memory or skill, including a downstreamed one, may influence first-pass generation.

Only Refinement performs runtime retrieval. It may inspect bounded, problem-related `candidate_imported`, `candidate`, `verified`, `active`, and `downstreamed` memories together with their mirrored skill strategies and measured evidence summaries. Superseded records are excluded. Every referenced memory/skill ID is checked against the retrieved set, and every proposed repair remains subject to normal measured acceptance and rollback.

Memory and skill lifecycle state is mirrored through one writer. Capturing later accepted or rejected evidence preserves an already-downstreamed strategy and its measured proof instead of silently demoting the skill package back to candidate status.

Frozen training runs keep independent knowledge snapshots, while `merge_into` unions evidence IDs and strategies across versions, recomputes acceptance counts from immutable evidence, and preserves the strongest lifecycle state. A new first-100 run automatically seeds its knowledge snapshot from the configured master library when its output directory is new.

## Batch safety

`train_first100.py` uses the frozen first-100 manifest, verifies every local image hash, writes one run per sample, saves progress after every terminal sample, and preserves failures. Under an identical frozen training identity, interrupted samples resume from the last validated canonical stage (input, observations, recognition, representation, or layout); recomputation begins at the first invalid stage and is recorded in `resume-report.json`. The shared knowledge directory is append-only at evidence level and aggregated by stable mechanism identity.
