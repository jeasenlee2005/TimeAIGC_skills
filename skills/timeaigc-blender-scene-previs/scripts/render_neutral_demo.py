"""Render an explicit synthetic proxy fixture; actor identity comes from its spec.

This is not a replacement for an approved production scene. Default actor mode
is cylinder/head only. No hand diagnostic is silently created by this renderer.
"""
import argparse, hashlib, json, math, sys
from pathlib import Path
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from canonical_actor import build_actor, semantic_record, GENERATOR_VERSION
from proxy_contract import resolve_actor_requests, build_proxy_contract


def validate_timeline(spec):
    # Blender's Python does not require Pillow; the downstream compositor
    # independently validates the complete text/layout contract.
    for key in ('width','height','fps','frame_count'):
        if type(spec.get(key)) is not int or spec[key]<=0:raise ValueError('Invalid '+key)
    cursor=0;ids=set()
    if not spec.get('shots'):raise ValueError('Explicit shots required')
    for shot in spec['shots']:
        if shot['id'] in ids or shot['start_frame']!=cursor or type(shot['end_frame']) is not int or shot['end_frame']<=cursor:
            raise ValueError('Invalid half-open shot timeline')
        if not isinstance(shot.get('summary'),str) or not isinstance(shot.get('dialogue'),str):raise ValueError('Shot text required')
        ids.add(shot['id']);cursor=shot['end_frame']
    if cursor!=spec['frame_count']:raise ValueError('Shot timeline does not cover all frames')


