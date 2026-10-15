"""Run using blender --background --python build_scene.py -- --spec ... --out ..."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
import bpy
from mathutils import Vector

sys.path.insert(0,str(Path(__file__).resolve().parent))
from scene_spec import read_spec, floorplan, validate as validate_scene_spec
from project_preflight import resolve_clip_actors
from shape_proxy import build_registry_actor, registry_semantic_record
from scale_adapter import validate_scene_scale, world_bounds_m
from registry_binding import prepare_registry_binding
from scene_binding import bind_scene
from canonical_actor import build_actor, semantic_record, GENERATOR_VERSION
from proxy_contract import resolve_actor_requests, build_proxy_contract, assert_no_known_proxy_objects


def material(name, rgb):
    m=bpy.data.materials.new(name);m.diffuse_color=(*rgb,1)
    return m


def primitive(o, collection):
    loc=o['location'];kind=o['kind']
    if kind=='box': bpy.ops.mesh.primitive_cube_add(size=1,location=loc)
    elif kind=='cylinder': bpy.ops.mesh.primitive_cylinder_add(vertices=32,radius=.5,depth=1,location=loc)
    else: bpy.ops.mesh.primitive_uv_sphere_add(segments=20,ring_count=12,radius=.5,location=loc)
    obj=bpy.context.object;obj.name=o['id'];obj.dimensions=o['size']
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    obj.rotation_euler.z=math.radians(o.get('rotation_z',0))
    obj.data.materials.append(material(o['id']+'_mat',o['color']))
    for c in list(obj.users_collection): c.objects.unlink(obj)
    collection.objects.link(obj)
    if kind=='box' and o.get('bevel',.025)>0:
        mod=obj.modifiers.new('soft_edges','BEVEL');mod.width=o.get('bevel',.025);mod.segments=2
        obj.modifiers.new('weighted_normals','WEIGHTED_NORMAL')
    return obj


def camera(name,c,collection):
    data=bpy.data.cameras.new(name);obj=bpy.data.objects.new(name,data);collection.objects.link(obj)
    pose(obj,c);return obj


def pose(obj,c,meters_per_blender_unit=1.0):
    obj.location=[x/meters_per_blender_unit for x in c['position']];obj.rotation_mode='QUATERNION'
    obj.rotation_quaternion=(Vector([x/meters_per_blender_unit for x in c['target']])-obj.location).to_track_quat('-Z','Y')
    obj.data.lens=c.get('lens',28);obj.data.sensor_width=36;obj.data.clip_start=.05/meters_per_blender_unit;obj.data.clip_end=200/meters_per_blender_unit


def render_setup(scene,w,h,engine):
    scene.render.engine=engine;scene.render.resolution_x=w;scene.render.resolution_y=h;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
    scene.view_settings.view_transform='Standard'
    shading=scene.display.shading;shading.light='STUDIO';shading.studiolight_rotate_z=.35
    shading.color_type='MATERIAL';shading.show_shadows=True;shading.show_cavity=True;shading.cavity_type='SCREEN'
    shading.background_type='WORLD';scene.world.color=(.18,.18,.18)


def mix(a,b,t):
    return {k:[x+(y-x)*t for x,y in zip(a[k],b[k])] for k in ('position','target')} | {'lens':a.get('lens',28)+(b.get('lens',28)-a.get('lens',28))*t}


def path_position(keys,t):
    for a,b in zip(keys,keys[1:]):
        if a['time']<=t<=b['time']:
            u=(t-a['time'])/(b['time']-a['time'])
            return [x+(y-x)*u for x,y in zip(a['position'],b['position'])]
    return keys[-1]['position']


def actor(a,collection):
    return build_actor(a['id'],collection,a['height_m'],a['color'],a['hand_lod'],
                       proxy_mode=a['proxy_mode'],allow_special_proxy=a['allow_special_proxy'])


def path_yaw(keys,t):
    for a,b in zip(keys,keys[1:]):
        if a['time']<=t<=b['time']:
            u=(t-a['time'])/(b['time']-a['time'])
            return math.radians(a.get('yaw_degrees',0)+(b.get('yaw_degrees',0)-a.get('yaw_degrees',0))*u)
    return math.radians(keys[-1].get('yaw_degrees',0))


def geometry_checks(d,clip):
    warnings=[]
    # Conservative oriented boxes only. Props are checked for actor footprint, not camera aim.
    def inside(point,o,pad=0):
        angle=-math.radians(o.get('rotation_z',0));dx=point[0]-o['location'][0];dy=point[1]-o['location'][1]
        x=dx*math.cos(angle)-dy*math.sin(angle);y=dx*math.sin(angle)+dy*math.cos(angle)
        return abs(x)<o['size'][0]/2+pad and abs(y)<o['size'][1]/2+pad and abs(point[2]-o['location'][2])<o['size'][2]/2
    for s in clip['shots']:
        for i in range(21):
            c=mix(s['camera_start'],s.get('camera_end',s['camera_start']),i/20)
            for o in d['objects']:
                if o.get('collidable',True) and o['kind']=='box' and inside(c['position'],o,.03):
                    warnings.append(f"camera {s['id']} intersects {o['id']}")
    for a in clip.get('actors',[]):
        for i in range(round(clip['duration']*12)+1):
            pos=path_position(a['keys'],i/12);pos[2]+=a.get('height',a.get('height_m',a.get('measurements_m',{}).get('head_top_height',1)))*.5
            for o in d['objects']:
                if o.get('collidable',True) and o['kind']=='box' and inside(pos,o,.16):
                    warnings.append(f"actor {a['id']} intersects {o['id']}")
    return sorted(set(warnings))


def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root');p.add_argument('--fixture-unregistered',action='store_true',help='Explicit synthetic regression only; no production character confirmation');p.add_argument('--spec',required=True);p.add_argument('--out',required=True);p.add_argument('--scene-blend');p.add_argument('--clip');p.add_argument('--render',choices=['views','clips','all','none'],default='all');p.add_argument('--engine',choices=['BLENDER_WORKBENCH'],default='BLENDER_WORKBENCH');p.add_argument('--allow-warnings',action='store_true');a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    d=json.loads(Path(a.spec).read_text(encoding='utf-8-sig'))
    project_bindings={}
    if a.project_root:
        if not a.scene_blend:raise ValueError('Project preview requires its registered confirmed --scene-blend')
        source_scene_binding=bind_scene(a.project_root,d['scene_id'],a.scene_blend)
        for clip in d.get('clips',[]):
            if a.clip and clip['id']!=a.clip: continue
            resolved,binding=resolve_clip_actors(a.project_root,clip.get('actors',[]),scene_id=d['scene_id'])
            binding['source_scene']=source_scene_binding
            clip['actors']=resolved;project_bindings[clip['id']]=binding
        if a.clip:d['clips']=[c for c in d.get('clips',[]) if c['id']==a.clip]
    elif not a.fixture_unregistered:
        raise ValueError('v2.2 production requires --project-root and project-wide character preflight')
    validate_scene_spec(d);out=Path(a.out).resolve()
    if out.exists() and any(out.iterdir()): raise ValueError('Output is not empty: choose a new version directory')
    out.mkdir(parents=True,exist_ok=True)
    selected=[c for c in d.get('clips',[]) if not a.clip or c['id']==a.clip]
    if a.clip and not selected: raise ValueError('Unknown clip')
    warnings={c['id']:geometry_checks(d,c) for c in selected}
    if any(warnings.values()) and not a.allow_warnings: raise ValueError(json.dumps(warnings,ensure_ascii=False))
    if a.scene_blend:
        source=Path(a.scene_blend).resolve()
        if not source.is_file(): raise ValueError('Source scene missing')
        # Source scenes and registered actor libraries are data, never executable
        # project code. Disable auto-run before loading either kind of .blend.
        bpy.context.preferences.filepaths.use_scripts_auto_execute = False
        bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
        scene=bpy.context.scene
        assert_no_known_proxy_objects(scene.objects)
        if scene.get('timeaigc_scene_id') and scene['timeaigc_scene_id']!=d['scene_id']: raise ValueError('Source scene ID differs from preview spec')
        if a.project_root:
            unit_record=next(iter(project_bindings.values()))['scene_units']
            unit_result=validate_scene_scale(scene,unit_record)
            meters_per_blender_unit=unit_result['meters_per_blender_unit'] if isinstance(unit_result,dict) else unit_result
        else:
            if abs(scene.unit_settings.scale_length-1)>1e-6: raise ValueError('Fixture scene must use meters')
            meters_per_blender_unit=1.0;unit_result={'fixture_only':True,'meters_per_blender_unit':1.0}
        if not scene.world: scene.world=bpy.data.worlds.new('world')
        cams=bpy.data.collections.new('PREVIS_CAMERAS');scene.collection.children.link(cams)
        if a.render=='views': raise ValueError('Static views belong to the asset skill')
        if any(x['id'] in bpy.data.objects for clip in selected for x in clip.get('actors',[])):
            raise ValueError('Actor ID collides with source scene object; choose unique IDs')
    else:
        if a.project_root:raise ValueError('v2.2 project preview requires approved --scene-blend; source unit geometry must be measured')
        meters_per_blender_unit=1.0;unit_result={'fixture_only':True,'meters_per_blender_unit':1.0}
        print('FIXTURE rebuild mode: production workflow requires --project-root and --scene-blend')
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene=bpy.context.scene;scene.world=bpy.data.worlds.new('world');scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
        env=bpy.data.collections.new('ENVIRONMENT');scene.collection.children.link(env)
        cams=bpy.data.collections.new('CAMERAS');scene.collection.children.link(cams)
        for o in d['objects']: primitive(o,env)
        for v in d['views']: camera(v['id'],v,cams)
    render_setup(scene,960,540,a.engine)
    if not a.scene_blend: scene.camera=bpy.data.objects[d['views'][0]['id']]
    floorplan(d,out/'floorplan.svg')
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'scene.blend'))
    if not a.scene_blend and a.render in ('views','all'):
        (out/'views').mkdir(exist_ok=True)
        for v in d['views']:
            scene.camera=bpy.data.objects[v['id']];scene.render.filepath=str(out/'views'/f"{v['id']}.png");bpy.ops.render.render(write_still=True)
    for clip in selected:
        scope={**d,**clip}
        requests=resolve_actor_requests(scope,for_clean=True)
        actorcol=bpy.data.collections.new('ACTORS_'+clip['id']);scene.collection.children.link(actorcol)
        actors={x['id']:(build_registry_actor(dict(x, _project_root=str(Path(a.project_root).resolve())),actorcol,meters_per_blender_unit) if a.project_root else actor(x,actorcol)) for x in requests}
        clipout=out/clip['id'];clipout.mkdir(exist_ok=True);frames=clipout/'frames';frames.mkdir(exist_ok=True)
        fps=clip.get('fps',12);n=round(clip['duration']*fps);scene.render.fps=fps;scene.frame_start=1;scene.frame_end=n
        render_setup(scene,clip.get('width',960),clip.get('height',540),a.engine)
        actor_records={k:registry_semantic_record(v) for k,v in actors.items()}
        clipcams={}
        for s in clip['shots']:
            cam=camera(clip['id']+'_'+s['id'],s['camera_start'],cams);clipcams[s['id']]=cam
            marker=scene.timeline_markers.new(s['id'],frame=round(s['start']*fps)+1);marker.camera=cam
        # Bake exact per-frame look-at; both saved .blend and rendered sequence are seek-safe.
        sample_records=[]
        for frame in range(1,n+1):
            t=(frame-1)/fps;scene.frame_set(frame)
            s=next(s for s in clip['shots'] if round(s['start']*fps)<=frame-1<round(s['end']*fps))
            u=(frame-1-round(s['start']*fps))/max(1,round((s['end']-s['start'])*fps)-1)
            c=mix(s['camera_start'],s.get('camera_end',s['camera_start']),u);cam=clipcams[s['id']];pose(cam,c,meters_per_blender_unit);scene.camera=cam
            cam.keyframe_insert('location',frame=frame);cam.keyframe_insert('rotation_quaternion',frame=frame);cam.data.keyframe_insert('lens',frame=frame)
            for item in clip.get('actors',[]):
                ob=actors[item['id']];ob.location=[x/meters_per_blender_unit for x in path_position(item['keys'],t)];ob.keyframe_insert('location',frame=frame)
                ob.rotation_euler.z=path_yaw(item['keys'],t);ob.keyframe_insert('rotation_euler',frame=frame)
                if registry_semantic_record(ob)!=actor_records[item['id']]:raise ValueError('Actor local geometry changed')
            if frame==1 or frame==n or frame%fps==0: sample_records.append({'frame':frame,'time':t,'shot':s['id'],'camera':c})
            if a.render in ('clips','all'):
                scene.render.filepath=str(frames/f'{frame:05d}.png');bpy.ops.render.render(write_still=True)
        scene.frame_set(1);scene.camera=clipcams[clip['shots'][0]['id']]
        bpy.ops.wm.save_as_mainfile(filepath=str(clipout/'previs.blend'))
        (clipout/'samples.json').write_text(json.dumps(sample_records,ensure_ascii=False,indent=2),encoding='utf-8')
        (clipout/'actor-geometry.json').write_text(json.dumps(actor_records,ensure_ascii=False,indent=2),encoding='utf-8')
        dual={'schema_version':2,'width':clip.get('width',960),'height':clip.get('height',540),'fps':fps,'frame_count':n,
              'actors':requests,'allow_special_proxy':scope.get('allow_special_proxy',False),
              'special_proxy_reason':scope.get('special_proxy_reason',''),
              'storyboard_text':clip['storyboard_text'],
              'shots':[{'id':s['id'],'start_frame':round(s['start']*fps),'end_frame':round(s['end']*fps),
                        'summary':s.get('summary',''),'dialogue':s.get('dialogue',''),
                        'source_text':s['source_text'],
                        'source_text_sha256':hashlib.sha256(s['source_text'].encode('utf-8')).hexdigest(),
                        'dialogue_sha256':hashlib.sha256(s.get('dialogue','').encode('utf-8')).hexdigest(),
                        **({'storyboard_fields':s['storyboard_fields']} if 'storyboard_fields' in s else {})} for s in clip['shots']]}
        if a.project_root:
            rb=prepare_registry_binding(project_bindings[clip['id']],Path(a.project_root),clipout)
            (clipout/'registry-binding.json').write_text(json.dumps(rb,ensure_ascii=False,indent=2),encoding='utf-8')
            dual['registry_binding_sha256']=hashlib.sha256((clipout/'registry-binding.json').read_bytes()).hexdigest()
            dual['project_id']=rb['project_id']
        dual['storyboard_sha256']=hashlib.sha256(dual['storyboard_text'].encode('utf-8')).hexdigest()
        (clipout/'timeline-v2.json').write_text(json.dumps(dual,ensure_ascii=False,indent=2),encoding='utf-8')
        contract=build_proxy_contract(dual,actor_records,for_clean=True)
        (clipout/'proxy-contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
        evidence={'schema':'timeaigc-render-evidence/v2','generator_version':GENERATOR_VERSION,
                  'spec_sha256':hashlib.sha256(Path(a.spec).read_bytes()).hexdigest(),
                  'timeline_sha256':hashlib.sha256((clipout/'timeline-v2.json').read_bytes()).hexdigest(),
                  'proxy_contract_sha256':contract['contract_sha256'],'actors':actor_records,
                  'frame_count':n,'fps':fps,'rendered_frame_count':n if a.render in ('clips','all') else 0,
                  'actual_proxy_modes':{key:record['proxy_mode'] for key,record in actor_records.items()},
                  'all_frames_fixed_local_geometry':True,'paid_generation_performed':False,
                  'limitations':contract['limitations'],'scene_units':unit_result,
                  'world_bounds_m':{key:world_bounds_m(ob,meters_per_blender_unit) for key,ob in actors.items()}}
        (clipout/'render-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        for ob in list(actorcol.objects): bpy.data.objects.remove(ob,do_unlink=True)
        bpy.data.collections.remove(actorcol)
        for ob in clipcams.values(): bpy.data.objects.remove(ob,do_unlink=True)
        scene.timeline_markers.clear()
    manifest={'scene_id':d['scene_id'],'spec_sha256':hashlib.sha256(Path(a.spec).read_bytes()).hexdigest(),'blender':bpy.app.version_string,'clips':[{'id':c['id'],'fps':c.get('fps',12),'duration':c['duration']} for c in selected],'warnings':warnings,'empty_scene':'scene.blend','coordinate_system':'meters, Z-up, +Y north','limitations':['proxy actors do not validate hand contact, facial acting, dialogue or photorealism','sampled collisions are conservative; inspect rendered occlusion and motion']}
    manifest['actor_generator']=GENERATOR_VERSION
    if a.scene_blend:
        manifest['source_blend']=str(source)
        manifest['source_blend_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
