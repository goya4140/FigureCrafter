from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any

from . import VERSION
from .common import sha256, write_json


STATUS_RANK = {"candidate_imported": 0, "candidate": 1, "verified": 2, "active": 3, "downstreamed": 4, "superseded": 5}


def _identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def _slug(value: Any) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "unresolved").lower()).strip("-")
    return (cleaned or "unresolved")[:80]


def _write_skill(root: Path, memory: dict[str, Any]) -> None:
    """Mirror a memory into its executable skill package without losing promotion state."""
    memory_id = memory["memory_id"]
    mechanism = memory["mechanism"]
    status = memory.get("status", "candidate")
    skill_dir = root / "skills" / memory_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    strategy = {
        "skill_id": memory_id,
        "memory_id": memory_id,
        "status": status,
        "instructions": memory.get("strategies", []),
        "evidence_ids": memory.get("evidence_ids", []),
    }
    for key in ("code_status", "downstream_proof"):
        if key in memory:
            strategy[key] = memory[key]
    write_json(skill_dir / "strategy.json", strategy)
    if status == "downstreamed":
        lifecycle = "Downstreamed: integrated into the current pipeline and supported by measured proof."
    elif status == "superseded":
        lifecycle = "Superseded: retained for audit history but excluded from retrieval and generation guidance."
    else:
        lifecycle = "Candidate only; promote after controlled probe validation."
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {memory_id[:63]}\ndescription: Prevention strategy for {mechanism}.\n---\n\n"
        + "\n\n".join(memory.get("strategies", []))
        + f"\n\n{lifecycle}\n"
    )


def capture(root: str | Path, issue: dict[str, Any], *, sample_id: str, round_id: str, accepted: bool | None, assessment: dict[str, Any] | None, artifact_paths: list[str | Path], knowledge_trace: dict[str, Any] | None = None) -> str:
    root = Path(root); stage = _slug(issue.get("owner_stage", "unknown")); mechanism = _slug(issue.get("mechanism"))
    evidence = {
        "schema_version": "decomposer.evidence.v1", "sample_id": sample_id, "round_id": round_id,
        "detected_in": issue.get("detected_in", "render_audit"), "owner_stage": stage,
        "mechanism": mechanism, "label_ids": issue.get("affected_label_ids", []),
        "primitive_ids": issue.get("affected_primitive_ids", []), "field": issue.get("field"),
        "region": issue.get("region"), "summary": issue.get("summary", ""), "strategy": issue.get("strategy", ""),
        "accepted": accepted, "assessment": assessment,
        "knowledge_trace": knowledge_trace or {},
        "artifact_refs": [{"path": str(Path(p)), "sha256": sha256(p)} for p in artifact_paths if Path(p).is_file()],
    }
    evidence_id = _identity(evidence); evidence["evidence_id"] = evidence_id
    evidence_path = root / "evidence" / f"{evidence_id}.json"
    is_new = not evidence_path.exists()
    write_json(evidence_path, evidence)
    memory_id = f"{stage}-{mechanism}"
    memory_path = root / "memory" / f"{memory_id}.json"
    if memory_path.exists(): memory = json.loads(memory_path.read_text())
    else: memory = {"schema_version": "decomposer.memory.v1", "memory_id": memory_id, "owner_stage": stage, "mechanism": mechanism, "status": "candidate", "evidence_ids": [], "accepted_count": 0, "rejected_count": 0, "strategies": []}
    if evidence_id not in memory["evidence_ids"]: memory["evidence_ids"].append(evidence_id)
    if is_new and accepted is True: memory["accepted_count"] += 1
    elif is_new and accepted is False: memory["rejected_count"] += 1
    strategy = issue.get("strategy")
    if strategy and strategy not in memory["strategies"]: memory["strategies"].append(strategy)
    write_json(memory_path, memory)
    _write_skill(root, memory)
    return evidence_id


def _artifact_candidates(root: Path, raw_path: str) -> list[Path]:
    path = Path(raw_path)
    if path.is_absolute():
        return [path]
    # Historical knowledge used both project-relative and Decomposer-relative
    # references. Resolve only those two documented conventions; never search
    # by basename, which could silently substitute unrelated evidence.
    project = next((parent for parent in (root.resolve(), *root.resolve().parents)
                    if (parent / "experiments").is_dir() and (parent / "Decomposer").is_dir()), None)
    if project is None:
        return [root / path]
    return list(dict.fromkeys((project / path, project / "Decomposer" / path)))


