"""Project-wide character preflight. Pure Python; never infers a cast from a clip.

All persisted paths are project relative. Confirmations are provenance supplied by
the project owner, not evidence that this module obtained an interactive answer.
Blender must separately measure the recorded scene calibration before rendering.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA = "timeaigc-character-registry/v1"
BINDING_SCHEMA = "timeaigc-character-binding/v1"
UNIT_FACTORS = {"m": 1.0, "cm": 0.01, "mm": 0.001}
DIMENSION_SEMANTICS = {
    "height": "sole_to_crown_including_head",
    "shoulder_height": "ground_to_withers",
    "head_top_height": "ground_to_head_top_excluding_protrusions",
    "body_height": "body_vertical_extent_excluding_protrusions",
    "body_length": "body_fore_aft_extent_excluding_tail",
    "body_width": "body_lateral_extent_excluding_protrusions",
    "horn_height": "horn_vertical_extent_above_head_top",
    "ear_height": "ear_vertical_extent_above_head_top",
    "tail_length": "tail_extension_beyond_body_rear",
}
REQUIRED_DIMENSIONS = {
    "human": ("height",),
    "quadruped": ("shoulder_height", "head_top_height", "body_length", "body_width"),
    "other": ("body_height", "body_length", "body_width"),
}
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _positive(value, *, allow_zero=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value >= 0 if allow_zero else value > 0))


def _confirmed(record):
    return isinstance(record, dict) and record.get("status") == "confirmed" and _text(record.get("source"))


def _question(code, message, **details):
    return {"code": code, "question": message, **details}


class PreflightBlocked(ValueError):
    def __init__(self, questions, *, stage="character_preflight"):
        self.report = {"schema": "timeaigc-preflight-report/v1", "status": "needs_user_input",
                       "stage": stage, "questions": questions, "generation_allowed": False}
        super().__init__("; ".join(q["question"] for q in questions))


def project_path(project_root, relative_path, *, must_exist=True):
    """Reject absolute, drive, traversal, alternate streams and symlink escapes."""
    root = Path(project_root).resolve()
    if not _text(relative_path) or "\\" in relative_path or ":" in relative_path or "\x00" in relative_path:
        raise ValueError("project paths must be nonempty relative paths using /")
    posix = PurePosixPath(relative_path)
    if (posix.is_absolute() or PureWindowsPath(relative_path).is_absolute()
            or any(part in ("..", ".", "") for part in relative_path.split("/"))
            or str(posix) != relative_path):
        raise ValueError("project path escapes or is not normalized")
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("project path resolves outside project") from exc
    if must_exist and not target.is_file():
        raise ValueError("registered project file is missing: " + relative_path)
    return target


def _read_json(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError("JSON document must be an object")
    return obj


def _atomic_json(path, value, expected_sha256):
    path = Path(path)
    payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(mode="wb", prefix=".registry-update-", suffix=".tmp", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        if file_sha256(path) != expected_sha256:
            raise RuntimeError("project file changed while preparing update; re-read before merging")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_index(root):
    try:
        path = project_path(root, ".timeaigc/project.json")
        return path, _read_json(path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise PreflightBlocked([_question("missing_project_index",
            "请提供项目索引或完整项目目录，以便在新任务中从项目文件接续；不能依赖聊天记忆。", detail=str(exc))],
            stage="project_discovery") from exc


def _select_source(root, index, registry):
    sources = dict(index.get("authoritative_sources", {})) if isinstance(index.get("authoritative_sources"), dict) else {}
    sources.update(registry.get("authoritative_sources", {}))
    conflicts = []
    for kind in ("segments", "screenplay"):
        record = sources.get(kind)
        if not isinstance(record, dict):
            continue
        statuses = ("confirmed",) if kind == "segments" else ("provided", "confirmed")
        if record.get("scope") != "complete_project" or record.get("status") not in statuses:
            continue
        if not _text(record.get("confirmation_source")):
            continue
        try:
            path = project_path(root, record.get("path"))
        except ValueError as exc:
            conflicts.append(_question("source_path_unavailable", "请定位已登记的全项目剧情文件；尺寸登记会保留。",
                                       source_kind=kind, detail=str(exc)))
            continue
        actual_hash = file_sha256(path)
        if not path.read_bytes().strip():
            conflicts.append(_question("empty_full_source", "全项目剧情文件为空，请补充完整资料。", source_kind=kind))
            continue
        if actual_hash != record.get("sha256"):
            raise PreflightBlocked([_question("source_content_conflict",
                "已确认的全项目剧情内容发生变化，请确认新版本及全项目角色清单；保留已有尺寸。",
                source_kind=kind, path=record.get("path"), recorded_sha256=record.get("sha256"), actual_sha256=actual_hash)],
                stage="full_project_source")
        return {"kind": kind, "path": record["path"], "sha256": actual_hash}
    raise PreflightBlocked([_question("missing_full_project_source",
        "请提供已确认的第一阶段全项目片段汇总；若没有，请提供完整剧本。收到前暂停角色预检，不能从当前片段推断全体角色。",
        available_source_issues=conflicts)], stage="full_project_source")


def _normal_measurements(form):
    result = {}
    for name, item in form.get("measurements", {}).items():
        result[name] = round(float(item["value"]) * UNIT_FACTORS[item["unit"]], 12)
    return result


def _semantic_record(character, form):
    measurements = _normal_measurements(form)
    model = form["model"]
    if not isinstance(model, dict):
        raise ValueError("model record must be an object")
    protrusions = [{key: item[key] for key in ("id", "kind", "bounds_min_m", "bounds_max_m")}
                   for item in form.get("protrusions", [])]
    model_semantics = {key: model[key] for key in ("choice", "version", "sha256", "collection") if key in model}
    if model.get("choice") == "existing_model":
        descriptor = model.get("asset_contract")
        model_semantics["asset_contract_sha256"] = descriptor.get("sha256") if isinstance(descriptor, dict) else None
    return {"kind": form["kind"], "color": [float(x) for x in character["color"]],
            "measurements_m": measurements,
            "measurement_semantics": {key: form["measurements"][key]["semantic"] for key in sorted(measurements)},
            "model": model_semantics,
            "protrusions": protrusions}


def load_existing_asset_contract(root, form):
    """Verify portable metadata; Blender must separately inspect actual meshes.

    Passing this gate proves file identity and descriptor compatibility only.
    It never attests that a .blend is safe or geometrically correct to import.
    """
    from blend_asset_contract import validate_asset_contract
    model = form["model"]
    path = project_path(root, model.get("path"))
    if path.suffix.lower() != ".blend":
        raise ValueError("existing_model supports a registered local .blend file only")
    if not _text(model.get("collection")):
        raise ValueError("existing_model requires an explicit source collection")
    entry = model.get("asset_contract")
    if not isinstance(entry, dict) or not _HASH.fullmatch(str(entry.get("sha256", ""))):
        raise ValueError("existing_model requires asset_contract {path, sha256}")
    contract_path = project_path(root, entry.get("path"))
    if contract_path.suffix.lower() != ".json" or file_sha256(contract_path) != entry["sha256"]:
        raise ValueError("asset contract file/hash differs from the confirmed record")
    if form.get("protrusions"):
        raise ValueError("existing_model requires measured protrusion rules; explicit proxy envelope boxes are unsupported")
    return validate_asset_contract(_read_json(contract_path), measurements_m=_normal_measurements(form),
                                   shape_kind=form["kind"], collection=model["collection"])


def form_semantic_fingerprint(character, form):
    """Intrinsic semantics only: relocating files/images or aliases preserves scale."""
    return canonical_hash(_semantic_record(character, form))


def _validate_form(root, character_id, form_id, form, issues):
    initial_issue_count = len(issues)
    where = {"character_id": character_id, "form_id": form_id}
    if not isinstance(form, dict) or form.get("kind") not in REQUIRED_DIMENSIONS:
        issues.append(_question("missing_form_kind", "请确认角色形态是人形、四足或其他非人形。", **where))
        return
    kind = form["kind"]
    if not _confirmed(form.get("confirmation")):
        issues.append(_question("unconfirmed_form", "请确认该形态登记；变身必须使用独立形态 ID。", **where))
    measures = form.get("measurements", {})
    if not isinstance(measures, dict):
        measures = {}
    for name in REQUIRED_DIMENSIONS[kind]:
        if name not in measures:
            message = ("请确认脚底至头顶的总身高（包含头部），并注明单位。" if name == "height"
                       else "请确认该非人形独立尺寸及单位，不能混用肩高、头顶高、体长或附肢范围。")
            issues.append(_question("missing_measurement", message, measurement=name,
                                    semantic=DIMENSION_SEMANTICS[name], **where))
    for name, item in measures.items():
        allow_zero = name in ("horn_height", "ear_height", "tail_length")
        if (name not in DIMENSION_SEMANTICS or not isinstance(item, dict)
                or item.get("unit") not in UNIT_FACTORS
                or not _positive(item.get("value"), allow_zero=allow_zero)
                or item.get("semantic") != DIMENSION_SEMANTICS.get(name)):
            issues.append(_question("invalid_measurement", "请更正尺寸、单位及测量语义；数值必须有限且有效。",
                                    measurement=name, **where))
        elif not _confirmed(item.get("confirmation")):
            issues.append(_question("unconfirmed_measurement", "请确认此尺寸；已有其他确认尺寸会继续复用。",
                                    measurement=name, **where))
    model = form.get("model", {})
    choices = ("cylinder",) if kind == "human" else ("existing_model", "simple_proxy", "placeholder")
    if not isinstance(model, dict) or model.get("choice") not in choices:
        issues.append(_question("missing_model_choice", "请先选择此非人形使用已有模型、简化形状代理或占位代理。"
                                if kind != "human" else "普通人形请确认沿用固定圆柱加头简模。", choices=list(choices), **where))
    elif not _text(model.get("version")):
        issues.append(_question("missing_model_version", "请登记模型版本；不覆盖旧预演历史。", **where))
    if isinstance(model, dict) and model.get("choice") in choices:
        try:
            model_path = project_path(root, model.get("path"))
            if not _HASH.fullmatch(str(model.get("sha256", ""))) or file_sha256(model_path) != model.get("sha256"):
                raise ValueError("model file hash does not match the confirmed record")
            if model.get("choice") != "existing_model" and model_path.suffix.lower() == ".json":
                profile = _read_json(model_path)
                dependencies = profile.get("runtime_dependencies", {})
                if not isinstance(dependencies, dict):
                    raise ValueError("runtime_dependencies must be a filename to SHA256 mapping")
                scripts = Path(__file__).resolve().parent
                for dependency, expected in dependencies.items():
                    if (not isinstance(dependency, str) or Path(dependency).name != dependency
                            or "/" in dependency or "\\" in dependency or ":" in dependency
                            or not dependency.endswith(".py") or not _HASH.fullmatch(str(expected))):
                        issues.append(_question("model_dependency_conflict",
                            "模型定义的运行依赖记录无效，请复核模型版本；已确认尺寸会保留。", dependency=str(dependency), **where))
                        continue
                    target = (scripts / dependency).resolve()
                    if target.parent != scripts or not target.is_file() or file_sha256(target) != expected:
                        issues.append(_question("model_dependency_conflict",
                            "模型生成器或依赖已变化，请复核并确认新的模型版本；不会自动更新哈希或重新询问已确认尺寸。",
                            dependency=dependency, expected_sha256=expected, **where))
        except ValueError as exc:
            issues.append(_question("model_file_conflict", "请核实已有模型的位置与内容；原尺寸和模型选择会保留。",
                                    detail=str(exc), **where))
        if model.get("choice") == "existing_model":
            try:
                load_existing_asset_contract(root, form)
            except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
                issues.append(_question("existing_asset_contract_conflict",
                    "Existing model choice and confirmed dimensions are retained. Repair the local .blend collection or asset contract; Blender must still inspect actual geometry after this metadata gate.",
                    detail=str(exc), **where))
    protrusions = form.get("protrusions", [])
    if not isinstance(protrusions, list):
        issues.append(_question("invalid_protrusions", "请以独立附肢包围盒登记角、耳、尾等额外范围。", **where))
        return
    seen = set()
    for p in protrusions:
        valid = isinstance(p, dict) and _ID.fullmatch(str(p.get("id", ""))) and _text(p.get("kind"))
        if valid:
            lower, upper = p.get("bounds_min_m"), p.get("bounds_max_m")
            valid = (isinstance(lower, list) and isinstance(upper, list) and len(lower) == len(upper) == 3
                     and all(not isinstance(n, bool) and isinstance(n, (int, float)) and math.isfinite(n) for n in lower + upper)
                     and all(a < b for a, b in zip(lower, upper)))
            valid = valid and p["id"] not in seen and _confirmed(p.get("confirmation"))
        if not valid:
            issues.append(_question("invalid_protrusion", "请确认每个附肢在形态局部空间中的米制范围、名称和唯一 ID。", **where))
        elif valid:
            seen.add(p["id"])
    # Match the deliberately limited supported generator before starting Blender.
    # These are proxy support limits, not requests to change confirmed anatomy.
    if len(issues) == initial_issue_count:
        normalized = _normal_measurements(form)
        if kind == "human" and not .05 <= normalized["height"] <= 100:
            issues.append(_question("unsupported_proxy_dimensions",
                "已确认尺寸会保留；此固定人形代理仅验证过 0.05 至 100 米，请先准备适配所需尺度的代理。", **where))
        elif kind != "human" and model.get("choice") != "existing_model":
            if any(not .005 <= normalized[key] <= 1000 for key in REQUIRED_DIMENSIONS[kind]):
                issues.append(_question("unsupported_proxy_dimensions",
                    "已确认尺寸会保留；此形状代理的主体尺寸支持 0.005 至 1000 米，请先准备适配代理。", **where))
            elif kind == "quadruped" and normalized["head_top_height"] < normalized["shoulder_height"]:
                issues.append(_question("unsupported_lowered_head_proxy",
                    "头顶低于肩部的已确认尺寸会保留；当前简化四足代理不支持该构型，请准备经过复核的适配模型。", **where))
            else:
                try:
                    from shape_contract import expected_shape_protrusions
                    expected_shape_protrusions({"shape_kind": kind, "measurements_m": normalized, "protrusions": protrusions})
                except (ValueError, KeyError, TypeError) as exc:
                    issues.append(_question("conflicting_protrusion_representation",
                        "请核对附肢表示：同一角、耳或尾只能选尺寸延伸或明确包围盒之一，且类型与范围须受支持。",
                        detail=str(exc), **where))


def _validate_characters(root, registry, source):
    issues = []
    chars = registry.get("characters")
    if not isinstance(chars, dict) or not chars:
        raise PreflightBlocked([_question("missing_project_cast",
            "请根据完整项目资料一次性确认全项目角色、别名及不同形态清单，再批量补充缺失身高和模型选择。")])
    inventory = registry.get("cast_inventory", {})
    inventory_ids = inventory.get("character_ids") if isinstance(inventory, dict) else None
    if (not isinstance(inventory_ids, list) or any(not isinstance(x, str) for x in inventory_ids)
            or len(inventory_ids) != len(set(inventory_ids)) or set(inventory_ids) != set(chars)
            or inventory.get("status") != "confirmed" or not _text(inventory.get("confirmation_source"))
            or inventory.get("source_sha256") != source["sha256"]):
        issues.append(_question("unconfirmed_project_cast",
            "请核对并确认完整剧情对应的全项目角色清单（含未出现在当前片段中的角色）；程序不会声称已自动提取全部角色。"))
    aliases = {}
    for cid, char in chars.items():
        if not _ID.fullmatch(str(cid)) or not isinstance(char, dict):
            issues.append(_question("invalid_character_id", "请使用稳定且唯一的角色 ID。", character_id=cid))
            continue
        names = [cid, char.get("name")]
        if not _text(char.get("name")):
            issues.append(_question("missing_character_name", "请登记角色名称。", character_id=cid))
        if not isinstance(char.get("aliases", []), list) or any(not _text(x) for x in char.get("aliases", [])):
            issues.append(_question("invalid_aliases", "请登记有效的角色别名列表。", character_id=cid))
        else:
            names += char.get("aliases", [])
        for name in names:
            if not _text(name):
                continue
            normalized = name.strip().casefold()
            if normalized in aliases and aliases[normalized] != cid:
                issues.append(_question("alias_conflict", "同一别名指向多个角色，请明确对应关系；不会猜测。",
                                        alias=name, character_ids=[aliases[normalized], cid]))
            aliases[normalized] = cid
        color = char.get("color")
        if (not isinstance(color, list) or len(color) != 3
                or any(not _positive(x, allow_zero=True) or x > 1 for x in color)):
            issues.append(_question("invalid_character_color", "请登记 0 至 1 的 RGB 颜色以区分角色。", character_id=cid))
        if not _text(char.get("color_name")):
            issues.append(_question("missing_color_name", "请登记角色颜色名称。", character_id=cid))
        forms = char.get("forms")
        if not isinstance(forms, dict) or not forms:
            issues.append(_question("missing_forms", "请登记该角色的基础形态；变身使用不同形态 ID。", character_id=cid))
            continue
        for fid, form in forms.items():
            if not _ID.fullmatch(str(fid)):
                issues.append(_question("invalid_form_id", "请使用稳定且唯一的形态 ID。", character_id=cid, form_id=fid))
            else:
                _validate_form(root, cid, fid, form, issues)
    if issues:
        raise PreflightBlocked(issues)
    return aliases


def _select_scene(index, registry, scene_id):
    units = registry.get("scene_units", {})
    selected = scene_id or index.get("active_scene_id")
    if not selected and isinstance(units, dict) and len(units) == 1:
        selected = next(iter(units))
    record = units.get(selected) if isinstance(units, dict) else None
    problem = not isinstance(record, dict)
    if not problem:
        cal = record.get("calibration", {})
        problem = (record.get("status") != "confirmed" or not _text(record.get("source"))
                   or not _positive(record.get("meters_per_blender_unit"))
                   or not isinstance(cal, dict) or not _text(cal.get("object"))
                   or cal.get("axis") not in ("x", "y", "z")
                   or not _positive(cal.get("length_m")) or not _positive(cal.get("tolerance_m", 1e-5)))
    if problem:
        raise PreflightBlocked([_question("unconfirmed_scene_units",
            "请确认场景每 Blender 单位对应的米数及可实测标尺对象、轴和真实长度；不能只改单位标签或猜测尺度。",
            scene_id=selected)], stage="scene_scale")
    return selected, copy.deepcopy(record)


def load_project_context(project_root, *, scene_id=None):
    root = Path(project_root).resolve()
    index_path, index = _load_index(root)
    registry = {}
    registry_path = None
    registry_entry = index.get("character_registry", {})
    if isinstance(registry_entry, dict) and registry_entry.get("path"):
        try:
            registry_path = project_path(root, registry_entry["path"])
            registry = _read_json(registry_path)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise PreflightBlocked([_question("registry_file_unavailable",
                "请定位项目的角色登记文件；文件移动不代表已确认尺寸失效，不应重新猜测尺寸。", detail=str(exc))]) from exc
    # Source check intentionally precedes cast/measurement questions.
    source = _select_source(root, index, registry)
    if registry.get("schema") != SCHEMA or not registry_path:
        raise PreflightBlocked([_question("missing_character_registry",
            "已找到完整项目资料，请建立项目级角色登记并一次性确认全体角色及缺失尺寸和模型选择。")])
    if not _text(registry.get("project_id")) or (index.get("project_id") and index["project_id"] != registry["project_id"]):
        raise PreflightBlocked([_question("project_identity_conflict", "请确认索引与角色登记属于同一项目。")])
    aliases = _validate_characters(root, registry, source)
    selected_scene, units = _select_scene(index, registry, scene_id)
    return {"project_root": root, "index": index, "index_path": index_path,
            "registry": registry, "registry_path": registry_path,
            "registry_sha256": file_sha256(registry_path), "source_document": source,
            "scene_id": selected_scene, "scene_units": units, "aliases": aliases}


def resolve_clip_actors(project_root, actor_requests, *, scene_id=None):
    context = load_project_context(project_root, scene_id=scene_id)
    if not isinstance(actor_requests, list):
        raise PreflightBlocked([_question("invalid_clip_actors", "当前片段角色引用必须是列表。")])
    resolved, used, issues, ids = [], [], [], set()
    registry = context["registry"]
    for request in actor_requests:
        if not isinstance(request, dict) or not _ID.fullmatch(str(request.get("id", ""))) or request.get("id") in ids:
            issues.append(_question("invalid_clip_actor_id", "请为当前片段中的每个演员实例指定唯一 ID。"))
            continue
        ids.add(request["id"])
        ref = request.get("character_id") or request.get("character_alias")
        cid = context["aliases"].get(str(ref).strip().casefold())
        if not cid:
            issues.append(_question("unknown_character", "发现新角色或未登记别名，请先补充全项目角色登记；不会从片段创建未经确认的身高。",
                                    actor_id=request["id"], requested_character=ref))
            continue
        char = registry["characters"][cid]
        fid = request.get("form_id")
        if not fid or fid not in char["forms"]:
            issues.append(_question("unknown_form", "请选择已确认的形态 ID；新增变身先单独登记尺寸。",
                                    character_id=cid, requested_form=fid, available_forms=list(char["forms"])))
            continue
        form = char["forms"][fid]
        dimensions = _normal_measurements(form)
        forbidden = ("height", "height_m", "measurements", "measurements_m", "color", "color_name", "model",
                     "model_choice", "model_path", "model_sha256", "model_version", "proxy_mode", "hand_lod",
                     "scale", "body_scale", "form_scale", "shape_kind", "protrusions", "model_collection",
                     "asset_contract", "asset_contract_path", "asset_contract_sha256", "_project_root")
        if any(key in request for key in forbidden):
            issues.append(_question("clip_intrinsic_override", "镜头只能提供位置、朝向和姿态，不能在片段内覆盖已确认的固有尺寸或模型。",
                                    actor_id=request["id"], fields=[x for x in forbidden if x in request]))
            continue
        result = copy.deepcopy(request)
        result.update({"character_id": cid, "form_id": fid, "character_name": char["name"],
                       "character_aliases": list(char.get("aliases", [])),
                       "color": char["color"], "color_name": char["color_name"], "hand_lod": "none",
                       "registry_character_id": cid, "registry_form_id": fid,
                       "registry_fingerprint": form_semantic_fingerprint(char, form),
                       "registry_semantic_sha256": form_semantic_fingerprint(char, form),
                       "measurements_m": dimensions, "model_choice": form["model"]["choice"],
                       "model_version": form["model"]["version"], "model_path": form["model"]["path"],
                       "model_sha256": form["model"]["sha256"]})
        if form["kind"] == "human":
            result.update(height=dimensions["height"], proxy_mode="cylinder")
        else:
            result.update(proxy_mode="shape_proxy", shape_kind=form["kind"],
                          protrusions=copy.deepcopy(form.get("protrusions", [])))
            if form["model"]["choice"] == "existing_model":
                result.update(proxy_mode="existing_model", model_collection=form["model"]["collection"],
                              asset_contract_path=form["model"]["asset_contract"]["path"],
                              asset_contract_sha256=form["model"]["asset_contract"]["sha256"],
                              asset_contract=load_existing_asset_contract(context["project_root"], form))
        resolved.append(result)
        entry = {"character_id": cid, "form_id": fid,
                 "semantic_sha256": form_semantic_fingerprint(char, form)}
        if entry not in used:
            used.append(entry)
    if issues:
        raise PreflightBlocked(issues, stage="clip_cast")
    binding = {"schema": BINDING_SCHEMA, "project_id": registry["project_id"],
               "registry": {"path": context["registry_path"].relative_to(context["project_root"]).as_posix(),
                            "sha256": context["registry_sha256"]},
               "source_document": context["source_document"],
               "cast_inventory_sha256": canonical_hash(registry["cast_inventory"]),
               "used_forms": sorted(used, key=lambda item: (item["character_id"], item["form_id"])),
               "scene_id": context["scene_id"], "scene_units": context["scene_units"]}
    return resolved, binding


def _scene_semantics(scene_units):
    cal = scene_units["calibration"]
    return {"meters_per_blender_unit": float(scene_units["meters_per_blender_unit"]),
            "calibration": {"object": cal["object"], "axis": cal["axis"], "length_m": float(cal["length_m"]),
                            "tolerance_m": float(cal.get("tolerance_m", 1e-5))}}


def validate_character_binding(binding, project_root):
    """Check current selected forms. Snapshot hash is provenance, not freshness.

    Unrelated confirmed cast additions do not stale this clip. Changes to the
    full source, used intrinsic forms, or selected scene calibration do.
    """
    issues, stale = [], []
    if not isinstance(binding, dict) or binding.get("schema") != BINDING_SCHEMA:
        return {"valid": False, "issues": [{"code": "invalid_character_binding"}], "stale_used_forms": []}
    try:
        root = Path(project_root).resolve()
        _, index = _load_index(root)
        registry = _read_json(project_path(root, index["character_registry"]["path"]))
        source = _select_source(root, index, registry)
        scene_id, scene_units = _select_scene(index, registry, binding.get("scene_id"))
        context = {"registry": registry, "source_document": source, "scene_id": scene_id, "scene_units": scene_units}
    except (PreflightBlocked, ValueError, OSError, KeyError, TypeError) as exc:
        return {"valid": False, "issues": [{"code": "current_project_preflight_blocked", "detail": str(exc)}],
                "stale_used_forms": []}
    registry = context["registry"]
    if binding.get("source_scene") is not None:
        try:
            from scene_binding import validate_scene_binding
            validate_scene_binding(binding, root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            issues.append({"code": "scene_asset_changed", "detail": str(exc)})
    if binding.get("project_id") != registry["project_id"]:
        issues.append({"code": "project_identity_changed"})
    source = binding.get("source_document", {})
    if any(source.get(key) != context["source_document"][key] for key in ("kind", "sha256")):
        issues.append({"code": "full_project_source_changed"})
    try:
        if _scene_semantics(binding["scene_units"]) != _scene_semantics(context["scene_units"]):
            issues.append({"code": "scene_scale_changed"})
    except (KeyError, TypeError, ValueError):
        issues.append({"code": "invalid_scene_unit_binding"})
    entries = binding.get("used_forms")
    if not isinstance(entries, list):
        issues.append({"code": "invalid_used_forms"})
        entries = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append({"code": "invalid_used_form"})
            continue
        cid, fid = entry.get("character_id"), entry.get("form_id")
        key = (cid, fid)
        if (not isinstance(cid, str) or not isinstance(fid, str) or key in seen
                or not _HASH.fullmatch(str(entry.get("semantic_sha256", "")))):
            issues.append({"code": "invalid_used_form"})
            continue
        seen.add(key)
        char = registry["characters"].get(cid)
        form = char.get("forms", {}).get(fid) if char else None
        form_issues = []
        if form:
            _validate_form(root, cid, fid, form, form_issues)
        if form_issues:
            issues.append({"code": "used_form_invalid", "character_id": cid, "form_id": fid, "questions": form_issues})
        try:
            different = not form or form_semantic_fingerprint(char, form) != entry["semantic_sha256"]
        except (KeyError, TypeError, ValueError, OverflowError):
            different = True
            issues.append({"code": "malformed_used_form", "character_id": cid, "form_id": fid})
        if different or form_issues:
            stale.append({"character_id": cid, "form_id": fid})
    if stale:
        issues.append({"code": "used_form_semantics_changed", "forms": stale})
    return {"valid": not issues, "issues": issues, "stale_used_forms": stale}


def mark_stale_previews(project_root, *, timestamp=None):
    """Append index stale events; never edits old manifest/video/registry records.

    Supported index previews are a list of {manifest, character_binding?} records
    or dict values of that shape. character_binding may be a binding dict or a
    relative JSON file. A manifest can expose character_binding inline or path.
    Unknown legacy records are reported as unverified, not silently approved.
    """
    root = Path(project_root).resolve()
    index_path, original = _load_index(root)
    index_hash = file_sha256(index_path)
    updated = copy.deepcopy(original)
    previews = updated.get("previews", [])
    records = previews.values() if isinstance(previews, dict) else previews if isinstance(previews, list) else []
    results = []
    when = timestamp or datetime.now(timezone.utc).isoformat()
    for record in records:
        if not isinstance(record, dict):
            results.append({"status": "legacy_unverified"})
            continue
        try:
            value = record.get("character_binding")
            if value is None and record.get("manifest"):
                manifest_path = project_path(root, record["manifest"])
                manifest = _read_json(manifest_path)
                value = manifest.get("registry_binding", manifest.get("character_binding"))
                if isinstance(value, dict) and "path" in value and "schema" not in value:
                    pointer = value
                    value = (manifest_path.parent / pointer["path"]).relative_to(root).as_posix()
                    if pointer.get("sha256") and file_sha256(project_path(root, value)) != pointer["sha256"]:
                        raise ValueError("historical binding pointer hash mismatch")
            if isinstance(value, str):
                value = _read_json(project_path(root, value))
            if value is None:
                results.append({"manifest": record.get("manifest"), "status": "legacy_unverified"})
                continue
            validation = validate_character_binding(value, root)
            if not validation["valid"]:
                event = {"event": "marked_stale", "checked_at": when, "issues": validation["issues"]}
                history = record.setdefault("status_history", [])
                # Repeated checks do not create endless duplicate events.
                if not history or history[-1].get("issues") != event["issues"]:
                    history.append(event)
                record["status"] = "stale"
            results.append({"manifest": record.get("manifest"), "status": "current" if validation["valid"] else "stale",
                            "validation": validation})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            results.append({"manifest": record.get("manifest"), "status": "unverified", "detail": str(exc)})
    if updated != original:
        _atomic_json(index_path, updated, index_hash)
    return {"updated": updated != original, "previews": results, "historical_files_modified": False}


def integrate_project_registry(project_root, registry_path=".timeaigc/character-registry.json"):
    """Merge only the registry link; preserve every existing project/index note."""
    root = Path(project_root).resolve()
    project_path(root, registry_path)
    index_path, index = _load_index(root)
    old_hash = file_sha256(index_path)
    existing = index.get("character_registry", {})
    if existing and not isinstance(existing, dict):
        raise ValueError("existing character_registry link has incompatible shape")
    index["character_registry"] = {**existing, "path": registry_path, "schema": SCHEMA}
    _atomic_json(index_path, index, old_hash)
    return index["character_registry"]


def register_preview(project_root, manifest_path):
    """Append a validated new schema-3 manifest to the existing project index."""
    root = Path(project_root).resolve()
    candidate = Path(manifest_path)
    if candidate.is_absolute():
        relative = candidate.resolve().relative_to(root).as_posix()
    else:
        relative = candidate.as_posix()
    path = project_path(root, relative)
    manifest = _read_json(path)
    if manifest.get("schema_version") != 3:
        raise ValueError("project registration requires schema_version 3")
    descriptor = manifest.get("registry_binding")
    if not isinstance(descriptor, dict):
        raise ValueError("manifest missing registry_binding descriptor")
    binding_relative = (path.parent / descriptor["path"]).resolve().relative_to(root).as_posix()
    binding_path = project_path(root, binding_relative)
    if file_sha256(binding_path) != descriptor.get("sha256"):
        raise ValueError("registry binding bytes do not match manifest")
    binding = _read_json(binding_path)
    validation = validate_character_binding(binding, root)
    if not validation["valid"]:
        raise ValueError("cannot register stale/unverified character binding: " + json.dumps(validation))
    index_path, index = _load_index(root)
    original_hash = file_sha256(index_path)
    if index.get("project_id") and index["project_id"] != binding["project_id"]:
        raise ValueError("preview belongs to another project")
    previews = index.setdefault("previews", [])
    if not isinstance(previews, (list, dict)):
        raise ValueError("unsupported previews index shape; preserve and request mapping")
    records = list(previews.values()) if isinstance(previews, dict) else previews
    if any((isinstance(item, str) and item == relative) or (isinstance(item, dict) and item.get("manifest") == relative) for item in records):
        raise ValueError("preview path/version already registered; never overwrite historical entry")
    record = {"manifest": relative, "manifest_sha256": file_sha256(path), "status": "ready",
              "character_binding": binding_relative, "project_id": binding["project_id"],
              "scene_id": binding["scene_id"], "used_forms": copy.deepcopy(binding["used_forms"]),
              "status_history": [{"event": "registered", "checked_at": datetime.now(timezone.utc).isoformat()}]}
    if isinstance(previews, list):
        previews.append(record)
    else:
        if relative in previews:
            raise ValueError("preview key already exists")
        previews[relative] = record
    _atomic_json(index_path, index, original_hash)
    return {"registered": True, "manifest": relative, "historical_entries_preserved": True}


def update_confirmed_form(project_root, character_id, form_id, new_form, *, reason, timestamp=None):
    """Apply already-confirmed input, retain the previous form, mark old previews.

    This is not an interactive confirmation mechanism. A caller must preserve the
    actual owner's confirmation provenance in new_form before invoking it.
    """
    if not _text(reason):
        raise ValueError("form update requires a reason")
    root = Path(project_root).resolve()
    _, index = _load_index(root)
    registry_path = project_path(root, index["character_registry"]["path"])
    original_hash = file_sha256(registry_path)
    registry = _read_json(registry_path)
    char = registry["characters"][character_id]
    problems = []
    _validate_form(root, character_id, form_id, new_form, problems)
    if problems:
        raise PreflightBlocked(problems)
    old_form = char["forms"].get(form_id)
    event = {"event": "confirmed_form_revision", "character_id": character_id, "form_id": form_id,
             "recorded_at": timestamp or datetime.now(timezone.utc).isoformat(), "reason": reason,
             "prior_form": copy.deepcopy(old_form),
             "prior_semantic_sha256": form_semantic_fingerprint(char, old_form) if old_form else None,
             "new_semantic_sha256": form_semantic_fingerprint(char, new_form)}
    if old_form == new_form:
        return {"updated": False, "stale_check": mark_stale_previews(root)}
    registry.setdefault("revision_history", []).append(event)
    char["forms"][form_id] = copy.deepcopy(new_form)
    _atomic_json(registry_path, registry, original_hash)
    return {"updated": True, "revision": event, "stale_check": mark_stale_previews(root)}
