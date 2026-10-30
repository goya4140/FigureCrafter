import json
from pathlib import Path

import pytest

from tools.audit_memory_eligibility import audit


def test_audit_excludes_candidate_and_superseded_even_with_stale_status(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "candidate.json").write_text(json.dumps({
        "memory_id": "candidate", "owner_stage": "layout", "mechanism": "alignment",
        "status": "candidate", "evidence_ids": [],
    }))
    (memory_dir / "superseded.json").write_text(json.dumps({
        "memory_id": "superseded", "owner_stage": "layout", "mechanism": "alignment-old",
        "status": "downstreamed", "superseded_by": "candidate", "evidence_ids": [],
    }))
    report = audit(tmp_path)
    assert report["memory_count"] == 2
    assert report["eligible_count"] == 0
    rows = {row["memory_id"]: row for row in report["rows"]}
    assert "not_downstreamed" in rows["candidate"]["exclusion_reasons"]
    assert "superseded" in rows["superseded"]["exclusion_reasons"]


def test_audit_fails_closed_for_missing_store(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="knowledge memory directory missing"):
        audit(tmp_path)