def _measured_assessment_valid(assessment: Any) -> bool:
    """Require reproducible measurements, including typed hard-gate repairs."""
    if not isinstance(assessment, dict) or assessment.get("accepted") is not True:
        return False
    def metric(section: str, key: str) -> float | None:
        values = assessment.get(section)
        value = values.get(key) if isinstance(values, dict) else None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    values = {name: (metric("before_local", name), metric("after_local", name),
                     metric("before_global", name), metric("after_global", name))
              for name in ("pixel_similarity", "edge_iou")}
    if any(value is None for row in values.values() for value in row):
        return False
    for key, expected in (
        ("local_pixel_delta", values["pixel_similarity"][1] - values["pixel_similarity"][0]),
        ("local_edge_delta", values["edge_iou"][1] - values["edge_iou"][0]),
        ("global_pixel_delta", values["pixel_similarity"][3] - values["pixel_similarity"][2]),
    ):
        stated = assessment.get(key)
        if not isinstance(stated, (int, float)) or isinstance(stated, bool) or not math.isfinite(stated) or abs(stated - expected) > .000002:
            return False
    measurement_type = assessment.get("measurement_type", "visual")
    if measurement_type == "visual":
        return ((assessment["local_pixel_delta"] >= .001 or assessment["local_edge_delta"] >= .003)
                and assessment["global_pixel_delta"] >= -.002)
    if measurement_type != "structural_hard_gate":
        return False
    gate = assessment.get("hard_gate") or {}
    before, after = gate.get("failures_before"), gate.get("failures_after")
    if (not isinstance(gate.get("name"), str) or not gate["name"]
            or not isinstance(before, int) or isinstance(before, bool) or before <= 0
            or not isinstance(after, int) or isinstance(after, bool) or after != 0):
        return False
    thresholds = assessment.get("visual_thresholds") or {}
    minimum_pixel = thresholds.get("minimum_pixel_similarity")
    minimum_edge = thresholds.get("minimum_edge_iou")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
               for value in (minimum_pixel, minimum_edge)):
        return False
    return (values["pixel_similarity"][3] >= minimum_pixel
            and values["edge_iou"][3] >= minimum_edge
            and assessment["global_pixel_delta"] >= -.002)


def _has_intact_accepted_evidence(root: Path, memory: dict[str, Any], required_evidence_id: str | None = None) -> bool:
    """Fail closed for proactive reuse while retaining historical status for audit."""
    for evidence_id in memory.get("evidence_ids", []):
        if required_evidence_id is not None and evidence_id != required_evidence_id:
            continue
        evidence_path = root / "evidence" / f"{evidence_id}.json"
        if not evidence_path.is_file():
            continue
        try:
            evidence = json.loads(evidence_path.read_text())
        except (OSError, ValueError):
            continue
        if (evidence.get("owner_stage") != memory.get("owner_stage")
                or evidence.get("mechanism") != memory.get("mechanism")
                or evidence.get("accepted") is not True
                or not _measured_assessment_valid(evidence.get("assessment"))):
            continue
        refs = evidence.get("artifact_refs")
        if not isinstance(refs, list) or not refs:
            continue
        valid = True
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("sha256"), str):
                valid = False
                break
            def matches(path: Path) -> bool:
                try:
                    return path.is_file() and sha256(path) == ref["sha256"]
                except OSError:
                    return False
            if not any(matches(path) for path in _artifact_candidates(root, ref["path"])):
                valid = False
                break
        if valid:
            return True
    return False


