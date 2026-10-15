"""Pure expected nonhuman occupied-space envelopes, shared by build and audit."""
import math


def expected_shape_protrusions(request):
    m=request['measurements_m'];length=float(m['body_length']);width=float(m['body_width'])
    body_height=float(m['shoulder_height'] if request['shape_kind']=='quadruped' else m['body_height'])
    head_top=float(m.get('head_top_height',body_height));head_width=width*.70
    head_depth=min(length*.32,width*.8);head_y=-length/2+head_depth*.25
    envelopes=[];ids=set()
    def envelope(identifier,kind,lo,hi):
        if not isinstance(identifier,str) or not identifier or identifier in ids:raise ValueError('Unique protrusion ID required')
        if kind not in ('horn','ear','tail','other'):raise ValueError('Unknown protrusion kind')
        if len(lo)!=3 or len(hi)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in [*lo,*hi]):
            raise ValueError('Protrusion envelope must have finite xyz bounds')
        if any(hi[i]<=lo[i] for i in range(3)):raise ValueError('Protrusion envelope must have positive extent on all axes')
        ids.add(identifier)
        envelopes.append({'id':identifier,'kind':kind,'bounds_min_m':list(lo),'bounds_max_m':list(hi),
                          'representation':'static occupied-space envelope'})
    explicit=request.get('protrusions',[])
    if not isinstance(explicit,list):raise ValueError('protrusions must be a list')
    for entry in explicit:
        envelope(entry.get('id'),entry.get('kind'),entry['bounds_min_m'],entry['bounds_max_m'])
    for key,kind in [('horn_height','horn'),('ear_height','ear'),('tail_length','tail')]:
        if key not in m:continue
        value=m[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError(key+' must be finite and nonnegative')
        if value==0:continue
        if any(e['kind']==kind for e in envelopes):raise ValueError('Use either exact '+kind+' envelope or '+key+', not both')
        thickness=max(.005,min(width*.10,value*.25))
        if kind=='tail':
            envelope('tail',kind,[-thickness/2,length/2,body_height*.45],
                     [thickness/2,length/2+value,body_height*.45+thickness])
        else:
            for sign,label in [(-1,'left'),(1,'right')]:
                x=sign*head_width*(.26 if kind=='horn' else .42)
                envelope(kind+'_'+label,kind,[x-thickness/2,head_y-thickness/2,head_top],
                         [x+thickness/2,head_y+thickness/2,head_top+value])
    return envelopes
