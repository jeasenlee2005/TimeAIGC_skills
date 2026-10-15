"""Package an existing clean render into same-timeline MP4s; fully decode and verify.

Requires installed Blender, Python+Pillow+numpy, and an explicitly selected font.
No downloads, upload, installation, resampling, scene rerender or paid API calls.
"""
import argparse, hashlib, json, math, os, shutil, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from dual_video import validate_spec
from reference_guard import build_upload_list, validate_references, ReferenceError
from mp4_timing import compare as compare_timing
from proxy_contract import build_proxy_contract, validate_proxy_contract
from generation_prompt import generate_prompt, validate_prompt_coverage
from registry_binding import validate_registry_binding, validate_bound_actor_subset, copy_registry_snapshots

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def json_payload_bytes(data):
    """Bytes of the pipeline's existing UTF-8, indent=2, native-newline writer."""
    return json.dumps(data,ensure_ascii=False,indent=2).replace('\n',os.linesep).encode('utf-8')


def write(path,data):Path(path).write_bytes(json_payload_bytes(data))


def validate_expanded_reuse(old_manifest, *, current_timeline_sha256,
                            current_clean_sha256, current_proxy_contract):
    """Reject cross-scene/mode reuse before copying any expanded review bytes.

    The caller first verifies the source manifest/files with reference_guard.
    Here the hashes must also match this run's clean video and the exact bytes
    that ``write`` will put in proxy-contract.json, not a semantic-only hash.
    """
    if not isinstance(old_manifest,dict):raise ValueError('Expanded source manifest must be an object')
    if old_manifest.get('source_timeline_sha256')!=current_timeline_sha256:
        raise ValueError('Expanded timeline differs')
    old_files=old_manifest.get('files')
    old_clean=old_files.get('clean_video') if isinstance(old_files,dict) else None
    if not isinstance(old_clean,dict) or old_clean.get('sha256')!=current_clean_sha256:
        raise ValueError('Expanded source clean video SHA256 differs from current clean video')
    binding=old_manifest.get('proxy_reference')
    if not isinstance(binding,dict) or binding.get('clean_video_sha256')!=current_clean_sha256:
        raise ValueError('Expanded source proxy reference is not bound to the current clean video')
    old_contract=binding.get('mode_contract')
    expected_contract_sha256=hashlib.sha256(json_payload_bytes(current_proxy_contract)).hexdigest()
    if not isinstance(old_contract,dict) or old_contract.get('sha256')!=expected_contract_sha256:
        raise ValueError('Expanded source mode contract file SHA256 differs from current serialized contract')
    return {'passed':True,'source_timeline_sha256':current_timeline_sha256,
            'clean_video_sha256':current_clean_sha256,'mode_contract_file_sha256':expected_contract_sha256,
            'scope':'Exact timeline, clean MP4 bytes, and serialized proxy contract match before expanded review copy.'}

