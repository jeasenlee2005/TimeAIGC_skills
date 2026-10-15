"""Deterministic spatial proxy v2: cylinder + head by default, no limbs.

The former articulated form requires both an explicit special mode and explicit
permission. Eye/gaze empties are non-rendering construction locations only.
No rig, IK, contact solver, gait, gestures or facial performance is provided.
"""
import hashlib
import json
import math
import bpy
from mathutils import Vector

GENERATOR_VERSION = 'timeaigc-neutral-2.0.0'
RATIOS = {'height': 1.0, 'head_height': .14, 'head_width': .135,
          'shoulder_width': .25, 'hip_width': .18, 'head_center': .93,
          'orientation_marker_height': .932, 'palm_length': .075}
CYLINDER_RATIOS = {'height':1.0,'body_height':.82,'body_radius':.115,
                   'head_height':.18,'head_width':.17,'head_depth':.16,'head_center':.91,
                   'nose_center_z':.912,'eyeline_z':.938,'marker_bottom':.58,
                   'marker_top':.70,'marker_half_angle_degrees':5.0}


def build_actor(actor_id, collection, height=1.76, color=(.12,.43,.72), hand_lod='standard',
                *, proxy_mode='cylinder', allow_special_proxy=False):
    """Build an explicit proxy. A legacy hand_lod must never select a hand mode.

    Cylinder accepts None/'none'/'standard' and records 'none'. In particular,
    asking only for closeup_static raises instead of enabling articulated limbs.
    Permission is scoped by the caller; user/project authorization belongs in
    the input contract and is not inferred from a role or an asset filename.
    """
    if not isinstance(actor_id,str) or not actor_id or actor_id in bpy.data.objects:
        raise ValueError('Actor ID missing or already exists')
    if isinstance(height,bool) or not isinstance(height,(int,float)) or not math.isfinite(height) or not .05<=height<=100:
        raise ValueError('height outside finite proxy range .05..100 m; use confirmed sole-to-crown height including head')
    if not isinstance(color,(list,tuple)) or len(color)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=1 for x in color):
        raise ValueError('Invalid finite RGB')
    if not isinstance(allow_special_proxy,bool):raise ValueError('allow_special_proxy must be boolean')
    if proxy_mode=='cylinder':
        if hand_lod not in (None,'none','standard'):
            raise ValueError('Cylinder proxy has no hands; closeup_static cannot select a special mode')
        root=_build_cylinder_actor(actor_id,collection,height,color)
    elif proxy_mode=='articulated_special':
        if allow_special_proxy is not True:
            raise ValueError('articulated_special requires explicit allow_special_proxy=True')
        if hand_lod not in ('standard','closeup_static'):
            raise ValueError('Special proxy requires standard or closeup_static hand LOD')
        root=_build_articulated_actor(actor_id,collection,height,color,hand_lod)
    else:
        raise ValueError('Unknown proxy_mode; use cylinder or explicitly authorized articulated_special')
    root['proxy_mode']=proxy_mode
    root['allow_special_proxy']=bool(allow_special_proxy and proxy_mode=='articulated_special')
    root['color_rgb']=list(color)
    return root


