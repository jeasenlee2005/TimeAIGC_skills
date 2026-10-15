"""Read-only registered static .blend collection import, with no mainfile open.

Append retains the source mesh datablocks and solid materials. A new actor root
flattens validated internal transforms into intrinsic meters. No mesh is rebuilt,
no script is run, and dimensions are checked rather than fitted. This is a
deliberately narrow local asset format, not a general .blend sanitizer.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import bpy
from mathutils import Matrix, Vector
from blend_asset_contract import (GEOMETRY_SCHEMA, canonical_hash, derive_geometry_evidence,
                                  validate_asset_contract, validate_geometry_record)

VERSION = 'timeaigc-static-blend-import-2.2.1'
_ID_GROUPS = ('objects', 'collections', 'meshes', 'materials', 'node_groups', 'images', 'texts',
              'actions', 'armatures', 'cameras', 'curves', 'hair_curves', 'lights', 'lightprobes',
              'scenes', 'worlds', 'sounds', 'movieclips', 'fonts', 'volumes', 'metaballs',
              'lattices', 'grease_pencils', 'textures', 'particles', 'libraries', 'shape_keys')


def _require(ok, message):
    if not ok: raise ValueError('blend_asset: ' + message)


def _rounded(values):
    result = [round(float(v), 9) for v in values]
    _require(all(math.isfinite(v) for v in result), 'nonfinite geometry or transform')
    return [0.0 if v == 0 else v for v in result]


def _matrix_values(matrix):
    return _rounded(v for row in matrix for v in row)


def _snapshot():
    return {name: set(getattr(bpy.data, name)) for name in _ID_GROUPS if hasattr(bpy.data, name)}


def _created(before):
    return {name: set(getattr(bpy.data, name)) - ids for name, ids in before.items()}


def _cleanup(before):
    """Remove only newly appended IDs; never orphan-purge the user's scene."""
    order = ('objects', 'collections', 'meshes', 'materials')
    for name in order:
        group = getattr(bpy.data, name)
        for item in list(_created(before)[name]):
            try: group.remove(item, do_unlink=True)
            except (ReferenceError, TypeError):
                try: group.remove(item)
                except (ReferenceError, RuntimeError): pass
    # Mesh removal can already free owned shape keys. Re-query live IDs rather
    # than calling a nonexistent shape_keys.remove on a stale handle.
    remaining = [item for items in _created(before).values() for item in items]
    if remaining: bpy.data.batch_remove(ids=tuple(remaining))


def _safe_custom_properties(value):
    if isinstance(value, bpy.types.ID):
        raise ValueError('blend_asset: custom property contains an external ID dependency')
    if hasattr(value, 'items'):
        for _, child in value.items(): _safe_custom_properties(child)
    elif isinstance(value, (list, tuple)):
        for child in value: _safe_custom_properties(child)


def _safe_id(item):
    _require(item.library is None and item.override_library is None, 'linked/overridden ID dependencies unsupported')
    _require(getattr(item, 'animation_data', None) is None, 'animation or drivers unsupported')
    _safe_custom_properties(dict(item.items()))


def _positive_transform(matrix, label):
    values = _matrix_values(matrix)
    _require(all(abs(values[12+i] - (1 if i == 3 else 0)) < 1e-7 for i in range(4)), label + ' has non-affine transform')
    linear = matrix.to_3x3(); columns = [linear.col[i] for i in range(3)]
    lengths = [v.length for v in columns]
    _require(min(lengths) > 1e-8 and linear.determinant() > 0, label + ' has collapsed or reflected transform')
    _require(all(abs(columns[i].dot(columns[j])) <= 1e-5 * lengths[i] * lengths[j] for i in range(3) for j in range(i)), label + ' has shear; bake and review the source mesh first')


