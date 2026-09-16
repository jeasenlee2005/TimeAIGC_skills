"""Encode clean reference videos and numbered technical contact sheets (Pillow + FFmpeg)."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw


def sheet(paths,out,columns=2,label=True):
    paths=list(paths)
    if not paths: raise ValueError('No input frames')
    tw,th=640,360;bar=28 if label else 0
    canvas=Image.new('RGB',(columns*tw,((len(paths)+columns-1)//columns)*(th+bar)),(245,245,245))
    draw=ImageDraw.Draw(canvas)
    for i,p in enumerate(paths):
        im=Image.open(p).convert('RGB');im.thumbnail((tw,th))
        x=(i%columns)*tw;y=(i//columns)*(th+bar)
        canvas.paste(im,(x+(tw-im.width)//2,y))
        if label: draw.text((x+10,y+th+5),p.stem,fill=(25,25,25))
    canvas.save(out)


def main():
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--ffmpeg',default='ffmpeg');a=p.parse_args()
    out=Path(a.output);m=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    views=sorted((out/'views').glob('*.png'))
    if views: sheet(views,out/'four-views.png',label=False);sheet(views,out/'four-views-labeled.png')
    for c in m['clips']:
        folder=out/c['id'];frames=sorted((folder/'frames').glob('*.png'))
        if not frames: continue
        expected=round(c['duration']*c['fps'])
        if len(frames)!=expected or [x.stem for x in frames]!=[f'{i:05d}' for i in range(1,expected+1)]: raise ValueError('Missing or unexpected frames')
        subprocess.run([a.ffmpeg,'-hide_banner','-loglevel','error','-y','-framerate',str(c['fps']),'-i',str(folder/'frames'/'%05d.png'),'-vf','fps=24','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(folder/'previs.mp4')],check=True)
        picks=sorted(set([0,len(frames)-1]+list(range(0,len(frames),c['fps']))))
        sheet([frames[i] for i in picks],folder/'contact-sheet.jpg',columns=3)
        shutil.copy2(frames[0],folder/'first-frame.png');shutil.copy2(frames[-1],folder/'last-frame.png')
    print(str(out.resolve()))


if __name__=='__main__':main()
