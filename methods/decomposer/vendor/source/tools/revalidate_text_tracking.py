#!/usr/bin/env python3
"""Replay audited native character-spacing calibration from a prior run."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_first100 import audit_run
from decomposer import VERSION
from decomposer.common import read_json, sha256, write_json
from decomposer.text_tracking_registration import run as run_text_tracking_registration
from decomposer.validation import validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source_run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    shutil.copytree(source, out)
    config = read_json(args.config)
    write_json(out / "revalidation-identity.json", {
        "version": VERSION, "source_run": str(source),
        "source_scene_sha256": sha256(source / "output" / "scene.json"),
        "source_pptx_sha256": sha256(source / "output" / "editable.pptx"),
        "config_sha256": sha256(args.config),
    })
    decision = run_text_tracking_registration(out, out / "input" / "normalized.png", config)
    validation = validate_run(out, out / "input" / "normalized.png", config)
    status = read_json(out / "status.json")
    status["version"] = VERSION
    status["text_tracking_registration"] = decision
    status["status"] = "render_success" if validation["passed"] else "validation_failed"
    status["validation"] = {
        "passed": validation["passed"], "visual": validation["visual"],
        "failure_count": len(validation["failures"]), "warning_count": len(validation["warnings"]),
    }
    status.setdefault("artifacts", {})["text_tracking_registration"] = "text-tracking-registration/decision.json"
    write_json(out / "status.json", status)
    audit = audit_run(out)
    write_json(out / "independent-audit.json", audit)
    print({"validation_passed": validation["passed"], "audit_passed": audit["passed"],
           "eligible": len(decision["eligible"]), "accepted": decision["accepted"],
           "selected_candidate_id": decision["selected_candidate_id"],
           "visual": validation["visual"].get("metrics"),
           "audit_failures": [row["code"] for row in audit["failures"]], "out": str(out)})


if __name__ == "__main__":
    main()
