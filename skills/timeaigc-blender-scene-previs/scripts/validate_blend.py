"""Read saved proxy artifacts, including historical versions; never save a .blend."""
import argparse,json,sys,math
from pathlib import Path
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from canonical_actor import semantic_record
from proxy_contract import validate_proxy_contract, CYLINDER_PARTS


def inspect_actor(actor,clean=True):
    parts=[ob for ob in actor.children if ob.type=='MESH']
    ids={ob['part_id'] for ob in parts}
    mode=actor.get('proxy_mode')
    if mode=='cylinder':
        assert ids==CYLINDER_PARTS and len(parts)==4
        assert actor.get('hand_lod')=='none' and not actor.get('allow_special_proxy')
        assert actor.get('purpose')=='model-reference'
        nose=next(ob for ob in parts if ob['part_id']=='nose')
        marker=next(ob for ob in parts if ob['part_id']=='front_marker')
        assert nose.location.y<0 and all(v.co.y<0 for v in marker.data.vertices)
    elif mode=='articulated_special':
        assert actor.get('allow_special_proxy') is True
        assert not clean or actor.get('hand_lod')=='standard'
        assert ids>={'head','nose','L_palm','R_palm'}
    else:
        # Historical v1 files remain readable, preserving their own evidence.
        assert mode is None and len(parts)==20
        assert ids>={'head','nose','L_palm','R_palm'}
    if clean:assert actor.get('hand_lod')!='closeup_static'
    locators=[ob for ob in actor.children if ob.type=='EMPTY']
    for locator in locators:
        assert locator.data is None and locator.hide_render and not locator.constraints and locator.animation_data is None
    assert all(not ob.constraints for ob in parts)
    return parts,locators