def _build_cylinder_actor(actor_id, collection, height, color):
    root=bpy.data.objects.new(actor_id,None);collection.objects.link(root)
    root['generator_version']=GENERATOR_VERSION;root['hand_lod']='none'
    root['purpose']='model-reference';root['forward_axis']='-Y';root['height_m']=height
    root['structure']='cylinder-and-head-only; no shoulders, hips, limbs, hands or feet'
    root['ratios_json']=json.dumps(CYLINDER_RATIOS,sort_keys=True)
    body_material=bpy.data.materials.new(actor_id+'_body');body_material.diffuse_color=(*color,1)
    nose_material=bpy.data.materials.new(actor_id+'_nose');nose_material.diffuse_color=(*(x*.82 for x in color),1)
    front_material=bpy.data.materials.new(actor_id+'_front_marker');front_material.diffuse_color=(.88,.89,.86,1)

    def register(ob,name,material):
        ob.name=actor_id+'__'+name;ob.data.name=ob.name+'_mesh';ob.parent=root
        for c in list(ob.users_collection):c.objects.unlink(ob)
        collection.objects.link(ob);ob.data.materials.append(material)
        ob['part_id']=name;ob['fixed_local_transform']=True
        return ob
    bpy.ops.mesh.primitive_cylinder_add(vertices=64,radius=height*.115,depth=height*.82,
                                       end_fill_type='NGON',location=(0,0,height*.41))
    body=register(bpy.context.object,'body_cylinder',body_material)
    for polygon in body.data.polygons:polygon.use_smooth=len(polygon.vertices)==4
    for name,position,size,material in [
            ('head',(0,0,.91),(.17,.16,.18),body_material),
            ('nose',(0,-.083,.912),(.024,.028,.022),nose_material)]:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24,ring_count=16,radius=1)
        ob=bpy.context.object;ob.location=Vector(position)*height;ob.scale=Vector(size)*height/2
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        register(ob,name,material)
        for polygon in ob.data.polygons:polygon.use_smooth=True

    # A narrow, short curved strip follows the *polygonal cylinder surface*,
    # with a sub-millimetre offset/thickness to avoid z-fighting. It is neither
    # floating chest geometry nor a text/arrow overlay. Only the front has it.
    vertices=[];faces=[];count=8;step=2*math.pi/64
    angles=[-math.pi/2+math.radians(-5+10*i/count) for i in range(count+1)]
    for offset in (.00005,.00045):
        for z in (.58,.70):
            for angle in angles:
                center=(math.floor(angle/step)+.5)*step
                surface_radius=.115*math.cos(step/2)/math.cos(angle-center)
                radius=(surface_radius+offset)*height
                vertices.append((radius*math.cos(angle),radius*math.sin(angle),z*height))
    n=count+1
    for i in range(count):
        faces.append((i,i+1,n+i+1,n+i))
        faces.append((2*n+i,3*n+i,3*n+i+1,2*n+i+1))
        faces.append((i,2*n+i,2*n+i+1,i+1))
        faces.append((n+i,n+i+1,3*n+i+1,3*n+i))
    faces.extend([(0,n,3*n,2*n),(n-1,3*n-1,4*n-1,2*n-1)])
    mesh=bpy.data.meshes.new(actor_id+'__front_marker_mesh');mesh.from_pydata(vertices,[],faces);mesh.update()
    import bmesh
    bm=bmesh.new();bm.from_mesh(mesh);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(mesh);bm.free()
    marker=bpy.data.objects.new(actor_id+'__front_marker',mesh);collection.objects.link(marker)
    register(marker,'front_marker',front_material)
    marker['surface_gap_ratio']=.00005;marker['surface_thickness_ratio']=.00040
    marker['purpose']='non-text front orientation marker'

    for name,position in [('eyeline_center',(0,-.076,.938)),
                          ('eye_R',(-.026,-.072,.938)),('eye_L',(.026,-.072,.938)),
                          ('gaze_target',(0,-.62,.938))]:
        locator=bpy.data.objects.new(actor_id+'__'+name,None);collection.objects.link(locator)
        locator.parent=root;locator.location=Vector(position)*height
        locator.empty_display_type='PLAIN_AXES';locator.empty_display_size=height*.015
        locator.hide_render=True;locator.show_in_front=True
        locator['locator_id']=name;locator['part_id']='locator_'+name
        locator['purpose']='non-rendering-construction';locator['render_geometry']=False
    return root


