"""Prepare/submit/query an explicitly authorized multimodal job with durable receipts."""
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from reference_guard import ReferenceError, inspect_manifest, sha256_file, validate_generation_prompt, validate_references


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


def prepare(request,cli,reference_manifest=None):
    path=Path(request).resolve();r=json.loads(path.read_text(encoding='utf-8-sig'))
    # Fail locally before invoking CLI help/probes. Review-only MP4s must never
    # become upload candidates just because somebody renames one or edits role.
    if reference_manifest is None:raise ReferenceError('--reference-manifest is required for prepare and submit')
    manifest_info=inspect_manifest(reference_manifest,require_proxy_contract=True)
    prompt_binding=validate_generation_prompt(manifest_info,r.get('prompt'))
    videos=r.get('videos',[])
    if not isinstance(videos,list):raise ValueError('videos must be a list')
    video_paths=[]
    for item in videos:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str):raise ValueError('Video path required')
        f=Path(item['path']);f=f if f.is_absolute() else path.parent/f
        video_paths.append(f)
    approved=validate_references(reference_manifest,video_paths)
    approved_by_path={row['path']:row for row in approved}
    excluded_hashes={row['sha256'] for row in manifest_info['excluded_reviews']}
    # Check every modality before CLI help: review bytes stay review-only even
    # if copied to an image/audio filename or assigned another reference role.
    for key in ('images','videos','audios'):
        items=r.get(key,[])
        if not isinstance(items,list):raise ValueError(key+' must be a list')
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('path'),str):raise ValueError('Reference path required')
            f=Path(item['path']);f=f if f.is_absolute() else path.parent/f;f=f.resolve()
            if not f.is_file() or not item.get('role'):raise ValueError('Missing file/reference role: '+str(f))
            if sha256_file(f) in excluded_hashes:raise ReferenceError('Review-only bytes cannot be used as any model reference')
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
            fingerprint={'path':str(f),'role':item['role'],'sha256':sha256_file(f)}
            if fingerprint['sha256'] in excluded_hashes:raise ReferenceError('Review-only bytes cannot be used as any model reference')
            if key=='videos':
                verified=approved_by_path.get(str(f))
                if not verified or fingerprint['sha256']!=verified['sha256']:raise ReferenceError('Video changed after clean-reference validation')
                fingerprint.update({'purpose':'model-reference','reference_manifest_sha256':manifest_info['reference_manifest_sha256']})
            cmd.extend([flag,str(f)]);fingerprints.append(fingerprint)
            if key=='videos':
                probe=run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(f)],30)
                if probe['returncode']!=0:raise ValueError('Cannot probe video')
                seconds=float(json.loads(probe['stdout'])['format']['duration'])
                if not 2<=seconds<=15:raise ValueError('Reference video must be 2..15s for this tested adapter')
                if item.get('role')=='camera_and_blocking' and abs(seconds-duration)>.1:raise ValueError('Reference preview duration differs from requested clip')
    prepared={'request':r,'command':cmd,'references':fingerprints,'help':help_text,'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'reference_manifest':manifest_info['reference_manifest'],'reference_manifest_sha256':manifest_info['reference_manifest_sha256'],
              'generation_prompt_binding':prompt_binding,'proxy_reference':manifest_info['proxy_reference']}
    verify_prepared_references(prepared)
    return prepared


def verify_prepared_references(prepared):
    """Recheck local bytes and unchanged manifest immediately before submission."""
    manifest=prepared['reference_manifest']
    current=inspect_manifest(manifest,require_proxy_contract=True)
    if current['reference_manifest_sha256']!=prepared['reference_manifest_sha256']:raise ReferenceError('Reference manifest changed after preparation')
    prompt_binding=validate_generation_prompt(current,prepared['request'].get('prompt'))
    if prompt_binding!=prepared.get('generation_prompt_binding'):raise ReferenceError('Prepared generation prompt binding changed')
    command=prepared['command']
    if command.count('--prompt')!=1 or command[command.index('--prompt')+1]!=prepared['request']['prompt']:raise ReferenceError('Prepared command prompt differs from bound prompt')
    excluded_hashes={row['sha256'] for row in current['excluded_reviews']}
    for row in prepared['references']:
        f=Path(row['path'])
        if not f.is_file():raise ReferenceError('Prepared reference is missing')
        current_hash=sha256_file(f)
        if current_hash in excluded_hashes:raise ReferenceError('Review-only bytes cannot be used as any model reference')
        if current_hash!=row['sha256']:raise ReferenceError('Prepared reference fingerprint changed')
    references=[row for row in prepared['references'] if row.get('purpose')=='model-reference']
    verified=validate_references(manifest,[row['path'] for row in references])
    if any(old['sha256']!=new['sha256'] for old,new in zip(references,verified)):raise ReferenceError('Prepared video fingerprint changed')


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','submit','query']);p.add_argument('request');p.add_argument('--receipt',required=True);p.add_argument('--cli',default='dreamina');p.add_argument('--download-dir');p.add_argument('--reference-manifest',help='Required v2 clean-reference manifest for prepare/submit');a=p.parse_args()
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
    if not a.reference_manifest:p.error('--reference-manifest is required for prepare and submit')
    prepared=prepare(a.request,cli,a.reference_manifest)
    if a.mode=='prepare':
        if receipt.exists():raise ValueError('Prepare receipt exists')
        save(receipt,{'status':'prepared',**prepared});print('PREPARED ONLY: no submission');return
    verify_prepared_references(prepared)
    save(receipt,{'status':'submitting','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),**prepared})
    result=run(prepared['command']);parsed=list(objects(result['stdout']))
    task=next((find(x,'submit_id') for x in parsed if find(x,'submit_id')),None)
    status=next((find(x,'gen_status') for x in parsed if find(x,'gen_status')),None)
    save(receipt,{'status':status or 'unknown','submit_id':task,**prepared,'submission':result})
    print(json.dumps({'status':status or 'unknown','submit_id':task,'response':result},ensure_ascii=False))


if __name__=='__main__':main()
