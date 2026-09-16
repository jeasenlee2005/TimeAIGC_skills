"""Prepare/submit/query an explicitly authorized multimodal job with durable receipts."""
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def objects(text):
    decoder=json.JSONDecoder()
    for match in re.finditer(r'\{',text):
        try:
            value,_=decoder.raw_decode(text[match.start():])
            if isinstance(value,dict):yield value
        except json.JSONDecodeError:pass


def find(data,key):
    if isinstance(data,dict):
        if key in data:return data[key]
        for value in data.values():
            result=find(value,key)
            if result is not None:return result
    elif isinstance(data,list):
        for value in data:
            result=find(value,key)
            if result is not None:return result


def run(command,timeout=180):
    try:
        p=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    except subprocess.TimeoutExpired as e:
        def dec(x):return x.decode('utf-8',errors='replace') if isinstance(x,bytes) else (x or '')
        return {'returncode':None,'stdout':dec(e.stdout),'stderr':dec(e.stderr),'error':'timeout; remote acceptance unknown; inspect list_task before any new submit'}


def prepare(request,cli):
    path=Path(request).resolve();r=json.loads(path.read_text(encoding='utf-8-sig'))
    help_result=run([cli,'multimodal2video','--help'],60)
    if help_result['returncode']!=0:raise ValueError('Cannot inspect CLI help: '+help_result['stderr'])
    help_text=help_result['stdout']
    # This adapter implements the observed CLI contract. Detect drift instead of guessing new flags.
    for flag in ('--image','--video','--video_resolution','--model_version','--duration'):
        if flag not in help_text:raise ValueError('CLI contract changed: '+flag)
    duration=r['duration'];model=r['model_version'];resolution=r['video_resolution']
    if isinstance(duration,bool) or not isinstance(duration,int) or not 4<=duration<=15:raise ValueError('This adapter supports integer 4..15s; split longer storyboards explicitly')
    if model not in re.findall(r'seedance[\w.]*',help_text):raise ValueError('Model absent from current CLI help')
    if resolution not in ('720p','1080p','4k') or (model!='seedance2.0_vip' and resolution!='720p'):raise ValueError('Resolution unsupported by this model/adapter')
    if r.get('ratio','16:9') not in ('1:1','3:4','16:9','4:3','9:16','21:9'):raise ValueError('Unsupported ratio')
    if not r.get('prompt','').strip():raise ValueError('Prompt required')
    if not r.get('review',{}).get('accepted') or not r['review'].get('reviewer') or not r['review'].get('basis'):raise ValueError('Record actual preview review; do not invent user approval')
    cmd=[cli,'multimodal2video','--model_version',model,'--duration',str(duration),'--video_resolution',resolution,'--ratio',r.get('ratio','16:9'),'--prompt',r['prompt']]
    fingerprints=[]
    if not r.get('images') and not r.get('videos'):raise ValueError('At least one image/video required')
    for key,flag,limit in [('images','--image',9),('videos','--video',3),('audios','--audio',3)]:
        items=r.get(key,[])
        if len(items)>limit:raise ValueError('Too many '+key)
        for item in items:
            f=Path(item['path']);f=f if f.is_absolute() else path.parent/f;f=f.resolve()
            if not f.is_file() or not item.get('role'):raise ValueError('Missing file/reference role: '+str(f))
            cmd.extend([flag,str(f)]);fingerprints.append({'path':str(f),'role':item['role'],'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
            if key=='videos':
                probe=run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(f)],30)
                if probe['returncode']!=0:raise ValueError('Cannot probe video')
                seconds=float(json.loads(probe['stdout'])['format']['duration'])
                if not 2<=seconds<=15:raise ValueError('Reference video must be 2..15s for this tested adapter')
                if item.get('role')=='camera_and_blocking' and abs(seconds-duration)>.1:raise ValueError('Reference preview duration differs from requested clip')
    return {'request':r,'command':cmd,'references':fingerprints,'help':help_text,'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','submit','query']);p.add_argument('request');p.add_argument('--receipt',required=True);p.add_argument('--cli',default='dreamina');p.add_argument('--download-dir');a=p.parse_args()
    receipt=Path(a.receipt).resolve();cli=shutil.which(a.cli) or a.cli
    if a.mode=='query':
        old=json.loads(receipt.read_text(encoding='utf-8'));task=old.get('submit_id')
        if not task:raise ValueError('No known submit_id. Inspect task history; do not resubmit blindly.')
        command=[cli,'query_result','--submit_id',str(task)]
        if a.download_dir:command.extend(['--download_dir',str(Path(a.download_dir).resolve())])
        result=run(command);parsed=list(objects(result['stdout']));status=next((find(x,'gen_status') for x in parsed if find(x,'gen_status')),None)
        old.setdefault('queries',[]).append({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'response':result})
        old['status']=status or 'unknown';save(receipt,old)
        print(json.dumps({'submit_id':task,'status':old['status'],'response':result},ensure_ascii=False));return
    if a.mode=='submit' and receipt.exists():raise ValueError('Receipt exists; query it or create a deliberately reviewed new request/receipt')
    prepared=prepare(a.request,cli)
    if a.mode=='prepare':
        if receipt.exists():raise ValueError('Prepare receipt exists')
        save(receipt,{'status':'prepared',**prepared});print('PREPARED ONLY: no submission');return
    save(receipt,{'status':'submitting','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),**prepared})
    result=run(prepared['command']);parsed=list(objects(result['stdout']))
    task=next((find(x,'submit_id') for x in parsed if find(x,'submit_id')),None)
    status=next((find(x,'gen_status') for x in parsed if find(x,'gen_status')),None)
    save(receipt,{'status':status or 'unknown','submit_id':task,**prepared,'submission':result})
    print(json.dumps({'status':status or 'unknown','submit_id':task,'response':result},ensure_ascii=False))


if __name__=='__main__':main()
