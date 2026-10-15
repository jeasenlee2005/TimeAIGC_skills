"""Resolve a confirmed empty scene through the project index without changing it."""
import json,hashlib
from pathlib import Path
from character_registry import project_path,PreflightBlocked

def bind_scene(project_root,scene_id,selected_path):
    root=Path(project_root).resolve()
    index=json.loads((root/'.timeaigc/project.json').read_text(encoding='utf-8-sig'))
    scene=index.get('scenes',{}).get(scene_id)
    if not isinstance(scene,dict):
        raise ValueError('Scene is not registered in project index: '+scene_id)
    version=scene.get('active_version')
    record=scene.get('versions',{}).get(version)
    manifest_rel=record.get('manifest') if isinstance(record,dict) else record
    manifest_path=project_path(root,manifest_rel)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if manifest.get('id')!=scene_id or manifest.get('version')!=version or manifest.get('state') not in ('confirmed','approved'):
        raise ValueError('Scene identity/version/confirmation is unresolved')
    entry=manifest.get('files',{}).get('scene')
    if not isinstance(entry,dict):raise ValueError('Scene manifest files.scene is required')
    model=project_path(manifest_path.parent,entry.get('path'))
    actual=hashlib.sha256(model.read_bytes()).hexdigest()
    if actual!=entry.get('sha256'):raise ValueError('Scene file changed from confirmed manifest')
    if Path(selected_path).resolve()!=model:raise ValueError('--scene-blend differs from active confirmed project scene')
    return {'id':scene_id,'version':version,'manifest':manifest_path.relative_to(root).as_posix(),
            'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            'path':model.relative_to(root).as_posix(),'sha256':actual}

def validate_scene_binding(binding,project_root):
    scene=binding.get('source_scene')
    if scene is None:return True
    actual=bind_scene(project_root,scene['id'],project_path(project_root,scene['path']))
    if actual!=scene:raise ValueError('Bound preview scene is stale: current scene/version/hash differs')
    return True