def inspect_build_scene_output(root,expected):
    """General builder has per-actor records and sparse camera samples."""
    result={}
    stage=root.parent/'scene.blend'
    bpy.ops.wm.open_mainfile(filepath=str(stage))
    assert not any(ob.get('generator_version') for ob in bpy.data.objects)
    result['source_stage_actor_count']=0
    bpy.ops.wm.open_mainfile(filepath=str(root/'previs.blend'))
    assert not any(ob.type in ('FONT','ARMATURE') for ob in bpy.data.objects)
    actors=[ob for ob in bpy.data.objects if ob.get('generator_version')]
    for actor in actors:inspect_actor(actor)
    records={actor.name:semantic_record(actor) for actor in actors}
    assert records==expected['actors']==json.loads((root/'actor-geometry.json').read_text(encoding='utf-8-sig'))
    contract=json.loads((root/'proxy-contract.json').read_text(encoding='utf-8-sig'))
    timeline=json.loads((root/'timeline-v2.json').read_text(encoding='utf-8-sig'))
    validate_proxy_contract(contract,spec=timeline,actor_records=records,for_clean=True)
    scene=bpy.context.scene
    assert scene.frame_end-scene.frame_start+1==expected['frame_count']==timeline['frame_count']
    samples=json.loads((root/'samples.json').read_text(encoding='utf-8-sig'))
    samples={row['frame']:row for row in samples}
    for frame in range(scene.frame_start,scene.frame_end+1):
        scene.frame_set(frame)
        assert all(semantic_record(actor)==records[actor.name] for actor in actors)
        shot=next(shot for shot in timeline['shots'] if shot['start_frame']<=frame-1<shot['end_frame'])
        marker=next(marker for marker in scene.timeline_markers if marker.name==shot['id'])
        assert scene.camera==marker.camera
        if frame in samples:
            row=samples[frame];assert row['shot']==shot['id'];camera=row['camera']
            assert all(abs(a-b)<1e-5 for a,b in zip(scene.camera.location,camera['position']))
            quaternion=(Vector(camera['target'])-Vector(camera['position'])).to_track_quat('-Z','Y')
            assert all(abs(a-b)<1e-5 for a,b in zip(scene.camera.rotation_quaternion,quaternion))
            assert abs(scene.camera.data.lens-camera['lens'])<1e-5
    result.update({'passed':True,'saved_blend_frames_checked':expected['frame_count'],
                   'all_saved_local_parts_constant':True,'saved_cut_cameras_match_timeline':True,
                   'saved_camera_samples_checked':len(samples),
                   'no_text_armature_or_diagnostic_hand_in_clean':True,
                   'actor_modes':{actor.name:actor['proxy_mode'] for actor in actors},
                   'limitations':['General builder stores sparse camera samples; this audit does not claim full per-frame root-state log comparison.']})
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--report',help='Optional separate audit JSON path; saved .blend files are never modified')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);root=Path(args.output).resolve();result={}
    expected=json.loads((root/'render-evidence.json').read_text(encoding='utf-8-sig'))
    report=Path(args.report).resolve() if args.report else root/'blend-validation.json'
    if expected.get('schema')=='timeaigc-render-evidence/v2' and isinstance(expected.get('actors'),dict):
        result=inspect_build_scene_output(root,expected)
        report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result));return
    bpy.ops.wm.open_mainfile(filepath=str(root/'scene.blend'))
    assert not any(ob.get('generator_version') for ob in bpy.data.objects)
    result['source_stage_actor_count']=0
    bpy.ops.wm.open_mainfile(filepath=str(root/'canonical-actor.blend'))
    actors=[ob for ob in bpy.data.objects if ob.get('generator_version')]
    assert len(actors)==1
    actor=actors[0];parts,locators=inspect_actor(actor)
    assert actor.name==expected['generator_record']['actor_id']
    points=[ob.matrix_world@Vector(co) for ob in parts for co in ob.bound_box]
    box={'min':[min(point[index] for point in points) for index in range(3)],
         'max':[max(point[index] for point in points) for index in range(3)]}
    assert abs(box['min'][2])<1e-5 and abs(box['max'][2]-actor['height_m'])<1e-5
    result['canonical']={'actor_id':actor.name,'actor_count':1,'mesh_parts':len(parts),'locator_count':len(locators),
                         'proxy_mode':actor.get('proxy_mode','legacy-articulated'),
                         'vertices':sum(len(ob.data.vertices) for ob in parts),'forward_axis':'-Y','bbox_m':box}
    assert semantic_record(actor)==expected['generator_record']
    bpy.ops.wm.open_mainfile(filepath=str(root/'previs.blend'))
    assert not any(ob.type in ('FONT','ARMATURE') for ob in bpy.data.objects)
    assert not any(ob.get('hand_lod')=='closeup_static' for ob in bpy.data.objects)
    actors=[ob for ob in bpy.data.objects if ob.get('generator_version')]
    expected_ids=set(expected.get('generator_records',{})) or set(expected['records'][0]['actors'])
    assert {actor.name for actor in actors}==expected_ids
    records={actor.name:semantic_record(actor) for actor in actors}
    for actor in actors:inspect_actor(actor)
    if 'generator_records' in expected:assert records==expected['generator_records']
    if 'proxy_contract' in expected:
        validate_proxy_contract(expected['proxy_contract'],actor_records=records,for_clean=True)
        assert records==json.loads((root/'actor-geometry.json').read_text(encoding='utf-8-sig'))
    scene=bpy.context.scene
    for row in expected['records']:
        scene.frame_set(row['frame']+1)
        assert all(abs(value-target)<1e-5 for value,target in zip(scene.camera.location,row['camera_position']))
        if 'camera_quaternion' in row:
            assert all(abs(value-target)<1e-5 for value,target in zip(scene.camera.rotation_quaternion,row['camera_quaternion']))
            assert abs(scene.camera.data.lens-row['camera_lens'])<1e-5
        for actor in actors:
            record=row['actors'][actor.name]
            assert all(abs(value-target)<1e-5 for value,target in zip(actor.location,record['position']))
            assert abs(actor.rotation_euler.z-record['yaw'])<1e-5
            assert semantic_record(actor)==records[actor.name]
            assert not any(ob.constraints for ob in actor.children)
            if 'visible' in record:
                assert any(not ob.hide_render for ob in actor.children if ob.type=='MESH')==record['visible']
    result['saved_blend_frames_checked']=len(expected['records'])
    result['all_saved_root_and_camera_states_match_render']=True
    result['all_saved_local_parts_constant']=True
    result['no_text_armature_or_diagnostic_hand_in_clean']=True
    result['actor_modes']={actor.name:actor.get('proxy_mode','legacy-articulated') for actor in actors}
    result['passed']=True
    report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__':main()
