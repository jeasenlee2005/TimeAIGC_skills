"""Fail-closed local allowlist for clean previs video references.

This checks provenance, paths, purposes, and bytes, not the visual semantics of
an MP4. The manifest must come from the reviewed local dual-delivery pipeline.
Never turn an untrusted video into an approved reference by editing its hash.
No network or media submission is performed by this module or its CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Iterable


class ReferenceError(ValueError):
    """The requested reference is not a verified clean allowlist member."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> None:
    raise ReferenceError(message)


def _entry(value: object, label: str, *, require_mp4: bool = True) -> dict:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    path, digest = value.get("path"), value.get("sha256")
    if not isinstance(path, str) or not path or "\\" in path:
        _fail(f"{label}.path must be a nonempty relative POSIX path")
    parts = PurePosixPath(path).parts
    if (PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive
            or any(part in (".", "..") for part in path.split("/"))
            or not parts or "\x00" in path or ":" in path):
        _fail(f"{label}.path must stay inside the manifest directory")
    if str(PurePosixPath(path)) != path:
        _fail(f"{label}.path must use a canonical relative path")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        _fail(f"{label}.sha256 must be a lowercase SHA256 digest")
    if require_mp4 and PurePosixPath(path).suffix.lower() != ".mp4":
        _fail(f"{label}.path must identify an MP4")
    return value


def _resolve_member(root: Path, relative: str) -> Path:
    proposed = root.joinpath(*PurePosixPath(relative).parts)
    resolved = proposed.resolve()
    if not resolved.is_relative_to(root):
        _fail(f"Reference escapes manifest directory: {relative}")
    # An alias can hide the review filename. Bind the explicit manifest path,
    # not only a same-content file or an indirect filesystem alias.
    if proposed != resolved:
        _fail(f"Reference path must not use a symbolic-link/junction alias: {relative}")
    if not resolved.is_file():
        _fail(f"Reference file is missing: {relative}")
    return resolved