def _build_articulated_actor(actor_id, collection, height=1.76, color=(.12,.43,.72), hand_lod='standard'):
    if not actor_id or actor_id in bpy.data.objects: raise ValueError('Actor ID missing or already exists')
    if not .5 <= height <= 2.5: raise ValueError('height outside proxy range')
    if len(color)!=3 or any(not 0<=x<=1 for x in color): raise ValueError('Invalid RGB')
    if hand_lod not in ('standard','closeup_static'): raise ValueError('Unknown hand LOD')
    root=bpy.data.objects.new(actor_id,None);collection.objects.link(root)
    root['generator_version']=GENERATOR_VERSION;root['hand_lod']=hand_lod
    root['purpose']='model-reference' if hand_lod=='standard' else 'diagnostic-only'
    root['forward_axis']='-Y';root['height_m']=height
    mat=bpy.data.materials.new(actor_id+'_body');mat.diffuse_color=(*color,1)
    marker=bpy.data.materials.new(actor_id+'_nose');marker.diffuse_color=(*(v*.82 for v in color),1)
    def part(name, xyz, size, material=mat, kind='ellipsoid', segments=16, ring_count=10):
        if kind=='ellipsoid':
            bpy.ops.mesh.primitive_uv_sphere_add(segments=segments,ring_count=ring_count,radius=1)
        else:
            bpy.ops.mesh.primitive_cube_add(size=2)
        ob=bpy.context.object;ob.name=actor_id+'__'+name;ob.data.name=ob.name+'_mesh'
        ob.location=Vector(xyz)*height;ob.scale=Vector(size)*height/2
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        ob.parent=root
        for c in list(ob.users_collection):c.objects.unlink(ob)
        collection.objects.link(ob);ob.data.materials.append(material)
        for poly in ob.data.polygons:poly.use_smooth=True
        ob['part_id']=name;ob['fixed_local_transform']=True
        return ob
    def capsule(name,a,b,width,depth=None):
        mid=(Vector(a)+Vector(b))/2
        ob=part(name,mid,(width,depth or width,(Vector(b)-Vector(a)).length+width*.5))
        ob.rotation_mode='QUATERNION';ob.rotation_quaternion=(Vector(b)-Vector(a)).to_track_quat('Z','Y')
        return ob
    def continuous_hand(side, sign):
        """Fuse connected palm/digit solids, rather than chaining ellipsoids.

        Digits are single capped ring meshes, with monotone longitudinal rings,
        gentle fixed curvature and a rounded endpoint. Root centerlines lie
        inside the palm. Voxel union removes internal intersections at the
        intended palm/digit attachment, leaving one closed connected surface.
        """
        palm_center=(sign*.188,-.01,.435)
        palm_size=(.052,.029,.075)
        palm_ob=part(side+'_palm',palm_center,palm_size,segments=40,ring_count=24)
        pieces=[palm_ob]
        digits=[]

        def tube(name, centers, radii, tip):
            vertices=[];faces=[];segments=20
            for center,(rx,ry) in zip(centers,radii):
                for j in range(segments):
                    theta=2*math.pi*j/segments
                    vertices.append(((center[0]+rx*math.cos(theta))*height,
                                     (center[1]+ry*math.sin(theta))*height,
                                     center[2]*height))
            # Ring orientation is clockwise as viewed down the digit axis.
            faces.append(tuple(reversed(range(segments))))
            for ring in range(len(centers)-1):
                for j in range(segments):
                    a=ring*segments+j;b=ring*segments+(j+1)%segments
                    faces.append((a,b,b+segments,a+segments))
            tip_index=len(vertices);vertices.append(tuple(v*height for v in tip))
            last=(len(centers)-1)*segments
            for j in range(segments):
                faces.append((last+j,last+(j+1)%segments,tip_index))
            mesh=bpy.data.meshes.new(actor_id+'__'+name+'_mesh')
            mesh.from_pydata(vertices,[],faces);mesh.update()
            ob=bpy.data.objects.new(actor_id+'__'+name,mesh);collection.objects.link(ob)
            ob.parent=root;ob.data.materials.append(mat);ob['part_id']=name
            for poly in mesh.polygons:poly.use_smooth=True
            pieces.append(ob)

        # Visible length excludes the portion embedded in the palm. Middle is
        # longest, ring slightly shorter, then index and little finger.
        definitions=[('index',.1695,.421,.061,-.003,.0052,.0060),
                     ('middle',.182,.420,.069,-.0008,.0054,.0062),
                     ('ring',.195,.419,.065,.0013,.0050,.0059),
                     ('little',.207,.421,.052,.0040,.0043,.0051)]
        # Several closely spaced distal rings form a rounded cap rather than
        # a long final cone; this changes only the dedicated closeup hand.
        times=(0,.08,.25,.48,.68,.84,.92,.96,.985,.997)
        profile=(.80,1.0,.98,.92,.86,.78,.68,.54,.35,.16)
        for name,x,z,length,spread,rx,ry in definitions:
            def center(t):return (sign*(x+spread*t),-.01-.0035*t*t,z-length*t)
            centers=[center(t) for t in times]
            tube(side+'_'+name,centers,[(rx*p,ry*p) for p in profile],center(1))
            digits.append({'name':name,'root':list(center(0)),'tip':list(center(1)),
                           'length':length,'radius_x':rx,'radius_y':ry,
                           'spread':spread,'curl_y':-.0035})

        # Thumb is a continuous oblique digit attached at the thenar region.
        # Its cross-sections use a local basis perpendicular to its centerline.
        thumb_path=[(sign*.169,-.010,.442),(sign*.163,-.011,.436),
                    (sign*.155,-.013,.428),(sign*.146,-.014,.417),
                    (sign*.143,-.0145,.411),(sign*.1416,-.0147,.4078)]
        thumb_radii=[.009,.0095,.0086,.0068,.0041,.0017]
        vertices=[];faces=[];segments=20
        for k,(center,radius) in enumerate(zip(thumb_path,thumb_radii)):
            prev=Vector(thumb_path[max(0,k-1)]);nxt=Vector(thumb_path[min(len(thumb_path)-1,k+1)])
            tangent=(nxt-prev).normalized()
            axis1=tangent.cross(Vector((0,1,0))).normalized();axis2=tangent.cross(axis1).normalized()
            for j in range(segments):
                theta=2*math.pi*j/segments
                p=Vector(center)+axis1*(radius*math.cos(theta))+axis2*(radius*.90*math.sin(theta))
                vertices.append(tuple(p*height))
        faces.append(tuple(reversed(range(segments))))
        for ring in range(len(thumb_path)-1):
            for j in range(segments):
                a=ring*segments+j;b=ring*segments+(j+1)%segments
                faces.append((a,b,b+segments,a+segments))
        tip=Vector((sign*.141,-.0148,.407));tip_index=len(vertices);vertices.append(tuple(tip*height))
        for j in range(segments):
            last=(len(thumb_path)-1)*segments
            faces.append((last+j,last+(j+1)%segments,tip_index))
        mesh=bpy.data.meshes.new(actor_id+'__'+side+'_thumb_continuous_mesh')
        mesh.from_pydata(vertices,[],faces);mesh.update()
        ob=bpy.data.objects.new(actor_id+'__'+side+'_thumb_continuous',mesh);collection.objects.link(ob)
        ob.parent=root;ob.data.materials.append(mat);pieces.append(ob)

        bpy.ops.object.select_all(action='DESELECT')
        for ob in pieces:ob.select_set(True)
        bpy.context.view_layer.objects.active=palm_ob
        bpy.ops.object.join()
        # Consistent outward normals are required for a reproducible solid union.
        import bmesh
        bm=bmesh.new();bm.from_mesh(palm_ob.data)
        bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(palm_ob.data);bm.free()
        remesh=palm_ob.modifiers.new('connected_static_hand','REMESH');remesh.mode='VOXEL'
        remesh.voxel_size=height*.00065;remesh.use_smooth_shade=True
        bpy.ops.object.modifier_apply(modifier=remesh.name)
        smooth=palm_ob.modifiers.new('small_surface_smoothing','SMOOTH');smooth.factor=.35;smooth.iterations=2
        bpy.ops.object.modifier_apply(modifier=smooth.name)
        for poly in palm_ob.data.polygons:poly.use_smooth=True
        palm_ob['part_id']=side+'_palm';palm_ob['fixed_local_transform']=True
        palm_ob['part_role']='continuous_palm_and_five_digits'
        palm_ob['static_analysis_only']=True
        construction={'side':side,'palm_center':palm_center,'palm_size':palm_size,
                      'fingers':digits,'thumb_root':thumb_path[0],'thumb_tip':list(tip),
                      'voxel_size_ratio':.00065,'method':'capped_continuous_digits_then_voxel_union',
                      'no_ik_or_contact_solver':True}
        palm_ob['construction_json']=json.dumps(construction,sort_keys=True)
        return palm_ob
    part('head',(0,0,.93),(.135,.115,.14))
    # Small nose bump shows front in silhouette; no lettering, eyes, arrow or expression.
    part('nose',(0,-.061,RATIOS['orientation_marker_height']),(.027,.037,.022),marker)
    part('neck',(0,0,.842),(.07,.07,.06))
    part('thorax',(0,0,.715),(.25,.135,.24))
    part('waist',(0,0,.593),(.16,.12,.12))
    part('pelvis',(0,0,.535),(.18,.145,.14))
    # Anatomical right = -X when forward is -Y and up is +Z.
    for side,sign in [('R',-1),('L',1)]:
        capsule(side+'_arm',(sign*.126,0,.786),(sign*.165,0,.623),.064,.06)
        capsule(side+'_forearm',(sign*.165,0,.623),(sign*.185,-.009,.475),.050,.045)
        capsule(side+'_thigh',(sign*.056,0,.50),(sign*.060,0,.295),.09,.096)
        capsule(side+'_shin',(sign*.060,0,.295),(sign*.060,0,.063),.064,.064)
        part(side+'_foot',(sign*.060,-.025,.031),(.068,.15,.062))
        if hand_lod=='closeup_static':
            continuous_hand(side,sign)
        else:
            # Preserve the original standard mesh construction exactly.
            palm=Vector((sign*.188,-.01,.435))
            part(side+'_palm',palm,(.047,.029,.075))
            part(side+'_thumb',(sign*.162,-.019,.444),(.022,.026,.038))
    # These are scene-editor construction markers, never renderable geometry.
    # They describe neutral eye-line locations and a default forward target;
    # there is no aim constraint, pupil, face rig or animation attached.
    for name,position in [('eyeline_center',(0,-.054,.950)),
                          ('eye_R',(-.026,-.054,.950)),
                          ('eye_L',(.026,-.054,.950)),
                          ('gaze_target',(0,-.62,.950))]:
        locator=bpy.data.objects.new(actor_id+'__'+name,None);collection.objects.link(locator)
        locator.parent=root;locator.location=Vector(position)*height
        locator.empty_display_type='PLAIN_AXES';locator.empty_display_size=height*.015
        locator.hide_render=True;locator.show_in_front=True
        locator['locator_id']=name;locator['part_id']='locator_'+name
        locator['purpose']='non-rendering-construction';locator['render_geometry']=False
    root['ratios_json']=json.dumps(RATIOS,sort_keys=True)
    return root