def _material_record(material):
    _safe_id(material)
    _require(abs(material.diffuse_color[3] - 1) < 1e-7, 'transparent materials unsupported')
    _require(not material.use_backface_culling, 'backface culling material unsupported')
    shader_hash = None
    if material.node_tree is not None:
        tree = material.node_tree; _safe_id(tree)
        _require(len(tree.nodes) == 2, 'only one constant Principled shader and one output node supported')
        shaders = [node for node in tree.nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled']
        outputs = [node for node in tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial']
        _require(len(shaders) == len(outputs) == 1 and len(tree.links) == 1, 'unsafe or unsupported material node graph')
        shader, output = shaders[0], outputs[0]; link = tree.links[0]
        _require(link.from_node == shader and link.to_node == output and link.from_socket.name == 'BSDF' and link.to_socket.name == 'Surface', 'material graph must be constant Principled to Surface')
        _require(not any(socket.is_linked for socket in shader.inputs), 'shader texture/attribute dependencies unsupported')
        _require(not shader.mute and not output.mute and output.is_active_output, 'muted/inactive material shader unsupported')
        _require(abs(shader.inputs['Alpha'].default_value - 1) < 1e-7 and abs(shader.inputs['Transmission Weight'].default_value) < 1e-7, 'transparent/transmissive shader unsupported')
        inputs = {}
        for socket in shader.inputs:
            if not hasattr(socket, 'default_value'): continue
            value = socket.default_value
            if isinstance(value, (bool, str)): inputs[socket.identifier] = value
            elif isinstance(value, (int, float)): inputs[socket.identifier] = _rounded([value])[0]
            else: inputs[socket.identifier] = _rounded(value)
        shader_hash = canonical_hash({'type': 'constant-principled', 'inputs': inputs, 'output_target': output.target})
    elif material.use_nodes:
        raise ValueError('blend_asset: enabled node material has no shader tree')
    return {'diffuse_color': _rounded(material.diffuse_color), 'metallic': round(float(material.metallic), 9), 'roughness': round(float(material.roughness), 9), 'shader_sha256': shader_hash}


def _source_objects(collection, contract):
    _safe_id(collection)
    _require(not collection.children, 'nested collections unsupported; prepare one flat collection')
    _require(not collection.hide_render and not collection.hide_viewport, 'hidden source collection unsupported')
    value = collection.get('timeaigc_meters_per_unit')
    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isclose(value, contract['meters_per_unit'], rel_tol=1e-8), 'source collection unit evidence differs from contract')
    objects = list(collection.objects); by_id = {}
    _require(objects, 'selected source collection is empty')
    for ob in objects:
        _safe_id(ob)
        identifier = ob.get('timeaigc_asset_id')
        _require(isinstance(identifier, str) and identifier and identifier not in by_id, 'each source object needs a unique timeaigc_asset_id')
        by_id[identifier] = ob
        _require(ob.type in ('MESH', 'EMPTY'), 'only static MESH and EMPTY objects supported')
        _require(ob.type != 'EMPTY' or ob.data is None, 'image/data-bearing EMPTY objects unsupported')
        _require(not ob.hide_render and not ob.hide_viewport, 'hidden source objects unsupported')
        _require(not ob.constraints and not ob.modifiers and not ob.particle_systems, 'constraints/modifiers/particles unsupported; bake reviewed geometry first')
        _require(ob.instance_type == 'NONE' and ob.instance_collection is None, 'collection instances unsupported')
        _require(not ob.parent or ob.parent in objects, 'object parent outside selected collection')
        _require(not ob.parent or ob.parent_type == 'OBJECT', 'bone/vertex parenting unsupported')
        _require(set(ob.users_collection) == {collection}, 'object linked to another collection')
        _positive_transform(ob.matrix_basis, identifier)
        _positive_transform(ob.matrix_parent_inverse, identifier + ' parent inverse')
        if ob.type == 'MESH':
            mesh = ob.data; _safe_id(mesh)
            _require(mesh.shape_keys is None and not ob.vertex_groups and not mesh.has_custom_normals, 'shape keys/vertex groups/custom normals unsupported')
            _require(not mesh.uv_layers and not mesh.color_attributes, 'UV/textured/vertex-color meshes unsupported')
            allowed = {'position', '.edge_verts', '.corner_vert', '.corner_edge', '.select_vert', '.select_edge', '.select_poly', '.uv_select_vert', '.uv_select_edge', '.uv_select_face', 'material_index', 'sharp_face', 'sharp_edge'}
            _require(all(a.name in allowed for a in mesh.attributes), 'custom mesh attributes unsupported: ' + ','.join(a.name for a in mesh.attributes if a.name not in allowed))
            _require(mesh.vertices and mesh.edges and mesh.polygons, 'source mesh must have vertices, edges and faces')
            _require(mesh.materials and all(m is not None for m in mesh.materials), 'each mesh needs explicit solid materials')
            _require(all(slot.link == 'DATA' for slot in ob.material_slots), 'object-linked material overrides unsupported')
            for material in mesh.materials:
                _material_record(material)
    root = by_id.get(contract['root_object'])
    _require(root is not None and root.type == 'EMPTY' and root.parent is None, 'root_object must select a parentless EMPTY in the collection')
    for ob in objects:
        seen = set(); current = ob
        while current is not root:
            _require(current not in seen and current.parent is not None, 'every object must descend from the one selected root; no cycles')
            seen.add(current); current = current.parent
    return by_id, root


def _world_matrix(ob, cache):
    if ob not in cache:
        cache[ob] = (_world_matrix(ob.parent, cache) @ ob.matrix_parent_inverse @ ob.matrix_basis) if ob.parent else ob.matrix_basis.copy()
    return cache[ob]


def _normalized_matrices(by_id, root, contract):
    cache = {}; root_matrix = _world_matrix(root, cache)
    _positive_transform(root_matrix, 'root world transform')
    rotation = root_matrix.to_quaternion().to_matrix().to_4x4()
    angle = {'-Y': 0, '+Y': math.pi, '+X': -math.pi / 2, '-X': math.pi / 2}[contract['forward_axis']]
    normalize = Matrix.Scale(contract['meters_per_unit'], 4) @ Matrix.Rotation(angle, 4, 'Z') @ rotation.inverted() @ Matrix.Translation(-root_matrix.translation)
    result = {}
    for identifier, ob in by_id.items():
        matrix = normalize @ _world_matrix(ob, cache)
        _positive_transform(matrix, identifier + ' normalized world transform')
        result[identifier] = matrix
    return result


def _mesh_record(identifier, ob, matrix):
    mesh = ob.data
    payload = {'vertices': [_rounded(v.co) for v in mesh.vertices],
               'edges': [list(e.vertices) for e in mesh.edges],
               'polygons': [{'vertices': list(p.vertices), 'material_index': p.material_index, 'use_smooth': p.use_smooth} for p in mesh.polygons],
               'sharp_edges': [bool(e.use_edge_sharp) for e in mesh.edges]}
    points = [matrix @ v.co for v in mesh.vertices]
    lo = _rounded(min(v[i] for v in points) for i in range(3)); hi = _rounded(max(v[i] for v in points) for i in range(3))
    indices = sorted({p.material_index for p in mesh.polygons})
    _require(all(0 <= i < len(mesh.materials) for i in indices), 'mesh polygon material index out of range')
    materials = [_material_record(mat) for mat in mesh.materials]
    return {'id': identifier, 'vertices': len(mesh.vertices), 'edges': len(mesh.edges), 'polygons': len(mesh.polygons),
            'mesh_sha256': canonical_hash(payload), 'canonical_matrix': _matrix_values(matrix),
            'bounds_m': {'min': lo, 'max': hi, 'dimensions': [hi[i] - lo[i] for i in range(3)]},
            'materials': materials, 'used_material_indices': indices}


def inspect_collection_geometry(collection, contract):
    """Inspect an already loaded source collection; safe for contract preparation."""
    validate_asset_contract(contract, allow_draft=True)
    by_id, root = _source_objects(collection, contract)
    matrices = _normalized_matrices(by_id, root, contract)
    parts = [_mesh_record(identifier, ob, matrices[identifier]) for identifier, ob in sorted(by_id.items()) if ob.type == 'MESH']
    record = {'schema': GEOMETRY_SCHEMA, 'parts': parts}
    record['geometry_sha256'] = canonical_hash(record)
    record.update(derive_geometry_evidence(parts, contract))
    return record


def _append_source(asset_path, contract, before):
    # This must happen before library load and remains disabled for the process.
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False
    with bpy.data.libraries.load(str(asset_path), link=False) as (source, destination):
        _require(contract['collection'] in source.collections, 'requested collection is missing from source .blend')
        destination.collections = [contract['collection']]
    appended = destination.collections[0]
    _require(appended is not None, 'collection append failed')
    new = _created(before)
    # Blender may retain the selected source's bookkeeping Library ID even for
    # local appended data. This is not an external linked geometry dependency.
    for library in new.get('libraries', ()):
        _require(Path(bpy.path.abspath(library.filepath)).resolve() == Path(asset_path).resolve() and library.parent is None,
                 'collection pulls a library other than the exact selected source')
    forbidden = {name: len(items) for name, items in new.items() if name not in ('collections', 'objects', 'meshes', 'materials', 'libraries') and items}
    _require(not forbidden, 'collection pulls unsupported datablock dependencies: ' + json.dumps(forbidden, sort_keys=True))
    _require(new['collections'] == {appended}, 'collection pulls outside/nested collections')
    _require(new['objects'] == set(appended.objects), 'collection pulls objects outside selected collection')
    _source_objects(appended, contract)
    _require(new['meshes'] == {ob.data for ob in appended.objects if ob.type == 'MESH'}, 'collection pulls outside mesh dependencies')
    _require(new['materials'] == {mat for ob in appended.objects if ob.type == 'MESH' for mat in ob.data.materials}, 'collection pulls outside material dependencies')
    return appended


def inspect_blend_file(asset_path, contract, measurements_m, color):
    """Read-only inspect and finalize a draft descriptor; source is never saved."""
    path = Path(asset_path).resolve()
    _require(path.is_file() and path.suffix.lower() == '.blend', 'asset must be an existing local .blend')
    validate_asset_contract(contract, measurements_m=measurements_m, allow_draft=True)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest(); before = _snapshot()
    try:
        collection = _append_source(path, contract, before)
        geometry = inspect_collection_geometry(collection, contract)
        final = dict(contract); final['expected_geometry_sha256'] = geometry['geometry_sha256']
        # An already-final contract is an identity assertion, never overwritten.
        if 'expected_geometry_sha256' in contract:
            _require(contract['expected_geometry_sha256'] == final['expected_geometry_sha256'], 'existing contract geometry identity differs')
        validate_geometry_record(geometry, final, measurements_m, color)
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == source_hash, 'source changed during inspection')
        return final, geometry
    finally:
        _cleanup(before)