def _has_current_qualified_proof(root: Path, memory: dict[str, Any]) -> bool:
    """Fail closed when promotion scope or current code identity is unproven."""
    if _is_superseded(memory):
        return False
    proof = memory.get("downstream_proof")
    if not isinstance(proof, dict) or proof.get("version") != VERSION:
        return False
    if not isinstance(proof.get("scope"), str) or not proof["scope"].strip():
        return False
    counterexamples = proof.get("counterexamples")
    if (not isinstance(counterexamples, list) or not counterexamples
            or any(not isinstance(item, str) or not item.strip() for item in counterexamples)):
        return False
    if proof.get("evidence_id") not in memory.get("evidence_ids", []):
        return False
    supersession = proof.get("supersession")
    if isinstance(supersession, dict) and supersession.get("superseded_by"):
        return False

    def intact(ref: Any) -> bool:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("sha256"), str):
            return False
        try:
            return any(path.is_file() and sha256(path) == ref["sha256"]
                       for path in _artifact_candidates(root, ref["path"]))
        except OSError:
            return False

    refs = proof.get("artifact_refs")
    if not isinstance(refs, list) or not refs or not all(intact(ref) for ref in refs):
        return False
    if not intact(proof.get("code_artifact")):
        return False
    # Historical proofs may name multiple implementation and verifier files
    # (for example validation_code_artifact). Every declared code identity
    # must still match, not just the primary implementation file.
    for key, ref in proof.items():
        if key != "code_artifact" and (key.endswith("_code_artifact") or key == "regression_test_artifact") and not intact(ref):
            return False
    return True


def _is_superseded(memory: dict[str, Any]) -> bool:
    """Treat recorded supersession as terminal even if lifecycle status drifts."""
    if memory.get("status") == "superseded" or memory.get("superseded_by"):
        return True
    for field in ("supersession_proof", "downstream_proof"):
        proof = memory.get(field)
        if not isinstance(proof, dict):
            continue
        relation = proof.get("supersession") if field == "downstream_proof" else proof
        if isinstance(relation, dict) and relation.get("superseded_by"):
            return True
    return False


def retrieve(
    root: str | Path | None,
    stage: str | None = None,
    limit: int = 8,
    statuses: set[str] | None = None,
    require_attested: bool = False,
) -> list[dict[str, Any]]:
    if not root or not (Path(root) / "memory").is_dir(): return []
    rank = {"downstreamed": 4, "active": 3, "verified": 2, "candidate": 1}
    rows = []
    for path in (Path(root) / "memory").glob("*.json"):
        item = json.loads(path.read_text())
        if _is_superseded(item): continue
        if stage and item.get("owner_stage") != stage: continue
        if item.get("status") not in rank: continue
        if statuses is not None and item.get("status") not in statuses: continue
        if require_attested:
            proof = item.get("downstream_proof") or {}
            if (item.get("status") != "downstreamed"
                    or not isinstance(proof, dict)
                    or not isinstance(proof.get("evidence_id"), str)
                    or not _has_intact_accepted_evidence(Path(root), item, proof["evidence_id"])
                    or not _has_current_qualified_proof(Path(root), item)):
                continue
        rows.append(item)
    rows.sort(key=lambda x: (rank[x["status"]], x.get("accepted_count", 0), len(x.get("evidence_ids", []))), reverse=True)
    return rows[:limit]


