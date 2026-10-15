"""Pure-Python contract binding proxy requests to actually built actor records.

Default cylinder has no limb or hand geometry. ``standard`` is accepted only as
a legacy cylinder input and normalizes to ``none``. Articulated references need
an explicit mode plus a clip/spec-level switch and a reason. This is metadata
and geometry validation, never an IK, acting or contact test.
"""
import hashlib
import json
import math
from shape_contract import expected_shape_protrusions

SCHEMA = 'timeaigc-proxy-contract/v1'
CYLINDER_PARTS = {'body_cylinder', 'head', 'nose', 'front_marker'}


def assert_no_known_proxy_objects(objects):
    """Reject known TimeAIGC actor metadata in a claimed empty source scene.

    This conservative check recognizes our own metadata/part IDs; it cannot
    identify arbitrary imported human meshes. It never edits the source scene.
    """
    known_parts = CYLINDER_PARTS | {'neck', 'thorax', 'waist', 'pelvis'}
    known_parts |= {side + '_' + name for side in ('R', 'L')
                    for name in ('arm', 'forearm', 'thigh', 'shin', 'foot', 'palm', 'thumb')}
    for obj in objects:
        if (obj.get('generator_version') is not None or obj.get('proxy_mode') is not None
                or obj.get('hand_lod') is not None or obj.get('part_id') in known_parts):
            name = getattr(obj, 'name', None) or obj.get('name', '<unnamed>')
            raise ValueError(f'source scene contains known proxy actor/object {name}; use a genuinely empty scene asset')
    return True


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, label, low, high):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool)
             and math.isfinite(value) and low <= value <= high, label)
    return float(value)


def _string(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label)
    return value


def _color(value):
    _require(isinstance(value, (list, tuple)) and len(value) == 3, 'color must have three RGB values')
    return [_number(item, 'color must contain finite values in 0..1', 0, 1) for item in value]


