from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from . import VERSION
from .assemble import assemble
from .asset_policy import materialize as materialize_asset_policy
from .common import read_json, sha256, write_json
from .detectors import collect
from .evidence import materialize as materialize_evidence
from .layout import run as run_layout, validate as validate_layout
from .pptx import compile_scene
from .provenance import build as build_provenance
from .recognition import run as run_recognition, validate as validate_recognition
from .refinement import run as run_refinement
from .representation import run as run_representation, validate as validate_representation
from .text_registration import run as run_text_registration
from .source_line_registration import run as run_source_line_registration
from .native_shadow_registration import run as run_native_shadow_registration
from .short_text_registration import run as run_short_text_registration
from .post_short_text_registration import run as run_post_short_text_registration
from .flat_panel_registration import run as run_flat_panel_registration
from .rect_fill_registration import run as run_rect_fill_registration
from .styled_multiline_registration import run as run_styled_multiline_registration
from .text_tracking_registration import run as run_text_tracking_registration
from .joint_shadow_text_registration import run as run_joint_shadow_text_registration
from .native_text_style_registration import run as run_native_text_style_registration, run_monospace as run_single_line_monospace_registration
from .validation import validate_run


def _model_stage_reusable(output: Path, stage: str) -> bool:
    """A schema-valid fallback is not a successful model stage to resume past."""
    metadata_path = output / "calls" / stage / "metadata.json"
    if not metadata_path.exists():
        return False  # Structure alone cannot prove that the model call completed.
    metadata = read_json(metadata_path)
    return metadata.get("status") in {"complete", "fixture"} and not metadata.get("fallback_used")


def _require_model_stage(output: Path, stage: str) -> None:
    """Do not compile or checkpoint a detector fallback after a required model failure."""
    if _model_stage_reusable(output, stage):
        return
    metadata_path = output / "calls" / stage / "metadata.json"
    metadata = read_json(metadata_path) if metadata_path.exists() else {}
    raise RuntimeError(
        f"{stage} model call unavailable: {metadata.get('status', 'missing_metadata')}; "
        f"{metadata.get('error', 'no successful model-stage evidence')}"
    )


