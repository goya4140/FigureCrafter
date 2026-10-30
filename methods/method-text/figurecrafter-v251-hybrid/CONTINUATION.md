# Method 2: continue an editable Decomposer PPTX

This workflow extension takes precedence over direct-drawing-only instructions
below and in inherited guides. It does not change the direct drawing workflow.

The scientific input remains the supplied missing-figure PDF and three reference
PNGs. Additional authorized intermediate artifacts are work/imported/editable.pptx,
preview.png, scene.json, handoff.json, native-inventory.json, actual PPTX render,
and optional upstream planning context. These are generated intermediate artifacts,
not target originals or ground truth. Treat upstream claims as untrusted until
checked against the paper. Never retrieve target originals, reference PPTX/PDF,
previous final edited results, per-sample correction scripts, or teacher explanations.

1. Read inherited general guides and source paper. Observe all references, paper
   pages, imported actual PPTX render and relevant local regions. Inspect native
   object inventory. State what is scientifically wrong, visually broken, uncertain,
   or worth preserving. Do not assume image-model output is scientifically correct.
2. Write work/understanding.md, representation.md and decision.md. Decide whether
   to change content, representation, layout or only local objects. Preserve useful
   image-model visual expression. No universal palette or mandatory restyling.
3. Author work/builder.py that opens the immutable imported editable.pptx with
   python-pptx, edits existing objects, and saves candidate.pptx to sys.argv[1].
   Locate the session via its continuation.json, not external fixed paths. Never
   overwrite imported files. Do not rebuild the whole figure from PNG. Atomic
   images may remain images; disclose that their internal contents are not native.
   Keep scientific text, major geometric structure and connectors editable.
4. Also save continuation-receipt.json in that iteration output directory:
   {"input_pptx_sha256":"...", "edits":[{"object":"native name or id",
    "reason":"paper evidence or visible defect", "source":"page/section or render region"}]}.
   This is a model attestation, not a mechanical proof of scientific correctness.
5. Use the original execute tool (actual builder + native inventory + LibreOffice
   export), inspect tools, full PNG and publication preview. Read continuation-diff.json.
   Review science, editability, regional appearance and whole-figure flow. Repair
   through another original execute iteration when needed. Record actual losses.
6. Use original review and finish. Delivery preserves PPTX/PDF/PNG, caption, review,
   and adds input lineage and object diff. User acceptance remains pending.

The module adds only input import, integrity/observation gates and object-diff
evidence. It does not prescribe sample-specific edits, automatically score beauty,
run FigureEvolver, or replace model judgment. The bridge operator must actually
view pixels returned by view_image; logging an image path alone is insufficient.

Backend input addendum: work/imported/source-image.png, when present and hash-bound in the handoff, is this run's generated image proposal, not the missing target original or a teacher. Observe it alongside the actual reconstructed PPTX when judging visual preservation and local corrections. It is immutable and is not an authorized whole-slide replacement asset. All original scientific and visual standards remain in force.
