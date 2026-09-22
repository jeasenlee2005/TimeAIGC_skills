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


def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--out',required=True);a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    d=read_spec(a.spec);out=Path(a.out).resolve()
    if d.get('clips'): raise ValueError('Static asset builder does not make dynamic clips')
    if out.exists() and any(out.iterdir()): raise ValueError('Choose an unused version directory')
    out.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s=bpy.context.scene;s.world=bpy.data.worlds.new('world');s.unit_settings.system='METRIC';s.unit_settings.scale_length=1;s['timeaigc_scene_id']=d['scene_id']
    env=bpy.data.collections.new('ENVIRONMENT');s.collection.children.link(env)
    cams=bpy.data.collections.new('CAMERAS');s.collection.children.link(cams)
    for o in d['objects']: primitive(o,env)
    for v in d['views']: camera(v['id'],v,cams)
    render_setup(s,960,540,'BLENDER_WORKBENCH');s.camera=bpy.data.objects[d['views'][0]['id']]
    floorplan(d,out/'floorplan.svg');bpy.ops.wm.save_as_mainfile(filepath=str(out/'scene.blend'))
    (out/'views').mkdir(exist_ok=True)
    for v in d['views']:
        s.camera=bpy.data.objects[v['id']];s.render.filepath=str(out/'views'/f"{v['id']}.png");bpy.ops.render.render(write_still=True)
    m={'scene_id':d['scene_id'],'spec_sha256':hashlib.sha256(Path(a.spec).read_bytes()).hexdigest(),'blender':bpy.app.version_string,'empty_scene':'scene.blend','clips':[]}
    (out/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