def validate_proxy_reference(manifest: dict, root: Path, *, require_current: bool = True) -> dict:
    """Bind current proxy modes to actual records, source, prompt, and clean bytes.

    This verifies a trusted pipeline's record and hashes; it does not recognize
    actor geometry from MP4 pixels. Historical manifests have no inferred mode.
    """
    binding = manifest.get("proxy_reference")
    if "proxy_reference" not in manifest:
        if require_current:
            _fail("Current proxy_reference contract required; legacy manifest is read-only and has no verified proxy mode")
        return {"proxy_contract_status": "legacy-unverified", "generation_eligible": False,
                "proxy_modes": None, "verification_scope": "Legacy byte verification only; actor modes are unknown, not inferred as cylinder."}
    if not isinstance(binding, dict) or binding.get("schema") != "timeaigc-proxy-reference/v1":
        _fail("Unsupported or malformed proxy_reference schema")
    if binding.get("purpose") != "model-reference":
        _fail("proxy_reference requires purpose: model-reference")
    clean = manifest["files"]["clean_video"]
    if binding.get("clean_video_sha256") != clean["sha256"]:
        _fail("Proxy reference is not bound to the declared clean video SHA256")
    bound_files = {}
    used_paths = set()
    for key in ("mode_contract", "source_spec", "source_storyboard", "actor_records", "generation_prompt", "prompt_coverage"):
        entry = _entry(binding.get(key), "proxy_reference." + key, require_mp4=False)
        path = _resolve_member(root, entry["path"])
        if path in used_paths:
            _fail("Proxy reference files must have distinct declared paths")
        used_paths.add(path)
        if sha256_file(path) != entry["sha256"]:
            _fail(f"Proxy reference {key} SHA256 does not match manifest")
        bound_files[key] = {"path": str(path), "sha256": entry["sha256"]}
    try:
        def read_json(key):
            return json.loads(Path(bound_files[key]["path"]).read_text(encoding="utf-8-sig"))
        contract, spec, records = read_json("mode_contract"), read_json("source_spec"), read_json("actor_records")
        coverage = read_json("prompt_coverage")
        if isinstance(records, dict) and "actors" in records:
            records = records["actors"]
        source_text = Path(bound_files["source_storyboard"]["path"]).read_bytes().decode("utf-8")
        prompt_text = Path(bound_files["generation_prompt"]["path"]).read_bytes().decode("utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceError("Proxy reference source files are not readable UTF-8/JSON") from exc
    if not source_text.strip() or not prompt_text.strip():
        _fail("Source storyboard and generation prompt must both be nonempty")
    if not isinstance(spec, dict) or source_text != spec.get("storyboard_text"):
        _fail("Bound source storyboard must exactly match source_spec.storyboard_text")
    if source_text not in prompt_text:
        _fail("Generation prompt must retain the complete source storyboard text verbatim")
    try:
        from proxy_contract import validate_proxy_contract
        from generation_prompt import validate_prompt_coverage
        validated = validate_proxy_contract(contract, spec=spec, actor_records=records, for_clean=True)
        validate_prompt_coverage(prompt_text, spec, validated, coverage)
    except (ImportError, TypeError, KeyError, ValueError) as exc:
        raise ReferenceError("Proxy mode contract failed validation: " + str(exc)) from exc
    return {
        "proxy_contract_status": "current-verified", "generation_eligible": True,
        "proxy_modes": [{"id": actor["id"], "proxy_mode": actor["proxy_mode"], "purpose": actor["purpose"]}
                        for actor in validated["actors"]],
        "proxy_contract_sha256": validated["contract_sha256"],
        "bound_files": bound_files,
        "verification_scope": "Trusted pipeline declarations, actual actor records, source/prompt, and byte hashes; no automatic visual-semantic detection.",
    }


def validate_generation_prompt(inspected: dict, prompt: object) -> dict:
    """Require the request prompt to be the current bound UTF-8 prompt text."""
    if not inspected.get("generation_eligible"):
        _fail("Legacy proxy reference cannot be used for a new generation request")
    entry = inspected["proxy_reference"]["bound_files"]["generation_prompt"]
    path = Path(entry["path"])
    if sha256_file(path) != entry["sha256"]:
        _fail("Bound generation prompt changed after manifest validation")
    expected = path.read_bytes().decode("utf-8")
    if not isinstance(prompt, str) or prompt != expected:
        _fail("Request prompt differs from the manifest-bound generation_prompt text")
    return {"path": str(path), "sha256": entry["sha256"],
            "proxy_contract_sha256": inspected["proxy_reference"]["proxy_contract_sha256"]}


def inspect_manifest(manifest_path: str | Path, *, require_proxy_contract: bool = False,
                     project_root: str | Path | None = None) -> dict:
    """Validate clean/default review/optional review variants and their hashes.

    All declared file paths are relative to the manifest's containing directory.
    ``files.review_variants`` is an optional list of additional review-only MP4
    entries. The default remains ``files.review_video`` for schema-2 callers.
    Requiring all declared review bytes helps reject stale delivery manifests.
    """
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file():
        _fail("Reference manifest is missing")
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceError("Reference manifest is not readable UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] not in (2, 3):
        _fail("Reference manifest requires schema_version: 2 or 3")
    version = manifest["schema_version"]
    if version == 2 and "registry_binding" in manifest:
        _fail("Registry-bound reference requires schema_version: 3; downgrade is forbidden")
    files = manifest.get("files")
    if not isinstance(files, dict):
        _fail("Manifest files must be an object")
    clean = _entry(files.get("clean_video"), "files.clean_video")
    review = _entry(files.get("review_video"), "files.review_video")
    variants = files.get("review_variants", [])
    if not isinstance(variants, list):
        _fail("files.review_variants must be a list when present")
    reviews = [("files.review_video", review)]
    reviews.extend((f"files.review_variants[{index}]", _entry(value, f"files.review_variants[{index}]"))
                   for index, value in enumerate(variants))
    if clean.get("purpose", "model-reference") != "model-reference":
        _fail("Clean video cannot have a review-only or unknown purpose")
    for label, entry in reviews:
        if entry.get("purpose") != "review-only":
            _fail(f"{label} must explicitly declare purpose: review-only")
        if clean["path"] == entry["path"] or clean["sha256"] == entry["sha256"]:
            _fail(f"Clean video and {label} must have different paths and bytes")
    policy = manifest.get("reference_policy")
    allowlist = policy.get("allowlist") if isinstance(policy, dict) else None
    if not isinstance(allowlist, list) or len(allowlist) != 1:
        _fail("Allowlist must contain exactly the declared clean video")
    allowed = _entry(allowlist[0], "reference_policy.allowlist[0]")
    if allowed.get("purpose") != "model-reference":
        _fail("Allowlisted reference requires purpose: model-reference")
    if (allowed["path"], allowed["sha256"]) != (clean["path"], clean["sha256"]):
        _fail("Allowlist must bind the exact clean video path and SHA256")
    root = manifest_path.parent
    clean_path = _resolve_member(root, clean["path"])
    if sha256_file(clean_path) != clean["sha256"]:
        _fail("Clean file SHA256 does not match manifest")
    declared_paths = {clean_path}
    excluded_reviews = []
    for label, entry in reviews:
        path = _resolve_member(root, entry["path"])
        if path in declared_paths:
            _fail(f"{label} resolves to a duplicate clean/review path")
        declared_paths.add(path)
        if sha256_file(path) != entry["sha256"]:
            _fail(f"{label} file SHA256 does not match manifest")
        excluded_reviews.append({"path": str(path), "purpose": "review-only", "sha256": entry["sha256"]})
    proxy_reference = validate_proxy_reference(manifest, root, require_current=require_proxy_contract or version == 3)
    registry_reference = None
    if proxy_reference.get("bound_files"):
        spec_path = Path(proxy_reference["bound_files"]["source_spec"]["path"])
        spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
        has_registry_identity = any(isinstance(actor, dict) and
                                   any(key in actor for key in ("registry_character_id", "character_id", "registry_form_id"))
                                   for actor in spec.get("actors", []))
        if version == 2 and (has_registry_identity or "registry_binding_sha256" in spec or "project_id" in spec):
            _fail("Registry-bound source_spec cannot downgrade to a schema-2 manifest")
    if version == 3:
        try:
            from registry_binding import validate_registry_binding, validate_bound_actor_subset
            entry = _entry(manifest.get("registry_binding"), "registry_binding", require_mp4=False)
            binding_path = _resolve_member(root, entry["path"])
            if sha256_file(binding_path) != entry["sha256"]:
                _fail("Registry binding SHA256 differs from preview manifest")
            binding = json.loads(binding_path.read_text(encoding="utf-8-sig"))
            if spec.get("registry_binding_sha256") != entry["sha256"]:
                _fail("source_spec registry binding SHA256 differs from preview manifest")
            if spec.get("project_id") != binding.get("project_id"):
                _fail("source_spec project_id differs from registry binding")
            registry_reference = validate_registry_binding(binding, project_root=project_root, artifact_root=root)
            snapshot = json.loads(Path(registry_reference["registry_snapshot"]["path"]).read_text(encoding="utf-8-sig"))
            validate_bound_actor_subset(binding, spec, snapshot, registry_reference["asset_contracts"])
            registry_reference["binding_file"] = {"path": str(binding_path), "sha256": entry["sha256"]}
            registry_reference["protected_files"].append(str(binding_path))
        except (ImportError, OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ReferenceError("Registry binding validation failed: " + str(exc)) from exc
    return {
        "schema_version": version,
        "reference_manifest": str(manifest_path),
        "reference_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "approved_videos": [{
            "path": str(clean_path),
            "relative_path": clean["path"],
            "sha256": clean["sha256"],
            "purpose": "model-reference",
        }],
        "excluded_review": excluded_reviews[0],
        "excluded_reviews": excluded_reviews,
        "proxy_reference": proxy_reference,
        "registry_reference": registry_reference,
        "proxy_contract_status": proxy_reference["proxy_contract_status"],
        "generation_eligible": proxy_reference["generation_eligible"],
        "verification_scope": proxy_reference["verification_scope"],
    }


def validate_references(manifest_path: str | Path, video_paths: Iterable[str | Path], *, require_proxy_contract: bool = True,
                        project_root: str | Path | None = None) -> list[dict]:
    """Validate explicit candidate paths. Relative candidates use manifest root.

    Same-byte copies and renamed review videos are not allowlist members.
    Empty and duplicate candidate lists are rejected to keep upload lists clear.
    """
    inspected = inspect_manifest(manifest_path, require_proxy_contract=require_proxy_contract, project_root=project_root)
    approved = inspected["approved_videos"][0]
    root = Path(inspected["reference_manifest"]).parent
    candidates = list(video_paths)
    if not candidates:
        _fail("At least one explicitly allowlisted clean video is required")
    if len(candidates) != 1:
        _fail("Only the single manifest-declared clean video may be referenced")
    candidate = Path(candidates[0])
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        _fail("Requested video is outside the manifest directory")
    if candidate != resolved:
        _fail("Requested video must not use traversal or a filesystem alias")
    if resolved != Path(approved["path"]):
        _fail("Requested video is not the manifest-declared clean allowlist path")
    # Recheck candidate at the boundary used by the calling upload adapter.
    if not resolved.is_file() or sha256_file(resolved) != approved["sha256"]:
        _fail("Requested video no longer matches the approved SHA256")
    return [{**approved, "reference_manifest_sha256": inspected["reference_manifest_sha256"],
             "proxy_contract_status": inspected["proxy_contract_status"],
             "generation_eligible": inspected["generation_eligible"]}]


def build_upload_list(manifest_path: str | Path, *, project_root: str | Path | None = None) -> dict:
    """Generate the video upload list solely from the checked clean allowlist."""
    inspected = inspect_manifest(manifest_path, require_proxy_contract=True, project_root=project_root)
    approved = inspected["approved_videos"]
    videos = validate_references(manifest_path, [row["path"] for row in approved], project_root=project_root)
    if any(row["reference_manifest_sha256"] != inspected["reference_manifest_sha256"] for row in videos):
        _fail("Reference manifest changed while the upload list was being built")
    return {
        "schema_version": inspected["schema_version"],
        "reference_manifest": inspected["reference_manifest"],
        "reference_manifest_sha256": inspected["reference_manifest_sha256"],
        "videos": videos,
        "excluded_review": inspected["excluded_review"],
        "excluded_reviews": inspected["excluded_reviews"],
        "proxy_reference": inspected["proxy_reference"],
        "registry_reference": inspected["registry_reference"],
        "proxy_contract_status": inspected["proxy_contract_status"],
        "generation_eligible": inspected["generation_eligible"],
        "verification_scope": inspected["verification_scope"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Local schema-2 or schema-3 preview manifest")
    parser.add_argument("--project-root", help="Current project containing .timeaigc/project.json; otherwise discover a matching ancestor")
    parser.add_argument("--video", action="append", help="Check explicit video; may only be declared clean output")
    parser.add_argument("--output", help="Write local clean-only upload list (no submission)")
    parser.add_argument("--legacy-inspect", action="store_true", help="Read-only historical byte verification; never create a generation upload list")
    args = parser.parse_args()
    try:
        if args.legacy_inspect:
            if args.output or args.video:
                _fail("--legacy-inspect is read-only and cannot be combined with --output or --video")
            print(json.dumps(inspect_manifest(args.manifest, project_root=args.project_root), ensure_ascii=False, indent=2))
            return
        if args.video:
            validate_references(args.manifest, args.video, project_root=args.project_root)
        result = build_upload_list(args.manifest, project_root=args.project_root)
        content = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            destination = Path(args.output).resolve()
            protected = {Path(result["reference_manifest"]),
                         *(Path(row["path"]) for row in result["excluded_reviews"]),
                         *(Path(row["path"]) for row in result["videos"]),
                         *(Path(row["path"]) for row in result["proxy_reference"]["bound_files"].values())}
            if result.get("registry_reference"):
                protected.update(Path(path) for path in result["registry_reference"]["protected_files"])
            if destination in protected:
                _fail("Upload-list output must not overwrite manifest, media, or bound project sources")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content + "\n", encoding="utf-8")
        print(content)
    except (ReferenceError, OSError) as exc:
        parser.exit(2, f"REFERENCE REJECTED: {exc}\n")


if __name__ == "__main__":
    main()