def resolve_actor_requests(spec, *, for_clean=True):
    """Normalize a clip or fixture spec without inventing actor identity/colors."""
    _require(isinstance(spec, dict), 'proxy spec must be an object')
    _require(isinstance(spec.get('actors'), list) and spec['actors'], 'explicit actors are required')
    _require(type(spec.get('allow_special_proxy', False)) is bool, 'allow_special_proxy must be boolean')
    allow = spec.get('allow_special_proxy', False)
    reason = spec.get('special_proxy_reason', '')
    _require(isinstance(reason, str), 'special_proxy_reason must be a string')
    result, seen = [], set()
    for actor in spec['actors']:
        _require(isinstance(actor, dict), 'each actor must be an object')
        actor_id = _string(actor.get('id'), 'actor id is required')
        _require(actor_id not in seen, 'duplicate actor id')
        _require(all(char.isalnum() or char in '_-' for char in actor_id), 'unsafe actor id')
        seen.add(actor_id)
        name = _string(actor.get('character_name', actor_id), 'character_name must be nonempty')
        mode = actor.get('proxy_mode', spec.get('proxy_mode', 'cylinder'))
        _require(mode in ('cylinder', 'articulated_special', 'shape_proxy', 'existing_model'), 'unknown proxy_mode')
        lod = actor.get('hand_lod', 'none' if mode in ('cylinder','shape_proxy','existing_model') else 'standard')
        if mode in ('cylinder','shape_proxy','existing_model'):
            _require(lod in (None, 'none', 'standard'), 'cylinder cannot contain a diagnostic or articulated hand')
            lod, purpose, actor_allow, actor_reason = 'none', 'model-reference', False, ''
        else:
            _require(allow is True and bool(reason.strip()),
                     'articulated_special requires clip/spec allow_special_proxy:true and a nonempty special_proxy_reason')
            _require(lod in ('standard', 'closeup_static'), 'unknown articulated hand_lod')
            purpose = 'diagnostic-only' if lod == 'closeup_static' else 'model-reference'
            actor_allow, actor_reason = True, reason
        _require(not for_clean or purpose == 'model-reference', 'closeup_static is diagnostic-only and cannot be a clean reference')
        height = actor.get('height', actor.get('height_m', 1.76))
        if 'height' in actor and 'height_m' in actor:
            _require(actor['height'] == actor['height_m'], 'ambiguous height/height_m values')
        color = _color(actor.get('color'))
        color_name = actor.get('color_name')
        if color_name is None:
            color_name = '#' + ''.join(f'{round(component * 255):02X}' for component in color)
        result.append({'id': actor_id, 'character_name': name,
                       'color_name': _string(color_name, 'color_name must be nonempty'),
                       'color': color, 'height_m': _number(height, 'height must be .05..100 meters', .05, 100),
                       'proxy_mode': mode, 'hand_lod': lod, 'purpose': purpose,
                       'allow_special_proxy': actor_allow, 'special_proxy_reason': actor_reason})
        for key in ('registry_character_id','registry_form_id','registry_fingerprint','character_aliases','shape_kind',
                    'model_choice','model_path','model_sha256','model_version','measurements_m','protrusions',
                    'model_collection','asset_contract_path','asset_contract_sha256','asset_contract'):
            if key in actor: result[-1][key]=actor[key]
        if mode in ('shape_proxy','existing_model'):
            _require(actor.get('model_choice') in (('existing_model',) if mode=='existing_model' else ('simple_proxy','placeholder')), 'nonhuman proxy requires an explicit supported model choice')
            _require(actor.get('shape_kind') in ('quadruped','other'), 'shape kind must identify nonhuman dimensions')
            measures=actor.get('measurements_m')
            _require(isinstance(measures,dict) and measures, 'shape measurements_m required')
            for key,value in measures.items():
                _number(value, 'shape measurements must be finite valid meters', 0 if key in ('horn_height','ear_height','tail_length') else .005,1000)
            result[-1].pop('height_m',None)
            if mode=='existing_model':
                from blend_asset_contract import validate_asset_contract
                for field in ('model_path','model_sha256','model_version','model_collection',
                              'asset_contract_path','asset_contract_sha256',
                              'registry_character_id','registry_form_id','registry_fingerprint'):
                    _string(actor.get(field), 'registered existing model requires '+field)
                for field in ('model_sha256','asset_contract_sha256','registry_fingerprint'):
                    digest=actor[field]
                    _require(len(digest)==64 and all(c in '0123456789abcdef' for c in digest),
                             'registered existing model requires SHA256 '+field)
                _require(not actor.get('protrusions'), 'existing-model explicit protrusion boxes are unsupported; use measured asset parts')
                validate_asset_contract(actor.get('asset_contract'), measurements_m=measures,
                                        shape_kind=actor.get('shape_kind'), collection=actor.get('model_collection'))

    return result


def _records(records):
    if isinstance(records, dict) and 'actors' in records:
        records = records['actors']
    if isinstance(records, dict):
        values = []
        for key, value in records.items():
            _require(isinstance(value, dict) and value.get('actor_id') == key, 'actor record dictionary key/id mismatch')
            values.append(value)
    else:
        _require(isinstance(records, list), 'actual actor records must be a list or dictionary')
        values = records
    result = {}
    for record in values:
        _require(isinstance(record, dict), 'actual actor record must be an object')
        actor_id = _string(record.get('actor_id'), 'actual actor_id required')
        _require(actor_id not in result, 'duplicate actual actor record')
        result[actor_id] = record
    return result


def _parts(record):
    parts = record.get('parts')
    _require(isinstance(parts, list) and parts, 'actual mesh parts are required')
    ids = [_string(part.get('id'), 'actual part id required') for part in parts]
    _require(len(set(ids)) == len(ids), 'duplicate actual part id')
    return sorted(ids)


