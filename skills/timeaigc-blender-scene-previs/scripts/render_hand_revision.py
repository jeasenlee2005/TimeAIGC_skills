"""Render and audit the isolated v1.1 static-hand revision using installed Blender.

No standard/reference media is rewritten. Baseline code must be the explicitly
reviewed, local v1.0.1 snapshot supplied by --baseline-python. Output is always a
fresh directory. The checks cover static geometry, not IK/contact/performance.
"""
import argparse
from array import array
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_actor import build_actor, semantic_record, GENERATOR_VERSION


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def pixel_digest(path):
    im = bpy.data.images.load(str(path), check_existing=False)
    result = {'width': im.size[0], 'height': im.size[1],
              'pixel_sha256': hashlib.sha256(array('f', im.pixels[:]).tobytes()).hexdigest(),
              'file_sha256': digest(path)}
    bpy.data.images.remove(im)
    return result


def remove_actor(root):
    for ob in list(root.children):
        bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.objects.remove(root, do_unlink=True)


def camera_pose(cam, position, target, scale):
    cam.location = position
    cam.rotation_mode = 'QUATERNION'
    cam.rotation_quaternion = (Vector(target) - cam.location).to_track_quat('-Z', 'Y')
    cam.data.type = 'ORTHO'
    cam.data.ortho_scale = scale
    cam.data.clip_start = .01
    cam.data.clip_end = 20


