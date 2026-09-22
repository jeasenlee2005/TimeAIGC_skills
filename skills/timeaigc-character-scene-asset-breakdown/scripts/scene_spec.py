"""Validate the shared metre/Z-up contract; create a vector floor plan. No Blender required."""
import argparse
import html
import json
import math
from pathlib import Path


def read_spec(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    validate(data)
    return data


def validate(d):
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    def vec(v, n, label):
        require(isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v), label)
    require(d.get('schema_version') == 1, 'schema_version must be 1')
    require(d.get('units') == 'meters' and d.get('up_axis') == 'Z', 'Use meters and Z up')
    require(isinstance(d.get('scene_id'), str) and d['scene_id'], 'scene_id required')
    vec(d['bounds'], 4, 'bounds: xmin,ymin,xmax,ymax')
    require(d['bounds'][0] < d['bounds'][2] and d['bounds'][1] < d['bounds'][3], 'Invalid bounds')
    require(d.get('objects'), 'objects required')
    ids = set()
    for o in d['objects']:
        require(isinstance(o.get('id'), str) and o['id'] not in ids, 'Object IDs must be unique')
        ids.add(o['id'])
        require(o.get('kind') in ('box', 'cylinder', 'sphere'), 'Unsupported primitive')
        require(o.get('role') in ('architecture', 'prop'), 'Empty scene cannot contain actors')
        vec(o['location'], 3, 'location needs xyz')
        vec(o['size'], 3, 'size needs xyz')
        require(all(x > 0 for x in o['size']), 'size must be positive')
        vec(o['color'], 3, 'color needs RGB')
        require(all(0 <= x <= 1 for x in o['color']), 'color range 0..1')
        require(math.isfinite(o.get('rotation_z', 0)), 'rotation_z must be finite')
    views = d.get('views', [])
    require(len(views) == 4, 'Exactly four perspective views required')
    view_ids = set()
    def camera(c):
        vec(c['position'], 3, 'camera position')
        vec(c['target'], 3, 'camera target')
        require(sum((a-b)**2 for a,b in zip(c['position'], c['target'])) > 1e-6, 'Camera cannot target itself')
        require(10 <= c.get('lens', 28) <= 200, 'lens must be 10..200 mm')
    for v in views:
        require(v['id'] not in view_ids and '/' not in v['id'] and '\\' not in v['id'] and v['id'] not in ('.', '..'), 'Invalid/duplicate view id')
        view_ids.add(v['id'])
        camera(v)
    clip_ids = set()
    for clip in d.get('clips', []):
        require(clip.get('id') and all(c.isalnum() or c in '_-' for c in clip['id']), 'Unsafe clip id')
        require(clip['id'] not in clip_ids, 'Duplicate clip ID')
        clip_ids.add(clip['id'])
        duration = clip['duration']
        require(isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0, 'Invalid duration')
        fps = clip.get('fps', 12)
        require(isinstance(fps, int) and 1 <= fps <= 60, 'fps must be integer 1..60')
        require(abs(duration*fps-round(duration*fps)) < 1e-6, 'Duration must align with frames')
        cursor = 0
        shot_ids = set()
        for s in clip['shots']:
            require(s['id'] not in shot_ids, 'Duplicate shot ID')
            shot_ids.add(s['id'])
            require(abs(s['start']-cursor) < 1e-6 and s['end'] > s['start'], 'Shot timeline gap/overlap')
            require(all(abs(t*fps-round(t*fps)) < 1e-6 for t in (s['start'],s['end'])), 'Shot cuts must align with frames')
            camera(s['camera_start'])
            camera(s.get('camera_end', s['camera_start']))
            require(bool(s.get('source_text')), 'Preserve source_text for every shot')
            cursor = s['end']
        require(abs(cursor-duration) < 1e-6, 'Shots must cover clip duration')
        actor_ids=set()
        for a in clip.get('actors', []):
            require(a['id'] not in actor_ids and a['id'] not in ids, 'Duplicate actor ID')
            actor_ids.add(a['id'])
            require(0.2 <= a['height'] <= 3, 'Invalid actor height')
            vec(a['color'],3,'actor RGB')
            keys=a['keys']
            require(len(keys)>=2 and keys[0]['time']==0 and keys[-1]['time']==duration, 'Actor keys must cover entire clip')
            last=-1
            for k in keys:
                require(last < k['time'] <= duration, 'Actor keys must increase')
                last=k['time']
                vec(k['position'],3,'actor xyz at feet')
    return True