def compare_sequences(root,spec,height,source_root=None):
    source_root=Path(source_root) if source_root is not None else root
    records=[]
    for i in range(spec['frame_count']):
        a=np.asarray(Image.open(source_root/'frames'/f'{i+1:05d}.png').convert('RGB'),dtype=np.float32)
        b=np.asarray(Image.open(source_root/'decoded-clean'/f'frame-{i+1:04d}.png').convert('RGB'),dtype=np.float32)
        c=np.asarray(Image.open(root/'decoded-review'/f'frame-{i+1:04d}.png').convert('RGB'),dtype=np.float32)[:spec['height']]
        e=np.abs(a-b);f=np.abs(b-c)
        mse=float(np.mean((a-b)**2));psnr=99 if mse==0 else 20*math.log10(255/math.sqrt(mse))
        records.append({'frame':i,'source_vs_clean_mae':float(e.mean()),'source_vs_clean_psnr':psnr,
                        'clean_vs_review_upper_mae':float(f.mean()),'clean_vs_review_upper_max':float(f.max())})
    result={'frames_checked':len(records),'source_vs_clean_worst_mae':max(r['source_vs_clean_mae'] for r in records),
            'source_vs_clean_min_psnr':min(r['source_vs_clean_psnr'] for r in records),
            'clean_vs_review_upper_worst_mae':max(r['clean_vs_review_upper_mae'] for r in records),
            'encoded_lossy_note':'PNG upper regions are pixel-exact. Independent H.264 encodes have bounded measured differences.',
            'thresholds':{'source_vs_clean_mae_max':3.0,'source_vs_clean_psnr_min':35,'clean_vs_review_upper_mae_max':3.0}}
    result['passed']=(result['source_vs_clean_worst_mae']<3 and result['source_vs_clean_min_psnr']>35 and result['clean_vs_review_upper_worst_mae']<3)
    if not result['passed']:write(root/'encoded-validation.json',result);raise ValueError('Decoded media quality check failed')
    checks=[]
    for kind,h in [('clean',spec['height']),('review',height)]:
        p=json.loads(((source_root if kind=='clean' else root)/f'decoded-{kind}'/'probe.json').read_text())
        assert p=={'frames':spec['frame_count'],'fps':float(spec['fps']),'width':spec['width'],'height':h},p
        checks.append(p)
    result['probes']=checks;result['fps_frame_count_dimensions_match']=True
    picks=sorted({0,spec['frame_count']//2,spec['frame_count']-1,*[s['start_frame'] for s in spec['shots']],*[s['start_frame']-1 for s in spec['shots'][1:]]})
    review_report=root/'review_frames/dual-video-report.json'
    if review_report.is_file():
        layouts=json.loads(review_report.read_text(encoding='utf-8'))['shots']
        boundaries=[p['start_frame'] for shot in layouts for p in shot.get('pages',[])]
        picks=sorted(set(picks)|{i for b in boundaries for i in (b-1,b,b+1) if 0<=i<spec['frame_count']})
    result['inspection_frames_zero_based']=picks
    write(root/'encoded-validation.json',result);write(root/'encoded-frame-errors.json',records)
    return result,picks

def sheet(paths,out,font_path,width=480):
    font=ImageFont.truetype(font_path,18);tiles=[]
    for label,path in paths:
        im=Image.open(path).convert('RGB');h=round(im.height*width/im.width);im=im.resize((width,h))
        t=Image.new('RGB',(width,h+32),(245,245,245));t.paste(im,(0,0));ImageDraw.Draw(t).text((8,h+4),label,font=font,fill=(20,20,20));tiles.append(t)
    cols=2;th=max(t.height for t in tiles);canvas=Image.new('RGB',(cols*width,math.ceil(len(tiles)/cols)*th),'white')
    for n,t in enumerate(tiles):canvas.paste(t,((n%cols)*width,(n//cols)*th))
    canvas.save(out)

def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root');p.add_argument('--fixture-unregistered',action='store_true');p.add_argument('--rendered-dir',required=True);p.add_argument('--timeline',required=True);p.add_argument('--blender',required=True);p.add_argument('--font',required=True)
    p.add_argument('--mode',choices=['compact','expanded'],default='compact')
    p.add_argument('--panel-height',default='auto',help='auto selects the minimum fixed height; or supply a positive pixel height')
    p.add_argument('--show-usage-hint',action='store_true',help='Display the exact optional user-approved usage sentence')
    p.add_argument('--max-chars-per-second',type=float,default=6.0)
    p.add_argument('--min-page-seconds',type=float,default=2.0)
    p.add_argument('--reuse-clean-from',help='Existing verified render directory: reuse source PNGs and copy identical clean MP4 bytes; never reencode it')
    p.add_argument('--expanded-review-from',help='Retain the verified expanded review from an existing same-timeline directory as review-expanded.mp4')
    a=p.parse_args();root=Path(a.rendered_dir).resolve();scripts=Path(__file__).resolve().parent
    source_root=Path(a.reuse_clean_from).resolve() if a.reuse_clean_from else root
    spec=json.loads(Path(a.timeline).read_text(encoding='utf-8-sig'));validate_spec(spec)
    registry_bound=bool(spec.get('project_id') or spec.get('registry_binding_sha256'))
    if registry_bound:
        rb_path=source_root/'registry-binding.json'
        if not rb_path.is_file() or sha(rb_path)!=spec.get('registry_binding_sha256'):raise ValueError('Registry binding missing or timeline mismatch')
        rb=json.loads(rb_path.read_text(encoding='utf-8'))
        registry_reference=validate_registry_binding(rb,project_root=a.project_root,artifact_root=source_root)
        validate_bound_actor_subset(rb,spec,json.loads((source_root/'registry-snapshot.json').read_text(encoding='utf-8')),registry_reference['asset_contracts'])
    elif not a.fixture_unregistered:raise ValueError('v2.2 production packaging requires registry-bound rendered timeline; --fixture-unregistered is explicit synthetic regression only')
    # Bind to the actual generated actors, never infer their mode from colors or a filename.
    actual_path=source_root/'actor-geometry.json'
    if not actual_path.is_file():raise ValueError('Missing actual actor-geometry.json; rerender with the current generator')
    actor_records=json.loads(actual_path.read_text(encoding='utf-8'))
    proxy_contract=build_proxy_contract(spec,actor_records)
    validate_proxy_contract(proxy_contract,spec=spec,actor_records=actor_records,for_clean=True)
    prompt_text,prompt_coverage=generate_prompt(spec,proxy_contract)
    validate_prompt_coverage(prompt_text,spec,proxy_contract,prompt_coverage)
    if (root/'preview-manifest.json').exists():raise ValueError('Already packaged: choose a new render version')
    root.mkdir(parents=True,exist_ok=True)
    logs=root/'pipeline-logs';logs.mkdir(exist_ok=True)
    def run(args,name):
        with (logs/(name+'.log')).open('w',encoding='utf-8') as log:subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True)
    if a.reuse_clean_from:
        inspected=validate_references(source_root/'preview-manifest.json',[str(source_root/'clean.mp4')],project_root=a.project_root)
        previous=json.loads((source_root/'preview-manifest.json').read_text(encoding='utf-8'))
        if previous['source_timeline_sha256']!=sha(a.timeline):raise ValueError('Reused clean timeline hash differs')
        if source_root==root or (root/'clean.mp4').exists():raise ValueError('Reuse requires a new destination')
        shutil.copy2(source_root/'clean.mp4',root/'clean.mp4')
        if sha(root/'clean.mp4')!=inspected[0]['sha256']:raise ValueError('Clean copy changed bytes')
    compose_args=[sys.executable,str(scripts/'dual_video.py'),'--spec',str(Path(a.timeline).resolve()),'--clean-frames',str(source_root/'frames'),'--output',str(root/'review_frames'),'--font',a.font,
         '--mode',a.mode,'--panel-height',str(a.panel_height),'--max-chars-per-second',str(a.max_chars_per_second),'--min-page-seconds',str(a.min_page_seconds)]
    if a.show_usage_hint:compose_args.append('--show-usage-hint')
    run(compose_args,'compose')
    report=json.loads((root/'review_frames/dual-video-report.json').read_text(encoding='utf-8'));rh=report['review_height']
    for kind,h in [('clean',spec['height']),('review',rh)]:
        if kind=='clean' and a.reuse_clean_from:continue
        seq=root/('frames' if kind=='clean' else 'review_frames')
        args=[a.blender,'--background','--factory-startup','--python-exit-code','1','--python',str(scripts/'blender_video.py'),'--']
        opts=['--fps',str(spec['fps']),'--width',str(spec['width']),'--height',str(h),'--frames',str(spec['frame_count'])]
        run(args+['encode','--input',str(seq),'--output',str(root/(kind+'.mp4'))]+opts,'encode-'+kind)
        run(args+['decode','--input',str(root/(kind+'.mp4')),'--output',str(root/('decoded-'+kind))]+opts,'decode-'+kind)
    metrics,picks=compare_sequences(root,spec,rh,source_root)
    timing=compare_timing(root/'clean.mp4',root/'review.mp4',spec['fps'],spec['frame_count'])
    write(root/'mp4-timing-validation.json',timing)
    for kind in ('clean','review'):
        sheet([(f'{kind} frame={i} t={i/spec["fps"]:.3f}s',(source_root if kind=='clean' else root)/f'decoded-{kind}'/f'frame-{i+1:04d}.png') for i in picks],root/(kind+'-contact-sheet.jpg'),a.font)
    if (root/'gallery').exists():
        sheet([(x.stem,x) for x in sorted((root/'gallery').glob('*.png'))],root/'actor-multiview.jpg',a.font)
    clean={'path':'clean.mp4','sha256':sha(root/'clean.mp4'),'purpose':'model-reference'}
    review={'path':'review.mp4','sha256':sha(root/'review.mp4'),'purpose':'review-only'}
    variants=[]
    if a.expanded_review_from:
        expanded_root=Path(a.expanded_review_from).resolve()
        old_manifest=json.loads((expanded_root/'preview-manifest.json').read_text(encoding='utf-8'))
        validate_references(expanded_root/'preview-manifest.json',[str(expanded_root/'clean.mp4')],project_root=a.project_root)
        reuse_validation=validate_expanded_reuse(old_manifest,current_timeline_sha256=sha(a.timeline),
                                                 current_clean_sha256=clean['sha256'],current_proxy_contract=proxy_contract)
        old_report=json.loads((expanded_root/'review_frames/dual-video-report.json').read_text(encoding='utf-8'))
        if old_report.get('mode',old_report.get('layout_mode','expanded'))!='expanded':raise ValueError('Requested full-text source is not expanded')
        destination=root/'review-expanded.mp4'
        if destination.exists():raise ValueError('Expanded output exists')
        shutil.copy2(expanded_root/old_manifest['files']['review_video']['path'],destination)
        if sha(destination)!=old_manifest['files']['review_video']['sha256']:raise ValueError('Expanded copy differs')
        write(root/'expanded-reuse-validation.json',reuse_validation)
        write(root/'expanded-timing-validation.json',compare_timing(root/'clean.mp4',destination,spec['fps'],spec['frame_count']))
        variants=[{'path':'review-expanded.mp4','sha256':sha(destination),'purpose':'review-only','layout':'expanded'}]
    manifest={'schema_version':2,'kind':'previs','status':'local-verified','source_timeline_sha256':sha(a.timeline),
        'fps':spec['fps'],'frame_count':spec['frame_count'],'duration':spec['frame_count']/spec['fps'],
        'cuts_zero_based':[s['start_frame'] for s in spec['shots'][1:]],'files':{'clean_video':clean,'review_video':review,'review_variants':variants},
        'reference_policy':{'allowlist':[clean],'review_only_never_upload':True},
        'review_layout':{'mode':a.mode,'panel_height':report['panel_height'],'timeline_modified':False,
                        'panel_height_requested':report['pagination_settings']['panel_height_requested'],
                        'panel_height_selection':report['pagination_settings']['panel_height_selection'],
                        'usage_hint_enabled':report['pagination_settings']['usage_hint_shown'],
                        'usage_hint_text':report['pagination_settings']['usage_hint_text'],
                         'page_timeline':[{ 'id':s['id'],'pages':s.get('pages',[])} for s in report['shots']]},
        'clean_reused_from':str(source_root) if a.reuse_clean_from else None,
        'verification':{'png_upper_pixel_exact':report['all_upper_regions_pixel_exact'],'encoded':metrics},
        'limitations':['No facial acting, gait, fingers animation, contact or IK validated. No paid generation executed.']}
    # The full original storyboard and dialogue are retained, without summary substitution.
    (root/'source-spec.json').write_bytes(Path(a.timeline).read_bytes())
    (root/'storyboard-source.txt').write_bytes(spec['storyboard_text'].encode('utf-8'))
    if actual_path.resolve()!=(root/'actor-geometry.json').resolve():shutil.copy2(actual_path,root/'actor-geometry.json')
    write(root/'proxy-contract.json',proxy_contract)
    (root/'generation-prompt.txt').write_bytes(prompt_text.encode('utf-8'))
    write(root/'generation-prompt-coverage.json',prompt_coverage)
    def binding(name):return {'path':name,'sha256':sha(root/name)}
    manifest['proxy_reference']={'schema':'timeaigc-proxy-reference/v1','purpose':'model-reference','clean_video_sha256':clean['sha256'],
        'mode_contract':binding('proxy-contract.json'),'source_spec':binding('source-spec.json'),
        'source_storyboard':binding('storyboard-source.txt'),'actor_records':binding('actor-geometry.json'),
        'generation_prompt':binding('generation-prompt.txt'),'prompt_coverage':binding('generation-prompt-coverage.json')}
    if registry_bound:
        copy_registry_snapshots(rb,source_root,root)
        manifest['schema_version']=3
        manifest['project_id']=spec['project_id']
        manifest['registry_binding']=binding('registry-binding.json')
    write(root/'preview-manifest.json',manifest)
    upload=build_upload_list(root/'preview-manifest.json',project_root=a.project_root);write(root/'upload-list.json',upload)
    rejected=[]
    for entry in [review,*variants]:
        try:validate_references(root/'preview-manifest.json',[str(root/entry['path'])],project_root=a.project_root)
        except ReferenceError as exc:rejected.append({'passed':True,'rejected':entry['path'],'reason':str(exc)})
        else:raise AssertionError('Review video was accepted')
    write(root/'upload-negative-test.json',{'passed':True,'cases':rejected})
    if registry_bound:
        from character_registry import register_preview
        registration=register_preview(a.project_root,root/'preview-manifest.json') if a.project_root else None
        if registration is not None:write(root/'project-registration.json',registration)
    print(json.dumps({'status':'passed','root':str(root),'metrics':metrics},ensure_ascii=False))

if __name__=='__main__':main()
