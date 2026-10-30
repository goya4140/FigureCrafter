from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

from .common import sha256, write_json


PROTOCOL_VERSION = "decomposer.evolution.v1"
STAGES = {
    1: {"start": 1, "end": 25, "focus": "problem-map-and-observability"},
    2: {"start": 26, "end": 50, "focus": "mechanism-convergence-and-generalization"},
    3: {"start": 51, "end": 75, "focus": "long-tail-composition-and-memory-reuse"},
    4: {"start": 76, "end": 100, "focus": "freeze-cumulative-regression-and-final-acceptance"},
}
TERMINAL_SUCCESS = {"render_success", "structural_success"}
QUALITY_FAILURE_STATUSES = {"validation_failed"}
# A quality failure is terminal for sample collection but blocks checkpoint
# promotion.  It must not be replayed merely because a later sample suffered a
# runtime interruption.
TERMINAL_PROCESSED = TERMINAL_SUCCESS | QUALITY_FAILURE_STATUSES
# Backward-compatible alias for older callers and frozen run identities.
HARD_FAILURE_STATUSES = QUALITY_FAILURE_STATUSES


def stage_spec(stage: int) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {sorted(STAGES)}")
    return {"stage": stage, **STAGES[stage]}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def issue_rows(run: Path) -> list[dict[str, Any]]:
    """Collect auditable issues without mutating the run or knowledge lifecycle."""
    rows: list[dict[str, Any]] = []
    validation_path = run / "output" / "validation.json"
    if validation_path.is_file():
        for failure in _json(validation_path).get("failures", []):
            rows.append({
                "detected_in": "validation",
                "owner_stage": failure.get("owner_stage", "unknown"),
                "mechanism": failure.get("code", "unknown"),
                "affected_label_ids": sorted(failure.get("label_ids", [])),
                "affected_primitive_ids": [],
                "evaluation_region": None,
                "accepted": False,
            })
    for decision_path in sorted((run / "refinement").glob("round-*/decision.json")):
        decision = _json(decision_path)
        assessment_path = decision_path.parent / "assessment.json"
        assessment = _json(assessment_path) if assessment_path.is_file() else {}
        rows.append({
            "detected_in": decision.get("detected_in", "render_audit"),
            "owner_stage": decision.get("owner_stage", "unknown"),
            "mechanism": decision.get("mechanism", "unknown"),
            "affected_label_ids": sorted(decision.get("affected_label_ids", [])),
            "affected_primitive_ids": sorted(decision.get("affected_primitive_ids", [])),
            "evaluation_region": decision.get("evaluation_region"),
            "evaluation_region_source": decision.get("evaluation_region_source"),
            "accepted": assessment.get("accepted"),
            "assessment": {
                key: assessment.get(key)
                for key in ("local_pixel_delta", "local_edge_delta", "global_pixel_delta")
                if key in assessment
            },
        })
    return rows


def issue_key(issue: dict[str, Any]) -> str:
    value = {
        "owner_stage": issue.get("owner_stage", "unknown"),
        "mechanism": issue.get("mechanism", "unknown"),
        "affected_label_ids": issue.get("affected_label_ids", []),
        "affected_primitive_ids": issue.get("affected_primitive_ids", []),
        "evaluation_region": issue.get("evaluation_region"),
    }
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _memory_injection(run: Path) -> dict[str, Any]:
    stages = {}
    for stage in ("recognition", "representation", "layout"):
        input_path = run / "calls" / stage / "input.json"
        memories = _json(input_path).get("applicable_memories", []) if input_path.is_file() else []
        stages[stage] = {
            "count": len(memories),
            "memory_ids": [item.get("memory_id") for item in memories],
        }
    return stages


def _walk_dicts(value: Any, path: str = ""):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, f"{path}/{index}")


