from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from decomposer.common import sha256


def _module():
    path = Path(__file__).resolve().parents[1] / "tools" / "replay_same_id_full_chain.py"
    spec = importlib.util.spec_from_file_location("replay_same_id_full_chain", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replay_preflight_rejects_historical_source_drift(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"original")
    baseline = tmp_path / "baseline"
    (baseline / "input").mkdir(parents=True)
    (baseline / "input/input.json").write_text(json.dumps({
        "source": str(source), "source_sha256": sha256(source),
    }))
    module = _module()
    assert module._verified_source(baseline) == (source, sha256(source))
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source image hash mismatch"):
        module._verified_source(baseline)
