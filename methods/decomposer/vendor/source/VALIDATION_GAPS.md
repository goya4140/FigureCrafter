# Remaining validation gaps after the v0.5.9 observability upgrade

This is a forward audit, not a claim that the current frozen training run already satisfies every item.

## Already enforced

- No SVG generation, validation, or export intermediate.
- Deterministic rebuild from canonical JSON scene data.
- PPTX package/XML validation and LibreOffice render validation.
- Full-slide raster rejection and localized raster authorization.
- Label-to-primitive-to-scene-to-OOXML provenance.
- Label-local refinement patches with measured acceptance and rollback.
- Evidence, mechanism memory, candidate skills, and measured downstream proofs.
- Category-independent exact editable text coverage for every non-equation object, including deterministic recovery from glyph-outline proposals without deleting composite geometry.
- Native 90/270-degree text rotation derived from explicit numeric or clockwise/counterclockwise Recognition geometry hints.
- Scene-to-OOXML arrowhead export coverage with endpoint facts exposed to refinement.
- Mixed prose/math identifiers represented as native editable styled text runs with exact Recognition-source identity, exported visible-text checks, and OOXML subscript/superscript baseline checks.
- Multiline styled runs serialized with explicit PowerPoint line breaks; provenance and independent audit preserve paragraph separators and empty lines instead of concatenating only `<a:t>` nodes.

## Newly enforced through v0.5.7

- The canonical scene, inventories, provenance and PPTX hashes are emitted in an independently checkable page manifest.
- Top-level `validation.json` separates package, object-source, editability, provenance, raster-safety and visual gates; render completion alone is not acceptance.
- Every rendered sample emits a source/render/difference contact sheet.
- Every first-100 terminal success can be re-audited with `audit_first100.py`; its audit independently recalculates editable-text, styled-text-run, asset-source, provenance, no-SVG, native 90/270-degree directional-text rotation, and arrow endpoint invariants directly from OOXML. It also verifies that every refinement acceptance region is either the selected difference tile containing an affected label or a region intersecting the affected scene owners.
- CJK primitives require both a compiler font route and a configured bundled-renderer Fontconfig environment. A controlled proof confirmed that this prevents silent Chinese glyph loss while preserving native editable text.

## Still open

1. Add predictive overflow/wrapping checks and consistent font-size-group validation beyond exact text-content preservation.
2. Detect text accidentally captured inside localized raster assets at a stronger per-asset level; the current gate uses OCR coverage and source-label policy, but does not prove every crop is free of tiny duplicated glyphs.
3. Add native table semantics for clear row/column tables rather than fragmented rectangles and text boxes.
4. Add a no-SVG strategy for full equations. Native styled runs now cover mixed prose and simple scripted identifiers, but fractions, radicals, matrices, and multi-level equations still need native Office Math/OMML or an equivalently editable representation; until implemented, those limitations remain explicit warnings.
5. Extend the measured `rect`/`round_rect`/`ellipse` repair with explicit corner-radius categories; arbitrary radii remain unavailable in native PowerPoint presets.
6. Validate continuous line/curve object granularity and dash style; arrow endpoint serialization is enforced, while rendered endpoint direction and size still need a dedicated local visual check.
7. Add script-specific rendered-glyph evidence beyond environment verification, preferably with multilingual OCR or deterministic glyph masks. The current CJK regression has controlled visual proof, but the general validation gate does not OCR every rendered language.

## Deliberate policy difference

Scientific figures may contain genuine photographic or rendered leaf regions. The current architecture permits exact, SAM-grounded, OCR-screened localized source crops because generative asset separation can alter scientific content. These assets must remain small, provenance-backed, free of editable-text duplication, and separately reported. Whole-page, whole-dashboard, whole-table, and whole-chart raster shortcuts remain forbidden.
