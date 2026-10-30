#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from decomposer.common import write_json


COMPATIBLE = {
    "recognition-positive-bounds", "recognition-positive-style", "recognition-whitespace-in-hex-color",
    "recognition-prose-in-color-field", "recognition-nonzero-path", "layout-canvas-placement",
    "layout-rotation-components", "representation-complete-labels", "representation-primitive-ids",
    "representation-image-routing", "representation-short-hex-color",
    "representation-nonfinite-derived-coordinates", "representation-custom-geometry-as-preset",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import compatible historical problem memories as non-active v7 candidates.")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[4] / "01_methods/Figure2PPTX/v6.1/experiments/v6.1-problem-memory-20260917/knowledge-v2"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source, out = Path(args.source), Path(args.out)
    imported = []
    for path in sorted((source / "memory").glob("*.json")):
        raw = json.loads(path.read_text())
        source_id = str(raw.get("memory_id") or path.stem)
        if source_id not in COMPATIBLE: continue
        stage = str(raw.get("stage") or source_id.split("-", 1)[0])
        mechanism = str(raw.get("mechanism") or source_id[len(stage) + 1:])
        strategies = []
        skill = source / "skills" / source_id / "strategy.json"
        if skill.exists():
            payload = json.loads(skill.read_text())
            for key in ("instruction", "strategy", "lesson"):
                if isinstance(payload.get(key), str): strategies.append(payload[key])
            for row in payload.get("proposals", []):
                if isinstance(row, dict) and isinstance(row.get("instruction"), str): strategies.append(row["instruction"])
        memory_id = f"{stage}-{mechanism}"
        memory = {
            "schema_version": "decomposer.memory.v1", "memory_id": memory_id,
            "owner_stage": stage, "mechanism": mechanism, "status": "candidate_imported",
            "evidence_ids": list(raw.get("evidence_ids", [])), "accepted_count": 0,
            "rejected_count": 0, "strategies": list(dict.fromkeys(strategies)),
            "source": str(path), "compatibility": "mechanism_only_not_yet_v7_validated",
        }
        write_json(out / "memory" / f"{memory_id}.json", memory)
        skill_dir = out / "skills" / memory_id; skill_dir.mkdir(parents=True, exist_ok=True)
        write_json(skill_dir / "strategy.json", {"skill_id": memory_id, "memory_id": memory_id, "status": "candidate_imported", "instructions": memory["strategies"], "source": str(skill) if skill.exists() else None})
        (skill_dir / "SKILL.md").write_text(f"---\nname: {memory_id[:63]}\ndescription: Imported candidate for {mechanism}; requires v7 probe validation.\n---\n\n" + "\n\n".join(memory["strategies"]) + "\n")
        imported.append(memory_id)
    write_json(out / "bootstrap-report.json", {"imported_count": len(imported), "memory_ids": imported, "source": str(source)})
    print(json.dumps({"imported_count": len(imported), "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__": main()