def generation_guidance(config: dict[str, Any], stage: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return only measured stage-specific experience for proactive generation guidance."""
    root = config.get("knowledge", {}).get("path") or config.get("refinement", {}).get("knowledge_path")
    return retrieve(root, stage=stage, limit=limit, statuses={"downstreamed"}, require_attested=True)


def _tokens(value: Any) -> set[str]:
    return {
        item for item in re.split(r"[^a-z0-9]+", str(value or "").casefold())
        if len(item) >= 3
    }


def _evidence_summary(root: Path, memory: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    rows = []
    for evidence_id in reversed(memory.get("evidence_ids", [])):
        path = root / "evidence" / f"{evidence_id}.json"
        if not path.is_file():
            continue
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        assessment = item.get("assessment") or {}
        rows.append({
            "evidence_id": evidence_id,
            "sample_id": item.get("sample_id"),
            "round_id": item.get("round_id"),
            "accepted": item.get("accepted"),
            "local_pixel_delta": assessment.get("local_pixel_delta"),
            "local_edge_delta": assessment.get("local_edge_delta"),
            "global_pixel_delta": assessment.get("global_pixel_delta"),
            "rejection_reason": assessment.get("rejection_reason") or assessment.get("error"),
        })
        if len(rows) >= limit:
            break
    return rows


def refinement_guidance(
    root: str | Path | None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    previous_attempts: list[dict[str, Any]] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Retrieve bounded, problem-related repair suggestions for refinement only.

    Candidate records remain explicitly labelled and are accompanied by compact
    measured history. Superseded records are excluded by ``retrieve``.
    """
    if not root or limit <= 0:
        return []
    knowledge_root = Path(root)
    attempts = previous_attempts or []
    owner_stages = {str(item.get("owner_stage")) for item in attempts if item.get("owner_stage")}
    mechanisms = {str(item.get("mechanism")) for item in attempts if item.get("mechanism")}
    context_tokens = set()
    for candidate in candidates or []:
        context_tokens.update(_tokens(candidate.get("candidate_id")))
        for label_id in candidate.get("label_ids", []):
            context_tokens.update(_tokens(label_id))
    for mechanism in mechanisms:
        context_tokens.update(_tokens(mechanism))

    scored = []
    allowed = {"candidate_imported", "candidate", "verified", "active", "downstreamed"}
    for memory_path in sorted((knowledge_root / "memory").glob("*.json")):
        try:
            memory = json.loads(memory_path.read_text())
        except (OSError, ValueError):
            continue
        if _is_superseded(memory) or memory.get("status") not in allowed:
            continue
        memory_id = str(memory.get("memory_id") or "")
        skill_path = knowledge_root / "skills" / memory_id / "strategy.json"
        try:
            skill = json.loads(skill_path.read_text()) if skill_path.is_file() else {}
        except (OSError, ValueError):
            skill = {}
        text_tokens = _tokens(memory.get("mechanism")) | _tokens(memory_id)
        for strategy in memory.get("strategies", []):
            text_tokens.update(_tokens(strategy))
        stage_match = int(bool(owner_stages) and memory.get("owner_stage") in owner_stages)
        mechanism_match = int(bool(mechanisms) and memory.get("mechanism") in mechanisms)
        lexical_overlap = len(context_tokens & text_tokens)
        accepted_count = int(memory.get("accepted_count", 0) or 0)
        rejected_count = int(memory.get("rejected_count", 0) or 0)
        evidence_count = len(memory.get("evidence_ids", []))
        row = {
            "memory_id": memory_id,
            "skill_id": skill.get("skill_id") or memory_id,
            "skill_instructions": list(skill.get("instructions", []))[:3],
            "skill_sha256": __import__("hashlib").sha256(skill_path.read_bytes()).hexdigest() if skill_path.is_file() else None,
            "status": memory.get("status", "candidate"),
            "owner_stage": memory.get("owner_stage"),
            "mechanism": memory.get("mechanism"),
            "strategies": list(memory.get("strategies", [])),
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "evidence_count": evidence_count,
            "evidence_summary": _evidence_summary(knowledge_root, memory),
            "retrieval_relevance": {
                "owner_stage_match": bool(stage_match),
                "mechanism_match": bool(mechanism_match),
                "lexical_overlap": lexical_overlap,
            },
        }
        # Relevance leads; measured success and evidence break ties. Rejections
        # lower priority so a repeatedly failed suggestion does not crowd out
        # alternatives, while its history remains visible if selected.
        score = (
            mechanism_match, stage_match, lexical_overlap,
            accepted_count, -rejected_count, evidence_count,
            STATUS_RANK.get(memory.get("status"), -1), memory_id,
        )
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:min(limit, 8)]]


def link_evidence(root: str | Path, memory_ids: list[str], evidence_id: str) -> list[str]:
    """Idempotently attach one measured outcome to source memories and skills."""
    root = Path(root)
    evidence_path = root / "evidence" / f"{evidence_id}.json"
    evidence = json.loads(evidence_path.read_text())
    linked = []
    for memory_id in dict.fromkeys(memory_ids):
        path = root / "memory" / f"{memory_id}.json"
        if not path.is_file():
            continue
        memory = json.loads(path.read_text())
        if _is_superseded(memory):
            continue
        ids = memory.setdefault("evidence_ids", [])
        if evidence_id not in ids:
            ids.append(evidence_id)
        outcomes = []
        for item_id in ids:
            item_path = root / "evidence" / f"{item_id}.json"
            if item_path.is_file():
                try:
                    outcomes.append(json.loads(item_path.read_text()).get("accepted"))
                except (OSError, ValueError):
                    pass
        memory["accepted_count"] = sum(value is True for value in outcomes)
        memory["rejected_count"] = sum(value is False for value in outcomes)
        write_json(path, memory)
        _write_skill(root, memory)
        linked.append(memory_id)
    return linked


