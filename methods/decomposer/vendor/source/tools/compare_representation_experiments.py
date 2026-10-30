"""Compare Representation experiments against the same frozen upstream."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics


UPSTREAM = (
    "input/input.json",
    "input/normalized.png",
    "observations/candidates.json",
    "observations/evidence-ir.json",
    "observations/ocr.stdout.log",
    "observations/ocr.stderr.log",
    "recognition/recognition.json",
    "calls/recognition/metadata.json",
    "representation/asset-decisions.json",
)


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def usage(events: Path) -> dict[str, int]:
    if not events.exists():
        return {}
    for line in reversed(events.read_text().splitlines()):
        event = json.loads(line)
        if event.get("type") == "turn.completed":
            return event.get("usage", {})
    return {}


def event_failures(events: Path) -> int:
    if not events.exists():
        return 0
    count = 0
    for line in events.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        count += event.get("type") in {"error", "turn.failed"}
    return count


def check_failure_count(check: dict) -> int | None:
    detail = check.get("detail")
    if not isinstance(detail, dict) or not isinstance(detail.get("failures"), list):
        return None
    return len(detail["failures"])


def summarize(run: Path, output_name: str) -> dict:
    call = run / "calls/representation"
    metadata = read(call / "metadata.json")
    if metadata.get("strategy") == "parallel_shards":
        shards = sorted((call / "shards").glob("shard-*"))
        usages = [usage(shard / "events.jsonl") for shard in shards]
        failures = sum(event_failures(shard / "events.jsonl") for shard in shards)
        call_statuses = [read(shard / "metadata.json").get("status") for shard in shards]
        first_call_metadata = read(shards[0] / "metadata.json")
        call_artifacts = [{name: digest(shard / name) for name in ("prompt.txt", "schema.json", "input.json", "response.txt")}
                          for shard in shards]
        calls = len(shards)
    else:
        usages = [usage(call / "events.jsonl")]
        failures = event_failures(call / "events.jsonl")
        call_statuses = [metadata.get("status")]
        first_call_metadata = metadata
        call_artifacts = [{name: digest(call / name) for name in ("prompt.txt", "schema.json", "input.json", "response.txt")
                           if (call / name).is_file()}]
        calls = 1
    output = run / output_name
    validation = read(output / "validation.json")
    report = read(output / "report.json")
    canonical = read(run / "representation/representation.json")
    normalization = read(run / "representation/canonicalization-report.json")
    return {
        "run": str(run.resolve()),
        "upstream_sha256": {name: digest(run / name) for name in UPSTREAM},
        "experiment_config_sha256": digest(run / "experiment-config.json")
        if (run / "experiment-config.json").is_file() else None,
        "representation": {
            "status": metadata.get("status"),
            "model_name": metadata.get("model_name"),
            "service_tier_requested": metadata.get("service_tier_requested", first_call_metadata.get("service_tier_requested")),
            "fast_mode_requested": metadata.get("fast_mode_requested", first_call_metadata.get("fast_mode_requested")),
            "strategy": metadata.get("strategy", "monolithic"),
            "seconds": metadata.get("seconds"),
            "model_calls": calls,
            "model_call_statuses": call_statuses,
            "call_artifact_sha256": call_artifacts,
            "model_error_events": failures,
            "fallback_used": metadata.get("fallback_used"),
            "input_tokens": sum(row.get("input_tokens", 0) for row in usages),
            "cached_input_tokens": sum(row.get("cached_input_tokens", 0) for row in usages),
            "output_tokens": sum(row.get("output_tokens", 0) for row in usages),
            "components": len(canonical["components"]),
            "primitives": sum(len(row["primitives"]) for row in canonical["components"]),
            "assets": len(canonical["assets"]),
            "degraded": canonical["degraded"],
            "canonical_repairs": normalization["repair_count"],
        },
        "from_representation_start_to_validation_seconds": round(
            (output / "validation.json").stat().st_mtime - (call / "input.json").stat().st_mtime, 3,
        ),
        "from_representation_start_to_final_status_seconds": round(
            (run / "status.json").stat().st_mtime - (call / "input.json").stat().st_mtime, 3,
        ),
        "validation": {
            "passed": validation["passed"],
            "failure_codes": [row["code"] for row in validation["failures"]],
            "checks_passed": {name: bool(value.get("passed"))
                              for name, value in validation["checks"].items()},
            "check_failure_counts": {
                name: count
                for name, value in validation["checks"].items()
                if (count := check_failure_count(value)) is not None
            },
            "visual": validation["visual"]["metrics"],
            "structure": report["structure"],
        },
    }


def variation(runs: list[dict]) -> dict[str, dict[str, float]]:
    return {
        name: {"median": statistics.median(values), "min": min(values), "max": max(values)}
        for name, values in {
            "representation_seconds": [item["representation"]["seconds"] for item in runs],
            "from_representation_start_to_final_status_seconds": [
                item["from_representation_start_to_final_status_seconds"] for item in runs],
            "input_tokens": [item["representation"]["input_tokens"] for item in runs],
            "cached_input_tokens": [item["representation"]["cached_input_tokens"] for item in runs],
            "output_tokens": [item["representation"]["output_tokens"] for item in runs],
            "pixel_similarity": [item["validation"]["visual"]["pixel_similarity"] for item in runs],
            "edge_iou": [item["validation"]["visual"]["edge_iou"] for item in runs],
            "rendered_native_text_ink_failures": [
                item["validation"]["check_failure_counts"].get("rendered_native_text_ink", 0)
                for item in runs],
        }.items()
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidates", type=Path, nargs="+")
    parser.add_argument("--baseline-output", default="output")
    parser.add_argument("--baseline-repeat", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    baseline = summarize(args.baseline, args.baseline_output)
    baselines = [baseline] + [summarize(path, "output") for path in args.baseline_repeat]
    candidates = [summarize(path, "output") for path in args.candidates]
    for run in baselines[1:] + candidates:
        if run["upstream_sha256"] != baseline["upstream_sha256"]:
            raise RuntimeError(f"upstream hash mismatch: {run['run']}")
    result = {"baseline": baseline, "baselines": baselines, "candidates": candidates,
              "upstream_identical": True, "baseline_variation": variation(baselines)}
    if candidates:
        result["candidate_variation"] = variation(candidates)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for entry in baselines + candidates:
        r = entry["representation"]
        v = entry["validation"]
        print(f"{Path(entry['run']).name}: {r['seconds']}s, {r['model_calls']} calls, "
              f"{r['input_tokens']} input / {r['output_tokens']} output tokens, "
              f"visual={v['visual']}, failures={v['failure_codes']}")