def import_registered_asset(request, target_collection):
    """Append exact registered local .blend meshes into one fresh actor root."""
    from registry_binding import safe_member
    _require(request.get('model_choice') == 'existing_model' and request.get('proxy_mode') == 'existing_model', 'explicit existing_model route required')
    _require(not request.get('protrusions'), 'explicit protrusion envelopes unsupported for imported meshes; register measured actual parts')
    actor_id = request.get('id'); _require(isinstance(actor_id, str) and actor_id and actor_id not in bpy.data.objects, 'fresh actor ID required')
    project_root = request.get('_project_root'); _require(project_root, 'runtime project root required for registered asset resolution')
    asset_path = safe_member(project_root, {'path': request.get('model_path'), 'sha256': request.get('model_sha256')}, 'registered .blend model')
    _require(asset_path.suffix.lower() == '.blend', 'only local .blend model assets supported')
    descriptor_path = safe_member(project_root, {'path': request.get('asset_contract_path'), 'sha256': request.get('asset_contract_sha256')}, 'registered asset contract')
    actual_contract = json.loads(descriptor_path.read_text(encoding='utf-8-sig'))
    contract = request.get('asset_contract')
    _require(actual_contract == contract, 'runtime asset contract differs from registered descriptor bytes')
    validate_asset_contract(contract, measurements_m=request.get('measurements_m'), shape_kind=request.get('shape_kind'), collection=request.get('model_collection'))
    _require(isinstance(request.get('model_version'), str) and request['model_version'], 'registered model version required')
    before = _snapshot()
    try:
        source_collection = _append_source(asset_path, contract, before)
        geometry = inspect_collection_geometry(source_collection, contract)
        validate_geometry_record(geometry, contract, request['measurements_m'], request['color'])
        by_id, source_root = _source_objects(source_collection, contract)
        matrices = _normalized_matrices(by_id, source_root, contract)
        actor_root = bpy.data.objects.new(actor_id, None); target_collection.objects.link(actor_root)
        for identifier, ob in by_id.items():
            if ob.type != 'MESH': continue
            matrix = matrices[identifier].copy()
            source_collection.objects.unlink(ob); target_collection.objects.link(ob)
            # matrix_basis decomposes/recomposes into location/rotation/scale,
            # introducing avoidable float drift for valid nontrivial child
            # transforms. parent_inverse stores this exact affine matrix.
            ob.parent = actor_root; ob.matrix_basis = Matrix.Identity(4); ob.matrix_parent_inverse = matrix
            ob.name = actor_id + '__' + identifier; ob['part_id'] = identifier; ob['fixed_local_transform'] = True
        # Empties and the source collection only encode already-preserved transforms.
        for ob in by_id.values():
            if ob.type == 'EMPTY': bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.collections.remove(source_collection)
        for library in _created(before).get('libraries', ()):
            # Blender 5.2 reports one bookkeeping user even when user_map is
            # empty and every appended ID is local. Verify actual references.
            _require(not bpy.data.user_map().get(library) and not any(item.library == library for ids in _created(before).values() for item in ids if item is not library),
                     'appended objects retain an external library dependency')
            bpy.data.libraries.remove(library)
        actor_root['generator_version'] = VERSION; actor_root['proxy_mode'] = 'existing_model'
        actor_root['model_choice'] = 'existing_model'; actor_root['shape_kind'] = request['shape_kind']
        actor_root['purpose'] = 'model-reference'; actor_root['hand_lod'] = 'none'; actor_root['forward_axis'] = '-Y'
        actor_root['measurements_json'] = json.dumps(request['measurements_m'], sort_keys=True)
        actor_root['color_rgb'] = list(request['color']); actor_root['protrusions_json'] = '[]'
        actor_root['asset_geometry_json'] = json.dumps(geometry, sort_keys=True)
        source = {key: request[key] for key in ('model_path', 'model_sha256', 'model_version', 'model_collection', 'asset_contract_sha256')}
        source['geometry_sha256'] = geometry['geometry_sha256']
        actor_root['source_asset_json'] = json.dumps(source, sort_keys=True)
        actor_root['asset_contract_json'] = json.dumps(contract, sort_keys=True)
        actor_root['structure'] = 'actual registered static mesh, topology and solid materials; no deformation, IK or contact'
        _require(hashlib.sha256(asset_path.read_bytes()).hexdigest() == request['model_sha256'], 'source asset changed during append')
        return actor_root
    except Exception:
        _cleanup(before)
        raise


