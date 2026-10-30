from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.layout import canonicalize
from decomposer.model import (
    ModelExecutionBlocked, ModelRequestError, _model_environment,
    _run_model_process, invoke_json,
)
from decomposer.schemas import (
    LAYOUT_PROPOSAL_SCHEMA,
    RECOGNITION_PROPOSAL_SCHEMA,
    REFINEMENT_SCHEMA,
    REPRESENTATION_PROPOSAL_SCHEMA,
    omit_null_fields,
)


class ModelContractTest(unittest.TestCase):
    def test_all_wire_nodes_are_explicit_closed_and_required(self):
        def check(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                self.assertIs(node["additionalProperties"], False)
                self.assertEqual(set(node["required"]), set(node["properties"]))
                for child in node["properties"].values():
                    check(child)
            if node.get("type") == "array":
                check(node["items"])
            for child in node.get("anyOf", []):
                check(child)

        for schema in (RECOGNITION_PROPOSAL_SCHEMA, REPRESENTATION_PROPOSAL_SCHEMA, LAYOUT_PROPOSAL_SCHEMA, REFINEMENT_SCHEMA):
            check(schema)

    def test_nullable_place_preserves_existing_values(self):
        wire = {"operations": [{
            "op": "place", "label_id": "a", "bbox": None,
            "rotation_deg": 90, "factor": None, "label_ids": None,
        }]}
        recognition = {"canvas": {"width": 100, "height": 100}, "objects": [{"id": "a", "bbox": [10, 20, 30, 40]}]}
        layout, _ = canonicalize(omit_null_fields(wire), recognition, {"components": [{"label_id": "a"}]})
        self.assertEqual(layout["placements"][0]["bbox"], [10, 20, 30, 40])
        self.assertEqual(layout["placements"][0]["rotation_deg"], 90)
        self.assertEqual(omit_null_fields({"a": False, "b": 0, "c": [], "d": None}), {"a": False, "b": 0, "c": []})

    def test_request_rejection_is_not_content_fallback(self):
        result = subprocess.CompletedProcess(
            [], 1, stdout=json.dumps({"type": "error", "message": "invalid_json_schema"}), stderr=""
        )
        with tempfile.TemporaryDirectory() as temp, patch("decomposer.model._run_model_process", return_value=result):
            directory = Path(temp)
            with self.assertRaises(ModelRequestError):
                invoke_json("recognition", "test", {}, RECOGNITION_PROPOSAL_SCHEMA, directory / "image.png", {}, directory)
            metadata = json.loads((directory / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "request_failed")
            self.assertFalse(metadata["fallback_used"])

    def test_usage_limit_stops_execution_without_fallback(self):
        result = subprocess.CompletedProcess(
            [], 1, stdout='{"type":"error"}',
            stderr="You've hit your usage limit. Try again next week.",
        )
        with tempfile.TemporaryDirectory() as temp, patch("decomposer.model._run_model_process", return_value=result):
            directory = Path(temp)
            with self.assertRaises(ModelExecutionBlocked):
                invoke_json("recognition", "test", {}, RECOGNITION_PROPOSAL_SCHEMA,
                            directory / "image.png", {}, directory)
            metadata = json.loads((directory / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "execution_blocked")
            self.assertFalse(metadata["fallback_used"])
            self.assertTrue((directory / "stderr.log").is_file())

    def test_successful_wire_output_normalizes_nulls(self):
        def run(args, *_):
            Path(args[args.index("-o") + 1]).write_text('{"operations": [], "optional": null}')
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp, patch("decomposer.model._run_model_process", side_effect=run):
            proposal, metadata = invoke_json("layout", "test", {}, LAYOUT_PROPOSAL_SCHEMA, Path(temp) / "image.png", {}, temp)
            self.assertEqual(proposal, {"operations": []})
            self.assertEqual(metadata["status"], "complete")

    def test_external_json_backend_uses_placeholder_contract(self):
        captured = {}

        def run(args, input_text, *_):
            captured["args"] = args
            captured["input"] = input_text
            return subprocess.CompletedProcess(args, 0, stdout='{"operations": []}', stderr="adapter-note")

        config = {"model": {
            "backend": "external-json",
            "name": "provider-model",
            "command": ["provider-adapter"],
            "arguments": ["--stage", "{stage}", "--schema", "{schema_file}", "--image", "{image_file}"],
        }}
        with tempfile.TemporaryDirectory() as temp, patch("decomposer.model._run_model_process", side_effect=run):
            directory = Path(temp)
            image = directory / "image.png"
            image.write_bytes(b"image")
            proposal, metadata = invoke_json(
                "layout", "test", {}, LAYOUT_PROPOSAL_SCHEMA, image, config, directory / "call"
            )
            self.assertEqual(proposal, {"operations": []})
            self.assertEqual(metadata["backend"], "external-json")
            self.assertEqual(metadata["model_name"], "provider-model")
            self.assertEqual(captured["args"][0:3], ["provider-adapter", "--stage", "layout"])
            self.assertIn("INPUT:", captured["input"])
            self.assertEqual((directory / "call" / "stderr.log").read_text(), "adapter-note")

    def test_external_environment_requires_explicit_secret_allowlist(self):
        model = {"environment_allowlist": ["TEST_PROVIDER_KEY"]}
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "allowed", "OTHER_SECRET": "blocked"}, clear=False):
            environment = _model_environment(model, "external-json")
        self.assertEqual(environment["TEST_PROVIDER_KEY"], "allowed")
        self.assertNotIn("OTHER_SECRET", environment)

    def test_timeout_reaps_model_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "child.pid"
            program = (
                "import subprocess,sys,time;"
                "child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                "open(sys.argv[1],'w').write(str(child.pid));"
                "time.sleep(60)"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_model_process([sys.executable, "-c", program, str(pid_file)], "", 0.5, os.environ.copy())
            child_pid = int(pid_file.read_text())
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True)
            self.assertTrue(state.returncode != 0 or not state.stdout.strip() or state.stdout.strip().startswith("Z"), state.stdout)


if __name__ == "__main__":
    unittest.main()