def mesh_audit(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    pending = set(v.index for v in bm.verts)
    sizes = []
    while pending:
        stack = [pending.pop()]
        size = 0
        while stack:
            vertex = bm.verts[stack.pop()]
            size += 1
            for edge in vertex.link_edges:
                other = edge.other_vert(vertex).index
                if other in pending:
                    pending.remove(other)
                    stack.append(other)
        sizes.append(size)
    result = {'object': ob.name, 'vertices': len(bm.verts), 'faces': len(bm.faces),
              'connected_components': len(sizes), 'component_vertex_counts': sizes,
              'nonmanifold_edges': sum(not e.is_manifold for e in bm.edges),
              'wire_edges': sum(e.is_wire for e in bm.edges),
              'degenerate_faces': sum(f.calc_area() <= 1e-14 for f in bm.faces),
              'signed_volume_m3': bm.calc_volume(signed=True)}
    bm.free()
    assert result['connected_components'] == 1, result
    assert result['nonmanifold_edges'] == 0 and result['wire_edges'] == 0, result
    assert result['degenerate_faces'] == 0 and result['signed_volume_m3'] > 0, result
    return result


def inside_mesh(ob, root_point):
    """Odd/even ray count in evaluated local coordinates, away from edges."""
    world = ob.parent.matrix_world @ Vector(root_point)
    origin = ob.matrix_world.inverted() @ world
    direction = Vector((.12341, .93217, .33953)).normalized()
    hits = 0
    for _ in range(100):
        hit, position, _normal, _face = ob.ray_cast(origin, direction, distance=100)
        if not hit:
            return bool(hits % 2)
        hits += 1
        origin = position + direction * 1e-6
    raise AssertionError('Unexpected ray intersection count')


def hand_audit(root):
    bpy.context.view_layer.update()
    height = root['height_m']
    results = []
    for ob in root.children:
        if ob.type != 'MESH' or ob.get('part_role') != 'continuous_palm_and_five_digits':
            continue
        result = mesh_audit(ob)
        construction = json.loads(ob['construction_json'])
        center = construction['palm_center']
        half = [v / 2 for v in construction['palm_size']]
        fingers = construction['fingers']
        roots = []
        centerline_checks = []
        for finger in fingers:
            q = sum(((x - c) / r) ** 2 for x, c, r in zip(finger['root'], center, half))
            roots.append({'finger': finger['name'], 'palm_ellipsoid_equation': q,
                          'root_inside_palm': q < 1})
            sign = 1 if finger['root'][0] > 0 else -1
            for t in (0, .2, .4, .6, .8, .90):
                x = finger['root'][0] + sign * finger['spread'] * t
                y = finger['root'][1] + finger['curl_y'] * t * t
                z = finger['root'][2] - finger['length'] * t
                inside = inside_mesh(ob, (x * height, y * height, z * height))
                centerline_checks.append({'finger': finger['name'], 'fraction': t, 'inside_final_solid': inside})
        thumb_q = sum(((x - c) / r) ** 2 for x, c, r in zip(construction['thumb_root'], center, half))
        roots.append({'finger': 'thumb', 'palm_ellipsoid_equation': thumb_q, 'root_inside_palm': thumb_q < 1})
        thumb_start = Vector(construction['thumb_root'])
        thumb_tip = Vector(construction['thumb_tip'])
        for t in (0, .2, .4, .6, .8):
            point = (thumb_start.lerp(thumb_tip, t)) * height
            centerline_checks.append({'finger': 'thumb', 'fraction': t,
                                      'inside_final_solid': inside_mesh(ob, point)})
        # At this level the palm has ended and all four fingers still exist.
        # Interdigital midpoints must remain outside: no accidental webbing.
        gap_z = .386
        sections = []
        for finger in fingers:
            t = (finger['root'][2] - gap_z) / finger['length']
            sign = 1 if finger['root'][0] > 0 else -1
            sections.append(Vector((finger['root'][0] + sign * finger['spread'] * t,
                                    finger['root'][1] + finger['curl_y'] * t * t, gap_z)))
        gap_checks = []
        for i in range(3):
            midpoint = sections[i].lerp(sections[i + 1], .5) * height
            gap_checks.append({'between': [fingers[i]['name'], fingers[i + 1]['name']],
                               'point_m': list(midpoint), 'outside_final_solid': not inside_mesh(ob, midpoint)})
        lengths = {finger['name']: finger['length'] for finger in fingers}
        assert lengths['middle'] > lengths['ring'] > lengths['index'] > lengths['little']
        assert all(item['root_inside_palm'] for item in roots), roots
        assert all(item['inside_final_solid'] for item in centerline_checks), centerline_checks
        assert all(item['outside_final_solid'] for item in gap_checks), gap_checks
        result.update({'construction': construction, 'attachment_checks': roots,
                       'centerline_checks': centerline_checks, 'interdigital_gap_checks': gap_checks,
                       'finger_length_order': ['middle', 'ring', 'index', 'little'],
                       'scope': 'Connected closed solid, explicit interior/gap samples. Not exhaustive collision/contact simulation.'})
        results.append(result)
    assert len(results) == 2
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--baseline-python', required=True)
    parser.add_argument('--protected-manifest')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a fresh empty hand-revision output directory')
    out.mkdir(parents=True, exist_ok=True)
    for name in ('before', 'pass-a', 'pass-b'):
        (out / name).mkdir()
    baseline_path = Path(args.baseline_python).resolve(strict=True)
    module_spec = importlib.util.spec_from_file_location('reviewed_local_actor_baseline', baseline_path)
    baseline = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(baseline)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.world = bpy.data.worlds.new('diagnostic_world')
    scene.world.color = (.22, .245, .27)
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x = 960
    scene.render.resolution_y = 960
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    shading = scene.display.shading
    shading.light = 'STUDIO'
    shading.color_type = 'MATERIAL'
    shading.show_shadows = True
    shading.show_cavity = True
    shading.cavity_type = 'BOTH'
    shading.background_type = 'WORLD'
    scene['purpose'] = 'diagnostic-only'
    scene['reference_policy'] = 'Never upload static hand diagnostic images as clean spatial reference'
    collection = bpy.data.collections.new('HAND_REVISION_DIAGNOSTIC')
    scene.collection.children.link(collection)
    cam = bpy.data.objects.new('DiagnosticCamera', bpy.data.cameras.new('DiagnosticCamera'))
    scene.collection.objects.link(cam)
    scene.camera = cam

    def render(path):
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        return pixel_digest(path)

    def show_hand(root, side):
        for ob in root.children:
            part_id = ob.get('part_id', '')
            hand_part = part_id.startswith(side + '_palm') or part_id.startswith(side + '_thumb') or part_id.startswith(side + '_finger')
            ob.hide_render = ob.type != 'MESH' or not hand_part

    def render_views(root, folder, names=None):
        height = root['height_m']
        results = {}
        for side, sign in (('L', 1), ('R', -1)):
            show_hand(root, side)
            target = Vector((sign * .181 * height, -.012 * height, .417 * height))
            offsets = {'front': (0, -1.2, 0), 'back': (0, 1.2, 0),
                       'thumb-side': (-sign * 1.2, 0, 0), 'outer-side': (sign * 1.2, 0, 0),
                       'three-quarter': (sign * .6, -1.1, .08)}
            for view, offset in offsets.items():
                name = side + '-' + view
                if names and name not in names:
                    continue
                camera_pose(cam, target + Vector(offset), target, height * .16)
                results[name] = render(folder / (name + '.png'))
        return results

    # Historical semantic schema compatibility is checked on an actual old root.
    old_standard = baseline.build_actor('same_actor', collection)
    old_record = baseline.semantic_record(old_standard)
    assert semantic_record(old_standard) == old_record, 'Historical semantic record/hash changed'
    remove_actor(old_standard)
    standard = build_actor('same_actor', collection, proxy_mode='articulated_special', allow_special_proxy=True)
    standard_record = semantic_record(standard)
    assert len(old_record['parts']) == len(standard_record['parts'])
    for old_part,new_part in zip(old_record['parts'],standard_record['parts']):
        assert {k:v for k,v in old_part.items() if k!='dimensions'} == {k:v for k,v in new_part.items() if k!='dimensions'}, 'Special standard mesh geometry changed'
        assert all(abs(a-b)<1e-6 for a,b in zip(old_part['dimensions'],new_part['dimensions'])), 'Local dimensions differ from historical dimensions'
    assert len(standard_record['parts']) == 20
    locators = [ob for ob in standard.children if ob.type == 'EMPTY']
    assert {ob.get('locator_id') for ob in locators} == {'eyeline_center', 'eye_L', 'eye_R', 'gaze_target'}
    assert all(ob.hide_render and ob.data is None and not ob.constraints and ob.animation_data is None for ob in locators)
    assert all(ob.parent == standard for ob in locators)
    gaze = next(ob for ob in locators if ob['locator_id'] == 'gaze_target')
    eyeline = next(ob for ob in locators if ob['locator_id'] == 'eyeline_center')
    direction = gaze.location - eyeline.location
    assert abs(direction.x) < 1e-8 and abs(direction.z) < 1e-8 and direction.y < 0
    camera_pose(cam, (0, -5, .90), (0, 0, .88), 2.02)
    standard_visible = render(out / 'standard-locators-hidden.png')
    # Even with editor display enabled, Empty objects have no render surface.
    for ob in locators:
        ob.hide_render = False
    standard_locators_enabled = render(out / 'standard-locators-enabled.png')
    assert standard_visible['pixel_sha256'] == standard_locators_enabled['pixel_sha256']
    for ob in locators:
        ob.hide_render = True
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'canonical-actor.blend'))
    write(out / 'standard-geometry-record.json', standard_record)
    remove_actor(standard)

    old_hand = baseline.build_actor('same_hand', collection, hand_lod='closeup_static')
    before_views = render_views(old_hand, out / 'before', {'L-front', 'L-three-quarter'})
    remove_actor(old_hand)

    first = build_actor('same_hand', collection, hand_lod='closeup_static', proxy_mode='articulated_special', allow_special_proxy=True)
    first_record = semantic_record(first)
    geometry = hand_audit(first)
    first_views = render_views(first, out / 'pass-a')
    # Save a useful front view; the right hand remains hidden in the viewport render.
    show_hand(first, 'L')
    target = Vector((.181 * 1.76, -.012 * 1.76, .417 * 1.76))
    camera_pose(cam, target + Vector((0, -1.2, 0)), target, 1.76 * .16)
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'hand-diagnostic-only.blend'))
    write(out / 'hand-geometry-pass-a.json', first_record)
    remove_actor(first)

    second = build_actor('same_hand', collection, hand_lod='closeup_static', proxy_mode='articulated_special', allow_special_proxy=True)
    second_record = semantic_record(second)
    assert first_record == second_record, 'Repeated hand generation differs'
    second_geometry = hand_audit(second)
    assert geometry == second_geometry
    second_views = render_views(second, out / 'pass-b')
    repeat = {name: first_views[name]['pixel_sha256'] == second_views[name]['pixel_sha256'] for name in first_views}
    assert all(repeat.values()), repeat
    write(out / 'hand-geometry-pass-b.json', second_record)
    assert not any(ob.type == 'FONT' for ob in scene.objects)
    assert all(not ob.constraints and ob.animation_data is None for ob in second.children)

    protected = []
    if args.protected_manifest:
        records = json.loads(Path(args.protected_manifest).read_text(encoding='utf-8-sig'))
        if isinstance(records, dict):
            records = [records]
        for item in records:
            path = Path(item['path'])
            actual = digest(path)
            protected.append({'path': str(path), 'unchanged': actual == item['sha256'], 'sha256': actual})
        assert all(item['unchanged'] for item in protected), protected

    report = {'schema': 'timeaigc-hand-revision-evidence/v1', 'status': 'checks-passed-visual-review-pending',
              'generator_version': GENERATOR_VERSION, 'blender': bpy.app.version_string,
              'baseline_python': str(baseline_path), 'baseline_sha256': digest(baseline_path),
              'generator_sha256': digest(Path(__file__).with_name('canonical_actor.py')),
              'script_sha256': digest(__file__), 'historical_semantic_schema_compatible': True,
              'standard_mesh_parts_unchanged': True, 'standard_mesh_count': 20,
              'dimension_semantics': 'v2 local vertex bounds; within 1e-6 of historical world-derived dimensions',
              'standard_before_semantic_sha256': old_record['semantic_sha256'],
              'standard_after_semantic_sha256': standard_record['semantic_sha256'],
              'locator_count': len(locators), 'locator_render_pixels_unchanged': True,
              'locator_renders': {'hidden': standard_visible, 'enabled': standard_locators_enabled},
              'locators': standard_record['locators'], 'hand_mesh_checks': geometry,
              'same_input_semantic_record_equal': first_record == second_record,
              'same_input_views_pixel_equal': repeat, 'before_views': before_views,
              'views_pass_a': first_views, 'views_pass_b': second_views,
              'protected_historical_outputs': protected,
              'visual_review': {'status': 'pending', 'required': 'View front/back/thumb-side/outer-side and three-quarter images for both hands.'},
              'limitations': ['Static construction only. No IK, contact solver, gestures or performance validation.',
                             'Geometry audit proves one closed connected solid plus stated sampled attachment/gap checks; not exhaustive self-collision proof.',
                             'Reference Library PNG was not readable; no claim of reference-image matching.',
                             'Existing clean/reference video and old .blend outputs are unchanged.']}
    write(out / 'hand-revision-evidence.json', report)
    print(json.dumps({'status': report['status'], 'out': str(out), 'version': GENERATOR_VERSION,
                      'standard_mesh_unchanged': True, 'repeat_views': len(repeat),
                      'hand_meshes': [{'name': x['object'], 'vertices': x['vertices'], 'components': x['connected_components']} for x in geometry]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
