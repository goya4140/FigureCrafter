import json
from pathlib import Path

from decomposer import VERSION
from decomposer.common import sha256, write_json
from decomposer.knowledge import _measured_assessment_valid, generation_guidance, retrieve
from audit_first100 import refinement_memory_injection


def test_refinement_audit_allows_candidates_but_rejects_superseded_and_over_limit(tmp_path: Path) -> None:
    first = tmp_path / "refinement/round-001/call/input.json"
    second = tmp_path / "refinement/round-002/call/input.json"
    write_json(first, {"applicable_memories": [
        {"memory_id": "verified", "status": "downstreamed"},
    ]})
    write_json(second, {"applicable_memories": [
        {"memory_id": "verified", "status": "downstreamed"},
        {"memory_id": "unpromoted", "status": "candidate"},
    ]})
    result = refinement_memory_injection(tmp_path)
    assert result["all_downstreamed"] is False
    assert result["no_superseded"] is True
    assert result["within_limit"] is True
    assert result["rounds"][0]["non_downstreamed_ids"] == []
    assert result["rounds"][1]["non_downstreamed_ids"] == ["unpromoted"]


def measured_assessment() -> dict:
    return {
        "accepted": True,
        "before_local": {"pixel_similarity": .8, "edge_iou": .3},
        "after_local": {"pixel_similarity": .82, "edge_iou": .31},
        "before_global": {"pixel_similarity": .9, "edge_iou": .4},
        "after_global": {"pixel_similarity": .902, "edge_iou": .405},
        "local_pixel_delta": .02, "local_edge_delta": .01,
        "global_pixel_delta": .002,
    }


def test_downstreamed_guidance_requires_current_artifact_identity(tmp_path: Path) -> None:
    project = tmp_path
    (project / "Decomposer").mkdir()
    knowledge = project / "experiments/batch/knowledge"
    (knowledge / "memory").mkdir(parents=True)
    (knowledge / "evidence").mkdir()
    artifact = project / "experiments/proof.json"
    artifact.write_text('{"measured":true}')
    code = project / "Decomposer/policy.py"
    code.write_text("POLICY = True\n")
    validator = project / "Decomposer/validation.py"
    validator.write_text("VALIDATE = True\n")
    for memory_id, status, evidence_id in (
        ("good", "downstreamed", "good-evidence"),
        ("stale", "downstreamed", "stale-evidence"),
        ("unpromoted", "candidate", "good-evidence"),
        ("imported", "candidate_imported", "missing-evidence"),
        ("old", "superseded", "good-evidence"),
    ):
        memory = {
            "memory_id": memory_id, "owner_stage": "representation",
            "mechanism": "text-owner-scope",
            "status": status, "evidence_ids": [evidence_id],
        }
        if memory_id == "good":
            memory["downstream_proof"] = {
                "version": VERSION,
                "scope": "representation text owners with measured visual mismatch",
                "counterexamples": ["non-text owners are excluded"],
                "evidence_id": evidence_id,
                "artifact_refs": [{"path": str(artifact), "sha256": sha256(artifact)}],
                "code_artifact": {"path": str(code), "sha256": sha256(code)},
                "validation_code_artifact": {"path": str(validator), "sha256": sha256(validator)},
                "supersession": None,
            }
        write_json(knowledge / "memory" / f"{memory_id}.json", memory)
    for evidence_id, digest in (("good-evidence", sha256(artifact)),
                                ("stale-evidence", "0" * 64)):
        write_json(knowledge / "evidence" / f"{evidence_id}.json", {
            "owner_stage": "representation", "mechanism": "text-owner-scope",
            "accepted": True, "assessment": measured_assessment(),
            "artifact_refs": [{"path": "experiments/proof.json", "sha256": digest}],
        })
    config = {"knowledge": {"path": str(knowledge)}}
    assert [row["memory_id"] for row in generation_guidance(config, "representation")] == ["good"]
    assert [row["memory_id"] for row in retrieve(knowledge, require_attested=True)] == ["good"]
    assert {row["memory_id"] for row in retrieve(knowledge)} == {"good", "stale", "unpromoted"}
    qualified = json.loads((knowledge / "memory/good.json").read_text())
    qualified["downstream_proof"]["counterexamples"] = []
    write_json(knowledge / "memory/good.json", qualified)
    assert generation_guidance(config, "representation") == []
    qualified["downstream_proof"]["counterexamples"] = ["non-text owners are excluded"]
    write_json(knowledge / "memory/good.json", qualified)
    code.write_text("POLICY = False\n")
    assert generation_guidance(config, "representation") == []
    code.write_text("POLICY = True\n")
    assert [row["memory_id"] for row in generation_guidance(config, "representation")] == ["good"]
    validator.write_text("VALIDATE = False\n")
    assert generation_guidance(config, "representation") == []
    validator.write_text("VALIDATE = True\n")
    assert [row["memory_id"] for row in generation_guidance(config, "representation")] == ["good"]
    wrong_link = json.loads((knowledge / "memory/good.json").read_text())
    wrong_link["evidence_ids"].append("unaccepted-proof")
    wrong_link["downstream_proof"]["evidence_id"] = "unaccepted-proof"
    write_json(knowledge / "memory/good.json", wrong_link)
    write_json(knowledge / "evidence/unaccepted-proof.json", {
        "owner_stage": "representation", "mechanism": "text-owner-scope",
        "accepted": False, "assessment": measured_assessment(),
        "artifact_refs": [{"path": str(artifact), "sha256": sha256(artifact)}],
    })
    assert generation_guidance(config, "representation") == []
    wrong_link["downstream_proof"]["evidence_id"] = "good-evidence"
    write_json(knowledge / "memory/good.json", wrong_link)
    assert [row["memory_id"] for row in generation_guidance(config, "representation")] == ["good"]
    wrong_stage = json.loads((knowledge / "evidence/good-evidence.json").read_text())
    wrong_stage["owner_stage"] = "layout"
    write_json(knowledge / "evidence/good-evidence.json", wrong_stage)
    assert generation_guidance(config, "representation") == []
    wrong_stage["owner_stage"] = "representation"
    write_json(knowledge / "evidence/good-evidence.json", wrong_stage)
    artifact.write_text('{"measured":false}')
    assert generation_guidance(config, "representation") == []
    assert json.loads((knowledge / "memory/good.json").read_text())["status"] == "downstreamed"


