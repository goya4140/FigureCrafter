from __future__ import annotations

import unittest

from decomposer.representation_compact import COMPACT_SCHEMA, decode


class RepresentationCompactTest(unittest.TestCase):
    def test_sparse_wire_decodes_to_existing_component_contract(self):
        recognition = {"objects": [
            {"id": "label-a"},
            {"id": "label-b", "style_hint": {"color": "#333333"}},
        ]}
        proposal = {"components": [
            {"label_id": "label-a", "primitives": [{
                "id": "box", "kind": "rect", "bbox": [0, 0, 80, 40],
                "style": [{"key": "fill", "value": "#ffffff"},
                          {"key": "stroke_width", "value": 1.5}],
            }]},
            {"label_id": "label-b", "primitives": [{
                "id": "text", "kind": "text", "bbox": [0, 0, 100, 30],
                "text": "x₂", "runs": [
                    {"text": "x", "source_text": "x", "style": [], "script": "normal"},
                    {"text": "₂", "source_text": "₂",
                     "style": [{"key": "italic", "value": True}], "script": "subscript"},
                ], "style": [{"key": "color", "value": "#111111"}],
            }]},
        ]}
        try:
            from jsonschema import validate
        except ImportError:
            pass
        else:
            validate(proposal, COMPACT_SCHEMA)
        decoded = decode(proposal, recognition)
        self.assertEqual(decoded["components"][0]["primitives"][0]["style"],
                         {"fill": "#ffffff", "stroke_width": 1.5})
        self.assertEqual(decoded["components"][1]["primitives"][0]["runs"][1]["style"],
                         {"italic": True})
        self.assertEqual(decoded["components"][1]["primitives"][0]["style"]["color"],
                         "#111111")

    def test_missing_text_color_uses_recognition_hint(self):
        recognition = {"objects": [{"id": "text", "style_hint": {
            "color": "#242424", "font_size": 34, "bold": True, "align": "left",
        }}]}
        proposal = {"components": [{"label_id": "text", "primitives": [{
            "id": "text", "kind": "text", "bbox": [0, 0, 10, 10], "text": "A",
            "runs": [], "style": [],
        }]}]}
        decoded = decode(proposal, recognition)
        self.assertEqual(decoded["components"][0]["primitives"][0]["style"]["color"],
                         "#242424")
        self.assertEqual(decoded["components"][0]["primitives"][0]["style"]["bold"], True)
        self.assertEqual(decoded["components"][0]["primitives"][0]["style"]["align"], "left")

    def test_duplicate_or_missing_owner_and_style_fail_closed(self):
        recognition = {"objects": [{"id": "a"}, {"id": "b"}]}
        component = {"label_id": "a", "primitives": [{
            "id": "box", "kind": "rect", "bbox": [0, 0, 1, 1], "style": [],
        }]}
        with self.assertRaisesRegex(ValueError, "missing, duplicate, or foreign"):
            decode({"components": [component, component]}, recognition)
        proposal = {"components": [component, {"label_id": "b", "primitives": [{
            "id": "box", "kind": "rect", "bbox": [0, 0, 1, 1],
            "style": [{"key": "fill", "value": "red"},
                      {"key": "fill", "value": "blue"}],
        }]}]}
        with self.assertRaisesRegex(ValueError, "duplicate compact style key"):
            decode(proposal, recognition)


if __name__ == "__main__":
    unittest.main()
