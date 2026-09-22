"""Arrange four existing images; no AI editing, source modification or cropping."""
import argparse
from pathlib import Path
from PIL import Image


def main():
    p=argparse.ArgumentParser();p.add_argument('--images',nargs=4,required=True);p.add_argument('--out',required=True);a=p.parse_args()
    out=Path(a.out)
    if out.exists(): raise ValueError('Choose a new output; do not overwrite accepted grids')
    images=[Image.open(f).convert('RGB') for f in a.images]
    w,h=1280,720;canvas=Image.new('RGB',(w*2,h*2),(30,30,30))
    for i,im in enumerate(images):
        im.thumbnail((w,h));canvas.paste(im,((i%2)*w+(w-im.width)//2,(i//2)*h+(h-im.height)//2))
    out.parent.mkdir(parents=True,exist_ok=True);canvas.save(out)
    print(str(out.resolve()))


if __name__=='__main__': main()
