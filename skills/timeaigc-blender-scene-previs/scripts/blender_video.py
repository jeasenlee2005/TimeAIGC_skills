"""Use installed Blender's FFmpeg codec, without external executable or network."""
import argparse, json, sys
from pathlib import Path
import bpy

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['encode','decode']);p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--fps',type=int,default=12);p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True);p.add_argument('--frames',type=int,required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);src=Path(a.input).resolve();dst=Path(a.output).resolve()
    if dst.exists():raise ValueError('Output exists: use a fresh path')
    bpy.ops.wm.read_factory_settings(use_empty=True);s=bpy.context.scene
    s.render.resolution_x=a.width;s.render.resolution_y=a.height;s.render.resolution_percentage=100
    s.render.fps=a.fps;s.frame_start=1;s.frame_end=a.frames
    s.view_settings.view_transform='Standard';s.view_settings.look='None';s.view_settings.exposure=0;s.view_settings.gamma=1
    ed=s.sequence_editor_create();s.render.use_sequencer=True
    if a.mode=='encode':
        frames=sorted(src.glob('*.png'))
        if len(frames)!=a.frames or [x.name for x in frames]!=[f'{i:05d}.png' for i in range(1,a.frames+1)]:raise ValueError('Frame sequence mismatch')
        strip=ed.strips.new_image('source',str(frames[0]),channel=1,frame_start=1)
        for f in frames[1:]:strip.elements.append(f.name)
        strip.frame_final_duration=a.frames
        if hasattr(s.render.image_settings,'media_type'):s.render.image_settings.media_type='VIDEO'
        s.render.image_settings.file_format='FFMPEG';s.render.ffmpeg.format='MPEG4';s.render.ffmpeg.codec='H264'
        s.render.ffmpeg.constant_rate_factor='HIGH';s.render.ffmpeg.ffmpeg_preset='GOOD';s.render.ffmpeg.audio_codec='NONE'
        s.render.filepath=str(dst);dst.parent.mkdir(parents=True,exist_ok=True)
    else:
        strip=ed.strips.new_movie('encoded',str(src),channel=1,frame_start=1)
        if strip.frame_duration!=a.frames:raise ValueError(f'Decoded frame count {strip.frame_duration} != {a.frames}')
        if strip.elements[0].orig_width!=a.width or strip.elements[0].orig_height!=a.height:raise ValueError('Encoded dimensions mismatch')
        dst.mkdir(parents=True);s.render.image_settings.file_format='PNG';s.render.image_settings.color_mode='RGB';s.render.filepath=str(dst/'frame-')
        (dst/'probe.json').write_text(json.dumps({'frames':strip.frame_duration,'fps':strip.fps,'width':strip.elements[0].orig_width,'height':strip.elements[0].orig_height},indent=2))
    bpy.ops.render.render(animation=True)

if __name__=='__main__':main()
