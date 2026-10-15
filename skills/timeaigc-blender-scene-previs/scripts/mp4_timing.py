"""Narrow, read-only ISO BMFF CFR timing check for our nonfragmented H.264 MP4s."""
import argparse,json,struct
from fractions import Fraction
from pathlib import Path

def boxes(data,start,end):
    pos=start
    while pos<end:
        if pos+8>end:raise ValueError('Truncated MP4 box')
        size,kind=struct.unpack_from('>I4s',data,pos);header=8
        if size==1:size=struct.unpack_from('>Q',data,pos+8)[0];header=16
        if size==0:size=end-pos
        if size<header or pos+size>end:raise ValueError('Invalid MP4 box bounds')
        yield kind.decode('ascii'),pos+header,pos+size
        pos+=size

def inspect(path):
    data=Path(path).read_bytes()
    def child(start,end,name):
        found=[(a,b) for k,a,b in boxes(data,start,end) if k==name]
        if len(found)!=1:raise ValueError(f'Expected one {name} box')
        return found[0]
    moov=child(0,len(data),'moov');video=None
    for k,a,b in boxes(data,*moov):
        if k!='trak':continue
        mdia=child(a,b,'mdia');hdlr=child(*mdia,'hdlr')
        if data[hdlr[0]+8:hdlr[0]+12]==b'vide':video=mdia;break
    if video is None:raise ValueError('No video track')
    start,end=child(*video,'mdhd');version=data[start]
    if version==0:timescale,duration=struct.unpack_from('>II',data,start+12)
    elif version==1:timescale,duration=struct.unpack_from('>IQ',data,start+20)
    else:raise ValueError('Unsupported mdhd version')
    stbl=child(*child(*video,'minf'),'stbl');stts=child(*stbl,'stts')
    count=struct.unpack_from('>I',data,stts[0]+4)[0];entries=[struct.unpack_from('>II',data,stts[0]+8+8*i) for i in range(count)]
    deltas=[delta for n,delta in entries for _ in range(n)]
    if len(deltas)>100000:raise ValueError('Too many frames for this validator')
    stsz=child(*stbl,'stsz');sample_count=struct.unpack_from('>I',data,stsz[0]+8)[0]
    assert sample_count==len(deltas)
    offsets=[0]*sample_count
    for k,a,b in boxes(data,*stbl):
        if k=='ctts':
            v=data[a];n=struct.unpack_from('>I',data,a+4)[0];offsets=[]
            for i in range(n):
                num=struct.unpack_from('>I',data,a+8+8*i)[0]
                val=struct.unpack_from('>i' if v==1 else '>I',data,a+12+8*i)[0];offsets.extend([val]*num)
            assert len(offsets)==sample_count
    dts=0;pts=[]
    for delta,offset in zip(deltas,offsets):pts.append(dts+offset);dts+=delta
    pts.sort();spacing=sorted(set(b-a for a,b in zip(pts,pts[1:])))
    assert len(spacing)==1 and spacing[0]>0
    assert duration==sum(deltas)
    normalized=[p-pts[0] for p in pts]
    return {'file':str(Path(path).resolve()),'timescale':timescale,'duration_ticks':duration,
            'frames':sample_count,'stts_entries':[list(x) for x in entries],
            'presentation_start_ticks':pts[0],'presentation_step_ticks':spacing[0],
            'fps_fraction':str(Fraction(timescale,spacing[0])),
            'duration_seconds':float(Fraction(duration,timescale)),
            'normalized_presentation_times':[str(Fraction(t,timescale)) for t in normalized]}

def compare(clean,review,fps,frames):
    a=inspect(clean);b=inspect(review)
    for r in [a,b]:
        assert r['frames']==frames and Fraction(r['fps_fraction'])==fps
        assert r['duration_seconds']==frames/fps
    assert a['normalized_presentation_times']==b['normalized_presentation_times']
    return {'passed':True,'same_normalized_pts_every_frame':True,'clean':a,'review':b}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('clean');p.add_argument('review');p.add_argument('--fps',type=int,required=True);p.add_argument('--frames',type=int,required=True);p.add_argument('--output',required=True);a=p.parse_args()
    Path(a.output).write_text(json.dumps(compare(a.clean,a.review,a.fps,a.frames),indent=2),encoding='utf-8')
