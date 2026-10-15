"""Actual renders and mode-gate checks for the default cylinder/head proxy v2.

Use installed Blender. All outputs go to one new directory. Historical code is
read only when its explicit reviewed path is provided; no historical file or
old video is modified. Test images contain no labels or rendering text.
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
from mathutils import Vector

sys.path.insert(0,str(Path(__file__).resolve().parent))
from canonical_actor import build_actor, semantic_record, GENERATOR_VERSION
from proxy_contract import resolve_actor_requests, build_proxy_contract, CYLINDER_PARTS


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pixels(path):
    image=bpy.data.images.load(str(path),check_existing=False)
    values=array('f',image.pixels[:]);size=list(image.size);bpy.data.images.remove(image)
    return values,size


def image_record(path):
    values,size=pixels(path)
    return {'path':str(path),'width':size[0],'height':size[1],
            'pixel_sha256':hashlib.sha256(values.tobytes()).hexdigest(),'sha256':sha(path)}


def image_difference(first,second):
    a,asize=pixels(first);b,bsize=pixels(second);assert asize==bsize
    changed=0;maximum=0.0
    for index in range(0,len(a),4):
        difference=max(abs(a[index+channel]-b[index+channel]) for channel in range(3))
        maximum=max(maximum,difference)
        changed+=difference>1/255
    return {'changed_pixels_above_1_255':changed,'total_pixels':asize[0]*asize[1],
            'max_rgb_difference':maximum,'pixel_exact':a==b}


def remove_actor(root):
    for ob in list(root.children):bpy.data.objects.remove(ob,do_unlink=True)
    bpy.data.objects.remove(root,do_unlink=True)


def show_actor(root,visible):
    for ob in root.children:ob.hide_render=not visible if ob.type=='MESH' else True


def camera_pose(camera,position,target,scale):
    camera.location=position;camera.rotation_mode='QUATERNION'
    camera.rotation_quaternion=(Vector(target)-camera.location).to_track_quat('-Z','Y')
    camera.data.type='ORTHO';camera.data.ortho_scale=scale;camera.data.clip_start=.01;camera.data.clip_end=100


def structural_checks(root):
    bpy.context.view_layer.update()
    meshes=[ob for ob in root.children if ob.type=='MESH']
    empties=[ob for ob in root.children if ob.type=='EMPTY']
    assert {ob['part_id'] for ob in meshes}==CYLINDER_PARTS and len(meshes)==4
    assert root['proxy_mode']=='cylinder' and root['hand_lod']=='none'
    assert root['purpose']=='model-reference' and not root['allow_special_proxy']
    assert all(not ob.constraints and ob.animation_data is None for ob in root.children)
    assert len(empties)==4 and all(ob.data is None and ob.hide_render for ob in empties)
    assert {ob['locator_id'] for ob in empties}=={'eyeline_center','eye_L','eye_R','gaze_target'}
    bounds=[ob.matrix_world@Vector(vertex) for ob in meshes for vertex in ob.bound_box]
    assert abs(min(point.z for point in bounds))<1e-5
    assert abs(max(point.z for point in bounds)-root['height_m'])<1e-5
    marker=next(ob for ob in meshes if ob['part_id']=='front_marker')
    nose=next(ob for ob in meshes if ob['part_id']=='nose')
    assert nose.location.y<0 and all(vertex.co.y<0 for vertex in marker.data.vertices)
    radius=root['height_m']*.115;step=2*math.pi/64
    surface_offsets=[]
    for vertex in marker.data.vertices:
        angle=math.atan2(vertex.co.y,vertex.co.x);face_center=(math.floor(angle/step)+.5)*step
        surface=radius*math.cos(step/2)/math.cos(angle-face_center)
        surface_offsets.append(math.hypot(vertex.co.x,vertex.co.y)-surface)
    height=root['height_m']
    assert min(surface_offsets)>0 and max(surface_offsets)<height*.00046
    return {'mesh_count':4,'mesh_part_ids':sorted(CYLINDER_PARTS),'no_limbs_or_hands':True,
            'nonrendering_empty_count':4,'height_m':height,'lowest_z':min(point.z for point in bounds),
            'highest_z':max(point.z for point in bounds),'front_marker_surface_offset_m':
                {'min':min(surface_offsets),'max':max(surface_offsets)},
            'front_marker_and_nose_axis':'-Y'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--historical-python')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    out=Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('Use a fresh cylinder validation directory')
    out.mkdir(parents=True,exist_ok=True)
    for name in ('pass-a','pass-b','marker-hidden','special-explicit'):(out/name).mkdir()
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    scene.world=bpy.data.worlds.new('world');scene.world.color=(.19,.21,.23)
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    scene.render.engine='BLENDER_WORKBENCH';scene.render.resolution_x=800;scene.render.resolution_y=800
    scene.render.resolution_percentage=100;scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
    scene.view_settings.view_transform='Standard';scene.view_settings.look='None'
    shading=scene.display.shading;shading.light='STUDIO';shading.color_type='MATERIAL'
    shading.show_shadows=True;shading.show_cavity=True;shading.cavity_type='BOTH';shading.background_type='WORLD'
    collection=bpy.data.collections.new('CYLINDER_PROXY_TEST');scene.collection.children.link(collection)
    camera=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));scene.collection.objects.link(camera);scene.camera=camera

    def render(path):
        scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
        return image_record(path)

    views={'front':(0,-5,.88),'side':(5,0,.88),'back':(0,5,.88),'three-quarter':(3,-5,.88)}
    def gallery(root,folder):
        results={}
        for name,position in views.items():
            camera_pose(camera,position,(0,0,.88),2.02)
            results[name]=render(folder/(name+'.png'))
        return results

    # Reject mode leakage before creating any objects.
    rejection_cases=[('closeup_without_mode',{'hand_lod':'closeup_static'}),
                     ('closeup_even_with_allow_but_no_mode',{'hand_lod':'closeup_static','allow_special_proxy':True}),
                     ('special_without_allow',{'proxy_mode':'articulated_special'}),
                     ('special_none_hand',{'proxy_mode':'articulated_special','allow_special_proxy':True,'hand_lod':'none'}),
                     ('unknown_mode',{'proxy_mode':'human'}),
                     ('truthy_nonboolean_permission',{'proxy_mode':'articulated_special','allow_special_proxy':'yes'})]
    rejected=[]
    for label,kwargs in rejection_cases:
        before=set(bpy.data.objects)
        try:build_actor('should_not_exist',collection,**kwargs)
        except ValueError as error:
            assert set(bpy.data.objects)==before
            rejected.append({'case':label,'rejected_before_object_creation':True,'message':str(error)})
        else:raise AssertionError('Unsafe mode combination accepted: '+label)

    # Legacy standard is an input alias for NONE in the default cylinder mode.
    aliases=[]
    for lod in ('standard','none',None):
        actor=build_actor('cylinder_actor',collection,hand_lod=lod)
        structural_checks(actor);record=semantic_record(actor)
        aliases.append(record);remove_actor(actor)
    assert aliases[0]==aliases[1]==aliases[2]
    actor=build_actor('cylinder_actor',collection,allow_special_proxy=True)
    assert semantic_record(actor)==aliases[0],'Permission alone must not enable special geometry'
    remove_actor(actor)

    first=build_actor('cylinder_actor',collection)
    structure=structural_checks(first);first_record=semantic_record(first)
    first_views=gallery(first,out/'pass-a')
    marker=next(ob for ob in first.children if ob.get('part_id')=='front_marker')
    marker.hide_render=True
    hidden_views=gallery(first,out/'marker-hidden')
    marker.hide_render=False
    marker_tests={name:image_difference(out/'pass-a'/(name+'.png'),out/'marker-hidden'/(name+'.png')) for name in views}
    assert marker_tests['front']['changed_pixels_above_1_255']>100
    assert marker_tests['back']['pixel_exact'],'Front marker should be fully hidden from the back'
    assert marker_tests['side']['changed_pixels_above_1_255'] < marker_tests['front']['changed_pixels_above_1_255']*.20
    camera_pose(camera,views['front'],(0,0,.88),2.02)
    render(out/'locators-hidden.png')
    for ob in first.children:
        if ob.type=='EMPTY':ob.hide_render=False
    render(out/'locators-enabled.png')
    locator_test=image_difference(out/'locators-hidden.png',out/'locators-enabled.png')
    assert locator_test['pixel_exact']
    for ob in first.children:
        if ob.type=='EMPTY':ob.hide_render=True
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'canonical-actor.blend'))
    remove_actor(first)
    second=build_actor('cylinder_actor',collection);second_record=semantic_record(second)
    assert first_record==second_record
    second_views=gallery(second,out/'pass-b')
    repeated={name:first_views[name]['pixel_sha256']==second_views[name]['pixel_sha256'] for name in views}
    assert all(repeated.values())

    # Real low-frame root-motion test keeps every local mesh/locator unchanged.
    motion=[]
    for index,(x,yaw) in enumerate(((0,0),(.25,25),(.25,25))):
        second.location.x=x;second.rotation_euler.z=math.radians(yaw)
        camera_pose(camera,(0,-5,.88),(0,0,.88),2.4)
        image=render(out/f'root-motion-{index}.png')
        assert semantic_record(second)==second_record
        motion.append({'frame':index,'root_x':x,'yaw_degrees':yaw,'render':image})
    second.location=(0,0,0);second.rotation_euler=(0,0,0)
    colors=[(.12,.43,.72),(.90,.29,.09),(.20,.68,.38)]
    group=[second]
    for index,color in enumerate(colors[1:],start=1):group.append(build_actor('color_'+str(index),collection,color=color))
    for index,actor in enumerate(group):actor.location.x=(index-1)*.85
    camera_pose(camera,(0,-6,.88),(0,0,.88),3.2)
    group_image=render(out/'multi-actor-colors.png')
    group_records={actor.name:semantic_record(actor) for actor in group}
    fixture={'actors':[{'id':actor.name,'character_name':'Test '+str(index+1),'color':colors[index],'height':1.76} for index,actor in enumerate(group)]}
    contract=build_proxy_contract(fixture,group_records,for_clean=True)
    for actor in group:remove_actor(actor)

    historical={}
    if args.historical_python:
        path=Path(args.historical_python).resolve(strict=True)
        module_spec=importlib.util.spec_from_file_location('reviewed_historical_actor',path)
        historical_module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(historical_module)
        actor=historical_module.build_actor('explicit_special',collection)
        old_record=historical_module.semantic_record(actor)
        assert semantic_record(actor)==old_record
        remove_actor(actor)
        actor=build_actor('explicit_special',collection,proxy_mode='articulated_special',allow_special_proxy=True)
        special_record=semantic_record(actor)
        assert len(old_record['parts'])==len(special_record['parts'])
        for old_part,new_part in zip(old_record['parts'],special_record['parts']):
            assert {k:v for k,v in old_part.items() if k!='dimensions'}=={k:v for k,v in new_part.items() if k!='dimensions'}
            assert all(abs(a-b)<1e-6 for a,b in zip(old_part['dimensions'],new_part['dimensions']))
        historical={'path':str(path),'sha256':sha(path),'old_semantic_schema_and_hash_compatible':True,
                    'special_standard_mesh_vertices_transforms_materials_match_historical_parts':True,
                    'dimension_semantics':'v2 local vertex bounds; within 1e-6 of historical world-derived dimensions'}
    else:
        actor=build_actor('explicit_special',collection,proxy_mode='articulated_special',allow_special_proxy=True)
        special_record=semantic_record(actor)
    assert len(special_record['parts'])==20 and special_record['allow_special_proxy'] is True
    camera_pose(camera,(0,-5,.88),(0,0,.88),2.02)
    special_standard_image=render(out/'special-explicit'/'standard.png')
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'special-explicit'/'articulated-special.blend'))
    remove_actor(actor)
    diagnostic=build_actor('explicit_diagnostic',collection,hand_lod='closeup_static',proxy_mode='articulated_special',allow_special_proxy=True)
    diagnostic_record=semantic_record(diagnostic)
    assert diagnostic_record['purpose']=='diagnostic-only' and diagnostic_record['allow_special_proxy'] is True
    for ob in diagnostic.children:
        ob.hide_render=ob.type!='MESH' or ob.get('part_id')!='L_palm'
    target=Vector((.181*1.76,-.012*1.76,.417*1.76))
    camera_pose(camera,target+Vector((0,-1.2,0)),target,1.76*.16)
    special_hand_image=render(out/'special-explicit'/'hand-diagnostic.png')
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'special-explicit'/'hand-diagnostic-only.blend'))
    diagnostic_spec={'allow_special_proxy':True,'special_proxy_reason':'Explicit local diagnostic test',
                     'actors':[{'id':diagnostic.name,'character_name':'Diagnostic','color':colors[0],
                                'height':1.76,'proxy_mode':'articulated_special','hand_lod':'closeup_static'}]}
    try:build_proxy_contract(diagnostic_spec,{diagnostic.name:diagnostic_record},for_clean=True)
    except ValueError as error:diagnostic_rejection=str(error)
    else:raise AssertionError('Diagnostic hand entered a clean proxy contract')
    build_proxy_contract(diagnostic_spec,{diagnostic.name:diagnostic_record},for_clean=False)
    write(out/'actor-geometry.json',{'cylinder_actor':first_record})
    write(out/'special-explicit'/'actor-geometry.json',{'explicit_special':special_record,'explicit_diagnostic':diagnostic_record})
    report={'schema':'timeaigc-cylinder-validation/v1','status':'checks-passed-visual-review-pending',
            'generator_version':GENERATOR_VERSION,'generator_sha256':sha(Path(__file__).with_name('canonical_actor.py')),
            'script_sha256':sha(__file__),'blender':bpy.app.version_string,'default_structure':structure,
            'mode_rejections':rejected,'default_aliases_same_none_record':True,'permission_alone_keeps_cylinder':True,
            'deterministic_rebuild_equal':first_record==second_record,'repeat_views_pixel_exact':repeated,
            'marker_view_checks':marker_tests,'nonrendering_locators_image_exact':locator_test,
            'default_views':first_views,'root_motion_checks':motion,'group_image':group_image,'group_contract':contract,
            'historical_compatibility':historical,'explicit_special_render':special_standard_image,
            'explicit_diagnostic_render':special_hand_image,'diagnostic_rejected_for_clean':diagnostic_rejection,
            'visual_review':{'status':'pending','images':'pass-a front/side/back/three-quarter and multi-actor-colors'},
            'limitations':['Synthetic proxy tests only. No IK, contact, gait, gestures, facial acting or video-generation validation.',
                           'Special articulated and hand examples are explicitly selected diagnostics, never the default cylinder.',
                           'No reference Library image match is claimed.']}
    write(out/'cylinder-validation.json',report)
    print(json.dumps({'status':report['status'],'version':GENERATOR_VERSION,'out':str(out),'marker_view_checks':marker_tests},ensure_ascii=False))


if __name__=='__main__':main()
