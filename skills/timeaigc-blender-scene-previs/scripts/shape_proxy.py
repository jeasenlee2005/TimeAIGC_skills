"""Registry-selected static proxies with explicit nonhuman measurement semantics.

All intrinsic coordinates are meters. Quadruped body is a grounded volume, not
an anatomical torso or gait rig. Its upper bound represents shoulder height.
Head-top excludes horns/ears; body length excludes head and tail. Protrusion
envelopes are separate occupied-space proxies, never animation or contact.
Registered static .blend collections use the separately validated mesh importer.
"""
import hashlib
import json
import math
import bpy
from mathutils import Vector
from canonical_actor import build_actor, semantic_record
from scale_adapter import positive, apply_intrinsic_meter_scale
from shape_contract import expected_shape_protrusions

SHAPE_VERSION = 'timeaigc-shape-proxy-2.2.0'


def _number(m, key):
    result = positive(m.get(key), 'measurements_m.' + key)
    if not .005 <= result <= 1000: raise ValueError('Shape dimension outside .005..1000 m: ' + key)
    return result


def _build_shape(request, collection):
    actor_id = request['id']
    if not isinstance(actor_id, str) or not actor_id or actor_id in bpy.data.objects:
        raise ValueError('Actor ID missing or already exists')
    m = request.get('measurements_m')
    if not isinstance(m, dict): raise ValueError('Explicit nonhuman measurements_m are required')
    kind = request.get('shape_kind')
    if kind not in ('quadruped', 'other'): raise ValueError('Nonhuman shape_kind must be quadruped or other')
    length = _number(m, 'body_length'); width = _number(m, 'body_width')
    if kind == 'quadruped':
        shoulder = _number(m, 'shoulder_height'); head_top = _number(m, 'head_top_height')
        if head_top < shoulder: raise ValueError('This simple quadruped proxy requires head_top_height >= shoulder_height; choose reviewed custom model for lowered-head anatomy')
        body_height = shoulder
    else:
        body_height = _number(m, 'body_height')
        head_top = _number(m, 'head_top_height') if 'head_top_height' in m else body_height
    color = request.get('color')
    if not isinstance(color, (list, tuple)) or len(color) != 3 or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in color):
        raise ValueError('Explicit finite RGB is required')
    root = bpy.data.objects.new(actor_id, None); collection.objects.link(root)
    root['generator_version'] = SHAPE_VERSION; root['proxy_mode'] = 'shape_proxy'
    root['shape_kind'] = kind; root['hand_lod'] = 'none'; root['purpose'] = 'model-reference'
    root['forward_axis'] = '-Y'; root['model_choice'] = request['model_choice']
    root['measurements_json'] = json.dumps(m, sort_keys=True); root['color_rgb'] = list(color)
    root['structure'] = 'static occupied-space volumes and head; no legs, hands, gait, IK or contact'
    material = bpy.data.materials.new(actor_id + '_body'); material.diffuse_color = (*color,1)
    marker_material = bpy.data.materials.new(actor_id + '_front'); marker_material.diffuse_color = (.9,.9,.85,1)

    def part(name, center, size, material=material, cube=False):
        if cube: bpy.ops.mesh.primitive_cube_add(size=1)
        else: bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=.5)
        ob = bpy.context.object; ob.name = actor_id + '__' + name
        ob.location = center; ob.scale = size
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        for c in list(ob.users_collection): c.objects.unlink(ob)
        collection.objects.link(ob); ob.parent = root; ob.data.materials.append(material)
        ob['part_id'] = name; ob['fixed_local_transform'] = True
        if not cube:
            for p in ob.data.polygons: p.use_smooth=True
        return ob

    placeholder = request['model_choice'] == 'placeholder'
    part('body_volume', (0,0,body_height/2), (width,length,body_height), cube=placeholder)
    head_height = min(body_height*.45, head_top*.5)
    head_depth = min(length*.32, width*.8)
    head_width = width*.70
    head_y = -length/2 + head_depth*.25
    if kind == 'quadruped' and not placeholder:
        anchor=Vector((0,-length*.30,body_height*.75))
        head_center=Vector((0,head_y,head_top-head_height/2))
        connection=head_center-anchor;neck_width=min(head_width*.55,head_height*.65)
        neck=part('neck_volume',(anchor+head_center)/2,
                  (neck_width,neck_width,connection.length+neck_width*.5))
        neck.rotation_mode='QUATERNION';neck.rotation_quaternion=connection.to_track_quat('Z','Y')
    part('head', (0,head_y,head_top-head_height/2), (head_width,head_depth,head_height), cube=placeholder)
    # Front stripe on head face, with a small geometric nose, never text.
    part('nose', (0,head_y-head_depth*.5,head_top-head_height*.58),
         (head_width*.22,head_depth*.20,head_height*.18))
    part('front_marker', (0,head_y-head_depth*.50-.0001,head_top-head_height*.35),
         (head_width*.12,.0002,head_height*.18),marker_material,cube=True)

    envelopes = expected_shape_protrusions(request)
    for envelope in envelopes:
        lo=envelope['bounds_min_m'];hi=envelope['bounds_max_m']
        part('protrusion_'+envelope['id'],[(hi[i]+lo[i])/2 for i in range(3)],
             [hi[i]-lo[i] for i in range(3)],cube=True)
        # Conventional horn/ear measures begin at the bare crown plane. The
        # curved head is lower at paired lateral positions, so a static base
        # connects the envelope to the head below that measurement plane.
        # It does not add height, an extra appendage, or an animation joint.
        if envelope['kind'] in ('horn','ear') and m.get(envelope['kind']+'_height',0)>0:
            base_low=head_top-head_height*.55
            part('protrusion_attachment_'+envelope['id'],
                 [(hi[0]+lo[0])/2,(hi[1]+lo[1])/2,(head_top+base_low)/2],
                 [hi[0]-lo[0],hi[1]-lo[1],head_top-base_low],cube=True)
    root['protrusions_json']=json.dumps(envelopes,sort_keys=True)
    return root