def merge_into(destination: str | Path, source: str | Path) -> dict[str, Any]:
    """Merge auditable knowledge across frozen runs without duplicating evidence or demoting lifecycle state."""
    destination, source = Path(destination), Path(source)
    for bucket in ("evidence", "memory", "skills"):
        (destination / bucket).mkdir(parents=True, exist_ok=True)
    copied_evidence = 0
    for path in (source / "evidence").glob("*.json"):
        target = destination / "evidence" / path.name
        if target.exists():
            if target.read_bytes() != path.read_bytes():
                raise ValueError(f"knowledge evidence identity collision: {path.name}")
            continue
        shutil.copy2(path, target)
        copied_evidence += 1
    merged_memories = 0
    for path in (source / "memory").glob("*.json"):
        incoming = json.loads(path.read_text())
        target = destination / "memory" / path.name
        if not target.exists():
            write_json(target, incoming)
            _write_skill(destination, incoming)
            merged_memories += 1
            continue
        current = json.loads(target.read_text())
        for key in ("evidence_ids", "strategies"):
            current[key] = list(dict.fromkeys(current.get(key, []) + incoming.get(key, [])))
        if STATUS_RANK.get(incoming.get("status"), -1) > STATUS_RANK.get(current.get("status"), -1):
            current["status"] = incoming["status"]
            for key in ("code_status", "downstream_proof"):
                if key in incoming:
                    current[key] = incoming[key]
        known = []
        for evidence_id in current.get("evidence_ids", []):
            evidence_path = destination / "evidence" / f"{evidence_id}.json"
            if evidence_path.exists():
                known.append(json.loads(evidence_path.read_text()).get("accepted"))
        current["accepted_count"] = sum(value is True for value in known)
        current["rejected_count"] = sum(value is False for value in known)
        write_json(target, current)
        _write_skill(destination, current)
        merged_memories += 1
    return {"copied_evidence": copied_evidence, "merged_memories": merged_memories}


def mark_downstreamed(root: str | Path, memory_id: str, evidence_id: str, proof: dict[str, Any]) -> None:
    """Promote only linked, measured evidence with intact code and probe artifacts."""
    root = Path(root); memory_path = root / "memory" / f"{memory_id}.json"
    memory = json.loads(memory_path.read_text())
    if evidence_id not in memory.get("evidence_ids", []): raise ValueError("proof evidence is not linked to memory")
    if memory.get("status") == "superseded": raise ValueError("superseded memory cannot be promoted")
    evidence = json.loads((root / "evidence" / f"{evidence_id}.json").read_text())
    assessment = evidence.get("assessment") or {}
    if evidence.get("accepted") is not True or assessment.get("accepted") is not True:
        raise ValueError("promotion requires accepted measured evidence")
    def metric(section: str, key: str) -> float:
        value = (assessment.get(section) or {}).get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"promotion measurement missing: {section}.{key}")
        return float(value)
    local_pixel = metric("after_local", "pixel_similarity") - metric("before_local", "pixel_similarity")
    local_edge = metric("after_local", "edge_iou") - metric("before_local", "edge_iou")
    global_pixel = metric("after_global", "pixel_similarity") - metric("before_global", "pixel_similarity")
    for key, calculated in (("local_pixel_delta", local_pixel), ("local_edge_delta", local_edge),
                            ("global_pixel_delta", global_pixel)):
        stated = assessment.get(key)
        if (not isinstance(stated, (int, float)) or isinstance(stated, bool)
                or not math.isfinite(stated) or abs(stated - calculated) > 0.000002):
            raise ValueError(f"promotion measurement mismatch: {key}")
    if not _measured_assessment_valid(assessment):
        raise ValueError("promotion requires a valid typed measured assessment")
    def verify_refs(refs: Any, label: str) -> None:
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"promotion requires {label} artifact references")
        for reference in refs:
            if not isinstance(reference, dict) or not reference.get("path") or not reference.get("sha256"):
                raise ValueError(f"promotion {label} artifact reference is incomplete")
            path = Path(reference["path"])
            if not path.is_absolute():
                raise ValueError(f"promotion {label} artifact path must be absolute: {path}")
            if not path.is_file() or sha256(path) != reference["sha256"]:
                raise ValueError(f"promotion {label} artifact identity mismatch: {path}")
    verify_refs(evidence.get("artifact_refs"), "evidence")
    if (not isinstance(proof, dict) or not proof.get("version") or not proof.get("scope")
            or not isinstance(proof.get("counterexamples"), list)):
        raise ValueError("promotion proof requires version, scope, and counterexamples")
    verify_refs(proof.get("artifact_refs"), "proof")
    verify_refs([proof.get("code_artifact")], "code")
    memory.update(status="downstreamed", code_status="integrated_and_measured", downstream_proof=proof)
    write_json(memory_path, memory)
    _write_skill(root, memory)