def test_unaccepted_or_missing_evidence_is_not_proactive_guidance(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    (knowledge / "memory").mkdir(parents=True)
    (knowledge / "evidence").mkdir()
    proof = tmp_path / "proof.json"
    proof.write_text("ok")
    for memory_id, evidence in (
        ("unaccepted", {"accepted": False, "assessment": {"accepted": True},
                        "artifact_refs": [{"path": str(proof), "sha256": sha256(proof)}]}),
        ("unmeasured", {"accepted": True, "assessment": {},
                        "artifact_refs": [{"path": str(proof), "sha256": sha256(proof)}]}),
        ("missing", None),
    ):
        write_json(knowledge / "memory" / f"{memory_id}.json", {
            "memory_id": memory_id, "owner_stage": "layout", "status": "downstreamed",
            "evidence_ids": [memory_id],
        })
        if evidence is not None:
            write_json(knowledge / "evidence" / f"{memory_id}.json", evidence)
    assert generation_guidance({"knowledge": {"path": str(knowledge)}}, "layout") == []


def test_supersession_relation_excludes_memory_even_if_status_drifted(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    (knowledge / "memory").mkdir(parents=True)
    for memory_id, relation in (
        ("top_level", {"superseded_by": "replacement"}),
        ("proof", {"supersession_proof": {"superseded_by": "replacement"}}),
        ("downstream", {"downstream_proof": {"supersession": {"superseded_by": "replacement"}}}),
    ):
        write_json(knowledge / "memory" / f"{memory_id}.json", {
            "memory_id": memory_id, "owner_stage": "layout", "status": "downstreamed",
            **relation,
        })
    assert retrieve(knowledge) == []
    assert retrieve(knowledge, require_attested=True) == []


def test_accepted_flag_without_numeric_measurement_is_not_guidance(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    (knowledge / "memory").mkdir(parents=True)
    (knowledge / "evidence").mkdir()
    proof = tmp_path / "proof.json"
    proof.write_text("ok")
    write_json(knowledge / "memory/flag-only.json", {
        "memory_id": "flag-only", "owner_stage": "representation",
        "status": "downstreamed", "evidence_ids": ["e1"],
    })
    write_json(knowledge / "evidence/e1.json", {
        "accepted": True, "assessment": {"accepted": True},
        "artifact_refs": [{"path": str(proof), "sha256": sha256(proof)}],
    })
    assert generation_guidance({"knowledge": {"path": str(knowledge)}}, "representation") == []


def test_structural_hard_gate_measurement_requires_gate_flip_and_visual_floor() -> None:
    assessment = {
        "measurement_type": "structural_hard_gate", "accepted": True,
        "hard_gate": {"name": "exact_editable_text_identity", "failures_before": 1, "failures_after": 0},
        "visual_thresholds": {"minimum_pixel_similarity": .94, "minimum_edge_iou": .2},
        "before_local": {"pixel_similarity": .915298, "edge_iou": .143121},
        "after_local": {"pixel_similarity": .873878, "edge_iou": .145185},
        "before_global": {"pixel_similarity": .958065, "edge_iou": .270737},
        "after_global": {"pixel_similarity": .957719, "edge_iou": .270054},
        "local_pixel_delta": -.041420, "local_edge_delta": .002064,
        "global_pixel_delta": -.000346,
    }
    assert _measured_assessment_valid(assessment)
    assert not _measured_assessment_valid({**assessment, "hard_gate": {**assessment["hard_gate"], "failures_after": 1}})
    assert not _measured_assessment_valid({**assessment, "visual_thresholds": {"minimum_pixel_similarity": .96, "minimum_edge_iou": .2}})
    assert not _measured_assessment_valid({**assessment, "global_pixel_delta": -.005})
    assert not _measured_assessment_valid({**assessment, "measurement_type": "untyped"})


def test_visual_measurement_must_have_accepted_net_gain() -> None:
    assessment = measured_assessment()
    assert _measured_assessment_valid(assessment)
    no_gain = {**assessment, "after_local": assessment["before_local"],
               "local_pixel_delta": 0, "local_edge_delta": 0}
    assert not _measured_assessment_valid(no_gain)
    visual_regression = {**assessment,
                         "after_global": {"pixel_similarity": .897, "edge_iou": .405},
                         "global_pixel_delta": -.003}
    assert not _measured_assessment_valid(visual_regression)