def _match_actor(actor, record, *, for_clean):
    _require(record.get('actor_id') == actor['id'], 'actual actor id mismatch')
    for field in ('proxy_mode', 'hand_lod', 'purpose', 'allow_special_proxy'):
        _require(record.get(field) == actor[field], f"actual {actor['id']} {field} mismatch")
    _require(type(record.get('allow_special_proxy')) is bool, 'actual allow_special_proxy must be boolean')
    if actor['proxy_mode']=='existing_model':
        from blend_asset_contract import validate_geometry_record
        _require(record.get('shape_kind')==actor.get('shape_kind'), 'actual existing-model shape kind mismatch')
        _require(record.get('model_choice')=='existing_model', 'actual existing-model choice mismatch')
        _require(record.get('forward_axis')=='-Y', 'actual existing-model forward axis must be canonical -Y')
        _require(record.get('measurements_m')==actor.get('measurements_m'), 'actual existing-model measurements mismatch')
        _require(record.get('protrusions',[])==actor.get('protrusions',[]), 'actual existing-model protrusions mismatch')
        source=record.get('source_asset',{})
        for key in ('model_path','model_sha256','model_version','model_collection','asset_contract_sha256'):
            _require(source.get(key)==actor.get(key), 'actual existing-model '+key+' mismatch')
        validate_geometry_record(record.get('asset_geometry'), actor.get('asset_contract'),
                                 actor.get('measurements_m'), actor.get('color'))
        _require(source.get('geometry_sha256')==record.get('asset_geometry',{}).get('geometry_sha256'),
                 'actual existing-model source geometry identity differs')
        _require(record.get('parts')==record.get('asset_geometry',{}).get('parts'), 'actual existing-model part evidence differs')
    elif actor['proxy_mode']!='shape_proxy':
        height = _number(record.get('height_m'), 'invalid actual height_m', .05, 100)
        _require(abs(height - actor['height_m']) <= 1e-6, 'actual height differs from requested height')
    else:
        for key in ('shape_kind','model_choice','measurements_m','protrusions'):
            expected=expected_shape_protrusions(actor) if key=='protrusions' else actor.get(key)
            _require(record.get(key,[] if key=='protrusions' else None)==expected, 'actual shape '+key+' mismatch')
    color = _color(record.get('color'))
    _require(all(abs(a - b) <= 1e-6 for a, b in zip(color, actor['color'])), 'actual color differs from actor mapping')
    part_ids = _parts(record)
    if actor['proxy_mode'] == 'cylinder':
        _require(set(part_ids) == CYLINDER_PARTS, 'cylinder must contain only body_cylinder/head/nose/front_marker, with no hands or limbs')
    elif actor['proxy_mode']=='shape_proxy':
        _require({'body_volume','head','front_marker'}.issubset(part_ids), 'shape proxy needs static body/head/front marker')
        _require(not any(any(token in name for token in ('palm','finger','arm','leg','thigh','shin')) for name in part_ids), 'shape proxy cannot imply limb performance')
    elif actor['proxy_mode']!='existing_model':
        _require(any('palm' in name for name in part_ids), 'articulated_special actual record lacks articulated geometry')
    _require(not for_clean or record['purpose'] == 'model-reference', 'diagnostic-only actor cannot become a clean reference')
    semantic = _string(record.get('semantic_sha256'), 'actual semantic_sha256 required')
    unsigned = {key: value for key, value in record.items() if key != 'semantic_sha256'}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode()).hexdigest()
    _require(semantic == expected, 'actual actor semantic hash does not match record content')
    return part_ids


def build_proxy_contract(spec, actual_actor_records, *, for_clean=True):
    requests = resolve_actor_requests(spec, for_clean=for_clean)
    records = _records(actual_actor_records)
    _require(set(records) == {actor['id'] for actor in requests}, 'actual actor set differs from requested actors')
    actors = []
    for request in requests:
        record = records[request['id']]
        parts = _match_actor(request, record, for_clean=for_clean)
        item=dict(request,color=list(record['color']),actual_semantic_sha256=record['semantic_sha256'],actual_part_ids=parts)
        if 'height_m' in record:item['height_m']=record['height_m']
        actors.append(item)
    contract = {'schema': SCHEMA, 'purpose': 'model-reference' if for_clean else 'diagnostic-only',
                'source_spec_sha256': canonical_sha256(spec),
                'actual_actor_records_sha256': canonical_sha256([records[key] for key in sorted(records)]),
                'allow_special_proxy': spec.get('allow_special_proxy', False),
                'special_proxy_reason': spec.get('special_proxy_reason', ''),
                'actors': actors,
                'limitations': ['No IK, grasp, contact, gait, gesture, expression or performance verification.',
                                'A proxy reference can influence a generation model; a prompt cannot guarantee otherwise.']}
    contract['contract_sha256'] = canonical_sha256(contract)
    return contract


