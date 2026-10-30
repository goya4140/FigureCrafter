#!/usr/bin/env python3
"""Read-only, fail-closed inventory of memory eligibility for handoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer import VERSION
from decomposer.knowledge import (
    _has_current_qualified_proof, _has_intact_accepted_evidence, _is_superseded,
)


def audit(root: Path) -> dict:
    root = root.resolve()
    if not (root / "memory").is_dir():
        raise FileNotFoundError(f"knowledge memory directory missing: {root / 'memory'}")
    rows = []
    for path in sorted((root / "memory").glob("*.json")):
        memory = json.loads(path.read_text())
        status = memory.get("status")
        proof = memory.get("downstream_proof")
        evidence_id = proof.get("evidence_id") if isinstance(proof, dict) else None
        superseded = _is_superseded(memory)
        evidence_ok = isinstance(evidence_id, str) and _has_intact_accepted_evidence(root, memory, evidence_id)
        proof_ok = _has_current_qualified_proof(root, memory)
        reasons = []
        if superseded:
            reasons.append("superseded")
        if status != "downstreamed":
            reasons.append("not_downstreamed")
        if not evidence_ok:
            reasons.append("accepted_measured_evidence_missing_or_stale")
        if not proof_ok:
            reasons.append("current_scoped_proof_missing_or_stale")
        rows.append({
            "memory_id": memory.get("memory_id"),
            "owner_stage": memory.get("owner_stage"),
            "mechanism": memory.get("mechanism"),
            "status": status,
            "evidence_id": evidence_id,
            "proof_version": proof.get("version") if isinstance(proof, dict) else None,
            "superseded": superseded,
            "accepted_measured_evidence_ok": bool(evidence_ok),
            "current_scoped_proof_ok": proof_ok,
            "eligible_for_proactive_retrieval": not reasons,
            "exclusion_reasons": reasons,
        })
    status_counts: dict[str, int] = {}
    for row in rows:
        key = str(row["status"])
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "schema_version": "decomposer.memory-eligibility-audit.v1",
        "knowledge_root": str(root), "candidate_code_version": VERSION,
        "memory_count": len(rows), "status_counts": status_counts,
        "eligible_count": sum(row["eligible_for_proactive_retrieval"] for row in rows),
        "eligible_memory_ids": [row["memory_id"] for row in rows if row["eligible_for_proactive_retrieval"]],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("knowledge_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.knowledge_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
