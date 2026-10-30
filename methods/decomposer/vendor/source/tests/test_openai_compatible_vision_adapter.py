import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "adapters" / "openai_compatible_vision.py"
SPEC = importlib.util.spec_from_file_location("openai_compatible_vision", MODULE_PATH)
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapter)


class OpenAICompatibleVisionAdapterTests(unittest.TestCase):
    def test_deepseek_json_object_request_contains_schema_and_image(self):
        schema = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array"}}}
        body = adapter.build_body(
            stage="recognition",
            model="deepseek-flash",
            prompt="Return JSON.",
            schema=schema,
            mime="image/png",
            image_data="AAAA",
            response_mode="json_object",
            reasoning_effort="high",
            max_tokens=65536,
            image_detail="original",
        )
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(body["max_tokens"], 65536)
        self.assertIn('"required":["items"]', body["messages"][0]["content"])
        image = body["messages"][1]["content"][1]
        self.assertEqual(image["type"], "image_url")
        self.assertEqual(image["image_url"]["detail"], "original")
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_provider_metadata_excludes_message_content(self):
        payload = {
            "id": "response-1",
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"total_tokens": 12},
        }
        metadata = adapter.response_metadata(payload, attempt=2)
        self.assertEqual(metadata["provider_metadata"]["finish_reason"], "stop")
        self.assertEqual(metadata["provider_metadata"]["attempt"], 2)
        self.assertNotIn("choices", metadata["provider_metadata"])
        self.assertNotIn("message", metadata["provider_metadata"])


if __name__ == "__main__":
    unittest.main()
