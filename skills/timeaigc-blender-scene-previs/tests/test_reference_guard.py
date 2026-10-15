"""Local dummy-byte provenance tests; these are not media-semantic tests.

No Dreamina/ffprobe/network calls run: adapter subprocesses are fully mocked.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import dreamina_job
from reference_guard import ReferenceError, build_upload_list, inspect_manifest, validate_references
from proxy_contract import build_proxy_contract, canonical_sha256
from generation_prompt import generate_prompt


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ReferenceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="previs_reference_guard_")
        self.root = Path(self.temporary.name).resolve()
        self.clean = self.root / "clean.mp4"
        self.review = self.root / "review.mp4"
        self.clean.write_bytes(b"DUMMY clean bytes: not a media semantic test")
        self.review.write_bytes(b"DUMMY review bytes: not a media semantic test")
        self.manifest_path = self.root / "preview-manifest.json"
        self.manifest = {
            "schema_version": 2,
            "files": {
                "clean_video": {"path": "clean.mp4", "sha256": digest(self.clean.read_bytes())},
                "review_video": {"path": "review.mp4", "sha256": digest(self.review.read_bytes()), "purpose": "review-only"},
            },
            "reference_policy": {"allowlist": [{
                "path": "clean.mp4", "sha256": digest(self.clean.read_bytes()), "purpose": "model-reference",
            }]},
        }
        self.add_current_proxy_binding()
        self.save_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def bind_payload(self, key, filename, payload):
        path = self.root / filename
        path.write_bytes(payload)
        self.manifest["proxy_reference"][key] = {"path": filename, "sha256": digest(payload)}
        return path

    def bind_json(self, key, filename, value):
        return self.bind_payload(key, filename, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))

    def add_current_proxy_binding(self):
        source = "镜头一：甲站在门边，镜头固定。\r\n甲：保留完整对白。"
        self.spec = {"actors": [{"id": "actor_A", "character_name": "甲", "color_name": "蓝色",
                                  "color": [.12, .43, .72], "height": 1.76}],
                     "storyboard_text": source,
                     "shots": [{"id": "shot_01", "source_text": source, "dialogue": "甲：保留完整对白。"}]}
        record = {"actor_id": "actor_A", "generator_version": "dummy-test-fixture",
                  "height_m": 1.76, "hand_lod": "none", "proxy_mode": "cylinder",
                  "color": [.12, .43, .72], "purpose": "model-reference", "allow_special_proxy": False,
                  "parts": [{"id": part} for part in ("body_cylinder", "head", "nose", "front_marker")]}
        self.records = {"actor_A": record}
        self.rehash_actor_record(record)
        self.refresh_current_proxy_binding()

    def refresh_current_proxy_binding(self):
        self.contract = build_proxy_contract(self.spec, self.records)
        self.prompt, self.coverage = generate_prompt(self.spec, self.contract)
        self.manifest["proxy_reference"] = {
            "schema": "timeaigc-proxy-reference/v1", "purpose": "model-reference",
            "clean_video_sha256": self.manifest["files"]["clean_video"]["sha256"],
        }
        self.bind_json("source_spec", "source-spec.json", self.spec)
        self.bind_json("actor_records", "actor-geometry.json", self.records)
        self.bind_json("mode_contract", "proxy-contract.json", self.contract)
        self.bind_json("prompt_coverage", "prompt-coverage.json", self.coverage)
        self.bind_payload("source_storyboard", "source-storyboard.txt", self.spec["storyboard_text"].encode("utf-8"))
        self.bind_payload("generation_prompt", "generation-prompt.txt", self.prompt.encode("utf-8"))

    @staticmethod
    def rehash_actor_record(record):
        record.pop("semantic_sha256", None)
        record["semantic_sha256"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

    def bind_rehashed_contract(self):
        self.contract.pop("contract_sha256", None)
        self.contract["contract_sha256"] = canonical_sha256(self.contract)
        self.bind_json("mode_contract", "proxy-contract.json", self.contract)

    def add_expanded_review(self):
        expanded = self.root / "review-expanded.mp4"
        expanded.write_bytes(b"DUMMY expanded review bytes: not a media semantic test")
        self.manifest["files"]["review_variants"] = [{
            "path": expanded.name, "sha256": digest(expanded.read_bytes()), "purpose": "review-only",
        }]
        self.save_manifest()
        return expanded

    def rejected(self, paths=None):
        self.save_manifest()
        with self.assertRaises(ReferenceError):
            validate_references(self.manifest_path, [self.clean] if paths is None else paths)

    def test_clean_upload_list_is_exactly_one_approved_video(self):
        result = build_upload_list(self.manifest_path)
        self.assertEqual(len(result["videos"]), 1)
        self.assertEqual(result["videos"][0]["path"], str(self.clean))
        self.assertEqual(result["videos"][0]["purpose"], "model-reference")
        self.assertEqual(result["videos"][0]["sha256"], digest(self.clean.read_bytes()))
        self.assertEqual(result["excluded_review"]["purpose"], "review-only")
        self.assertEqual(result["excluded_reviews"], [result["excluded_review"]])
        self.assertEqual(result["reference_manifest_sha256"], digest(self.manifest_path.read_bytes()))
        self.assertEqual(validate_references(self.manifest_path, ["clean.mp4"])[0]["path"], str(self.clean))
        self.assertTrue(result["generation_eligible"])
        self.assertEqual(result["proxy_contract_status"], "current-verified")

    def test_legacy_manifest_is_only_readable_without_inventing_mode(self):
        del self.manifest["proxy_reference"]
        self.save_manifest()
        inspected = inspect_manifest(self.manifest_path)
        self.assertFalse(inspected["generation_eligible"])
        self.assertEqual(inspected["proxy_contract_status"], "legacy-unverified")
        self.assertIsNone(inspected["proxy_reference"]["proxy_modes"])
        with self.assertRaises(ReferenceError):
            build_upload_list(self.manifest_path)
        with self.assertRaises(ReferenceError):
            validate_references(self.manifest_path, [self.clean])

    def test_cli_legacy_inspect_is_read_only(self):
        del self.manifest["proxy_reference"]
        self.save_manifest()
        command = [sys.executable, str(SCRIPTS / "reference_guard.py"), str(self.manifest_path), "--legacy-inspect"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["generation_eligible"])
        result = subprocess.run(command + ["--output", str(self.root / "legacy-upload.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "legacy-upload.json").exists())

    def test_proxy_binding_clean_video_drift_rejected(self):
        self.manifest["proxy_reference"]["clean_video_sha256"] = "0" * 64
        self.rejected()

    def test_each_proxy_file_binding_is_required(self):
        original = copy.deepcopy(self.manifest["proxy_reference"])
        for key in ("source_spec", "actor_records", "mode_contract", "source_storyboard", "generation_prompt", "prompt_coverage"):
            with self.subTest(missing=key):
                self.manifest["proxy_reference"] = copy.deepcopy(original)
                del self.manifest["proxy_reference"][key]
                self.rejected()

    def test_missing_storyboard_file_rejected(self):
        (self.root / "source-storyboard.txt").unlink()
        self.rejected()

    def test_storyboard_changed_with_rebound_file_hash_still_rejected(self):
        self.bind_payload("source_storyboard", "source-storyboard.txt", b"abridged storyboard")
        self.rejected()

    def test_prompt_byte_drift_rejected(self):
        (self.root / "generation-prompt.txt").write_bytes((self.prompt + "extra text").encode("utf-8"))
        self.rejected()

    def test_prompt_mode_text_changed_and_rehashed_still_rejected(self):
        # Retain the full source text; changing the proxy instructions is still drift.
        changed = self.prompt.replace("cylinder", "articulated_special")
        self.assertNotEqual(changed, self.prompt)
        self.bind_payload("generation_prompt", "generation-prompt.txt", changed.encode("utf-8"))
        self.rejected()

    def test_prompt_coverage_rehashed_drift_rejected(self):
        self.coverage["prompt_sha256"] = "0" * 64
        self.bind_json("prompt_coverage", "prompt-coverage.json", self.coverage)
        self.rejected()

    def test_actual_mode_missing_cannot_be_assumed_cylinder(self):
        record = self.records["actor_A"]
        del record["proxy_mode"]
        self.rehash_actor_record(record)
        self.contract["actual_actor_records_sha256"] = canonical_sha256([record])
        self.contract["actors"][0]["actual_semantic_sha256"] = record["semantic_sha256"]
        self.bind_json("actor_records", "actor-geometry.json", self.records)
        self.bind_rehashed_contract()
        self.rejected()

    def test_actual_mode_mismatch_rehashed_record_rejected(self):
        record = self.records["actor_A"]
        record["proxy_mode"] = "articulated_special"
        self.rehash_actor_record(record)
        self.contract["actual_actor_records_sha256"] = canonical_sha256([record])
        self.contract["actors"][0]["actual_semantic_sha256"] = record["semantic_sha256"]
        self.bind_json("actor_records", "actor-geometry.json", self.records)
        self.bind_rehashed_contract()
        self.rejected()

    def test_hidden_limbs_in_actual_cylinder_rejected(self):
        record = self.records["actor_A"]
        record["parts"].append({"id": "left_palm"})
        self.rehash_actor_record(record)
        self.contract["actual_actor_records_sha256"] = canonical_sha256([record])
        self.contract["actors"][0]["actual_semantic_sha256"] = record["semantic_sha256"]
        self.contract["actors"][0]["actual_part_ids"].append("left_palm")
        self.bind_json("actor_records", "actor-geometry.json", self.records)
        self.bind_rehashed_contract()
        self.rejected()

    def test_missing_explicit_special_gate_rejected_even_when_rehashed(self):
        for allow, reason in ((False, "special test"), (True, "")):
            with self.subTest(allow=allow, reason=reason):
                self.spec["actors"][0]["proxy_mode"] = "articulated_special"
                self.spec["allow_special_proxy"] = allow
                self.spec["special_proxy_reason"] = reason
                self.contract["source_spec_sha256"] = canonical_sha256(self.spec)
                self.contract["allow_special_proxy"] = allow
                self.contract["special_proxy_reason"] = reason
                self.contract["actors"][0]["proxy_mode"] = "articulated_special"
                self.bind_json("source_spec", "source-spec.json", self.spec)
                self.bind_rehashed_contract()
                self.rejected()

    def test_explicitly_authorized_special_mode_passes_with_matching_actual_records(self):
        self.spec["actors"][0].update({"proxy_mode": "articulated_special", "hand_lod": "standard"})
        self.spec.update({"allow_special_proxy": True, "special_proxy_reason": "Fixture for an explicitly requested special spatial proxy"})
        record = self.records["actor_A"]
        record.update({"proxy_mode": "articulated_special", "hand_lod": "standard", "allow_special_proxy": True,
                       "parts": [{"id": "head"}, {"id": "left_palm"}, {"id": "right_palm"}]})
        self.rehash_actor_record(record)
        self.refresh_current_proxy_binding()
        self.save_manifest()
        result = build_upload_list(self.manifest_path)
        self.assertEqual(result["proxy_reference"]["proxy_modes"], [
            {"id": "actor_A", "proxy_mode": "articulated_special", "purpose": "model-reference"}])
        with patch.object(dreamina_job, "run", side_effect=self.fake_run):
            prepared = dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
        self.assertEqual(prepared["generation_prompt_binding"]["proxy_contract_sha256"], self.contract["contract_sha256"])

    def test_special_actual_mode_cannot_be_enabled_by_permission_without_explicit_mode(self):
        self.spec.update({"allow_special_proxy": True, "special_proxy_reason": "Permission alone must not select a mode"})
        record = self.records["actor_A"]
        record.update({"proxy_mode": "articulated_special", "hand_lod": "standard", "allow_special_proxy": True})
        self.rehash_actor_record(record)
        self.contract["source_spec_sha256"] = canonical_sha256(self.spec)
        self.contract.update({"allow_special_proxy": True, "special_proxy_reason": self.spec["special_proxy_reason"]})
        self.contract["actual_actor_records_sha256"] = canonical_sha256([record])
        self.contract["actors"][0]["actual_semantic_sha256"] = record["semantic_sha256"]
        self.bind_json("source_spec", "source-spec.json", self.spec)
        self.bind_json("actor_records", "actor-geometry.json", self.records)
        self.bind_rehashed_contract()
        self.rejected()

    def test_closeup_static_cannot_be_declared_clean(self):
        self.spec["actors"][0].update({"proxy_mode": "articulated_special", "hand_lod": "closeup_static"})
        self.spec.update({"allow_special_proxy": True, "special_proxy_reason": "diagnostic fixture"})
        self.contract["source_spec_sha256"] = canonical_sha256(self.spec)
        self.contract.update({"allow_special_proxy": True, "special_proxy_reason": "diagnostic fixture"})
        self.contract["actors"][0].update({"proxy_mode": "articulated_special", "hand_lod": "closeup_static",
                                             "purpose": "diagnostic-only", "allow_special_proxy": True,
                                             "special_proxy_reason": "diagnostic fixture"})
        self.bind_json("source_spec", "source-spec.json", self.spec)
        self.bind_rehashed_contract()
        self.rejected()

    def test_review_video_rejected(self):
        self.rejected([self.review])

    def test_optional_empty_review_variants_preserves_schema2(self):
        self.manifest["files"]["review_variants"] = []
        self.save_manifest()
        result = inspect_manifest(self.manifest_path)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["excluded_reviews"], [result["excluded_review"]])

    def test_expanded_review_is_excluded_and_never_in_upload_videos(self):
        expanded = self.add_expanded_review()
        result = build_upload_list(self.manifest_path)
        self.assertEqual(result["excluded_review"]["path"], str(self.review))
        self.assertEqual([row["path"] for row in result["excluded_reviews"]], [str(self.review), str(expanded)])
        self.assertTrue(all(row["purpose"] == "review-only" for row in result["excluded_reviews"]))
        self.assertEqual([row["path"] for row in result["videos"]], [str(self.clean)])
        self.rejected([expanded])

    def test_renamed_expanded_review_video_rejected(self):
        expanded = self.add_expanded_review()
        renamed = self.root / "renamed-clean.mp4"
        renamed.write_bytes(expanded.read_bytes())
        self.rejected([renamed])

    def test_expanded_review_bytes_cannot_replace_clean(self):
        expanded = self.add_expanded_review()
        self.clean.write_bytes(expanded.read_bytes())
        self.rejected()

    def test_every_declared_review_variant_hash_is_checked(self):
        expanded = self.add_expanded_review()
        expanded.write_bytes(b"tampered expanded review")
        self.rejected()

    def test_missing_expanded_review_invalidates_manifest(self):
        expanded = self.add_expanded_review()
        expanded.unlink()
        self.rejected()

    def test_invalid_review_variants_shapes_rejected(self):
        for variants in (None, {}, "review-expanded.mp4", [None], ["review-expanded.mp4"]):
            with self.subTest(variants=variants):
                self.manifest["files"]["review_variants"] = variants
                self.rejected()

    def test_review_variant_requires_review_only_purpose(self):
        self.add_expanded_review()
        for purpose in (None, "model-reference", "unknown"):
            with self.subTest(purpose=purpose):
                self.manifest["files"]["review_variants"][0]["purpose"] = purpose
                self.rejected()

    def test_review_variant_cannot_have_clean_hash(self):
        expanded = self.add_expanded_review()
        expanded.write_bytes(self.clean.read_bytes())
        self.manifest["files"]["review_variants"][0]["sha256"] = digest(expanded.read_bytes())
        self.rejected()

    def test_review_variant_paths_must_be_unique_and_contained(self):
        self.add_expanded_review()
        for path in ("review.mp4", "clean.mp4", "../review-expanded.mp4", str(self.review)):
            with self.subTest(path=path):
                self.manifest["files"]["review_variants"][0]["path"] = path
                self.rejected()

    def test_duplicate_review_variants_rejected(self):
        self.add_expanded_review()
        self.manifest["files"]["review_variants"].append(copy.deepcopy(self.manifest["files"]["review_variants"][0]))
        self.rejected()

    def test_same_review_bytes_at_distinct_paths_are_all_excluded(self):
        expanded = self.add_expanded_review()
        expanded.write_bytes(self.review.read_bytes())
        self.manifest["files"]["review_variants"][0]["sha256"] = digest(expanded.read_bytes())
        self.save_manifest()
        result = inspect_manifest(self.manifest_path)
        self.assertEqual(len(result["excluded_reviews"]), 2)
        self.rejected([expanded])

    def test_review_renamed_to_unlisted_name_rejected(self):
        renamed = self.root / "looks_clean.mp4"
        renamed.write_bytes(self.review.read_bytes())
        self.rejected([renamed])

    def test_review_replaces_clean_rejected(self):
        self.clean.write_bytes(self.review.read_bytes())
        self.rejected()

    def test_same_byte_clean_copy_is_not_allowlisted(self):
        copy_path = self.root / "clean_copy.mp4"
        copy_path.write_bytes(self.clean.read_bytes())
        self.rejected([copy_path])

    def test_tampered_clean_bytes_rejected(self):
        self.clean.write_bytes(b"changed clean content")
        self.rejected()

    def test_tampered_clean_manifest_hash_rejected(self):
        self.manifest["files"]["clean_video"]["sha256"] = "0" * 64
        self.manifest["reference_policy"]["allowlist"][0]["sha256"] = "0" * 64
        self.rejected()

    def test_tampered_allowlist_hash_rejected(self):
        self.manifest["reference_policy"]["allowlist"][0]["sha256"] = "f" * 64
        self.rejected()

    def test_review_tampering_also_invalidates_dual_manifest(self):
        self.review.write_bytes(b"changed review content")
        self.rejected()

    def test_review_allowlist_purpose_rejected(self):
        self.manifest["reference_policy"]["allowlist"][0]["purpose"] = "review-only"
        self.rejected()

    def test_missing_or_wrong_review_purpose_rejected(self):
        for purpose in (None, "model-reference", "unknown"):
            with self.subTest(purpose=purpose):
                self.manifest["files"]["review_video"]["purpose"] = purpose
                self.rejected()

    def test_missing_allowlist_purpose_rejected(self):
        del self.manifest["reference_policy"]["allowlist"][0]["purpose"]
        self.rejected()

    def test_clean_cannot_declare_review_purpose(self):
        self.manifest["files"]["clean_video"]["purpose"] = "review-only"
        self.rejected()

    def test_extra_or_empty_allowlist_rejected(self):
        allowed = copy.deepcopy(self.manifest["reference_policy"]["allowlist"][0])
        for value in ([], [allowed, allowed], None, {}):
            with self.subTest(value=value):
                self.manifest["reference_policy"]["allowlist"] = value
                self.rejected()

    def test_allowlist_cannot_target_review_even_with_model_purpose(self):
        self.manifest["reference_policy"]["allowlist"][0].update(self.manifest["files"]["review_video"])
        self.manifest["reference_policy"]["allowlist"][0]["purpose"] = "model-reference"
        self.rejected()

    def test_clean_review_same_hash_rejected(self):
        self.review.write_bytes(self.clean.read_bytes())
        self.manifest["files"]["review_video"]["sha256"] = digest(self.review.read_bytes())
        self.rejected()

    def test_clean_review_same_path_rejected(self):
        self.manifest["files"]["review_video"]["path"] = "clean.mp4"
        self.rejected()

    def test_unlisted_outside_directory_candidate_rejected(self):
        # Even a clean-byte duplicate outside the output root is disallowed.
        with tempfile.TemporaryDirectory(prefix="previs_guard_outside_") as outer:
            outside = Path(outer).resolve() / "clean.mp4"
            outside.write_bytes(self.clean.read_bytes())
            self.rejected([outside])

    def test_manifest_escape_and_absolute_paths_rejected(self):
        for path in ("../clean.mp4", str(self.clean), "C:/foreign/clean.mp4", "a/../clean.mp4", "./clean.mp4", "folder\\clean.mp4", "clean.mp4:stream", "folder//clean.mp4"):
            with self.subTest(path=path):
                self.manifest["files"]["clean_video"]["path"] = path
                self.manifest["reference_policy"]["allowlist"][0]["path"] = path
                self.rejected()

    def test_nested_declared_clean_path_is_allowed(self):
        nested = self.root / "media" / "clean.mp4"
        nested.parent.mkdir()
        self.clean.replace(nested)
        self.manifest["files"]["clean_video"]["path"] = "media/clean.mp4"
        self.manifest["reference_policy"]["allowlist"][0]["path"] = "media/clean.mp4"
        self.save_manifest()
        self.assertEqual(validate_references(self.manifest_path, [nested])[0]["path"], str(nested))

    def test_missing_media_rejected(self):
        self.review.unlink()
        self.rejected()

    def test_wrong_schema_rejected(self):
        for version in (1, 3, "2", True, None):
            with self.subTest(version=version):
                self.manifest["schema_version"] = version
                self.rejected()

    def test_unreadable_json_and_missing_manifest_rejected(self):
        for value in (b"{broken", b"\xff\xfe\xff", b"[]"):
            with self.subTest(value=value):
                self.manifest_path.write_bytes(value)
                with self.assertRaises(ReferenceError):
                    inspect_manifest(self.manifest_path)
        self.manifest_path.unlink()
        with self.assertRaises(ReferenceError):
            inspect_manifest(self.manifest_path)

    def test_changed_manifest_during_upload_list_build_rejected(self):
        # Simulate a concurrent regeneration between two local checks.
        altered = validate_references(self.manifest_path, [self.clean])
        altered[0]["reference_manifest_sha256"] = "0" * 64
        with patch("reference_guard.validate_references", return_value=altered):
            with self.assertRaises(ReferenceError):
                build_upload_list(self.manifest_path)

    def test_empty_or_duplicate_candidates_rejected(self):
        self.rejected([])
        self.rejected([self.clean, self.clean])

    def test_unknown_sha_format_rejected(self):
        for value in ("", "x" * 64, "A" * 64, None):
            with self.subTest(value=value):
                self.manifest["files"]["clean_video"]["sha256"] = value
                self.rejected()

    def test_symlink_outside_is_rejected_when_host_supports_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="previs_guard_link_") as other:
            target = Path(other).resolve() / "clean.mp4"
            target.write_bytes(self.clean.read_bytes())
            self.clean.unlink()
            try:
                self.clean.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Host cannot create symlinks without extra permissions: {exc}")
            self.rejected()

    def test_cli_local_output_and_review_rejection(self):
        destination = self.root / "upload-list.json"
        result = subprocess.run([sys.executable, str(SCRIPTS / "reference_guard.py"), str(self.manifest_path), "--output", str(destination)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["videos"][0]["path"], str(self.clean))
        refused = self.root / "must_not_exist.json"
        result = subprocess.run([sys.executable, str(SCRIPTS / "reference_guard.py"), str(self.manifest_path), "--video", str(self.review), "--output", str(refused)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("REFERENCE REJECTED", result.stderr)
        self.assertFalse(refused.exists())

    def test_cli_cannot_overwrite_manifest(self):
        original = self.manifest_path.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPTS / "reference_guard.py"), str(self.manifest_path), "--output", str(self.manifest_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.manifest_path.read_bytes(), original)

    def test_cli_cannot_overwrite_expanded_review(self):
        expanded = self.add_expanded_review()
        original = expanded.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPTS / "reference_guard.py"), str(self.manifest_path), "--output", str(expanded)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(expanded.read_bytes(), original)

    def request_file(self, video="clean.mp4"):
        request_path = self.root / "request.json"
        request = {
            "duration": 4, "model_version": "seedance2.0_vip", "video_resolution": "720p",
            "prompt": self.prompt, "videos": [{"path": video, "role": "camera_and_blocking"}],
            "review": {"accepted": True, "reviewer": "unit-test fixture", "basis": "mocked fixture; no production approval"},
        }
        request_path.write_text(json.dumps(request), encoding="utf-8")
        return request_path

    @staticmethod
    def fake_run(command, timeout=180):
        if command[0] == "mock-dreamina" and command[1:] == ["multimodal2video", "--help"]:
            return {"returncode": 0, "stdout": "--image --video --video_resolution --model_version --duration seedance2.0_vip", "stderr": ""}
        if command[0] == "ffprobe":
            return {"returncode": 0, "stdout": json.dumps({"format": {"duration": "4.0"}}), "stderr": ""}
        raise AssertionError("Unexpected external operation: " + repr(command))

    def test_adapter_missing_manifest_fails_before_external_cli(self):
        with patch.object(dreamina_job, "run") as mocked:
            with self.assertRaises(ReferenceError):
                dreamina_job.prepare(self.request_file(), "mock-dreamina")
            mocked.assert_not_called()

    def test_adapter_legacy_manifest_fails_before_external_cli(self):
        del self.manifest["proxy_reference"]
        self.save_manifest()
        with patch.object(dreamina_job, "run") as mocked:
            with self.assertRaises(ReferenceError):
                dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
            mocked.assert_not_called()

    def test_adapter_request_prompt_drift_fails_before_external_cli(self):
        request_path = self.request_file()
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["prompt"] += "Changed after binding"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        with patch.object(dreamina_job, "run") as mocked:
            with self.assertRaises(ReferenceError):
                dreamina_job.prepare(request_path, "mock-dreamina", self.manifest_path)
            mocked.assert_not_called()

    def test_adapter_rejects_command_prompt_changed_after_preparation(self):
        with patch.object(dreamina_job, "run", side_effect=self.fake_run):
            prepared = dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
        prepared["command"][prepared["command"].index("--prompt") + 1] += "Changed command prompt"
        with self.assertRaises(ReferenceError):
            dreamina_job.verify_prepared_references(prepared)

    def test_adapter_review_fails_before_external_cli(self):
        with patch.object(dreamina_job, "run") as mocked:
            with self.assertRaises(ReferenceError):
                dreamina_job.prepare(self.request_file("review.mp4"), "mock-dreamina", self.manifest_path)
            mocked.assert_not_called()

    def test_adapter_expanded_review_video_fails_before_external_cli(self):
        expanded = self.add_expanded_review()
        with patch.object(dreamina_job, "run") as mocked:
            with self.assertRaises(ReferenceError):
                dreamina_job.prepare(self.request_file(expanded.name), "mock-dreamina", self.manifest_path)
            mocked.assert_not_called()

    def test_adapter_expanded_review_fake_image_or_audio_fails_before_external_cli(self):
        expanded = self.add_expanded_review()
        for key, suffix in (("images", ".png"), ("audios", ".wav")):
            with self.subTest(modality=key):
                request_path = self.request_file()
                disguised = self.root / ("expanded-disguised" + suffix)
                disguised.write_bytes(expanded.read_bytes())
                request = json.loads(request_path.read_text(encoding="utf-8"))
                request[key] = [{"path": disguised.name, "role": "scene" if key == "images" else "sound"}]
                request_path.write_text(json.dumps(request), encoding="utf-8")
                with patch.object(dreamina_job, "run") as mocked:
                    with self.assertRaises(ReferenceError):
                        dreamina_job.prepare(request_path, "mock-dreamina", self.manifest_path)
                    mocked.assert_not_called()

    def test_adapter_valid_preparation_supports_default_and_expanded_review(self):
        self.add_expanded_review()
        with patch.object(dreamina_job, "run", side_effect=self.fake_run) as mocked:
            prepared = dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual([row["path"] for row in prepared["references"]], [str(self.clean)])
        dreamina_job.verify_prepared_references(prepared)

    def test_adapter_rejects_attachment_replaced_with_review_after_preparation(self):
        expanded = self.add_expanded_review()
        for key, suffix in (("images", ".png"), ("audios", ".wav")):
            with self.subTest(modality=key):
                request_path = self.request_file()
                attachment = self.root / ("attachment" + suffix)
                attachment.write_bytes(b"DUMMY valid non-review reference")
                request = json.loads(request_path.read_text(encoding="utf-8"))
                request[key] = [{"path": attachment.name, "role": "scene" if key == "images" else "sound"}]
                request_path.write_text(json.dumps(request), encoding="utf-8")
                with patch.object(dreamina_job, "run", side_effect=self.fake_run):
                    prepared = dreamina_job.prepare(request_path, "mock-dreamina", self.manifest_path)
                attachment.write_bytes(expanded.read_bytes())
                with self.assertRaises(ReferenceError):
                    dreamina_job.verify_prepared_references(prepared)

    def test_adapter_prepared_clean_reference_contains_manifest_binding(self):
        with patch.object(dreamina_job, "run", side_effect=self.fake_run) as mocked:
            prepared = dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(prepared["references"][0]["purpose"], "model-reference")
        self.assertEqual(prepared["reference_manifest_sha256"], digest(self.manifest_path.read_bytes()))
        self.assertIn(str(self.clean), prepared["command"])
        self.assertNotIn(str(self.review), prepared["command"])

    def test_adapter_rechecks_bytes_before_submission(self):
        with patch.object(dreamina_job, "run", side_effect=self.fake_run):
            prepared = dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
        self.clean.write_bytes(self.review.read_bytes())
        with self.assertRaises(ReferenceError):
            dreamina_job.verify_prepared_references(prepared)

    def test_adapter_rechecks_manifest_before_submission(self):
        with patch.object(dreamina_job, "run", side_effect=self.fake_run):
            prepared = dreamina_job.prepare(self.request_file(), "mock-dreamina", self.manifest_path)
        self.manifest["notes"] = "This is a newer manifest"
        self.save_manifest()
        with self.assertRaises(ReferenceError):
            dreamina_job.verify_prepared_references(prepared)

    def test_adapter_rejects_review_bytes_disguised_as_image(self):
        request_path = self.request_file()
        disguised = self.root / "review_disguised.png"
        disguised.write_bytes(self.review.read_bytes())
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["images"] = [{"path": disguised.name, "role": "scene"}]
        request_path.write_text(json.dumps(request), encoding="utf-8")
        with patch.object(dreamina_job, "run", side_effect=self.fake_run):
            with self.assertRaises(ReferenceError):
                dreamina_job.prepare(request_path, "mock-dreamina", self.manifest_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