def mark_superseded(root: str | Path, memory_id: str, proof: dict[str, Any]) -> None:
    """Retain a contradicted memory for audit while removing it from retrieval."""
    root = Path(root); memory_path = root / "memory" / f"{memory_id}.json"
    memory = json.loads(memory_path.read_text())
    memory.update(status="superseded", code_status="superseded_by_measured_evidence", supersession_proof=proof)
    write_json(memory_path, memory)
    _write_skill(root, memory)


def consolidate_mechanisms(
    root: str | Path,
    canonical_memory_id: str,
    alias_memory_ids: list[str],
    proof: dict[str, Any],
) -> dict[str, Any]:
    """Merge historical aliases without losing evidence or retrievable lifecycle state."""
    root = Path(root)
    if not proof.get("version") or not proof.get("reason") or not proof.get("artifact_refs"):
        raise ValueError("consolidation requires version, reason, and artifact references")
    for reference in proof["artifact_refs"]:
        if not isinstance(reference, dict) or not reference.get("path") or not reference.get("sha256"):
            raise ValueError("consolidation artifact reference lacks path or SHA-256")
        path = Path(reference["path"])
        if not path.is_file() or sha256(path) != reference["sha256"]:
            raise ValueError(f"consolidation artifact identity mismatch: {path}")
    target_path = root / "memory" / f"{canonical_memory_id}.json"
    target = json.loads(target_path.read_text())
    aliases = list(dict.fromkeys(alias_memory_ids))
    if canonical_memory_id in aliases:
        raise ValueError("canonical memory cannot supersede itself")
    pending = []
    for alias_id in aliases:
        alias_path = root / "memory" / f"{alias_id}.json"
        alias = json.loads(alias_path.read_text())
        if alias.get("owner_stage") != target.get("owner_stage"):
            raise ValueError(f"cross-stage mechanism consolidation: {alias_id}")
        pending.append((alias_id, alias_path, alias))
    for item in [target] + [alias for _, _, alias in pending]:
        for evidence_id in item.get("evidence_ids", []):
            if not (root / "evidence" / f"{evidence_id}.json").is_file():
                raise ValueError(f"missing evidence for {item['memory_id']}: {evidence_id}")
    merged: list[str] = []
    for alias_id, alias_path, alias in pending:
        for key in ("evidence_ids", "strategies"):
            target[key] = list(dict.fromkeys(target.get(key, []) + alias.get(key, [])))
        if alias.get("status") != "superseded" and STATUS_RANK.get(alias.get("status"), -1) > STATUS_RANK.get(target.get("status"), -1):
            target["status"] = alias["status"]
            for key in ("code_status", "downstream_proof"):
                if key in alias:
                    target[key] = alias[key]
        alias.update(status="superseded", code_status="superseded_by_measured_evidence", supersession_proof={
            **proof, "superseded_by": canonical_memory_id,
        })
        write_json(alias_path, alias)
        _write_skill(root, alias)
        merged.append(alias_id)
    target["supersedes"] = list(dict.fromkeys(target.get("supersedes", []) + merged))
    target["consolidation_proof"] = proof
    known = [
        json.loads((root / "evidence" / f"{evidence_id}.json").read_text()).get("accepted")
        for evidence_id in target.get("evidence_ids", [])
    ]
    target["accepted_count"] = sum(value is True for value in known)
    target["rejected_count"] = sum(value is False for value in known)
    write_json(target_path, target)
    _write_skill(root, target)
    return {"canonical_memory_id": canonical_memory_id, "superseded": merged, "evidence_count": len(target.get("evidence_ids", []))}