def floorplan(d, out):
    xmin,ymin,xmax,ymax=d['bounds']
    scale=85
    margin=75
    w=(xmax-xmin)*scale+2*margin+290
    h=max((ymax-ymin)*scale+2*margin, 700)
    def xy(x,y): return margin+(x-xmin)*scale, margin+(ymax-y)*scale
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">', '<rect width="100%" height="100%" fill="#f7f5ef"/>', '<g font-family="Microsoft YaHei,Arial" font-size="13" fill="#202b38">',f'<text x="30" y="28" font-size="20">{html.escape(d["scene_id"])} · PLAN · meters / Z up</text>']
    for x in range(math.ceil(xmin), math.floor(xmax)+1):
        xx,yy=xy(x,ymax)
        svg.append(f'<path d="M{xx},{yy} v{(ymax-ymin)*scale}" stroke="#dedbd2"/><text x="{xx}" y="{yy-8}">{x}</text>')
    for y in range(math.ceil(ymin), math.floor(ymax)+1):
        xx,yy=xy(xmin,y)
        svg.append(f'<path d="M{xx},{yy} h{(xmax-xmin)*scale}" stroke="#dedbd2"/><text x="{xx-26}" y="{yy+4}">{y}</text>')
    legendx=margin+(xmax-xmin)*scale+25
    idx=0
    for o in sorted(d['objects'], key=lambda o:o['location'][2]):
        if o.get('plan_hidden'): continue
        idx+=1
        x,y=xy(*o['location'][:2]); sx,sy=[z*scale for z in o['size'][:2]]
        color='#'+''.join(f'{round(c*255):02x}' for c in o['color'])
        if o['kind'] in ('cylinder','sphere'):
            shape=f'<ellipse cx="{x}" cy="{y}" rx="{sx/2}" ry="{sy/2}"'
        else: shape=f'<rect x="{x-sx/2}" y="{y-sy/2}" width="{sx}" height="{sy}"'
        svg.append(shape+f' transform="rotate({-o.get("rotation_z",0)} {x} {y})" fill="{color}" stroke="#46505a" stroke-width="1.2"/>')
        if o['role']=='prop':
            svg.append(f'<text x="{x}" y="{y+4}" text-anchor="middle" font-weight="bold">{idx}</text>')
        svg.append(f'<text x="{legendx}" y="{58+idx*17}">{idx}. {html.escape(o.get("label",o["id"]))}</text>')
    for v in d['views']:
        x,y=xy(*v['position'][:2]); tx,ty=xy(*v['target'][:2])
        svg.append(f'<path d="M{x},{y} L{tx},{ty}" stroke="#357bba" stroke-dasharray="4 4"/><circle cx="{x}" cy="{y}" r="7" fill="#246fa9"/><text x="{x+9}" y="{y-7}">{html.escape(v["id"])}</text>')
    svg.extend([f'<text x="30" y="{h-28}">+Y ↑   +X → | {xmax-xmin:g}m × {ymax-ymin:g}m | Dimensions inferred unless source says otherwise</text>','</g></svg>'])
    Path(out).write_text('\n'.join(svg),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('spec');p.add_argument('--floorplan');a=p.parse_args()
    d=read_spec(a.spec)
    if a.floorplan: floorplan(d,a.floorplan)
    print(json.dumps({'valid':True,'scene_id':d['scene_id'],'objects':len(d['objects'])},ensure_ascii=False))