def imported_semantic_record(root):
    """Freshly fingerprint current imported mesh data, excluding actor pose/units."""
    contract = json.loads(root['asset_contract_json'])
    recorded = json.loads(root['asset_geometry_json'])
    children = list(root.children)
    for ob in children:
        _require(ob.type == 'MESH' and ob.parent == root and ob.parent_type == 'OBJECT', 'imported child hierarchy changed')
        _safe_id(ob); _safe_id(ob.data)
        _require(not ob.modifiers and not ob.constraints and not ob.vertex_groups and ob.data.shape_keys is None,
                 'imported child has deformation, animation or dependency changes')
        _positive_transform(ob.matrix_parent_inverse @ ob.matrix_basis, 'current imported child transform')
    parts = [_mesh_record(ob['part_id'], ob, ob.matrix_parent_inverse @ ob.matrix_basis) for ob in sorted(children, key=lambda o: o.get('part_id', ''))]
    geometry = {'schema': GEOMETRY_SCHEMA, 'parts': parts}; geometry['geometry_sha256'] = canonical_hash(geometry)
    geometry.update(derive_geometry_evidence(parts, contract))
    measurements = json.loads(root['measurements_json']); color = _rounded(root['color_rgb'])
    validate_geometry_record(geometry, contract, measurements, color)
    _require(geometry == recorded, 'imported actual geometry differs after placement')
    result = {'actor_id': root.name, 'generator_version': VERSION, 'proxy_mode': 'existing_model',
              'shape_kind': root['shape_kind'], 'model_choice': 'existing_model', 'measurements_m': measurements,
              'protrusions': [], 'hand_lod': 'none', 'purpose': 'model-reference', 'allow_special_proxy': False,
              'forward_axis': '-Y', 'color': color, 'source_asset': json.loads(root['source_asset_json']),
              'asset_geometry': geometry, 'parts': parts,
              'limitations': 'static imported mesh; no deformation, limb animation, gait, IK or contact'}
    result['semantic_sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
