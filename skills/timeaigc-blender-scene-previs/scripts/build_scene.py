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
from scene_spec import read_spec, floorplan


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


def pose(obj,c):
    obj.location=c['position'];obj.rotation_mode='QUATERNION'
    obj.rotation_quaternion=(Vector(c['target'])-obj.location).to_track_quat('-Z','Y')
    obj.data.lens=c.get('lens',28);obj.data.sensor_width=36;obj.data.clip_start=.05;obj.data.clip_end=200


def render_setup(scene,w,h,engine):
    scene.render.engine=engine;scene.render.resolution_x=w;scene.render.resolution_y=h;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
    scene.view_settings.view_transform='Standard'
    shading=scene.display.shading;shading.light='STUDIO';shading.studiolight_rotate_z=.35
    shading.color_type='MATERIAL';shading.show_shadows=True;shading.show_cavity=True;shading.cavity_type='BOTH'
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
    root=bpy.data.objects.new(a['id'],None);collection.objects.link(root)
    h=a['height'];rgb=a['color']
    # Spatial occupancy only: no articulated limbs, gait, gestures or facial cues.
    parts=[('occupancy','sphere',[0,0,h*.43],[h*.26,h*.19,h*.82]),('head','sphere',[0,0,h*.91],[h*.17]*3)]
    for name,kind,loc,size in parts:
        ob=primitive({'id':a['id']+'_'+name,'kind':kind,'location':loc,'size':size,'color':rgb},collection);ob.parent=root
    return root


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
            pos=path_position(a['keys'],i/12);pos[2]+=a['height']*.5
            for o in d['objects']:
                if o.get('collidable',True) and o['kind']=='box' and inside(pos,o,.16):
                    warnings.append(f"actor {a['id']} intersects {o['id']}")
    return sorted(set(warnings))


def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--out',required=True);p.add_argument('--scene-blend');p.add_argument('--clip');p.add_argument('--render',choices=['views','clips','all','none'],default='all');p.add_argument('--engine',choices=['BLENDER_WORKBENCH'],default='BLENDER_WORKBENCH');p.add_argument('--allow-warnings',action='store_true');a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    d=read_spec(a.spec);out=Path(a.out).resolve()
    if out.exists() and any(out.iterdir()): raise ValueError('Output is not empty: choose a new version directory')
    out.mkdir(parents=True,exist_ok=True)
    selected=[c for c in d.get('clips',[]) if not a.clip or c['id']==a.clip]
    if a.clip and not selected: raise ValueError('Unknown clip')
    warnings={c['id']:geometry_checks(d,c) for c in selected}
    if any(warnings.values()) and not a.allow_warnings: raise ValueError(json.dumps(warnings,ensure_ascii=False))
    if a.scene_blend:
        source=Path(a.scene_blend).resolve()
        if not source.is_file(): raise ValueError('Source scene missing')
        bpy.ops.wm.open_mainfile(filepath=str(source))
        scene=bpy.context.scene
        if scene.get('timeaigc_scene_id') and scene['timeaigc_scene_id']!=d['scene_id']: raise ValueError('Source scene ID differs from preview spec')
        if abs(scene.unit_settings.scale_length-1)>1e-6: raise ValueError('Adapt source units explicitly before preview')
        if not scene.world: scene.world=bpy.data.worlds.new('world')
        cams=bpy.data.collections.new('PREVIS_CAMERAS');scene.collection.children.link(cams)
        if a.render=='views': raise ValueError('Static views belong to the asset skill')
        if any(x['id'] in bpy.data.objects for clip in selected for x in clip.get('actors',[])):
            raise ValueError('Actor ID collides with source scene object; choose unique IDs')
    else:
        print('LEGACY rebuild mode: current workflow should use --scene-blend with an approved asset scene')
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
        actorcol=bpy.data.collections.new('ACTORS_'+clip['id']);scene.collection.children.link(actorcol)
        actors={x['id']:actor(x,actorcol) for x in clip.get('actors',[])}
        clipout=out/clip['id'];clipout.mkdir(exist_ok=True);frames=clipout/'frames';frames.mkdir(exist_ok=True)
        fps=clip.get('fps',12);n=round(clip['duration']*fps);scene.render.fps=fps;scene.frame_start=1;scene.frame_end=n
        render_setup(scene,640,360,a.engine)
        clipcams={}
        for s in clip['shots']:
            cam=camera(clip['id']+'_'+s['id'],s['camera_start'],cams);clipcams[s['id']]=cam
            marker=scene.timeline_markers.new(s['id'],frame=round(s['start']*fps)+1);marker.camera=cam
        # Bake exact per-frame look-at; both saved .blend and rendered sequence are seek-safe.
        sample_records=[]
        for frame in range(1,n+1):
            t=(frame-1)/fps;scene.frame_set(frame)
            s=next(s for s in clip['shots'] if s['start']<=t<s['end'])
            u=min(1,(t-s['start'])/max(1/fps,s['end']-s['start']-1/fps))
            c=mix(s['camera_start'],s.get('camera_end',s['camera_start']),u);cam=clipcams[s['id']];pose(cam,c);scene.camera=cam
            cam.keyframe_insert('location',frame=frame);cam.keyframe_insert('rotation_quaternion',frame=frame);cam.data.keyframe_insert('lens',frame=frame)
            for item in clip.get('actors',[]):
                ob=actors[item['id']];ob.location=path_position(item['keys'],t);ob.keyframe_insert('location',frame=frame)
            if frame==1 or frame==n or frame%fps==0: sample_records.append({'frame':frame,'time':t,'shot':s['id'],'camera':c})
            if a.render in ('clips','all'):
                scene.render.filepath=str(frames/f'{frame:05d}.png');bpy.ops.render.render(write_still=True)
        scene.frame_set(1);scene.camera=clipcams[clip['shots'][0]['id']]
        bpy.ops.wm.save_as_mainfile(filepath=str(clipout/'previs.blend'))
        (clipout/'samples.json').write_text(json.dumps(sample_records,ensure_ascii=False,indent=2),encoding='utf-8')
        for ob in list(actorcol.objects): bpy.data.objects.remove(ob,do_unlink=True)
        bpy.data.collections.remove(actorcol)
        for ob in clipcams.values(): bpy.data.objects.remove(ob,do_unlink=True)
        scene.timeline_markers.clear()
    manifest={'scene_id':d['scene_id'],'spec_sha256':hashlib.sha256(Path(a.spec).read_bytes()).hexdigest(),'blender':bpy.app.version_string,'clips':[{'id':c['id'],'fps':c.get('fps',12),'duration':c['duration']} for c in selected],'warnings':warnings,'empty_scene':'scene.blend','coordinate_system':'meters, Z-up, +Y north','limitations':['proxy actors do not validate hand contact, facial acting, dialogue or photorealism','sampled collisions are conservative; inspect rendered occlusion and motion']}
    if a.scene_blend:
        manifest['source_blend']=str(source)
        manifest['source_blend_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
