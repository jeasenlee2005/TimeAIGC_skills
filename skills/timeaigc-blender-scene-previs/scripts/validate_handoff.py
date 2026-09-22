"""Validate portable scene/previs handoffs; never infer visual approval from files."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def local(root, value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Use nonempty project-relative paths with / separators')
    p=Path(value)
    if p.is_absolute() or ':' in value:
        raise ValueError('Absolute paths are not portable: '+value)
    resolved=(root/p).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('Path escapes project: '+value)
    return resolved


def ref(root, value):
    if not isinstance(value, dict) or not value.get('sha256'):
        raise ValueError('File reference requires path and sha256')
    p=local(root,value['path'])
    if not p.is_file():
        raise ValueError('Missing file: '+str(p))
    if hashlib.sha256(p.read_bytes()).hexdigest()!=value['sha256']:
        raise ValueError('Changed file; review dependent outputs: '+str(p))
    return p


def require(ok, message):
    if not ok: raise ValueError(message)


def validate(root, manifest):
    root=Path(root).resolve();d=read(manifest)
    require(d.get('schema_version')==1,'Unsupported handoff schema')
    for key in ('version','review','files'):
        require(bool(d.get(key)), 'Missing '+key)
    for item in d['files'].values(): ref(root,item)
    if d.get('kind')=='scene':
        for k in ('scene_id','name','state','geometry_status','appearance_status'):
            require(bool(d.get(k)),'Missing '+k)
        require(d['geometry_status'] in ('not_built','draft','ready','needs_review'),'Invalid geometry status')
        require(d['appearance_status'] in ('not_generated','partial','complete','needs_review'),'Invalid appearance status')
        views=d.get('views',[])
        require(len(views)==4 and {v['id'] for v in views}=={'V01','V02','V03','V04'},'Require four unique view IDs')
        require([v['id'] for v in views if v.get('main_view')]==['V01'],'V01 must be the only main view')
        for v in views:
            for k in ('gray','appearance'):
                if v.get(k): ref(root,v[k])
        if d['geometry_status']=='ready':
            require(d['review'].get('by') in ('user','agent') and bool(d['review'].get('basis')),'Ready scene needs real review evidence')
            require(all(k in d['files'] for k in ('blend','scene_spec','floorplan')),'Ready scene missing geometry files')
            require(all(v.get('gray') and v.get('camera_id') for v in views),'Ready scene missing camera views')
            spec=read(ref(root,d['files']['scene_spec']))
            require(spec.get('scene_id')==d['scene_id'],'Scene spec ID differs from asset ID')
            require(spec.get('units')=='meters' and spec.get('up_axis')=='Z','Unadapted scene coordinates')
            require(all(v['camera_id'] in {c['id'] for c in spec.get('views',[])} for v in views),'Unknown camera binding')
        if d['appearance_status']=='complete':
            require(all(k in d['files'] for k in ('main_image','appearance_grid')) and all(v.get('appearance') for v in views),'Appearance incomplete')
            main=next(v for v in views if v['id']=='V01')
            require(main['appearance']['sha256']==d['files']['main_image']['sha256'],'V01 must preserve original main image')
    elif d.get('kind')=='previs':
        for k in ('clip_id','scene_id','scene_version','scene_manifest','storyboard','source_range','duration','shot_count','one_take','status'):
            require(k in d,'Missing '+k)
        scene_path=ref(root,d['scene_manifest']);ref(root,d['storyboard'])
        require(read(scene_path).get('kind')=='scene','Expected scene manifest, not another preview')
        scene=validate(root,scene_path)
        require(scene['kind']=='scene' and scene['scene_id']==d['scene_id'] and scene['version']==d['scene_version'],'Scene binding mismatch')
        require(scene['geometry_status']=='ready','Scene not ready for previs')
        a,b=d['source_range'];require(0<=a<b and abs((b-a)-d['duration'])<1e-6,'Source range must match preview duration')
        require(isinstance(d['one_take'],bool) and isinstance(d['shot_count'],int) and d['shot_count']>0,'Invalid shot structure')
        require(not d['one_take'] or d['shot_count']==1,'One take cannot silently become cuts')
        require(d['status'] in ('draft','ready','needs_review'),'Invalid preview status')
        if d['status']=='ready':
            require(all(k in d['files'] for k in ('blend','video','prompt','upload_list')),'Preview deliverables missing')
            require(d['review'].get('by') in ('user','agent') and bool(d['review'].get('basis')),'Ready preview needs actual review')
        index=root/'.timeaigc/project.json'
        if index.exists():
            current=read(index).get('scenes',{}).get(d['scene_id'],{}).get('active_version')
            historical=d.get('historical_scene_review',{})
            explicit_old=historical.get('by')=='user' and bool(historical.get('basis'))
            require(not current or current==d['scene_version'] or explicit_old,'Active scene version changed; re-review preview')
    else:
        raise ValueError('Unknown handoff kind')
    return d


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--manifest',required=True);a=p.parse_args()
    d=validate(a.root,a.manifest)
    print(json.dumps({'valid':True,'kind':d['kind'],'version':d['version'],'visual_quality':'not evaluated'},ensure_ascii=False))