def semantic_record(root):
    def floats(vals):return [round(float(x),7) for x in vals]
    def local_dimensions(ob):
        # Object.dimensions includes dependency-graph world-scale roundoff when
        # a parent rotates. V2 records immutable local bounds instead.
        if 'proxy_mode' not in root:return floats(ob.dimensions)
        return floats((max(v.co[i] for v in ob.data.vertices)-min(v.co[i] for v in ob.data.vertices))*abs(ob.scale[i]) for i in range(3))
    parts=[]
    for o in sorted((x for x in root.children if x.type=='MESH'),key=lambda x:x['part_id']):
        parts.append({'id':o['part_id'],'vertices':len(o.data.vertices),'polygons':len(o.data.polygons),
                      'location':floats(o.location),'rotation':floats(o.matrix_basis.to_quaternion()),
                      'dimensions':local_dimensions(o),'scale':floats(o.scale),
                      'material':floats(o.data.materials[0].diffuse_color),
                      'mesh_sha256':hashlib.sha256(json.dumps([floats(v.co) for v in o.data.vertices]).encode()).hexdigest()})
    locators=[]
    for o in sorted((x for x in root.children if x.type=='EMPTY'),key=lambda x:x.get('locator_id',x.name)):
        locators.append({'id':o.get('locator_id',o.name),'type':'EMPTY','location':floats(o.location),
                         'rotation':floats(o.matrix_basis.to_quaternion()),'scale':floats(o.scale),
                         'render_geometry':False,'purpose':o.get('purpose','unclassified')})
    # Respect the saved asset version, including pre-1.1 files. With no locator
    # children, keep the original record schema/hash rather than relabeling an
    # old .blend as a newly generated asset.
    data={'actor_id':root.name,'generator_version':root.get('generator_version',GENERATOR_VERSION),
          'height_m':root['height_m'],'hand_lod':root['hand_lod'],
          'ratios':json.loads(root.get('ratios_json',json.dumps(RATIOS))),'parts':parts}
    if locators:data['locators']=locators
    # New metadata is conditional so historical assets preserve their schema
    # and semantic hash. They are readable history, not relabelled v2 assets.
    if 'proxy_mode' in root:
        data.update({'proxy_mode':root['proxy_mode'],'color':floats(root['color_rgb']),
                     'purpose':root['purpose'],'allow_special_proxy':bool(root.get('allow_special_proxy',False))})
    data['semantic_sha256']=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    return data
