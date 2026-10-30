from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from decomposer.common import write_json
from decomposer.knowledge import link_evidence, refinement_guidance
from decomposer.refinement import _hard_failure_keys, _knowledge_use, _next_round, _recover_pending_commit, _text_identity


def _memory(root: Path, index: int, *, status: str = "candidate", stage: str = "layout", mechanism: str | None = None) -> str:
    memory_id = f"{stage}-{mechanism or f'offset-{index}'}"
    write_json(root / "memory" / f"{memory_id}.json", {
        "memory_id": memory_id,
        "owner_stage": stage,
        "mechanism": mechanism or f"offset-{index}",
        "status": status,
        "accepted_count": index,
        "rejected_count": 0,
        "evidence_ids": [],
        "strategies": ["Adjust the measured object placement only."],
    })
    write_json(root / "skills" / memory_id / "strategy.json", {
        "skill_id": f"skill-{memory_id}", "memory_id": memory_id,
        "status": status, "instructions": ["Adjust placement."],
    })
    return memory_id


def test_refinement_retrieves_candidates_excludes_superseded_and_caps_at_eight(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    for index in range(10):
        status = ("candidate_imported", "candidate", "verified", "active", "downstreamed")[index % 5]
        _memory(root, index, status=status)
    superseded = _memory(root, 99, status="superseded")
    rows = refinement_guidance(
        root,
        candidates=[{"candidate_id": "tile-0-0", "label_ids": ["object-placement"]}],
        previous_attempts=[{"owner_stage": "layout", "mechanism": "offset-4"}],
        limit=20,
    )
    assert len(rows) == 8
    assert all(row["memory_id"] != superseded for row in rows)
    assert {row["status"] for row in rows} & {"candidate", "candidate_imported"}
    assert rows[0]["mechanism"] == "offset-4"
    assert all("skill_id" in row and "evidence_summary" in row for row in rows)


def test_model_knowledge_references_are_bounded_to_retrieved_ids() -> None:
    guidance = [{"memory_id": "m1", "skill_id": "s1", "status": "candidate"}]
    trace = _knowledge_use({
        "referenced_memory_ids": ["m1", "invented"],
        "referenced_skill_ids": ["s1", "invented-skill"],
        "patch_source_memory_ids": ["m1", "other"],
    }, guidance)
    assert trace["referenced_memory_ids"] == ["m1"]
    assert trace["referenced_skill_ids"] == ["s1"]
    assert trace["patch_source_memory_ids"] == ["m1"]
    assert set(trace["invalid_reference_ids"]) == {"invented", "invented-skill", "other"}


def test_evidence_linking_is_idempotent_for_acceptance_and_rejection(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    memory_id = _memory(root, 1)
    write_json(root / "evidence" / "accepted.json", {"accepted": True})
    write_json(root / "evidence" / "rejected.json", {"accepted": False})
    assert link_evidence(root, [memory_id], "accepted") == [memory_id]
    assert link_evidence(root, [memory_id], "accepted") == [memory_id]
    assert link_evidence(root, [memory_id], "rejected") == [memory_id]
    memory = json.loads((root / "memory" / f"{memory_id}.json").read_text())
    assert memory["evidence_ids"] == ["accepted", "rejected"]
    assert memory["accepted_count"] == 1
    assert memory["rejected_count"] == 1
    skill = json.loads((root / "skills" / memory_id / "strategy.json").read_text())
    assert skill["evidence_ids"] == ["accepted", "rejected"]


def test_hard_gate_diff_excludes_only_visual_threshold() -> None:
    validation = {"failures": [
        {"code": "visual-quality-below-threshold", "label_ids": ["a"]},
        {"code": "compiler-fallback-present", "label_ids": ["b"]},
    ]}
    assert _hard_failure_keys(validation) == {("compiler-fallback-present", ("b",))}


def test_text_identity_detects_recognition_and_primitive_changes() -> None:
    recognition = {"objects": [{"id": "a", "text": "Exact"}]}
    representation = {"components": [{"label_id": "a", "primitives": [
        {"id": "text", "kind": "text", "text": "Exact"},
    ]}]}
    original = _text_identity(recognition, representation)
    representation["components"][0]["primitives"][0]["text"] = "Changed"
    assert _text_identity(recognition, representation) != original


def test_two_experiment_knowledge_roots_do_not_cross_contaminate(tmp_path: Path) -> None:
    first, second = tmp_path / "model-a", tmp_path / "model-b"
    for root in (first, second):
        _memory(root, 1)
    memory_id = "layout-offset-1"
    write_json(first / "evidence" / "run-a.json", {"accepted": True})
    link_evidence(first, [memory_id], "run-a")
    first_memory = json.loads((first / "memory" / f"{memory_id}.json").read_text())
    second_memory = json.loads((second / "memory" / f"{memory_id}.json").read_text())
    assert first_memory["evidence_ids"] == ["run-a"]
    assert second_memory["evidence_ids"] == []


def test_resume_recovers_accepted_candidate_once_and_reuses_incomplete_round(tmp_path: Path) -> None:
    root = tmp_path / "run"
    for stage in ("recognition", "representation", "layout"):
        write_json(root / stage / f"{stage}.json", {"source": True})
    (root / "output").mkdir(parents=True)
    (root / "output" / "editable.pptx").write_bytes(b"old")
    round_dir = root / "refinement" / "round-001"
    write_json(round_dir / "assessment.json", {"accepted": True})
    for stage in ("recognition", "representation", "layout"):
        write_json(round_dir / stage / f"{stage}.json", {"candidate": stage})
    (round_dir / "output").mkdir(parents=True)
    for name in ("editable.pptx", "preview.png", "scene.json"):
        (round_dir / "output" / name).write_bytes(name.encode())
    assert _recover_pending_commit(root) == ["round-001"]
    assert _recover_pending_commit(root) == []
    assert json.loads((root / "layout/layout.json").read_text()) == {"candidate": "layout"}
    incomplete = root / "refinement" / "round-002"
    incomplete.mkdir()
    assert _next_round(root, 3) == 2


class RefinementMemoryPolicyTest(unittest.TestCase):
    def _with_path(self, function) -> None:
        with tempfile.TemporaryDirectory(prefix="refinement-memory-policy-") as name:
            function(Path(name))

    def test_retrieval_policy(self):
        self._with_path(test_refinement_retrieves_candidates_excludes_superseded_and_caps_at_eight)

    def test_reference_bounds(self):
        test_model_knowledge_references_are_bounded_to_retrieved_ids()

    def test_evidence_idempotency(self):
        self._with_path(test_evidence_linking_is_idempotent_for_acceptance_and_rejection)

    def test_hard_gate_policy(self):
        test_hard_gate_diff_excludes_only_visual_threshold()

    def test_text_identity_policy(self):
        test_text_identity_detects_recognition_and_primitive_changes()

    def test_experiment_isolation(self):
        self._with_path(test_two_experiment_knowledge_roots_do_not_cross_contaminate)

    def test_resume_transaction(self):
        self._with_path(test_resume_recovers_accepted_candidate_once_and_reuses_incomplete_round)
