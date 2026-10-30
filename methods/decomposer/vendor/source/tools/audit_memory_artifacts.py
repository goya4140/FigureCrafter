#!/usr/bin/env python3
"""Read-only audit of historical downstreamed memory artifact identities."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from decomposer.common import sha256, write_json


def candidate_paths(raw_path: str, project_root: Path) -> list[Path]:
    """Resolve the two historical relative-path conventions without guessing."""
    raw = Path(raw_path)
    paths = [raw] if raw.is_absolute() else [project_root / raw, project_root / "Decomposer" / raw]
    return list(dict.fromkeys(path.resolve() for path in paths))


def audit(knowledge_root: Path, project_root: Path) -> dict[str, Any]:
    records = []
    memories = sorted((knowledge_root / "memory").glob("*.json"))
    downstreamed_count = 0
    evidence_ids: set[str] = set()
    for memory_path in memories:
        memory = json.loads(memory_path.read_text())
        if memory.get("status") != "downstreamed":
            continue
        downstreamed_count += 1
        memory_id = memory["memory_id"]
        for evidence_id in memory.get("evidence_ids", []):
            evidence_ids.add(evidence_id)
            evidence_path = knowledge_root / "evidence" / f"{evidence_id}.json"
            if not evidence_path.is_file():
                records.append({"memory_id": memory_id, "evidence_id": evidence_id,
                                "status": "missing_evidence", "raw_path": None})
                continue
            evidence = json.loads(evidence_path.read_text())
            for ref in evidence.get("artifact_refs") or []:
                raw_path = ref.get("path")
                expected = ref.get("sha256")
                if not isinstance(raw_path, str) or not raw_path or not isinstance(expected, str):
                    records.append({"memory_id": memory_id, "evidence_id": evidence_id,
                                    "status": "malformed_reference", "raw_path": raw_path})
                    continue
                existing = [path for path in candidate_paths(raw_path, project_root) if path.is_file()]
                observed = [{"path": str(path), "sha256": sha256(path)} for path in existing]
                matching = [item for item in observed if item["sha256"] == expected]
                status = "hash_match" if matching else ("hash_mismatch" if existing else "missing_artifact")
                records.append({"memory_id": memory_id, "evidence_id": evidence_id,
                                "status": status, "raw_path": raw_path,
                                "expected_sha256": expected, "observed": observed,
                                "matched_path": matching[0]["path"] if matching else None})
    counts = Counter(record["status"] for record in records)
    return {"schema_version": "decomposer.legacy-memory-artifact-audit.v1",
            "knowledge_root": str(knowledge_root.resolve()),
            "project_root": str(project_root.resolve()),
            "downstreamed_memory_count": downstreamed_count,
            "linked_evidence_count": len(evidence_ids),
            "reference_count": len(records),
            "status_counts": dict(sorted(counts.items())),
            "affected_memory_ids": sorted({record["memory_id"] for record in records
                                           if record["status"] != "hash_match"}),
            "records": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = audit(args.knowledge, args.project)
    write_json(args.out, result)
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
