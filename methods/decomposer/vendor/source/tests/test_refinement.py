from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.pipeline import execute
from decomposer.common import sha256, style
from decomposer.knowledge import capture, consolidate_mechanisms, mark_downstreamed, mark_superseded, merge_into, retrieve
from decomposer.polygons import _path_commands, normalize_numeric_path_words
from decomposer.refinement import _candidate_alignment, _canonical_mechanism, _evaluation_region, _native_text_visibility_regression, apply_patch
from decomposer.representation import canonicalize as canonicalize_representation


def _measured_assessment():
    return {
        "accepted": True,
        "before_local": {"pixel_similarity": .8, "edge_iou": .3},
        "after_local": {"pixel_similarity": .82, "edge_iou": .31},
        "before_global": {"pixel_similarity": .9, "edge_iou": .4},
        "after_global": {"pixel_similarity": .902, "edge_iou": .405},
        "local_pixel_delta": .02,
        "local_edge_delta": .01,
        "global_pixel_delta": .002,
    }


def _promotion_artifacts(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    probe = root / "probe-report.json"
    code = root / "policy.py"
    probe.write_text('{"measured":true}')
    code.write_text("POLICY_VERSION = '0.6.19'\n")
    proof = {
        "version": "0.6.19", "scope": "high-confidence image leaves",
        "counterexamples": ["low-confidence leaves are excluded"],
        "artifact_refs": [{"path": str(probe), "sha256": sha256(probe)}],
        "code_artifact": {"path": str(code), "sha256": sha256(code)},
    }
    return probe, proof


class RefinementTest(unittest.TestCase):
    def test_refinement_rejects_new_affected_text_ink_collapse(self):
        with tempfile.TemporaryDirectory(prefix="refinement-ink-") as name:
            root = Path(name)
            scene = {"elements": [{"id": "title/text", "source_label_id": "title", "kind": "text",
                "bbox": [10, 10, 100, 50], "text": "Title\nSecond line", "style": {"color": "#111111"}}]}
            reference = Image.new("RGB", (130, 80), "white")
            current = reference.copy()
            candidate = reference.copy()
            for image, box in ((reference, (20, 20, 60, 45)), (current, (20, 20, 60, 45)),
                               (candidate, (20, 20, 30, 30))):
                ImageDraw.Draw(image).rectangle(box, fill="black")
            paths = [root / f"{key}.png" for key in ("reference", "current", "candidate")]
            for image, path in zip((reference, current, candidate), paths):
                image.save(path)
            result = _native_text_visibility_regression(paths[0], scene, paths[1], scene, paths[2], ["title"])
            self.assertFalse(result["passed"])
            self.assertEqual(result["regressions"][0]["element_id"], "title/text")
            self.assertEqual(result["regressions"][0]["evaluation_region_source"], "affected_scene_owners")
            self.assertTrue(_native_text_visibility_regression(paths[0], scene, paths[1], scene, paths[2], ["other"])["passed"])

    def test_primitive_text_patch_cannot_change_recognition_spacing(self):
        recognition = {"canvas": {"width": 100, "height": 40}, "background": "#FFFFFF", "objects": [
            {"id": "tokens", "category": "shape", "bbox": [0, 0, 100, 40],
             "text": "1  2  3", "style_hint": {}},
        ]}
        representation = {"components": [{"label_id": "tokens", "width": 100, "height": 40,
            "primitive_order": ["text"], "primitives": [
                {"id": "text", "kind": "text", "bbox": [0, 0, 100, 40], "text": "1  2  3", "style": {}},
            ]}]}
        layout = {"placements": [{"label_id": "tokens", "bbox": [0, 0, 100, 40],
            "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["tokens"]}
        decision = {"owner_stage": "representation", "affected_label_ids": ["tokens"],
            "operations": [{"op": "primitive_text", "label_id": "tokens", "primitive_id": "tokens/text",
                            "text": "1     2     3"}]}
        with self.assertRaisesRegex(AssertionError, "exact recognition text identity"):
            apply_patch(recognition, representation, layout, decision)

    def test_nonacceptable_refinement_must_intersect_selected_difference_candidate(self):
        discovery = {"candidates": [{"candidate_id": "tile-0-0", "label_ids": ["a", "b"]}]}
        self.assertEqual(
            _candidate_alignment({"selected_candidate_id": "tile-0-0", "affected_label_ids": ["b"]}, discovery),
            (True, "aligned"),
        )
        self.assertEqual(
            _candidate_alignment({"selected_candidate_id": "tile-0-0", "affected_label_ids": ["c"]}, discovery),
            (False, "affected_labels_do_not_intersect_selected_candidate"),
        )
        self.assertEqual(
            _candidate_alignment({"selected_candidate_id": "missing", "affected_label_ids": ["a"]}, discovery),
            (False, "selected_candidate_missing_or_unknown"),
        )

    def test_evaluation_region_uses_affected_owner_when_tile_is_missing_or_unrelated(self):
        scene = {"canvas": {"width": 1000, "height": 500}, "elements": [
            {"id": "input_equation/equation", "source_label_id": "input_equation", "bbox": [40, 20, 180, 60]},
            {"id": "camera_equation/equation", "source_label_id": "camera_equation", "bbox": [50, 300, 200, 60]},
        ]}
        discovery = {"candidates": [{"candidate_id": "tile-2-0", "region": [0, 250, 300, 125], "label_ids": ["camera_equation"]}]}
        proposal = {"affected_label_ids": ["input_equation"], "affected_primitive_ids": ["input_equation/equation"]}
        region, source = _evaluation_region(proposal, discovery, scene)
        self.assertEqual(source, "affected_scene_owners")
        self.assertLessEqual(region[0], 40)
        self.assertLessEqual(region[1], 20)
        self.assertGreaterEqual(region[0] + region[2], 220)
        self.assertGreaterEqual(region[1] + region[3], 80)

    def test_evaluation_region_never_uses_unrelated_tile_without_scene_owner(self):
        scene = {"canvas": {"width": 1000, "height": 500}, "elements": []}
        discovery = {"candidates": [{"candidate_id": "tile-0-0", "region": [0, 0, 100, 100],
                                     "label_ids": ["other"]}]}
        for selected_id in ("tile-0-0", "missing"):
            region, source = _evaluation_region({"selected_candidate_id": selected_id,
                "affected_label_ids": ["target"], "affected_primitive_ids": ["target/text"]},
                discovery, scene)
            self.assertEqual(source, "full_canvas_unresolved")
            self.assertEqual(region, [0.0, 0.0, 1000.0, 500.0])

    def test_latex_mechanism_aliases_share_one_stable_identity(self):
        for value in ("raw-latex-in-text-primitive", "literal-latex-in-text-primitive", "literal-tex-in-text-primitive"):
            decision = {"mechanism": value, "owner_stage": "representation", "field": "text"}
            _canonical_mechanism(decision)
            self.assertEqual(decision["mechanism"], "literal-math-markup-in-text-primitive")
    def test_partial_crop_mechanism_aliases_share_one_stable_identity(self):
        for value in (
            "partial-object-raster-crop",
            "partial-object-crop-stretched-to-full-bbox",
            "partial-source-crop-stretched-to-object",
            "source-crop-stretched-to-object-bounds",
            "partial-object-source-crop",
        ):
            decision = {"mechanism": value, "owner_stage": "representation", "field": "asset_crop"}
            _canonical_mechanism(decision)
            self.assertEqual(decision["mechanism"], "partial-object-source-crop")
    def test_merge_knowledge_unions_evidence_without_demotion_or_double_counting(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-merge-") as name:
            root = Path(name); destination = root / "destination"; source = root / "source"
            issue = {"owner_stage": "representation", "mechanism": "merge-test", "strategy": "Preserve verified state."}
            probe, proof = _promotion_artifacts(destination)
            first = capture(destination, issue, sample_id="a", round_id="1", accepted=True,
                            assessment=_measured_assessment(), artifact_paths=[probe])
            mark_downstreamed(destination, "representation-merge-test", first, proof)
            capture(source, issue, sample_id="b", round_id="1", accepted=False, assessment={"accepted": False}, artifact_paths=[])
            merge_into(destination, source)
            merge_into(destination, source)
            memory = json.loads((destination / "memory/representation-merge-test.json").read_text())
            skill = json.loads((destination / "skills/representation-merge-test/strategy.json").read_text())
            self.assertEqual(memory["status"], "downstreamed")
            self.assertEqual(skill["status"], "downstreamed")
            self.assertEqual(memory["accepted_count"], 1)
            self.assertEqual(memory["rejected_count"], 1)
            self.assertEqual(len(memory["evidence_ids"]), 2)

    def test_consolidate_mechanism_aliases_preserves_proof_and_excludes_old_ids(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-consolidate-") as name:
            canonical = "representation-partial-object-source-crop"
            aliases = [
                "representation-partial-object-raster-crop",
                "representation-partial-object-crop-stretched-to-full-bbox",
                "representation-partial-source-crop-stretched-to-object",
                "representation-source-crop-stretched-to-object-bounds",
            ]
            probe, promotion_proof = _promotion_artifacts(name)
            for index, mechanism in enumerate((
                "partial-object-source-crop",
                "partial-object-raster-crop",
                "partial-object-crop-stretched-to-full-bbox",
                "partial-source-crop-stretched-to-object",
                "source-crop-stretched-to-object-bounds",
            )):
                evidence_id = capture(name, {
                    "owner_stage": "representation", "mechanism": mechanism,
                    "strategy": "Use a verified complete object crop.",
                }, sample_id=f"sample-{index}", round_id="1", accepted=index == 1,
                    assessment=_measured_assessment() if index == 1 else {"accepted": False},
                    artifact_paths=[probe])
                if index == 1:
                    mark_downstreamed(name, aliases[0], evidence_id, promotion_proof)
            artifact = Path(name) / "probe-report.json"
            artifact.write_text('{"measured":true}')
            proof = {"version": "0.6.19", "reason": "same measured crop mechanism", "artifact_refs": [{
                "path": str(artifact), "sha256": sha256(artifact),
            }]}
            result = consolidate_mechanisms(name, canonical, aliases, proof)
            self.assertEqual(result["evidence_count"], 5)
            self.assertEqual(result["superseded"], aliases)
            self.assertEqual(consolidate_mechanisms(name, canonical, aliases, proof)["evidence_count"], 5)
            target = json.loads((Path(name) / "memory" / f"{canonical}.json").read_text())
            self.assertEqual(target["status"], "downstreamed")
            self.assertEqual(target["accepted_count"], 1)
            self.assertEqual(target["rejected_count"], 4)
            self.assertEqual(target["supersedes"], aliases)
            self.assertEqual(target["downstream_proof"]["version"], "0.6.19")
            self.assertEqual([item["memory_id"] for item in retrieve(name)], [canonical])
            for alias in aliases:
                old = json.loads((Path(name) / "memory" / f"{alias}.json").read_text())
                self.assertEqual(old["status"], "superseded")
                self.assertEqual(old["supersession_proof"]["superseded_by"], canonical)

    def test_consolidation_rejects_unverified_artifact_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-consolidate-proof-") as name:
            canonical = "representation-complete-crop"
            alias = "representation-fragment-crop"
            for mechanism in ("complete-crop", "fragment-crop"):
                capture(name, {"owner_stage": "representation", "mechanism": mechanism},
                        sample_id=mechanism, round_id="1", accepted=None, assessment=None, artifact_paths=[])
            artifact = Path(name) / "probe-report.json"
            artifact.write_text('{"measured":true}')
            proof = {"version": "0.6.19", "reason": "same mechanism", "artifact_refs": [{
                "path": str(artifact), "sha256": "wrong-hash",
            }]}
            with self.assertRaisesRegex(ValueError, "artifact identity mismatch"):
                consolidate_mechanisms(name, canonical, [alias], proof)
            old = json.loads((Path(name) / "memory" / f"{alias}.json").read_text())
            self.assertEqual(old["status"], "candidate")

    def test_downstream_promotion_requires_measured_intact_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-promotion-gate-") as name:
            issue = {"owner_stage": "representation", "mechanism": "crop-gate"}
            memory_id = "representation-crop-gate"
            probe, proof = _promotion_artifacts(name)
            no_artifact = capture(name, issue, sample_id="missing-artifact", round_id="1",
                                  accepted=True, assessment=_measured_assessment(), artifact_paths=[])
            with self.assertRaisesRegex(ValueError, "evidence artifact references"):
                mark_downstreamed(name, memory_id, no_artifact, proof)
            no_measurement = capture(name, issue, sample_id="missing-measurement", round_id="1",
                                     accepted=True, assessment={"accepted": True}, artifact_paths=[probe])
            with self.assertRaisesRegex(ValueError, "measurement missing"):
                mark_downstreamed(name, memory_id, no_measurement, proof)
            measured = capture(name, issue, sample_id="measured", round_id="1",
                               accepted=True, assessment=_measured_assessment(), artifact_paths=[probe])
            stale = {**proof, "code_artifact": {**proof["code_artifact"], "sha256": "stale"}}
            with self.assertRaisesRegex(ValueError, "code artifact identity mismatch"):
                mark_downstreamed(name, memory_id, measured, stale)
            relative = {**proof, "code_artifact": {**proof["code_artifact"], "path": "policy.py"}}
            with self.assertRaisesRegex(ValueError, "code artifact path must be absolute"):
                mark_downstreamed(name, memory_id, measured, relative)
            memory_path = Path(name) / "memory" / f"{memory_id}.json"
            self.assertEqual(json.loads(memory_path.read_text())["status"], "candidate")
            mark_downstreamed(name, memory_id, measured, proof)
            self.assertEqual(json.loads(memory_path.read_text())["status"], "downstreamed")
            mark_superseded(name, memory_id, {"reason": "contradicted by measured evidence"})
            with self.assertRaisesRegex(ValueError, "superseded memory cannot be promoted"):
                mark_downstreamed(name, memory_id, measured, proof)
            self.assertEqual(json.loads(memory_path.read_text())["status"], "superseded")

    def test_new_evidence_does_not_demote_downstreamed_skill(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-skill-state-") as name:
            issue = {
                "owner_stage": "representation",
                "mechanism": "corner-rounding",
                "affected_label_ids": ["panel"],
                "affected_primitive_ids": ["panel/body"],
                "strategy": "Use the measured corner family.",
            }
            evidence_id = capture(name, issue, sample_id="a", round_id="1", accepted=True, assessment={"accepted": True}, artifact_paths=[])
            probe, proof = _promotion_artifacts(name)
            evidence_id = capture(name, issue, sample_id="a-measured", round_id="1", accepted=True,
                                  assessment=_measured_assessment(), artifact_paths=[probe])
            mark_downstreamed(name, "representation-corner-rounding", evidence_id, proof)
            capture(name, issue, sample_id="b", round_id="1", accepted=False, assessment={"accepted": False}, artifact_paths=[])
            memory = json.loads((Path(name) / "memory/representation-corner-rounding.json").read_text())
            skill = json.loads((Path(name) / "skills/representation-corner-rounding/strategy.json").read_text())
            skill_md = (Path(name) / "skills/representation-corner-rounding/SKILL.md").read_text()
            self.assertEqual(memory["status"], "downstreamed")
            self.assertEqual(skill["status"], "downstreamed")
            self.assertEqual(skill["downstream_proof"], proof)
            self.assertIn("Downstreamed", skill_md)
            self.assertNotIn("Candidate only", skill_md)

    def test_superseded_memory_is_retained_but_excluded_from_retrieval(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-supersede-") as name:
            issue = {"owner_stage": "compiler", "mechanism": "false-diagnosis", "strategy": "Guess export loss."}
            capture(name, issue, sample_id="a", round_id="1", accepted=None, assessment=None, artifact_paths=[])
            mark_superseded(name, "compiler-false-diagnosis", {"reason": "contradicted_by_ooxml"})
            memory = json.loads((Path(name) / "memory/compiler-false-diagnosis.json").read_text())
            skill_md = (Path(name) / "skills/compiler-false-diagnosis/SKILL.md").read_text()
            self.assertEqual(memory["status"], "superseded")
            self.assertEqual(retrieve(name), [])
            self.assertIn("excluded from retrieval", skill_md)

    def test_retrieve_prioritizes_downstreamed_and_excludes_unverified_imports(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-memory-") as name:
            memory = Path(name) / "memory"; memory.mkdir()
            rows = [
                {"memory_id": "imported", "owner_stage": "layout", "status": "candidate_imported", "evidence_ids": ["a"]},
                {"memory_id": "candidate", "owner_stage": "layout", "status": "candidate", "evidence_ids": ["b"], "accepted_count": 2},
                {"memory_id": "integrated", "owner_stage": "compiler", "status": "downstreamed", "evidence_ids": ["c"], "accepted_count": 1},
            ]
            for row in rows:
                (memory / f"{row['memory_id']}.json").write_text(json.dumps(row))
            retrieved = retrieve(name)
            self.assertEqual([row["memory_id"] for row in retrieved], ["integrated", "candidate"])

    def test_path_parser_supports_hv_multiple_subpaths_and_relative_commands(self):
        paths = _path_commands("M38 0 H65 V26 H38 Z M70 0 h26 v26 h-26 z")
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], [("M", 38.0, 0.0), ("L", 65.0, 0.0), ("L", 65.0, 26.0), ("L", 38.0, 26.0), ("Z",)])
        self.assertEqual(paths[1][-1], ("Z",))

    def test_path_parser_supports_absolute_and_relative_arcs(self):
        paths = _path_commands(
            "M27 0 A27 27 0 1 0 -27 0 A27 27 0 1 0 27 0 Z "
            "M9 9 a9 9 0 1 0 -18 0 a9 9 0 1 0 18 0 Z"
        )
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(item[0] == "C" for item in paths[0][1:-1]))
        self.assertAlmostEqual(paths[0][-2][-2], 27.0)
        self.assertAlmostEqual(paths[0][-2][-1], 0.0)
        self.assertAlmostEqual(paths[1][-2][-2], 9.0)
        self.assertAlmostEqual(paths[1][-2][-1], 9.0)

    def test_path_parser_rejects_unsupported_commands_explicitly(self):
        with self.assertRaisesRegex(ValueError, "unsupported path syntax"):
            _path_commands("M0 0 S10 10 20 20 Z")

    def test_spelled_numeric_path_coordinate_is_repaired_and_audited(self):
        source = "M 52 49 C 91 -22 204 -7 267 48 C 316 87 329 152 270 180 C 213 209 96 164 65 123 C 46 99 45 seventy 52 49 Z"
        self.assertIn(("C", 46.0, 99.0, 45.0, 70.0, 52.0, 49.0), _path_commands(normalize_numeric_path_words(source))[0])
        self.assertEqual(normalize_numeric_path_words("M 0 nonsense 10 10 Z"), "M 0 nonsense 10 10 Z")
        recognition = {"canvas": {"width": 300, "height": 200}, "objects": [
            {"id": "region", "category": "shape", "bbox": [0, 0, 300, 200], "text": "", "style_hint": {}},
        ]}
        proposal = {"components": [{"label_id": "region", "width": 300, "height": 200, "primitives": [
            {"id": "outline", "kind": "path", "bbox": [0, 0, 300, 200], "path": source, "style": {}},
        ]}]}
        with tempfile.TemporaryDirectory(prefix="decomposer-path-repair-") as name:
            representation, report = canonicalize_representation(proposal, recognition, Path(name) / "unused.png", name)
        self.assertEqual(representation["components"][0]["primitives"][0]["path"], source.replace("seventy", "70"))
        self.assertEqual(report["repairs"][0]["action"], "normalized_numeric_path_words")
        self.assertEqual(report["path_validation_issues"], [])

    def test_primitive_path_patch_requires_path_owner_and_valid_geometry(self):
        recognition = {"canvas": {"width": 100, "height": 100}, "background": "#FFFFFF", "objects": [
            {"id": "a", "category": "shape", "bbox": [0, 0, 100, 100], "text": "", "style_hint": {}},
        ]}
        representation = {"components": [{"label_id": "a", "category": "shape", "width": 100, "height": 100, "primitives": [
            {"id": "p", "kind": "path", "bbox": [0, 0, 100, 100], "path": "M0 0 L100 0 Z", "style": {}, "editable": True},
        ], "primitive_order": ["p"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "a", "bbox": [0, 0, 100, 100], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["a"], "degraded": False}
        decision = {"owner_stage": "representation", "affected_label_ids": ["a"], "operations": [
            {"op": "primitive_path", "label_id": "a", "primitive_id": "a/p", "path": "M0 0 Lseventy 0 Z"},
        ]}
        _, updated, _ = apply_patch(recognition, representation, layout, decision)
        self.assertEqual(updated["components"][0]["primitives"][0]["path"], "M0 0 L70 0 Z")
        decision["operations"][0]["path"] = "M0 0 S10 10 20 20 Z"
        with self.assertRaisesRegex(ValueError, "unsupported path syntax"):
            apply_patch(recognition, representation, layout, decision)
        representation["components"][0]["primitives"][0]["kind"] = "rect"
        with self.assertRaisesRegex(ValueError, "primitive_path is unsupported"):
            apply_patch(recognition, representation, layout, decision)

    def test_dash_type_is_editable_and_boolean_patch_remains_compatible(self):
        self.assertEqual(style({"dashed": True})["dash_type"], "dash")
        self.assertEqual(style({"dash_type": "sysDot"})["dash_type"], "sysDot")
        recognition = {"canvas": {"width": 100, "height": 100}, "background": "#FFFFFF", "objects": [
            {"id": "a", "category": "shape", "bbox": [0, 0, 100, 100], "text": "", "style_hint": {}},
        ]}
        representation = {"components": [{"label_id": "a", "category": "shape", "width": 100, "height": 100, "primitives": [
            {"id": "p", "kind": "line", "points": [[0, 0], [100, 0]], "style": style({"dashed": True}), "editable": True},
        ], "primitive_order": ["p"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "a", "bbox": [0, 0, 100, 100], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["a"], "degraded": False}
        decision = {"owner_stage": "representation", "affected_label_ids": ["a"], "operations": [
            {"op": "primitive_style", "label_id": "a", "primitive_id": "a/p", "style": {"dash_type": "sysDot"}},
        ]}
        _, updated, _ = apply_patch(recognition, representation, layout, decision)
        self.assertEqual(updated["components"][0]["primitives"][0]["style"]["dash_type"], "sysDot")
        decision["operations"][0]["style"] = {"dashed": False}
        _, updated, _ = apply_patch(recognition, representation, layout, decision)
        self.assertEqual(updated["components"][0]["primitives"][0]["style"]["dash_type"], "solid")

    def test_label_local_patch_rejects_undeclared_label(self):
        recognition = {"canvas": {"width": 100, "height": 100}, "background": "#FFFFFF", "objects": [{"id": "a", "category": "shape", "bbox": [0, 0, 10, 10]}]}
        representation = {"components": [{"label_id": "a", "category": "shape", "width": 10, "height": 10, "primitives": [{"id": "p", "kind": "rect", "bbox": [0, 0, 10, 10], "style": {}, "editable": True}], "primitive_order": ["p"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "a", "bbox": [0, 0, 10, 10], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["a"], "degraded": False}
        decision = {"owner_stage": "layout", "affected_label_ids": [], "operations": [{"op": "layout_bbox", "label_id": "a", "bbox": [20, 20, 10, 10]}]}
        with self.assertRaises(ValueError): apply_patch(recognition, representation, layout, decision)

    def test_primitive_kind_patch_supports_only_bbox_native_shapes(self):
        recognition = {"canvas": {"width": 100, "height": 100}, "background": "#FFFFFF", "objects": [{"id": "a", "category": "shape", "bbox": [0, 0, 10, 10]}]}
        representation = {"components": [{"label_id": "a", "category": "shape", "width": 10, "height": 10, "primitives": [{"id": "p", "kind": "round_rect", "bbox": [0, 0, 10, 10], "style": {}, "editable": True}], "primitive_order": ["p"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "a", "bbox": [0, 0, 10, 10], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["a"], "degraded": False}
        decision = {"owner_stage": "representation", "affected_label_ids": ["a"], "operations": [{"op": "primitive_kind", "label_id": "a", "primitive_id": "a/p", "kind": "rect"}]}
        _, updated, _ = apply_patch(recognition, representation, layout, decision)
        self.assertEqual(updated["components"][0]["primitives"][0]["kind"], "rect")
        decision["operations"][0]["kind"] = "path"
        with self.assertRaisesRegex(ValueError, "unsafe primitive kind transition"):
            apply_patch(recognition, representation, layout, decision)

    def test_primitive_arrowhead_patch_supports_only_line_like_primitives(self):
        recognition = {"canvas": {"width": 100, "height": 100}, "background": "#FFFFFF", "objects": [{"id": "a", "category": "arrow", "bbox": [0, 0, 20, 10]}]}
        representation = {"components": [{"label_id": "a", "category": "arrow", "width": 20, "height": 10, "primitives": [{"id": "p", "kind": "path", "bbox": [0, 0, 20, 10], "path": "M0 5 L20 5", "arrow_start": False, "arrow_end": False, "style": {}, "editable": True}], "primitive_order": ["p"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "a", "bbox": [0, 0, 20, 10], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["a"], "degraded": False}
        decision = {"owner_stage": "representation", "affected_label_ids": ["a"], "operations": [{"op": "primitive_arrowheads", "label_id": "a", "primitive_id": "a/p", "arrow_start": True, "arrow_end": True}]}
        _, updated, _ = apply_patch(recognition, representation, layout, decision)
        primitive = updated["components"][0]["primitives"][0]
        self.assertTrue(primitive["arrow_start"])
        self.assertTrue(primitive["arrow_end"])
        representation["components"][0]["primitives"][0]["kind"] = "rect"
        with self.assertRaisesRegex(ValueError, "primitive_arrowheads is unsupported"):
            apply_patch(recognition, representation, layout, decision)

    def test_text_runs_patch_preserves_source_identity_and_script(self):
        recognition = {"canvas": {"width": 200, "height": 60}, "background": "#FFFFFF", "objects": [{"id": "title", "category": "text", "bbox": [0, 0, 200, 60], "text": "I_Source"}]}
        representation = {"components": [{"label_id": "title", "category": "text", "width": 200, "height": 60, "primitives": [{"id": "text", "kind": "text", "bbox": [0, 0, 200, 60], "text": "I_Source", "style": {"font_size": 30}, "editable": True}], "primitive_order": ["text"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "title", "bbox": [0, 0, 200, 60], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["title"], "degraded": False}
        runs = [
            {"text": "I", "source_text": "I", "style": {"italic": True}, "script": "normal"},
            {"text": "Source", "source_text": "_Source", "style": {}, "script": "subscript"},
        ]
        decision = {"owner_stage": "representation", "affected_label_ids": ["title"], "operations": [{"op": "primitive_text_runs", "label_id": "title", "primitive_id": "title/text", "runs": runs}]}
        _, updated, _ = apply_patch(recognition, representation, layout, decision)
        primitive = updated["components"][0]["primitives"][0]
        self.assertEqual("".join(item["source_text"] for item in primitive["runs"]), "I_Source")
        self.assertEqual("".join(item["text"] for item in primitive["runs"]), "ISource")
        self.assertEqual(primitive["runs"][1]["script"], "subscript")
        decision["operations"][0]["runs"][1]["source_text"] = "Source"
        with self.assertRaisesRegex(ValueError, "must exactly preserve primitive text"):
            apply_patch(recognition, representation, layout, decision)

    def test_primitive_patch_rejects_mismatched_qualified_owner(self):
        recognition = {"canvas": {"width": 100, "height": 100}, "background": "#FFFFFF", "objects": [{"id": "a", "category": "shape", "bbox": [0, 0, 10, 10]}]}
        representation = {"components": [{"label_id": "a", "category": "shape", "width": 10, "height": 10, "primitives": [{"id": "p", "kind": "rect", "bbox": [0, 0, 10, 10], "style": {}, "editable": True}], "primitive_order": ["p"]}], "assets": {}, "degraded": False}
        layout = {"placements": [{"label_id": "a", "bbox": [0, 0, 10, 10], "rotation_deg": 0, "font_scale": 1, "visible": True}], "component_order": ["a"], "degraded": False}
        decision = {"owner_stage": "representation", "affected_label_ids": ["a"], "operations": [{"op": "primitive_kind", "label_id": "a", "primitive_id": "other/p", "kind": "round_rect"}]}
        with self.assertRaisesRegex(ValueError, "is not owned by label"):
            apply_patch(recognition, representation, layout, decision)

    def test_rendered_refinement_improves_and_writes_knowledge(self):
        if not shutil.which("node") or not shutil.which("soffice") or not shutil.which("pdftoppm"):
            self.skipTest("rendering runtime unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-refine-") as name:
            root = Path(name); source = root / "source.png"
            image = Image.new("RGB", (400, 200), "white"); ImageDraw.Draw(image).rectangle((40, 50, 140, 110), fill="#4472C4"); image.save(source)
            fixtures = root / "fixtures"; fixtures.mkdir()
            (fixtures / "recognition.json").write_text(json.dumps({"background": "#FFFFFF", "objects": [{"id": "box", "category": "shape", "bbox": [40, 50, 100, 60], "style_hint": {"fill": "#4472C4", "stroke": "none"}}]}))
            (fixtures / "representation.json").write_text(json.dumps({"components": [{"label_id": "box", "width": 100, "height": 60, "primitives": [{"id": "body", "kind": "rect", "bbox": [0, 0, 100, 60], "style": {"fill": "#4472C4", "stroke": "none"}}]}]}))
            (fixtures / "layout.json").write_text(json.dumps({"operations": [{"op": "place", "label_id": "box", "bbox": [220, 50, 100, 60]}]}))
            (fixtures / "refinement.json").write_text(json.dumps({"acceptable": False, "selected_candidate_id": "tile-1-2", "owner_stage": "layout", "mechanism": "global-placement-offset", "summary": "The blue box is shifted right.", "affected_label_ids": ["box"], "affected_primitive_ids": [], "field": "bbox", "confidence": .99, "strategy": "Preserve the source bbox when no relation requires displacement.", "operations": [{"op": "layout_bbox", "label_id": "box", "primitive_id": None, "bbox": [40, 50, 100, 60], "points": None, "text": None, "category": None, "connects": None, "style": None, "number": None, "label_ids": None}]}))
            config = {"detectors": {}, "runtime": {"node": "node", "soffice": shutil.which("soffice"), "pdftoppm": shutil.which("pdftoppm"), "timeout_seconds": 180}, "refinement": {"max_rounds": 1}}
            status = execute(source, root / "run", config, fixture_dir=fixtures)
            self.assertEqual(status["refinement"]["accepted_rounds"], 1)
            self.assertTrue(status["validation"]["passed"])
            self.assertTrue((root / "run/output/provenance.json").exists())
            self.assertTrue((root / "run/output/page-manifest.json").exists())
            self.assertTrue((root / "run/output/validation.json").exists())
            self.assertTrue((root / "run/output/contact-sheet.png").exists())
            self.assertTrue(list((root / "run/knowledge/memory").glob("*.json")))
            assessment = json.loads((root / "run/refinement/round-001/assessment.json").read_text())
            self.assertTrue(assessment["accepted"])


if __name__ == "__main__": unittest.main()