def build_registry_actor(resolved_actor, collection, meters_per_blender_unit=1.0):
    """Build only a registry-resolved actor; appearance never chooses a model."""
    request = dict(resolved_actor)
    choice = request.get('model_choice')
    if choice == 'existing_model':
        from blend_asset import import_registered_asset
        root = import_registered_asset(request, collection)
    elif choice == 'cylinder':
        if request.get('proxy_mode','cylinder') != 'cylinder': raise ValueError('Cylinder choice conflicts with proxy_mode')
        m=request.get('measurements_m',{});height=m.get('height',request.get('height',request.get('height_m')))
        root=build_actor(request['id'],collection,height,request['color'],request.get('hand_lod','none'),proxy_mode='cylinder')
    elif choice in ('simple_proxy','placeholder'):
        if request.get('proxy_mode','shape_proxy')!='shape_proxy':raise ValueError('Nonhuman choice conflicts with proxy_mode')
        root=_build_shape(request,collection)
    else: raise ValueError('Explicit confirmed model_choice required')
    for key in ('registry_character_id','registry_form_id','registry_fingerprint','character_name','color_name'):
        if key in request:root[key]=request[key]
    apply_intrinsic_meter_scale(root,meters_per_blender_unit)
    return root


def registry_semantic_record(root):
    """Pose/unit conversion excluded; hashes identify intrinsic dimensions/model."""
    if root.get('proxy_mode') == 'existing_model':
        from blend_asset import imported_semantic_record
        return imported_semantic_record(root)
    if root.get('proxy_mode')!='shape_proxy': return semantic_record(root)
    def floats(values):return [round(float(v),7) for v in values]
    parts=[]
    for ob in sorted((o for o in root.children if o.type=='MESH'),key=lambda x:x['part_id']):
        verts=[floats(v.co) for v in ob.data.vertices]
        parts.append({'id':ob['part_id'],'vertices':len(verts),'polygons':len(ob.data.polygons),
                      'location':floats(ob.location),'rotation':floats(ob.matrix_basis.to_quaternion()),
                      'dimensions':floats((max(v.co[i] for v in ob.data.vertices)-min(v.co[i] for v in ob.data.vertices))*abs(ob.scale[i]) for i in range(3)),
                      'scale':floats(ob.scale),'material':floats(ob.data.materials[0].diffuse_color),
                      'mesh_sha256':hashlib.sha256(json.dumps(verts).encode()).hexdigest()})
    result={'actor_id':root.name,'generator_version':root['generator_version'],
            'proxy_mode':'shape_proxy','shape_kind':root['shape_kind'],
            'model_choice':root['model_choice'],'measurements_m':json.loads(root['measurements_json']),
            'protrusions':json.loads(root['protrusions_json']),'hand_lod':'none','purpose':'model-reference',
            'allow_special_proxy':False,'forward_axis':'-Y','color':floats(root['color_rgb']),
            'parts':parts,'limitations':'static occupied-space proxy; no limb animation, gait, IK or contact'}
    result['semantic_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result