def _measured_memory_reuse(run: Path, injection: dict[str, Any], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return conservative, artifact-backed reuse evidence instead of counting prompt injection as reuse."""
    evidence: list[dict[str, Any]] = []
    representation_memories = set(injection.get("representation", {}).get("memory_ids", []))
    styled_memory = "representation-literal-math-markup-in-text-primitive"
    representation_path = run / "representation" / "representation.json"
    validation_path = run / "output" / "validation.json"
    if styled_memory in representation_memories and representation_path.is_file() and validation_path.is_file():
        representation = _json(representation_path)
        validation = _json(validation_path)
        styled_check = validation.get("checks", {}).get("styled_text_run_export", {})
        applications = []
        for path, item in _walk_dicts(representation):
            runs = item.get("runs")
            if not isinstance(runs, list) or not runs:
                continue
            scripted = [
                run_item for run_item in runs
                if run_item.get("script") in {"subscript", "superscript"}
                and run_item.get("source_text") != run_item.get("text")
            ]
            if scripted:
                applications.append({
                    "primitive_path": path,
                    "scripts": sorted({run_item.get("script") for run_item in scripted}),
                    "source_texts": [run_item.get("source_text") for run_item in scripted],
                    "visible_texts": [run_item.get("text") for run_item in scripted],
                })
        if applications and styled_check.get("passed"):
            accepted_related_refinement = any(
                issue.get("accepted") is True
                and issue.get("owner_stage") == "representation"
                and issue.get("mechanism") in {
                    "literal-math-markup-in-text-primitive",
                    "unwarranted-math-markup-conversion",
                    "text-box-font-metric-overflow",
                }
                for issue in issues
            )
            evidence.append({
                "memory_id": styled_memory,
                "owner_stage": "representation",
                "mechanism": "literal-math-markup-in-text-primitive",
                "proof": "native-styled-runs-with-script-baseline",
                "first_pass": not accepted_related_refinement,
                "applications": applications,
                "artifacts": [
                    str(representation_path.relative_to(run)),
                    str(validation_path.relative_to(run)),
                    "output/editable.pptx",
                ],
            })
    return evidence


def observation_report(
    root: Path,
    progress: dict[str, Any],
    spec: dict[str, Any],
    *,
    observation_number: int,
    cumulative_regression: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = progress.get("samples", [])
    issues: list[dict[str, Any]] = []
    memory_injection_samples = 0
    memory_reuse_samples = 0
    first_pass_memory_reuse_samples = 0
    memory_reuse_evidence: list[dict[str, Any]] = []
    accepted_rounds = 0
    attempted_rounds = 0
    refinement_pixel_delta = 0.0
    refinement_edge_delta = 0.0
    versions = Counter()
    for row in rows:
        run = root / "runs" / row["sample_id"]
        sample_issues = issue_rows(run)
        for issue in sample_issues:
            issues.append({"sample_id": row["sample_id"], "sample_index": row.get("sample_index"), **issue})
        injection = _memory_injection(run)
        if any(item["count"] for item in injection.values()):
            memory_injection_samples += 1
        reuse_evidence = _measured_memory_reuse(run, injection, sample_issues)
        if reuse_evidence:
            memory_reuse_samples += 1
            first_pass_memory_reuse_samples += any(item.get("first_pass") for item in reuse_evidence)
            memory_reuse_evidence.append({
                "sample_index": row.get("sample_index"),
                "sample_id": row["sample_id"],
                "evidence": reuse_evidence,
            })
        refinement = row.get("refinement") or {}
        accepted_rounds += int(refinement.get("accepted_rounds", 0))
        history = refinement.get("history", [])
        attempted_rounds += len(history)
        for issue in sample_issues:
            if issue.get("accepted") is True:
                assessment = issue.get("assessment", {})
                refinement_pixel_delta += float(assessment.get("local_pixel_delta", 0) or 0)
                refinement_edge_delta += float(assessment.get("local_edge_delta", 0) or 0)
        if row.get("version"):
            versions[row["version"]] += 1
    clusters: dict[str, dict[str, Any]] = {}
    for issue in issues:
        key = issue_key(issue)
        cluster = clusters.setdefault(key, {
            "owner_stage": issue["owner_stage"], "mechanism": issue["mechanism"],
            "affected_label_ids": issue.get("affected_label_ids", []),
            "affected_primitive_ids": issue.get("affected_primitive_ids", []),
            "evaluation_region": issue.get("evaluation_region"),
            "samples": [], "accepted": 0, "rejected": 0,
        })
        cluster["samples"].append({"sample_index": issue.get("sample_index"), "sample_id": issue["sample_id"]})
        cluster["accepted"] += issue.get("accepted") is True
        cluster["rejected"] += issue.get("accepted") is False
    seconds = [float(row.get("seconds", 0)) for row in rows if row.get("seconds") is not None]
    successful = [row for row in rows if row.get("status") in TERMINAL_SUCCESS]
    first_pass = [row for row in successful if int((row.get("refinement") or {}).get("accepted_rounds", 0)) == 0]
    recurrent = [item for item in clusters.values() if len({row["sample_id"] for row in item["samples"]}) > 1]
    report = {
        "schema_version": "decomposer.observation.v2",
        "read_only": True,
        "protocol_version": PROTOCOL_VERSION,
        "stage": spec,
        "observation_number": observation_number,
        "observed_samples": len(rows),
        "sample_indices": [row.get("sample_index") for row in rows],
        "status_counts": dict(sorted(Counter(row.get("status", "unknown") for row in rows).items())),
        "efficiency": {
            "mean_seconds_per_sample": round(statistics.mean(seconds), 3) if seconds else None,
            "median_seconds_per_sample": round(statistics.median(seconds), 3) if seconds else None,
            "versions_per_sample": round(sum(versions.values()) / len(rows), 3) if rows else None,
            "version_counts": dict(sorted(versions.items())),
            "first_pass_success_rate": round(len(first_pass) / len(rows), 6) if rows else None,
            "refinement_attempted_rounds": attempted_rounds,
            "refinement_accepted_rounds": accepted_rounds,
            "refinement_net_local_pixel_delta": round(refinement_pixel_delta, 6),
            "refinement_net_local_edge_delta": round(refinement_edge_delta, 6),
            "error_recurrence_rate": round(len(recurrent) / len(clusters), 6) if clusters else 0.0,
            "memory_injection_rate": round(memory_injection_samples / len(rows), 6) if rows else None,
            "memory_reuse_rate": round(memory_reuse_samples / len(rows), 6) if rows else None,
            "first_pass_memory_reuse_rate": round(first_pass_memory_reuse_samples / len(rows), 6) if rows else None,
            "upgrade_coverage_samples": 0,
            "cumulative_regression_pass_rate": (cumulative_regression or {}).get("pass_rate"),
        },
        "issue_count": len(issues),
        "memory_reuse_evidence": memory_reuse_evidence,
        "clusters": sorted(clusters.values(), key=lambda item: (-len(item["samples"]), item["owner_stage"], item["mechanism"])),
        "policy": {
            "framework_mutation_allowed": False,
            "note": "Observation reports collect and cluster evidence only; they do not promote memories or change framework code.",
        },
    }
    return report


def write_observation(root: Path, progress: dict[str, Any], spec: dict[str, Any], number: int) -> Path:
    path = root / "observations" / f"after-{number:03d}-samples.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, observation_report(root, progress, spec, observation_number=number))
    return path


def freeze_checkpoint(root: Path, progress: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    observation_paths = sorted((root / "observations").glob("after-*-samples.json"))
    artifacts = []
    for row in progress.get("samples", []):
        run = root / "runs" / row["sample_id"]
        for relative in ("status.json", "output/validation.json", "output/page-manifest.json", "output/editable.pptx"):
            path = run / relative
            if path.is_file():
                artifacts.append({"sample_id": row["sample_id"], "path": str(path.relative_to(root)), "sha256": sha256(path)})
        audit_path = root / f"sample-{int(row.get('sample_index', 0)):03d}-audit.json"
        if audit_path.is_file():
            artifacts.append({"sample_id": row["sample_id"], "path": str(audit_path.relative_to(root)), "sha256": sha256(audit_path)})
    knowledge_files = []
    knowledge_root = root / "knowledge"
    if knowledge_root.is_dir():
        for path in sorted(item for item in knowledge_root.rglob("*") if item.is_file()):
            knowledge_files.append({"path": str(path.relative_to(root)), "sha256": sha256(path)})
    knowledge_tree_sha256 = hashlib.sha256(
        json.dumps(knowledge_files, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    checkpoint_blockers = progress.get("checkpoint_blockers", [])
    all_terminal_success = all(row.get("status") in TERMINAL_SUCCESS for row in progress.get("samples", []))
    checkpoint_eligible = all_terminal_success and not checkpoint_blockers
    checkpoint = {
        "schema_version": "decomposer.stage-checkpoint.v2",
        "protocol_version": PROTOCOL_VERSION,
        "frozen": True,
        "stage": spec,
        "sample_count": len(progress.get("samples", [])),
        "all_terminal_success": all_terminal_success,
        "checkpoint_status": "eligible" if checkpoint_eligible else "blocked",
        "checkpoint_eligible": checkpoint_eligible,
        "checkpoint_blockers": checkpoint_blockers,
        "observation_reports": [{"path": str(path.relative_to(root)), "sha256": sha256(path)} for path in observation_paths],
        "artifacts": artifacts,
        "knowledge_snapshot": {
            "file_count": len(knowledge_files),
            "tree_sha256": knowledge_tree_sha256,
            "files": knowledge_files,
        },
        "training_identity_sha256": sha256(root / "training-identity.json"),
        "frozen_config_sha256": sha256(root / "frozen-config.json") if (root / "frozen-config.json").is_file() else None,
        "progress_sha256": sha256(root / "progress.json"),
    }
    write_json(root / "checkpoint.json", checkpoint)
    return checkpoint