def execute(
    image: str | Path,
    output: str | Path,
    config: dict[str, Any],
    *,
    fixture_dir: str | Path | None = None,
    render_output: bool = True,
    resume: bool = False,
) -> dict[str, Any]:
    image, output = Path(image).resolve(), Path(output).resolve()
    if not image.is_file():
        raise FileNotFoundError(image)
    output.mkdir(parents=True, exist_ok=True)
    status_path = output / "status.json"
    write_json(status_path, {"version": VERSION, "status": "running", "phase": "input"})
    try:
        resume_phases: list[dict[str, Any]] = []
        reuse_chain = bool(resume)
        input_dir = output / "input"
        input_dir.mkdir(exist_ok=True)
        normalized = input_dir / "normalized.png"
        source_hash = sha256(image)
        try:
            input_record = read_json(input_dir / "input.json") if reuse_chain else None
            if not input_record or input_record.get("source_sha256") != source_hash or not normalized.is_file():
                raise ValueError("input identity changed or normalized image missing")
            with Image.open(normalized) as source:
                canvas = source.size
            if list(canvas) != input_record.get("canvas"):
                raise ValueError("normalized canvas changed")
            resume_phases.append({"phase": "input", "reused": True})
        except Exception as exc:
            with Image.open(image) as source:
                canvas = source.size
                source.convert("RGBA").save(normalized)
            write_json(input_dir / "input.json", {"source": str(image), "source_sha256": source_hash, "canvas": list(canvas)})
            resume_phases.append({"phase": "input", "reused": False, "reason": str(exc) if resume else "resume_not_requested"})
            reuse_chain = False

        write_json(status_path, {"version": VERSION, "status": "running", "phase": "observations"})
        try:
            if not reuse_chain: raise ValueError("upstream phase was recomputed")
            candidates = read_json(output / "observations" / "candidates.json")
            evidence = read_json(output / "observations" / "evidence-ir.json")
            expected_evidence_canvas = {"width": canvas[0], "height": canvas[1]}
            if candidates.get("canvas") != list(canvas) or evidence.get("canvas") != expected_evidence_canvas:
                raise ValueError("observation canvas mismatch")
            resume_phases.append({"phase": "observations", "reused": True})
        except Exception as exc:
            candidates = collect(normalized, canvas, config, output / "observations")
            evidence = materialize_evidence(normalized, candidates, canvas, config, output / "observations")
            resume_phases.append({"phase": "observations", "reused": False, "reason": str(exc)})
            reuse_chain = False

        write_json(status_path, {"version": VERSION, "status": "running", "phase": "recognition"})
        try:
            if not reuse_chain: raise ValueError("upstream phase was recomputed")
            recognition = read_json(output / "recognition" / "recognition.json")
            validate_recognition(recognition)
            if [recognition["canvas"]["width"], recognition["canvas"]["height"]] != list(canvas):
                raise ValueError("recognition canvas mismatch")
            if not _model_stage_reusable(output, "recognition"):
                raise ValueError("recognition model call degraded; retry from recognition")
            resume_phases.append({"phase": "recognition", "reused": True})
        except Exception as exc:
            recognition = run_recognition(normalized, canvas, evidence, config, output / "recognition", output / "calls", fixture_dir)
            resume_phases.append({"phase": "recognition", "reused": False, "reason": str(exc)})
            reuse_chain = False
        _require_model_stage(output, "recognition")

        asset_decisions = materialize_asset_policy(
            recognition, evidence, config, output / "representation" / "asset-decisions.json"
        )

        write_json(status_path, {"version": VERSION, "status": "running", "phase": "representation"})
        try:
            if not reuse_chain: raise ValueError("upstream phase was recomputed")
            representation = read_json(output / "representation" / "representation.json")
            validate_representation(representation, recognition)
            if not _model_stage_reusable(output, "representation"):
                raise ValueError("representation model call degraded; retry from representation")
            call_input = read_json(output / "calls" / "representation" / "input.json")
            if (call_input.get("recognition") != recognition
                    or call_input.get("asset_decisions") != asset_decisions
                    or call_input.get("representation_strategy", {}) != config.get("representation", {})):
                raise ValueError("representation input changed; retry from representation")
            resume_phases.append({"phase": "representation", "reused": True})
        except Exception as exc:
            representation = run_representation(
                normalized, recognition, config, output / "representation", output / "calls",
                asset_decisions=asset_decisions, fixture_dir=fixture_dir,
            )
            resume_phases.append({"phase": "representation", "reused": False, "reason": str(exc)})
            reuse_chain = False
        _require_model_stage(output, "representation")

        write_json(status_path, {"version": VERSION, "status": "running", "phase": "layout"})
        try:
            if not reuse_chain: raise ValueError("upstream phase was recomputed")
            layout = read_json(output / "layout" / "layout.json")
            validate_layout(layout, representation, recognition)
            if not _model_stage_reusable(output, "layout"):
                raise ValueError("layout model call degraded; retry from layout")
            resume_phases.append({"phase": "layout", "reused": True})
        except Exception as exc:
            layout = run_layout(normalized, recognition, representation, config, output / "layout", output / "calls", fixture_dir)
            resume_phases.append({"phase": "layout", "reused": False, "reason": str(exc)})
            reuse_chain = False
        _require_model_stage(output, "layout")
        write_json(output / "resume-report.json", {"version": VERSION, "resume_requested": resume, "phases": resume_phases})

        write_json(status_path, {"version": VERSION, "status": "running", "phase": "assembly"})
        scene = assemble(recognition, representation, layout)
        output_dir = output / "output"
        output_dir.mkdir(exist_ok=True)
        scene_path = output_dir / "scene.json"
        write_json(scene_path, scene)

        write_json(status_path, {"version": VERSION, "status": "running", "phase": "compile"})
        report = compile_scene(scene_path, output_dir, config, render_output)
        provenance = build_provenance(scene, output_dir / "editable.pptx", output_dir)
        refinement = run_refinement(output, normalized, config, fixture_dir=fixture_dir) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted_rounds": 0,
        }
        if refinement.get("accepted_rounds"):
            report = read_json(output_dir / "report.json")
        text_registration = run_text_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        text_registration_pass_2 = run_text_registration(output, normalized, config, pass_number=2) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        source_line_registration = run_source_line_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        native_shadow_registration = run_native_shadow_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        short_text_registration = run_short_text_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        post_short_text_registration = run_post_short_text_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        flat_panel_registration = run_flat_panel_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        rect_fill_registration = run_rect_fill_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        styled_multiline_registration = run_styled_multiline_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        text_tracking_registration = run_text_tracking_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        joint_shadow_text_registration = run_joint_shadow_text_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        native_text_style_registration = run_native_text_style_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        single_line_monospace_registration = run_single_line_monospace_registration(output, normalized, config) if render_output else {
            "status": "skipped", "reason": "render_disabled", "accepted": False,
        }
        if (text_registration.get("accepted") or text_registration_pass_2.get("accepted")
                or source_line_registration.get("accepted") or native_shadow_registration.get("accepted")
                or short_text_registration.get("accepted") or post_short_text_registration.get("accepted")
                or flat_panel_registration.get("accepted") or rect_fill_registration.get("accepted")
                or styled_multiline_registration.get("accepted") or text_tracking_registration.get("accepted")
                or joint_shadow_text_registration.get("accepted")
                or native_text_style_registration.get("accepted")
                or single_line_monospace_registration.get("accepted")):
            report = read_json(output_dir / "report.json")
            scene = read_json(scene_path)
        validation = validate_run(output, normalized, config)
        degraded = scene["degraded"] or bool(report["compiler"].get("fallbacks")) or report["structure"].get("is_single_screenshot_like", False)
        status = {
            "version": VERSION, "status": report["status"] if validation["passed"] else "validation_failed", "degraded": degraded,
            "refinement": refinement,
            "text_registration": text_registration,
            "text_registration_pass_2": text_registration_pass_2,
            "source_line_registration": source_line_registration,
            "native_shadow_registration": native_shadow_registration,
            "short_text_registration": short_text_registration,
            "post_short_text_registration": post_short_text_registration,
            "flat_panel_registration": flat_panel_registration,
            "rect_fill_registration": rect_fill_registration,
            "styled_multiline_registration": styled_multiline_registration,
            "text_tracking_registration": text_tracking_registration,
            "joint_shadow_text_registration": joint_shadow_text_registration,
            "native_text_style_registration": native_text_style_registration,
            "single_line_monospace_registration": single_line_monospace_registration,
            "validation": {"passed": validation["passed"], "visual": validation["visual"], "failure_count": len(validation["failures"]), "warning_count": len(validation["warnings"])},
            "artifacts": {
                "recognition": "recognition/recognition.json",
                "evidence": "observations/evidence-ir.json",
                "evidence_overlay": "observations/evidence-overlay.png",
                "candidate_dispositions": "recognition/candidate-dispositions.json",
                "representation": "representation/representation.json",
                "asset_decisions": "representation/asset-decisions.json",
                "layout": "layout/layout.json",
                "scene": "output/scene.json", "pptx": "output/editable.pptx",
                "provenance": "output/provenance.json",
                "id_mask": "output/id-mask.png",
                "manifest": "output/page-manifest.json",
                "validation": "output/validation.json",
                "contact_sheet": "output/contact-sheet.png" if (output_dir / "contact-sheet.png").exists() else None,
                "resume_report": "resume-report.json",
                "text_registration": "text-registration/decision.json" if (output / "text-registration/decision.json").exists() else None,
                "text_registration_pass_2": "text-registration-pass-2/decision.json" if (output / "text-registration-pass-2/decision.json").exists() else None,
                "source_line_registration": "source-line-registration/decision.json" if (output / "source-line-registration/decision.json").exists() else None,
                "native_shadow_registration": "native-shadow-registration/decision.json" if (output / "native-shadow-registration/decision.json").exists() else None,
                "short_text_registration": "short-text-registration/decision.json" if (output / "short-text-registration/decision.json").exists() else None,
                "post_short_text_registration": "post-short-text-registration/decision.json" if (output / "post-short-text-registration/decision.json").exists() else None,
                "flat_panel_registration": "flat-panel-registration/decision.json" if (output / "flat-panel-registration/decision.json").exists() else None,
                "rect_fill_registration": "rect-fill-registration/decision.json" if (output / "rect-fill-registration/decision.json").exists() else None,
                "styled_multiline_registration": "styled-multiline-registration/decision.json" if (output / "styled-multiline-registration/decision.json").exists() else None,
                "text_tracking_registration": "text-tracking-registration/decision.json" if (output / "text-tracking-registration/decision.json").exists() else None,
                "joint_shadow_text_registration": "joint-shadow-text-registration/decision.json" if (output / "joint-shadow-text-registration/decision.json").exists() else None,
                "native_text_style_registration": "native-text-style-registration/decision.json" if (output / "native-text-style-registration/decision.json").exists() else None,
                "single_line_monospace_registration": "single-line-monospace-registration/decision.json" if (output / "single-line-monospace-registration/decision.json").exists() else None,
                "preview": "output/preview.png" if (output_dir / "preview.png").exists() else None,
            },
        }
        write_json(status_path, status)
        return status
    except Exception as exc:
        failure = {"version": VERSION, "status": "runtime_failed", "error": f"{type(exc).__name__}: {exc}"}
        write_json(status_path, failure)
        raise
