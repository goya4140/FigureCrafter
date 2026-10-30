from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
from typing import Any

from .common import write_json


def _normalized_items(name: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raw_items = payload.get("regions") if name == "sam" else payload.get("text_regions")
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["source"] = "sam3" if name == "sam" else "ocr"
        if name == "sam":
            item["label"] = str(item.get("role") or item.get("label") or item.get("prompt") or "unknown").strip().lower()
        else:
            item["label"] = "text"
        items.append(item)
    return items


def collect(image: str | Path, canvas: tuple[int, int], config: dict[str, Any], directory: str | Path) -> dict[str, Any]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"canvas": list(canvas), "items": [], "sources": {}}
    for name in ("sam", "ocr"):
        detector = config.get("detectors", {}).get(name)
        if not detector or not detector.get("command"):
            results["sources"][name] = {"status": "disabled"}
            continue
        request = {
            "image": base64.b64encode(Path(image).read_bytes()).decode(),
            "schema_version": f"decomposer.{name}-request.v2",
            "canvas": {"width": canvas[0], "height": canvas[1]},
            "options": detector.get("options", {}),
        }
        try:
            process = subprocess.run(
                detector["command"], input=json.dumps(request), capture_output=True, text=True,
                timeout=detector.get("timeout_seconds", 600),
            )
            (directory / f"{name}.stdout.log").write_text(process.stdout)
            (directory / f"{name}.stderr.log").write_text(process.stderr)
            if process.returncode:
                raise RuntimeError(f"exit code {process.returncode}")
            payload = json.loads(process.stdout)
            items = _normalized_items(name, payload)
            results["items"].extend(items)
            results["sources"][name] = {
                "status": "ready", "count": len(items),
                "schema_version": payload.get("schema_version") if isinstance(payload, dict) else None,
            }
        except Exception as exc:
            results["sources"][name] = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    write_json(directory / "candidates.json", results)
    return results