def camera_pose(cam,position,target,lens=48):
    cam.location=position;cam.rotation_mode='QUATERNION'
    cam.rotation_quaternion=(Vector(target)-cam.location).to_track_quat('-Z','Y')
    cam.data.lens=lens;cam.data.sensor_width=36


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--spec',required=True);ap.add_argument('--out',required=True)
    args=ap.parse_args(sys.argv[sys.argv.index('--')+1:]);spec_path=Path(args.spec).resolve()
    spec=json.loads(spec_path.read_text(encoding='utf-8-sig'));validate_timeline(spec)
    requests=resolve_actor_requests(spec,for_clean=True)
    out=Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('Choose empty output folder')
    out.mkdir(parents=True,exist_ok=True);(out/'frames').mkdir();(out/'gallery').mkdir()
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    scene.world=bpy.data.worlds.new('world');scene.world.color=(.16,.18,.20)
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    scene.render.engine='BLENDER_WORKBENCH';scene.render.resolution_x=spec['width'];scene.render.resolution_y=spec['height'];scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
    scene.render.fps=spec['fps'];scene.frame_start=1;scene.frame_end=spec['frame_count'];scene.view_settings.view_transform='Standard'
    shading=scene.display.shading;shading.light='STUDIO';shading.color_type='MATERIAL';shading.show_shadows=True;shading.show_cavity=True;shading.cavity_type='BOTH';shading.background_type='WORLD'
    env=bpy.data.collections.new('SYNTHETIC_EMPTY_STAGE');scene.collection.children.link(env)
    bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,-.07));floor=bpy.context.object;floor.name='floor';floor.dimensions=(200,200,.14)
    for collection in list(floor.users_collection):collection.objects.unlink(floor)
    env.objects.link(floor);material=bpy.data.materials.new('floor_mat');material.diffuse_color=(.30,.33,.36,1);floor.data.materials.append(material)
    cam=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));scene.collection.objects.link(cam);scene.camera=cam
    height=max(request['height_m'] for request in requests)
    camera_pose(cam,(0,-height*2.84,height*.65),(0,0,height*.5))
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'scene.blend'))
    collection=bpy.data.collections.new('NEUTRAL_ACTORS');scene.collection.children.link(collection)

    def construct(request):
        actor=build_actor(request['id'],collection,request['height_m'],request['color'],request['hand_lod'],
                          proxy_mode=request['proxy_mode'],allow_special_proxy=request['allow_special_proxy'])
        actor['character_name']=request['character_name'];actor['color_name']=request['color_name']
        return actor

    first=construct(requests[0]);before=semantic_record(first)
    for ob in list(first.children):bpy.data.objects.remove(ob,do_unlink=True)
    bpy.data.objects.remove(first,do_unlink=True)
    first=construct(requests[0]);after=semantic_record(first)
    assert before==after,'Generator is not deterministic'
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'canonical-actor.blend'))
    actors=[first]+[construct(request) for request in requests[1:]]
    initial={actor.name:semantic_record(actor) for actor in actors}
    contract=build_proxy_contract(spec,initial,for_clean=True)
    write(out/'actor-geometry.json',initial);write(out/'proxy-contract.json',contract)
    write(out/'timeline-v2.json',spec)

    def visibility(actor,visible):
        for ob in actor.children:ob.hide_render=not visible if ob.type=='MESH' else True
    def render(path):scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
    def reset():
        for actor in actors:actor.location=(0,0,0);actor.rotation_euler=(0,0,0);visibility(actor,False)
    def group():
        spacing=height*.46
        for index,actor in enumerate(actors):actor.location.x=(index-(len(actors)-1)/2)*spacing;visibility(actor,True)
    def group_camera():
        distance=max(height*3.4,len(actors)*height*.95)
        return (0,-distance,height*.82),(0,0,height*.5)

    for name,position in [('front',(0,-height*2.84,height*.65)),('side',(height*2.84,0,height*.65)),('back',(0,height*2.84,height*.65))]:
        reset();visibility(first,True);camera_pose(cam,position,(0,0,height*.5));render(out/'gallery'/f'{name}.png')
    reset();group();position,target=group_camera();camera_pose(cam,position,target,45);render(out/'gallery'/'multi-actor-colors.png')
    reset();visibility(first,True)
    logs=[]
    for frame in range(spec['frame_count']):
        scene.frame_set(frame+1);shot=next(item for item in spec['shots'] if item['start_frame']<=frame<item['end_frame'])
        u=(frame-shot['start_frame'])/max(1,shot['end_frame']-shot['start_frame']-1)
        case=shot.get('visual_case',{'S01':'front','S02':'side','S03':'back','S04':'group'}.get(shot['id'],'blocking'))
        reset()
        if case=='front':
            visibility(first,True);position=(0,-height*(2.84-.17*u),height*.65);target=(0,0,height*.5)
        elif case=='side':visibility(first,True);position=(height*2.84,0,height*.65);target=(0,0,height*.5)
        elif case=='back':visibility(first,True);position=(0,height*2.84,height*.65);target=(0,0,height*.5)
        elif case=='group':group();position,target=group_camera()
        elif case=='blocking':
            visibility(first,True);first.location.x=height*(-.40+.28*u);first.rotation_euler.z=math.radians(u*35)
            if len(actors)>1:visibility(actors[1],True);actors[1].location.x=height*.40
            position=(0,-height*2.84,height*.80);target=(0,0,height*.5)
        else:raise ValueError('Unknown explicit synthetic visual_case: '+case)
        camera_pose(cam,position,target)
        cam.keyframe_insert('location',frame=frame+1);cam.keyframe_insert('rotation_quaternion',frame=frame+1)
        cam.data.keyframe_insert('lens',frame=frame+1)
        for actor in actors:
            actor.keyframe_insert('location',frame=frame+1);actor.keyframe_insert('rotation_euler',frame=frame+1)
            for ob in actor.children:
                if ob.type=='MESH':ob.keyframe_insert('hide_render',frame=frame+1)
            assert semantic_record(actor)==initial[actor.name],'Local proxy geometry changed'
        render(out/'frames'/f'{frame+1:05d}.png')
        logs.append({'frame':frame,'shot':shot['id'],'visual_case':case,'camera_position':list(cam.location),
                     'camera_quaternion':list(cam.rotation_quaternion),'camera_lens':cam.data.lens,
                     'actors':{actor.name:{'position':list(actor.location),'yaw':actor.rotation_euler.z,
                                         'visible':any(not ob.hide_render for ob in actor.children if ob.type=='MESH')} for actor in actors}})
    assert not any(ob.type in ('FONT','ARMATURE') for ob in scene.objects)
    assert all(not ob.constraints for actor in actors for ob in actor.children)
    assert all(actor.get('hand_lod')!='closeup_static' for actor in actors)
    scene.frame_set(1);bpy.ops.wm.save_as_mainfile(filepath=str(out/'previs.blend'))
    evidence={'schema_version':2,'spec_path':str(spec_path),'spec_sha256':hashlib.sha256(spec_path.read_bytes()).hexdigest(),
              'generator_sha256':hashlib.sha256(Path(__file__).with_name('canonical_actor.py').read_bytes()).hexdigest(),
              'renderer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'generator_version':GENERATOR_VERSION,'generator_record':after,'generator_records':initial,
              'actors':contract['actors'],'proxy_contract':contract,'deterministic_rebuild_equal':before==after,
              'all_frames_fixed_local_geometry':True,'clean_scene_text_objects':0,
              'frame_count':spec['frame_count'],'fps':spec['fps'],'blender':bpy.app.version_string,
              'default_cylinder_only':all(actor['proxy_mode']=='cylinder' for actor in contract['actors']),
              'limitations':['Synthetic fixture; not a verified production scene. No IK/contact/gait/gesture/acting validation.',
                             'Reference Library PNG unavailable; no reference-image match claimed.'], 'records':logs}
    write(out/'render-evidence.json',evidence)
    print(json.dumps({'status':'rendered','frames':len(logs),'actors':[actor['id'] for actor in contract['actors']],
                      'proxy_modes':[actor['proxy_mode'] for actor in contract['actors']],'out':str(out)},ensure_ascii=False))

if __name__=='__main__':main()
