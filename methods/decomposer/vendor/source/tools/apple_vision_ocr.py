#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    request = json.load(sys.stdin)
    script = Path(__file__).resolve().with_name("ocr_apple_vision.swift")
    with tempfile.TemporaryDirectory(prefix="decomposer-apple-ocr-") as name:
        image = Path(name) / "input.png"
        image.write_bytes(base64.b64decode(request["image"]))
        process = subprocess.run(["/usr/bin/env", "swift", str(script), str(image)], capture_output=True, text=True, timeout=300)
    if process.returncode:
        print(process.stderr, file=sys.stderr); raise SystemExit(process.returncode)
    raw = json.loads(process.stdout)
    items = [{"id": f"ocr{index:04d}", "bbox": [item["x"], item["y"], item["w"], item["h"]], "text": item["text"], "confidence": item["confidence"]} for index, item in enumerate(raw.get("items", []))]
    print(json.dumps({"schema_version": "decomposer.ocr-result.v2", "provider": "apple-vision", "items": items}, ensure_ascii=False))


if __name__ == "__main__": main()
