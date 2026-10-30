import hashlib
import json
from pathlib import Path

from tools.verify_stage_snapshot import verify


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path) -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"samples": [{"sample_id": "one"}]})
    spec = {"stage": 1, "start": 1, "end": 1}
    write_json(batch / "training-identity.json", {
        "stage": spec, "sample_indices": [1], "requested_samples": 1,
        "manifest": str(manifest), "manifest_sha256": sha(manifest),
    })
    write_json(batch / "progress.json", {
        "stage": spec, "status": "complete", "completed": 1,
        "samples": [{"sample_index": 1, "sample_id": "one", "status": "render_success"}],
        "checkpoint_blockers": [],
    })
    write_json(batch / "runs" / "one" / "status.json", {"status": "render_success"})
    write_json(batch / "runs" / "one" / "output" / "scene.json", {"elements": []})
    write_json(batch / "runs" / "one" / "output" / "page-manifest.json", {"page": 1})
    write_json(batch / "runs" / "one" / "output" / "validation.json", {"passed": True})
    (batch / "runs" / "one" / "output" / "editable.pptx").write_bytes(b"synthetic pptx")
    write_json(batch / "sample-001-audit.json", {"passed": True})
    write_json(batch / "frozen-config.json", {"model": "fixture"})
    knowledge_files = []
    tree_hash = hashlib.sha256(json.dumps(knowledge_files, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode()).hexdigest()
    write_json(batch / "checkpoint.json", {
        "stage": spec, "sample_count": 1, "frozen": True,
        "all_terminal_success": True, "checkpoint_blockers": [],
        "checkpoint_eligible": True, "checkpoint_status": "eligible",
        "artifacts": [{"path": "runs/one/" + relative,
                       "sha256": sha(batch / "runs" / "one" / relative)}
                      for relative in ("status.json", "output/editable.pptx",
                                       "output/page-manifest.json", "output/validation.json")],
        "observation_reports": [],
        "knowledge_snapshot": {"file_count": 0, "tree_sha256": tree_hash, "files": knowledge_files},
        "training_identity_sha256": sha(batch / "training-identity.json"),
        "frozen_config_sha256": sha(batch / "frozen-config.json"),
        "progress_sha256": sha(batch / "progress.json"),
    })
    return batch


def test_snapshot_verifies_identity_and_hashes(tmp_path: Path) -> None:
    report = verify(fixture(tmp_path))
    assert report["complete_coverage"]
    assert report["identity_and_snapshot_consistent"]
    assert report["checkpoint_hashes_checked"] == 7
    assert report["independent_candidate_quality_eligible"] is None


def test_snapshot_detects_tampered_artifact(tmp_path: Path) -> None:
    batch = fixture(tmp_path)
    write_json(batch / "runs" / "one" / "status.json", {"status": "validation_failed"})
    report = verify(batch)
    assert not report["identity_and_snapshot_consistent"]
    assert any("checkpoint hash mismatch" in error for error in report["errors"])


def test_snapshot_rejects_false_completion(tmp_path: Path) -> None:
    batch = fixture(tmp_path)
    progress = json.loads((batch / "progress.json").read_text())
    progress["samples"] = []
    progress["completed"] = 0
    write_json(batch / "progress.json", progress)
    report = verify(batch)
    assert not report["complete_coverage"]
    assert not report["identity_and_snapshot_consistent"]
    assert any("completion without full stage coverage" in error for error in report["errors"])


def test_snapshot_rejects_nonstage_identity_clearly(tmp_path: Path) -> None:
    batch = fixture(tmp_path)
    write_json(batch / "training-identity.json", {"schema_version": "cumulative-regression"})
    report = verify(batch)
    assert not report["identity_and_snapshot_consistent"]
    assert "not a stage-training identity" in report["errors"][0]


def test_snapshot_rejects_inconsistent_checkpoint_verdict(tmp_path: Path) -> None:
    batch = fixture(tmp_path)
    checkpoint_path = batch / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["all_terminal_success"] = False
    checkpoint["checkpoint_blockers"] = [{"reason": "invented"}]
    write_json(checkpoint_path, checkpoint)
    report = verify(batch)
    assert not report["identity_and_snapshot_consistent"]
    assert any("all_terminal_success" in error for error in report["errors"])
    assert any("blockers differ" in error for error in report["errors"])


def test_snapshot_rejects_omitted_success_artifact(tmp_path: Path) -> None:
    batch = fixture(tmp_path)
    checkpoint_path = batch / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["artifacts"] = [item for item in checkpoint["artifacts"]
                               if not item["path"].endswith("editable.pptx")]
    write_json(checkpoint_path, checkpoint)
    report = verify(batch)
    assert any("checkpoint omits successful artifact" in error for error in report["errors"])
