"""Pure contract checks for the deliberately narrow static .blend asset route.

The contract selects actual mesh parts by persisted ``timeaigc_asset_id``.
It never supplies replacement geometry or authorizes scaling to fit dimensions.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re

SCHEMA = 'timeaigc-blend-asset/v1'
GEOMETRY_SCHEMA = 'timeaigc-blend-geometry/v1'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _require(ok, text):
    if not ok:
        raise ValueError('blend_asset: ' + text)


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _ids(value, label):
    _require(isinstance(value, list) and value and all(isinstance(x, str) and x and len(x) <= 128 for x in value), label + ' requires nonempty stable object IDs')
    _require(len(set(value)) == len(value), label + ' contains repeated object IDs')


def validate_asset_contract(contract, measurements_m=None, shape_kind=None, collection=None, allow_draft=False):
    _require(isinstance(contract, dict) and contract.get('schema') == SCHEMA, 'unsupported asset contract schema')
    required = {'schema', 'collection', 'root_object', 'meters_per_unit', 'forward_axis', 'up_axis', 'tolerance_m', 'shape_kind', 'ground_objects', 'body_objects', 'head_objects', 'front_marker_objects', 'color_objects', 'measurement_rules'}
    _require(required <= contract.keys(), 'contract missing fields: ' + ', '.join(sorted(required - contract.keys())))
    _require(not (contract.keys() - required - {'expected_geometry_sha256', 'notes'}), 'unknown asset contract fields')
    for key in ('collection', 'root_object'):
        _require(isinstance(contract[key], str) and bool(contract[key]) and len(contract[key]) <= 128, key + ' must be a stable name/ID')
    _require(_finite(contract['meters_per_unit']) and 1e-6 <= contract['meters_per_unit'] <= 1000, 'meters_per_unit outside supported range')
    _require(_finite(contract['tolerance_m']) and 0 < contract['tolerance_m'] <= .001, 'tolerance_m must be positive and at most 0.001 m')
    _require(contract['forward_axis'] in ('+X', '-X', '+Y', '-Y') and contract['up_axis'] == '+Z', 'only horizontal axis-aligned source forward and +Z up supported')
    _require(contract['shape_kind'] in ('quadruped', 'other'), 'only registered nonhuman static meshes supported')
    if shape_kind is not None:
        _require(contract['shape_kind'] == shape_kind, 'contract shape_kind differs from registry')
    if collection is not None:
        _require(contract['collection'] == collection, 'selected collection differs from contract')
    for key in ('ground_objects', 'body_objects', 'head_objects', 'front_marker_objects', 'color_objects'):
        _ids(contract[key], key)
    rules = contract['measurement_rules']
    _require(isinstance(rules, dict) and rules, 'measurement_rules must be nonempty')
    for name, rule in rules.items():
        _require(isinstance(name, str) and name and isinstance(rule, dict), 'invalid measurement rule')
        _require(set(rule) == {'objects', 'axis', 'mode'}, 'measurement rule requires exactly objects/axis/mode')
        _ids(rule['objects'], 'measurement_rules.' + name)
        _require(rule['axis'] in ('x', 'y', 'z') and rule['mode'] in ('extent', 'min', 'max'), 'measurement rule axis/mode invalid')
        semantic_axes = {'body_width': ('x', 'extent'), 'body_length': ('y', 'extent'),
                         'shoulder_height': ('z', 'max'), 'head_top_height': ('z', 'max'),
                         'body_height': ('z', 'extent'), 'horn_height': ('z', 'extent'),
                         'ear_height': ('z', 'extent'), 'tail_length': ('y', 'extent')}
        _require(name in semantic_axes and (rule['axis'], rule['mode']) == semantic_axes[name], 'measurement axis/mode must match registered physical semantics: ' + name)
        if name in ('body_width', 'body_length', 'body_height', 'shoulder_height'):
            _require(set(rule['objects']) == set(contract['body_objects']), 'body dimension rule must select exactly body_objects')
        elif name == 'head_top_height':
            _require(set(rule['objects']) == set(contract['head_objects']), 'head height rule must select exactly head_objects')
    if measurements_m is not None:
        _require(isinstance(measurements_m, dict) and set(measurements_m) == set(rules), 'measurement rule keys must exactly match registered measurements')
        _require(all(_finite(v) and (0 if name in ('horn_height', 'ear_height', 'tail_length') else .005) <= v <= 1000 for name, v in measurements_m.items()), 'registered dimensions outside supported range (.005..1000 m; optional appendages 0..1000 m)')
    expected = contract.get('expected_geometry_sha256')
    if expected is not None or not allow_draft:
        _require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'expected_geometry_sha256 is required; run inspect_blend_asset.py to prepare it')
    return copy.deepcopy(contract)


def _bounds(parts, ids):
    _require(all(name in parts for name in ids), 'contract selects a missing mesh object ID')
    lo = [min(parts[name]['bounds_m']['min'][i] for name in ids) for i in range(3)]
    hi = [max(parts[name]['bounds_m']['max'][i] for name in ids) for i in range(3)]
    return {'min': lo, 'max': hi, 'dimensions': [hi[i] - lo[i] for i in range(3)]}


def derive_geometry_evidence(parts_list, contract):
    """Derive physical measurements from normalized actual vertex bounds."""
    parts = {part['id']: part for part in parts_list}
    _require(len(parts) == len(parts_list) and parts, 'geometry part IDs must be unique and nonempty')
    measured = {}
    for name, rule in contract['measurement_rules'].items():
        bounds = _bounds(parts, rule['objects']); axis = 'xyz'.index(rule['axis'])
        measured[name] = bounds['dimensions' if rule['mode'] == 'extent' else rule['mode']][axis]
    bounds = _bounds(parts, list(parts))
    ground = _bounds(parts, contract['ground_objects'])['min'][2]
    marker = _bounds(parts, contract['front_marker_objects'])
    body = _bounds(parts, contract['body_objects']); head = _bounds(parts, contract['head_objects'])
    center = lambda b: [(b['min'][i] + b['max'][i]) / 2 for i in range(3)]
    colors = {}
    for name in contract['color_objects']:
        _require(name in parts, 'color_objects selects missing mesh')
        colors[name] = [parts[name]['materials'][index]['diffuse_color'][:3] for index in parts[name]['used_material_indices']]
    return {'bounds_m': bounds, 'measurements_m': measured, 'ground_min_m': ground,
            'forward_evidence': {'marker_center_m': center(marker), 'head_center_m': center(head), 'body_center_m': center(body), 'canonical_forward_axis': '-Y'},
            'color_evidence': colors}


def validate_geometry_record(record, contract, measurements_m, color):
    validate_asset_contract(contract, measurements_m=measurements_m)
    _require(isinstance(record, dict) and record.get('schema') == GEOMETRY_SCHEMA, 'unsupported geometry record')
    _require(set(record) == {'schema', 'parts', 'geometry_sha256', 'bounds_m', 'measurements_m', 'ground_min_m', 'forward_evidence', 'color_evidence'}, 'geometry record fields differ')
    parts = record.get('parts')
    _require(isinstance(parts, list) and bool(parts), 'geometry record needs mesh parts')
    for part in parts:
        _require(isinstance(part, dict) and set(part) == {'id', 'vertices', 'edges', 'polygons', 'mesh_sha256', 'canonical_matrix', 'bounds_m', 'materials', 'used_material_indices'}, 'geometry part fields differ')
        _require(isinstance(part['id'], str) and part['id'], 'mesh part ID missing')
        for key in ('vertices', 'edges', 'polygons'):
            _require(isinstance(part[key], int) and not isinstance(part[key], bool) and part[key] > 0, 'mesh part topology counts invalid')
        _require(isinstance(part['mesh_sha256'], str) and re.fullmatch('[0-9a-f]{64}', part['mesh_sha256']), 'mesh hash invalid')
        _require(isinstance(part['canonical_matrix'], list) and len(part['canonical_matrix']) == 16 and all(_finite(v) for v in part['canonical_matrix']), 'canonical transform invalid')
        bounds = part['bounds_m']
        _require(isinstance(bounds, dict) and set(bounds) == {'min', 'max', 'dimensions'} and all(isinstance(bounds[k], list) and len(bounds[k]) == 3 and all(_finite(v) for v in bounds[k]) for k in bounds), 'mesh vertex bounds invalid')
        _require(all(bounds['max'][i] >= bounds['min'][i] and abs(bounds['dimensions'][i] - bounds['max'][i] + bounds['min'][i]) <= 1e-8 for i in range(3)), 'mesh vertex bounds inconsistent')
        _require(isinstance(part['materials'], list) and bool(part['materials']), 'mesh material evidence missing')
        indices = part['used_material_indices']
        _require(isinstance(indices, list) and bool(indices) and len(set(indices)) == len(indices) and all(isinstance(i, int) and 0 <= i < len(part['materials']) for i in indices), 'used material indices invalid')
        for material in part['materials']:
            _require(isinstance(material, dict) and set(material) == {'diffuse_color', 'metallic', 'roughness', 'shader_sha256'}, 'only simple solid material evidence supported')
            _require(isinstance(material['diffuse_color'], list) and len(material['diffuse_color']) == 4 and all(_finite(v) and 0 <= v <= 1 for v in material['diffuse_color']), 'material diffuse color invalid')
            _require(all(_finite(material[k]) and 0 <= material[k] <= 1 for k in ('metallic', 'roughness')), 'material parameters invalid')
            _require(material['shader_sha256'] is None or isinstance(material['shader_sha256'], str) and re.fullmatch('[0-9a-f]{64}', material['shader_sha256']), 'solid shader identity invalid')
    expected = canonical_hash({'schema': GEOMETRY_SCHEMA, 'parts': parts})
    _require(record['geometry_sha256'] == expected == contract['expected_geometry_sha256'], 'actual mesh/topology/material/transform identity differs from registered asset contract')
    evidence = derive_geometry_evidence(parts, contract)
    _require(all(record[k] == value for k, value in evidence.items()), 'geometry evidence differs from actual part bounds')
    tolerance = contract['tolerance_m']
    for name, actual in evidence['measurements_m'].items():
        _require(abs(actual - measurements_m[name]) <= tolerance, 'actual geometry dimension differs: ' + name)
    indexed = {part['id']: part for part in parts}
    body = _bounds(indexed, contract['body_objects']); head = _bounds(indexed, contract['head_objects'])
    for key in ('horn_height', 'ear_height', 'tail_length'):
        if key not in contract['measurement_rules']: continue
        appendage = _bounds(indexed, contract['measurement_rules'][key]['objects'])
        axis = 1 if key == 'tail_length' else 2
        anchor = body['max'][1] if key == 'tail_length' else head['max'][2]
        _require(abs(appendage['min'][axis] - anchor) <= tolerance, key + ' part must begin at the measured body rear/head crown plane; overlapping roots require separate measured parts')
    _require(abs(evidence['ground_min_m']) <= tolerance and abs(evidence['bounds_m']['min'][2]) <= tolerance, 'actual mesh floor must coincide with registered root ground plane; no below-ground mesh allowed')
    forward = evidence['forward_evidence']
    _require(forward['marker_center_m'][1] < min(forward['body_center_m'][1], forward['head_center_m'][1]) - tolerance and forward['marker_center_m'][2] > tolerance, 'actual front marker is not ahead of both body and head in canonical -Y')
    _require(isinstance(color, (list, tuple)) and len(color) == 3 and all(_finite(v) and 0 <= v <= 1 for v in color), 'registered RGB invalid')
    for name, colors in evidence['color_evidence'].items():
        _require(colors and all(all(abs(rgb[i] - color[i]) <= 1e-6 for i in range(3)) for rgb in colors), 'source body material differs from registered color: ' + name)
    return True