def validate_proxy_contract(contract, *, spec=None, actor_records=None, for_clean=True):
    _require(isinstance(contract, dict) and contract.get('schema') == SCHEMA, 'current proxy contract required')
    unsigned = {key: value for key, value in contract.items() if key != 'contract_sha256'}
    _require(contract.get('contract_sha256') == canonical_sha256(unsigned), 'proxy contract hash mismatch')
    _require(not for_clean or contract.get('purpose') == 'model-reference', 'diagnostic contract forbidden for clean references')
    actors = contract.get('actors')
    normalized = resolve_actor_requests({'actors': actors,
                                        'allow_special_proxy': contract.get('allow_special_proxy', False),
                                        'special_proxy_reason': contract.get('special_proxy_reason', '')}, for_clean=for_clean)
    for actor, expected in zip(actors, normalized):
        for key in ('id', 'character_name', 'color_name', 'proxy_mode', 'hand_lod', 'purpose', 'allow_special_proxy', 'special_proxy_reason'):
            _require(actor.get(key) == expected[key], f'contract actor {key} is not canonical')
        parts = actor.get('actual_part_ids')
        _require(isinstance(parts, list), 'contract actual part IDs missing')
        if actor['proxy_mode'] == 'cylinder':
            _require(set(parts) == CYLINDER_PARTS and len(parts) == 4, 'contract cylinder contains invalid geometry')
    if spec is not None:
        _require(contract.get('source_spec_sha256') == canonical_sha256(spec), 'source spec hash differs from proxy contract')
        requests = resolve_actor_requests(spec, for_clean=for_clean)
        _require(len(requests) == len(actors), 'contract actor count differs from spec')
        for expected, actual in zip(requests, actors):
            for field in ('id', 'character_name', 'color_name', 'proxy_mode', 'hand_lod', 'purpose', 'allow_special_proxy', 'special_proxy_reason'):
                _require(expected[field] == actual[field], f'contract mapping {field} differs from spec')
            if expected['proxy_mode'] in ('shape_proxy','existing_model'):
                for key in ('shape_kind','model_choice','measurements_m','protrusions'):
                    _require(expected.get(key)==actual.get(key),'contract shape '+key+' differs from spec')
            else:
                _require(abs(expected['height_m'] - actual['height_m']) <= 1e-6, 'contract height differs from spec')
            for key in ('registry_character_id','registry_form_id','registry_fingerprint','character_aliases'):
                _require(expected.get(key)==actual.get(key),'contract registry '+key+' differs from spec')
            if expected['proxy_mode']=='existing_model':
                for key in ('model_path','model_sha256','model_version','model_collection',
                            'asset_contract_path','asset_contract_sha256','asset_contract'):
                    _require(expected.get(key)==actual.get(key),'contract existing-model '+key+' differs from spec')
            _require(all(abs(a - b) <= 1e-6 for a, b in zip(expected['color'], actual['color'])), 'contract color differs from spec')
    if actor_records is not None:
        records = _records(actor_records)
        _require(set(records) == {actor['id'] for actor in actors}, 'actual actor set differs from contract')
        _require(contract.get('actual_actor_records_sha256') == canonical_sha256([records[key] for key in sorted(records)]),
                 'actual actor records hash differs from contract')
        for actor in actors:
            parts = _match_actor(actor, records[actor['id']], for_clean=for_clean)
            _require(parts == actor['actual_part_ids'] and records[actor['id']]['semantic_sha256'] == actor['actual_semantic_sha256'],
                     'actual geometry differs from contract')
    return contract
