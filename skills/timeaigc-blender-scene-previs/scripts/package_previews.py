"""Compatibility entry: dual MP4 delivery, original fps, verified clean allowlist."""
import argparse,json,subprocess,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--blender',required=True);p.add_argument('--font',required=True)
    p.add_argument('--mode',choices=['compact','expanded'],default='compact');p.add_argument('--panel-height',default='auto')
    p.add_argument('--show-usage-hint',action='store_true')
    p.add_argument('--max-chars-per-second',type=float,default=6.0);p.add_argument('--min-page-seconds',type=float,default=2.0);a=p.parse_args()
    root=Path(a.output).resolve();m=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for c in m['clips']:
        folder=root/c['id']
        if not (folder/'frames/00001.png').is_file():raise ValueError(f'Missing actual renders: {folder}')
        command=[sys.executable,str(Path(__file__).with_name('run_pipeline.py')),
            '--rendered-dir',str(folder),'--timeline',str(folder/'timeline-v2.json'),
            '--blender',a.blender,'--font',a.font,'--mode',a.mode,'--panel-height',str(a.panel_height),
            '--max-chars-per-second',str(a.max_chars_per_second),'--min-page-seconds',str(a.min_page_seconds)]
        if a.show_usage_hint:command.append('--show-usage-hint')
        subprocess.run(command,check=True)

if __name__=='__main__':main()
