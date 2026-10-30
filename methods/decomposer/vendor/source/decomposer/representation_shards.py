"""Optional parallel model transport for the unchanged Representation IR."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time
from typing import Any

from PIL import Image

from .common import write_json
from .model import fixture_or_model
from .schemas import REPRESENTATION_PROPOSAL_SCHEMA


_CATEGORY_WEIGHT = {
    "chart": 8,
    "equation": 3,
    "shape": 2,
    "text": 1,
    "arrow": 1,
    "image": 1,
}


def _weight(obj: dict[str, Any]) -> int:
    return _CATEGORY_WEIGHT.get(str(obj.get("category", "")), 2)


def partition_objects(objects: list[dict[str, Any]], count: int) -> list[list[dict[str, Any]]]:
    """Spatially split semantic owners with approximately equal output work."""
    if not objects:
        return []
    count = max(1, min(int(count), len(objects)))

    def split(rows: list[dict[str, Any]], groups: int) -> list[list[dict[str, Any]]]:
        if groups == 1:
            return [rows]
        centers = [
            (item["bbox"][0] + item["bbox"][2] / 2,
             item["bbox"][1] + item["bbox"][3] / 2)
            for item in rows
        ]
        axis = 0 if max(p[0] for p in centers) - min(p[0] for p in centers) >= max(p[1] for p in centers) - min(p[1] for p in centers) else 1
        ordered = sorted(rows, key=lambda item: (
            item["bbox"][axis] + item["bbox"][axis + 2] / 2,
            str(item.get("id", "")),
        ))
        left_groups = groups // 2
        right_groups = groups - left_groups
        target = sum(_weight(item) for item in ordered) * left_groups / groups
        running = 0
        choices = []
        for index in range(1, len(ordered)):
            running += _weight(ordered[index - 1])
            if index >= left_groups and len(ordered) - index >= right_groups:
                choices.append((abs(running - target), index))
        cut = min(choices)[1]
        return split(ordered[:cut], left_groups) + split(ordered[cut:], right_groups)

    return split(objects, count)


def _usage(call_dir: Path) -> dict[str, int]:
    events = call_dir / "events.jsonl"
    if not events.is_file():
        return {}
    for line in reversed(events.read_text().splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            return {key: int(value) for key, value in event.get("usage", {}).items() if isinstance(value, (int, float))}
    return {}


def _image_region(rows: list[dict[str, Any]], canvas: dict[str, Any]) -> list[int]:
    """Bound a shard image without changing any global or component coordinates."""
    width, height = int(canvas["width"]), int(canvas["height"])
    padding = max(24, round(min(width, height) * 0.025))
    left = max(0, int(min(row["bbox"][0] for row in rows)) - padding)
    top = max(0, int(min(row["bbox"][1] for row in rows)) - padding)
    right = min(width, int(max(row["bbox"][0] + row["bbox"][2] for row in rows) + 0.999) + padding)
    bottom = min(height, int(max(row["bbox"][1] + row["bbox"][3] for row in rows) + 0.999) + padding)
    return [left, top, max(1, right - left), max(1, bottom - top)]


def generate(
    image: str | Path,
    recognition: dict[str, Any],
    asset_decisions: dict[str, Any],
    config: dict[str, Any],
    calls_dir: str | Path,
    prompt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call the same Representation contract in isolated groups, then merge it."""
    call_root = Path(calls_dir) / "representation"
    call_root.mkdir(parents=True, exist_ok=True)
    settings = config.get("representation", {})
    image_scope = settings.get("image_scope", "full")
    if image_scope not in {"full", "shard_crop"}:
        raise ValueError(f"unsupported representation image_scope: {image_scope}")
    groups = partition_objects(recognition["objects"], int(settings.get("shards", 4)))
    concurrency = max(1, min(int(settings.get("max_concurrency", 4)), len(groups)))
    decisions = {row.get("label_id"): row for row in asset_decisions.get("decisions", [])}
    overview = [
        {"id": row["id"], "category": row["category"], "bbox": row["bbox"]}
        for row in recognition["objects"]
    ]
    payload = {
        "recognition": recognition,
        "asset_decisions": asset_decisions,
        "representation_strategy": settings,
    }
    write_json(call_root / "input.json", payload)
    shard_prompt = prompt + (
        "\nThis call owns only the objects in INPUT.recognition.objects. "
        "Return exactly one component for each of those label IDs, and no others. "
        "INPUT.context_objects is a position-only index for cross-object context, "
        "not a set of objects to represent.\n"
    )
    if image_scope == "shard_crop":
        shard_prompt += (
            "The attached image is a source-resolution crop. INPUT.image_region gives its "
            "global x, y, width, and height within INPUT.recognition.canvas. "
            "Object bboxes remain global; primitive coordinates remain component-local.\n"
        )

    def invoke(index: int, rows: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
        assigned = {row["id"] for row in rows}
        shard_recognition = {**recognition, "objects": rows}
        shard_assets = {
            **asset_decisions,
            "decisions": [decisions[row["id"]] for row in rows if row["id"] in decisions],
        }
        call_dir = call_root / "shards" / f"shard-{index:03d}"
        region = None
        shard_image = image
        if image_scope == "shard_crop":
            region = _image_region(rows, recognition["canvas"])
            call_dir.mkdir(parents=True, exist_ok=True)
            shard_image = call_dir / "image-crop.png"
            x, y, width, height = region
            with Image.open(image) as source:
                source.crop((x, y, x + width, y + height)).save(shard_image)
        shard_payload = {
            "recognition": shard_recognition, "asset_decisions": shard_assets,
            "context_objects": overview, "applicable_memories": [],
        }
        if region is not None:
            shard_payload["image_region"] = region
        proposal, metadata = fixture_or_model(
            "representation", shard_prompt,
            shard_payload,
            REPRESENTATION_PROPOSAL_SCHEMA, shard_image, config, call_dir, None,
        )
        components = proposal.get("components", [])
        labels = [item.get("label_id") for item in components if isinstance(item, dict)]
        if metadata.get("status") != "complete" or metadata.get("fallback_used"):
            raise RuntimeError(f"representation shard {index} model call failed: {metadata.get('error', metadata.get('status'))}")
        if len(labels) != len(rows) or len(set(labels)) != len(rows) or set(labels) != assigned:
            raise ValueError(f"representation shard {index} returned missing, duplicate, or foreign labels")
        return index, components, {**metadata, "usage": _usage(call_dir),
                                   "image_region": region, "label_ids": [row["id"] for row in rows]}

    started = time.monotonic()
    results: dict[int, list[dict[str, Any]]] = {}
    reports: dict[int, dict[str, Any]] = {}
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(invoke, i, rows): i for i, rows in enumerate(groups, 1)}
            for future in as_completed(futures):
                index, components, report = future.result()
                results[index] = components
                reports[index] = report
        supplied = {item["label_id"]: item for index in sorted(results) for item in results[index]}
        proposal = {"components": [supplied[row["id"]] for row in recognition["objects"]]}
        metadata = {
            "stage": "representation", "status": "complete", "fallback_used": False,
            "backend": config.get("model", {}).get("backend", "codex-cli"),
            "model_name": config.get("model", {}).get("name"),
            "strategy": "parallel_shards", "shard_count": len(groups),
            "max_concurrency": concurrency, "image_scope": image_scope,
            "seconds": round(time.monotonic() - started, 3),
            "shards": [reports[index] for index in sorted(reports)],
        }
    except Exception as exc:
        metadata = {
            "stage": "representation", "status": "failed", "fallback_used": False,
            "strategy": "parallel_shards", "seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }
        write_json(call_root / "metadata.json", metadata)
        raise
    write_json(call_root / "metadata.json", metadata)
    return proposal, metadata
