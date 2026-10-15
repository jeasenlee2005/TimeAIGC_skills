"""Portable, immutable character-registry provenance for v2.2 previews.

The snapshot proves which confirmed dimensions were rendered. The live project
proves those used forms and the scene calibration are still current. A changed
registry file is not itself stale: unrelated actors, aliases and same-content
path relinks can change its bytes without changing the selected forms.

This is local consistency checking, not a signed attestation. Deliberately
rewriting every trusted input and evidence record cannot be detected by hashes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


class RegistryBindingError(ValueError):
    """A preview cannot be proved current against its project registry."""


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(ok, message):
    if not ok:
        raise RegistryBindingError(message)


def _digest(value, label):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
             label + " must be a lowercase SHA256")
    return value


def safe_member(root, entry, label):
    """Resolve canonical POSIX members without traversal or filesystem aliases."""
    _require(isinstance(entry, dict), label + " must be an object")
    relative = entry.get("path")
    _require(isinstance(relative, str) and relative and "\\" not in relative
             and ":" not in relative and "\x00" not in relative,
             label + ".path must be a relative POSIX path")
    path = PurePosixPath(relative)
    _require(not path.is_absolute() and not PureWindowsPath(relative).drive
             and not any(part in (".", "..", "") for part in relative.split("/"))
             and str(path) == relative, label + ".path must remain inside its root")
    root = Path(root).resolve()
    proposed = root.joinpath(*path.parts)
    resolved = proposed.resolve()
    _require(resolved.is_relative_to(root) and proposed == resolved,
             label + " uses an escaping path or filesystem alias")
    _require(resolved.is_file(), label + " file is missing: " + relative)
    if "sha256" in entry:
        _digest(entry["sha256"], label + ".sha256")
        _require(sha256_file(resolved) == entry["sha256"], label + " SHA256 mismatch")
    return resolved


def _read_json(path, label):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryBindingError(label + " is not readable UTF-8 JSON") from exc
    _require(isinstance(data, dict), label + " must be a JSON object")
    return data


def _used_forms(binding):
    forms = binding.get("used_forms")
    _require(isinstance(forms, list) and forms, "binding.used_forms must be nonempty")
    result = {}
    for item in forms:
        _require(isinstance(item, dict), "binding used form must be an object")
        character_id, form_id = item.get("character_id"), item.get("form_id")
        _require(isinstance(character_id, str) and character_id
                 and isinstance(form_id, str) and form_id, "used form requires stable character_id/form_id")
        key = (character_id, form_id)
        _require(key not in result, "duplicate character/form in binding")
        result[key] = _digest(item.get("semantic_sha256"), "used form semantic_sha256")
    return result


def _validate_snapshot(binding, snapshot, index_snapshot):
    from character_registry import canonical_hash, form_semantic_fingerprint
    _require(snapshot.get("schema") == "timeaigc-character-registry/v1", "unsupported registry snapshot schema")
    _require(snapshot.get("project_id") == binding.get("project_id"), "registry snapshot project_id differs")
    source = binding.get("source_document")
    _require(isinstance(source, dict), "binding requires source_document")
    _require(not index_snapshot.get("project_id") or index_snapshot.get("project_id") == binding.get("project_id"),
             "project index snapshot belongs to another project")
    _require(index_snapshot.get("character_registry", {}).get("path") == binding.get("registry", {}).get("path"),
             "snapshot project index registry path differs")
    sources = dict(index_snapshot.get("authoritative_sources", {}))
    sources.update(snapshot.get("authoritative_sources", {}))
    original_source = sources.get(source.get("kind"))
    _require(isinstance(original_source, dict), "snapshot lacks the bound complete-project source")
    _require(original_source.get("sha256") == source.get("sha256")
             and original_source.get("path") == source.get("path"), "snapshot source document binding differs")
    statuses = ("provided", "confirmed") if source.get("kind") == "screenplay" else ("confirmed",)
    _require(original_source.get("scope") == "complete_project"
             and original_source.get("status") in statuses
             and bool(original_source.get("confirmation_source")), "snapshot source must be complete with recorded provenance")
    _digest(source.get("sha256"), "source_document.sha256")
    inventory = snapshot.get("cast_inventory", {})
    _require(canonical_hash(inventory) == binding.get("cast_inventory_sha256"), "snapshot cast inventory hash differs")
    _require(inventory.get("status") == "confirmed"
             and inventory.get("source_sha256") == source.get("sha256"),
             "snapshot cast inventory must be confirmed against the complete-project source")
    for (character_id, form_id), expected in _used_forms(binding).items():
        _require(character_id in inventory.get("character_ids", []), "snapshot used character is missing from project cast inventory")
        character = snapshot.get("characters", {}).get(character_id)
        _require(isinstance(character, dict), "snapshot used character is missing: " + character_id)
        form = character.get("forms", {}).get(form_id)
        _require(isinstance(form, dict), "snapshot used form is missing: " + character_id + "/" + form_id)
        _require(form.get("confirmation", {}).get("status") == "confirmed",
                 "snapshot used form was not confirmed")
        measurements = form.get("measurements")
        _require(isinstance(measurements, dict) and measurements,
                 "snapshot used form has no confirmed measurements")
        for measurement in measurements.values():
            _require(isinstance(measurement, dict)
                     and measurement.get("confirmation", {}).get("status") == "confirmed",
                     "snapshot used form measurement was not confirmed")
        _require(form_semantic_fingerprint(character, form) == expected,
                 "snapshot used form semantic fingerprint differs: " + character_id + "/" + form_id)
    _require(snapshot.get("scene_units", {}).get(binding.get("scene_id")) == binding.get("scene_units"),
             "snapshot scene calibration differs from binding")


def find_project_root(binding, artifact_root, project_root=None):
    """An explicit root or matching ancestor is required; no global search."""
    expected_id = binding.get("project_id")
    _require(isinstance(expected_id, str) and expected_id, "binding project_id is required")
    if project_root is not None:
        selected = Path(project_root).resolve()
        _require((selected / ".timeaigc/project.json").is_file(), "explicit project root lacks .timeaigc/project.json")
        return selected
    start = Path(artifact_root).resolve()
    for candidate in (start, *start.parents):
        index_path = candidate / ".timeaigc/project.json"
        if not index_path.is_file():
            continue
        index = _read_json(index_path, "project index")
        if index.get("project_id") == expected_id:
            return candidate
        registry_entry = index.get("character_registry")
        if isinstance(registry_entry, dict):
            try:
                registry = _read_json(safe_member(candidate, {"path": registry_entry.get("path")}, "project registry"), "project registry")
            except RegistryBindingError:
                continue
            if registry.get("project_id") == expected_id:
                return candidate
    raise RegistryBindingError("Current project unavailable: keep the preview inside its project or pass --project-root; registry snapshot alone cannot authorize generation")


def prepare_registry_binding(binding, project_root, output_dir):
    """Freeze the registry bytes; caller writes returned registry-binding.json.

    Existing identical snapshots are reusable. Existing different bytes are
    refused so reruns cannot silently rewrite preview history.
    """
    from character_registry import validate_character_binding
    _require(isinstance(binding, dict) and binding.get("schema") == "timeaigc-character-binding/v1",
             "current character binding schema required")
    project_root = Path(project_root).resolve()
    _digest(binding.get("registry", {}).get("sha256"), "registry.sha256")
    result = validate_character_binding(binding, project_root)
    _require(result.get("valid") is True, "character binding is stale or incomplete: " + json.dumps(result.get("issues", []), ensure_ascii=False))
    registry_path = safe_member(project_root, binding.get("registry"), "source registry")
    payload = registry_path.read_bytes()
    snapshot = _read_json(registry_path, "source registry")
    index_path = safe_member(project_root, {"path": ".timeaigc/project.json"}, "project index")
    index_payload = index_path.read_bytes()
    _validate_snapshot(binding, snapshot, _read_json(index_path, "project index"))
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = {"registry_snapshot": ("registry-snapshot.json", payload),
                 "project_index_snapshot": ("project-index-snapshot.json", index_payload)}
    asset_snapshots = []
    for character_id, form_id in _used_forms(binding):
        model = snapshot["characters"][character_id]["forms"][form_id]["model"]
        if model.get("choice") == "existing_model":
            descriptor = safe_member(project_root, model.get("asset_contract"), "source asset contract")
            data = descriptor.read_bytes()
            filename = "asset-contracts/asset-%03d.json" % len(asset_snapshots)
            snapshots["asset_contract_%03d" % len(asset_snapshots)] = (filename, data)
            asset_snapshots.append({"character_id": character_id, "form_id": form_id,
                                    "path": filename, "sha256": hashlib.sha256(data).hexdigest()})
    for filename, data in snapshots.values():
        destination = output_dir / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        _require(destination.resolve().is_relative_to(output_dir) and not destination.parent.is_symlink(),
                 "snapshot directory must not escape the preview")
        if destination.exists():
            _require(destination.is_file() and not destination.is_symlink() and destination.read_bytes() == data,
                     "refusing to replace a different project snapshot; choose a new preview version")
        else:
            with destination.open("xb") as stream:
                stream.write(data)
    frozen = copy.deepcopy(binding)
    for key, (filename, data) in snapshots.items():
        if not key.startswith("asset_contract_"):
            frozen[key] = {"path": filename, "sha256": hashlib.sha256(data).hexdigest()}
    if asset_snapshots:
        frozen["asset_contract_snapshots"] = asset_snapshots
    return frozen


def validate_asset_contract_snapshots(binding, snapshot, artifact_root):
    """Return verified descriptor contents keyed by character then form."""
    from character_registry import UNIT_FACTORS
    from blend_asset_contract import validate_asset_contract
    expected = {}
    for character_id, form_id in _used_forms(binding):
        form = snapshot["characters"][character_id]["forms"][form_id]
        if form["model"].get("choice") == "existing_model":
            expected[(character_id, form_id)] = form
    entries = binding.get("asset_contract_snapshots", [])
    _require(isinstance(entries, list), "asset_contract_snapshots must be a list")
    seen, contracts, paths = set(), {}, []
    for entry in entries:
        _require(isinstance(entry, dict), "asset contract snapshot must be an object")
        key = (entry.get("character_id"), entry.get("form_id"))
        _require(all(isinstance(x, str) for x in key) and key in expected and key not in seen,
                 "unexpected or duplicate asset contract snapshot")
        seen.add(key)
        form = expected[key]
        _require(entry.get("sha256") == form["model"].get("asset_contract", {}).get("sha256"),
                 "asset contract snapshot hash differs from frozen registry")
        path = safe_member(artifact_root, entry, "asset contract snapshot")
        descriptor = _read_json(path, "asset contract snapshot")
        dimensions = {name: round(float(item["value"]) * UNIT_FACTORS[item["unit"]], 12)
                      for name, item in form["measurements"].items()}
        try:
            descriptor = validate_asset_contract(descriptor, measurements_m=dimensions,
                shape_kind=form["kind"], collection=form["model"]["collection"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RegistryBindingError("frozen asset contract is incompatible: " + str(exc)) from exc
        contracts.setdefault(key[0], {})[key[1]] = descriptor
        paths.append(path)
    _require(seen == set(expected), "missing immutable asset contract snapshot for existing_model")
    return contracts, paths


def copy_registry_snapshots(binding, source_root, destination_root):
    """Carry immutable provenance to a derivative output without self-copying."""
    source_root, destination_root = Path(source_root).resolve(), Path(destination_root).resolve()
    if source_root == destination_root:
        return []
    entries = [{"path": "registry-binding.json"}, binding["registry_snapshot"],
               binding["project_index_snapshot"], *binding.get("asset_contract_snapshots", [])]
    copied = []
    for entry in entries:
        source = safe_member(source_root, entry, "source registry snapshot")
        destination = destination_root / entry["path"]
        _require(destination.resolve().is_relative_to(destination_root)
                 and not destination.is_symlink(), "derivative snapshot path escapes output")
        payload = source.read_bytes()
        if destination.exists():
            _require(destination.is_file() and destination.read_bytes() == payload,
                     "refusing to replace different derivative registry evidence")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(payload)
        copied.append(str(destination))
    return copied


def validate_registry_binding(binding, project_root=None, artifact_root=None):
    """Validate immutable provenance and freshness against current project files."""
    from character_registry import PreflightBlocked, load_project_context, validate_character_binding
    _require(isinstance(binding, dict) and binding.get("schema") == "timeaigc-character-binding/v1",
             "current character binding schema required")
    _require(artifact_root is not None, "artifact_root is required for frozen registry verification")
    _digest(binding.get("registry", {}).get("sha256"), "registry.sha256")
    _digest(binding.get("registry_snapshot", {}).get("sha256"), "registry_snapshot.sha256")
    _digest(binding.get("project_index_snapshot", {}).get("sha256"), "project_index_snapshot.sha256")
    snapshot_path = safe_member(artifact_root, binding.get("registry_snapshot"), "registry snapshot")
    index_snapshot_path = safe_member(artifact_root, binding.get("project_index_snapshot"), "project index snapshot")
    _require(binding["registry_snapshot"].get("sha256") == binding.get("registry", {}).get("sha256"),
             "snapshot must contain exactly the originally bound registry bytes")
    snapshot = _read_json(snapshot_path, "registry snapshot")
    _validate_snapshot(binding, snapshot, _read_json(index_snapshot_path, "project index snapshot"))
    asset_contracts, asset_snapshot_paths = validate_asset_contract_snapshots(binding, snapshot, artifact_root)
    current_root = find_project_root(binding, artifact_root, project_root)
    result = validate_character_binding(binding, current_root)
    _require(result.get("valid") is True, "preview registry binding is stale or incomplete: " + json.dumps(result.get("issues", []), ensure_ascii=False))
    # Staleness is selected-form specific. Authorizing new generation still
    # requires the project-wide preflight; incomplete unrelated additions do
    # not rewrite old history but cannot silently bypass the complete cast gate.
    try:
        current_context = load_project_context(current_root, scene_id=binding.get("scene_id"))
    except PreflightBlocked as exc:
        raise RegistryBindingError("Full-project preflight incomplete; existing preview history is preserved: "
                                   + json.dumps(exc.report, ensure_ascii=False)) from exc
    index_path = current_root / ".timeaigc/project.json"
    index = _read_json(index_path, "project index")
    live_registry = safe_member(current_root, {"path": index.get("character_registry", {}).get("path")}, "current registry")
    protected = [snapshot_path, index_snapshot_path, index_path, live_registry,
                 safe_member(current_root, current_context["source_document"], "current complete-project source")]
    protected.extend(asset_snapshot_paths)
    for character_id, form_id in _used_forms(binding):
        model = current_context["registry"]["characters"][character_id]["forms"][form_id]["model"]
        if model.get("path"):
            protected.append(safe_member(current_root, model, "current used model"))
        if model.get("choice") == "existing_model":
            protected.append(safe_member(current_root, model.get("asset_contract"), "current asset contract"))
    return {"status": "current-verified", "generation_eligible": True,
            "project_id": binding["project_id"], "project_root": str(current_root),
            "registry_snapshot": {"path": str(snapshot_path), "sha256": sha256_file(snapshot_path)},
            "project_index_snapshot": {"path": str(index_snapshot_path), "sha256": sha256_file(index_snapshot_path)},
            "current_registry": {"path": str(live_registry), "sha256": sha256_file(live_registry)},
            "used_forms": copy.deepcopy(binding["used_forms"]), "scene_id": binding["scene_id"],
            "source_document": copy.deepcopy(binding["source_document"]),
            "asset_contracts": asset_contracts,
            "protected_files": list(dict.fromkeys(str(path) for path in protected)),
            "verification_scope": "Frozen registry semantics and current used forms, complete-project source, model files, and confirmed scene calibration; not a signed or visual attestation."}


def validate_bound_actor_subset(binding, source_spec, registry_snapshot=None, asset_contract_snapshots=None):
    """A clip may instantiate only the exact forms its binding declares."""
    expected = _used_forms(binding)
    actors = source_spec.get("actors") if isinstance(source_spec, dict) else None
    _require(isinstance(actors, list) and actors, "registry-bound source needs explicit actors")
    observed = {}
    for actor in actors:
        _require(isinstance(actor, dict), "registry-bound actor must be an object")
        def value(primary, compatibility):
            if primary in actor and compatibility in actor:
                _require(actor[primary] == actor[compatibility], "ambiguous actor registry identity")
            return actor.get(primary, actor.get(compatibility))
        key = (value("registry_character_id", "character_id"), value("registry_form_id", "form_id"))
        _require(key in expected, "source actor is not a bound registered form")
        _require(value("registry_fingerprint", "registry_semantic_sha256") == expected[key], "source actor registry fingerprint differs")
        if actor.get("proxy_mode") == "existing_model" or actor.get("model_choice") == "existing_model":
            _require(registry_snapshot is not None, "existing_model requires frozen registry verification")
        if registry_snapshot is not None:
            from character_registry import UNIT_FACTORS
            character = registry_snapshot["characters"][key[0]]
            form = character["forms"][key[1]]
            dimensions = {name: round(float(item["value"]) * UNIT_FACTORS[item["unit"]], 12)
                          for name, item in form["measurements"].items()}
            _require(actor.get("color") == character["color"], "source actor color differs from frozen registry")
            _require(actor.get("model_choice") == form["model"]["choice"]
                     and actor.get("model_version") == form["model"]["version"],
                     "source actor model differs from frozen registry")
            _require(actor.get("measurements_m") == dimensions,
                     "source actor dimensions differ from frozen registry")
            if form["kind"] == "human":
                _require(actor.get("proxy_mode") == "cylinder", "registered human default must remain cylinder")
                height = value("height_m", "height")
                _require(height == dimensions["height"], "source actor height differs from frozen registry")
            else:
                mode = "existing_model" if form["model"]["choice"] == "existing_model" else "shape_proxy"
                _require(actor.get("proxy_mode") == mode and actor.get("shape_kind") == form["kind"],
                         "source actor shape differs from frozen registry")
                _require(actor.get("protrusions", []) == form.get("protrusions", []),
                         "source actor protrusions differ from frozen registry")
                if mode == "existing_model":
                    model = form["model"]
                    _require(actor.get("model_path") == model.get("path")
                             and actor.get("model_sha256") == model.get("sha256")
                             and actor.get("model_collection") == model.get("collection")
                             and actor.get("asset_contract_path") == model.get("asset_contract", {}).get("path")
                             and actor.get("asset_contract_sha256") == model.get("asset_contract", {}).get("sha256"),
                             "source existing-model identity differs from frozen registry")
                    descriptor = (asset_contract_snapshots or {}).get(key[0], {}).get(key[1])
                    _require(isinstance(descriptor, dict) and actor.get("asset_contract") == descriptor,
                             "source asset contract differs from immutable verified descriptor")
        observed[key] = expected[key]
    _require(observed == expected, "binding contains unused or missing clip forms")
    return True
