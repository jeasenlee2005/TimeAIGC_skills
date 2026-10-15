"""Emit complete storyboard prose with actual proxy mapping and hash coverage.

No network calls, paid generation, summary substitution or newline normalization.
"""
import argparse
import hashlib
import json
from pathlib import Path

from proxy_contract import canonical_sha256, validate_proxy_contract


def text_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validate_storyboard(spec):
    story = spec.get('storyboard_text')
    if not isinstance(story, str) or not story.strip():
        raise ValueError('complete storyboard_text is required; summary is not a substitute')
    if 'storyboard_sha256' in spec and spec['storyboard_sha256'] != text_sha256(story):
        raise ValueError('storyboard_sha256 does not match exact source text')
    shots = spec.get('shots')
    if not isinstance(shots, list) or not shots:
        raise ValueError('storyboard shots are required')
    seen = set()
    for shot in shots:
        shot_id = shot.get('id')
        if not isinstance(shot_id, str) or not shot_id or shot_id in seen:
            raise ValueError('storyboard shot IDs must be nonempty and unique')
        seen.add(shot_id)
        source = shot.get('source_text')
        dialogue = shot.get('dialogue')
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f'{shot_id}: complete source_text required; summary cannot replace it')
        if not isinstance(dialogue, str) or dialogue not in source:
            raise ValueError(f'{shot_id}: source_text must preserve dialogue verbatim, including CRLF')
        if story.count(source) != 1:
            raise ValueError(f'{shot_id}: storyboard_text must contain its complete source_text exactly once')
        for field, text in (('source_text_sha256', source), ('dialogue_sha256', dialogue)):
            if field in shot and shot[field] != text_sha256(text):
                raise ValueError(f'{shot_id}: {field} differs from the exact original text')
        # Optional explicit fields make completeness mechanically auditable.
        fields = shot.get('storyboard_fields')
        if fields is not None:
            required = ('composition', 'camera', 'blocking', 'action', 'gesture', 'expression', 'sound')
            if not isinstance(fields, dict) or any(not isinstance(fields.get(key), str) or not fields[key].strip() for key in required):
                raise ValueError(f'{shot_id}: storyboard_fields must include composition/camera/blocking/action/gesture/expression/sound')
            if any(fields[key] not in source for key in required):
                raise ValueError(f'{shot_id}: source_text lost a complete storyboard field')
    return True


def generate_prompt(spec, contract):
    validate_proxy_contract(contract, spec=spec, for_clean=True)
    validate_storyboard(spec)
    lines = [
        '生成参考职责与完整分镜',
        '素材顺序：素材1为本次清单中经核验的干净空间参考视频。说明版、手部诊断图和文字栏不得作为生成参考。',
        '真实人物身份与服装由人物资产图和完整分镜决定；场景图提供外观与材质，素材编号以实际上传清单为准。',
        '角色映射（依据实际已构建代理记录，颜色仅表示角色身份，不定义服装、皮肤或美术外观）：',
    ]
    for actor in contract['actors']:
        rgb = ', '.join(format(value, '.7g') for value in actor['color'])
        if not actor.get('registry_character_id') and actor['proxy_mode'] not in ('shape_proxy','existing_model'):
            lines.append(f"- {actor['id']} → {actor['character_name']}；{actor['color_name']}，RGB({rgb})；"
                         f"身高 {actor['height_m']:.3f} 米；实际代理 {actor['proxy_mode']}；用途 {actor['purpose']}。")
        else:
            if actor['proxy_mode'] in ('shape_proxy','existing_model'):
                dimensions='、'.join(f'{key}={value:g}米' for key,value in actor['measurements_m'].items())
                dimensions += '；突出部及实际网格按登记模型校验' if actor['proxy_mode']=='existing_model' else '；突出部包络单独按确认记录保留'
            else:
                dimensions=f"脚底至头顶总高（含头）{actor['height_m']:.3f}米" if actor.get('registry_character_id') else f"总高 {actor['height_m']:.3f} 米"
            lines.append(f"- {actor['id']} → {actor['character_name']}；{actor['color_name']}；RGB({rgb})；"
                         f"{dimensions}；实际代理 {actor['proxy_mode']}；用途 {actor['purpose']}。")
    modes = {actor['proxy_mode'] for actor in contract['actors']}
    for actor in contract['actors']:
        aliases=actor.get('character_aliases',[])
        if aliases:
            lines.append(f"已登记称呼对应：{actor['character_name']}（{actor['id']}，{actor['color_name']}）的别名为："+'、'.join(aliases)+'。对白中的这些称呼均对应此角色。')
    if 'cylinder' in modes:
        lines.extend([
            '圆柱代理：头部与圆柱身体仅表达占位、身高和整体朝向；没有手和四肢。浅色躯干符号只表示正面方向，不是衣服图案。',
            '干净视频仅参考构图、机位、站位与整体路径。不要模仿圆柱外形、无肢体状态、僵直、滑行或躯干符号。',
            '圆柱外形、无肢体状态和浅色前条不得出现在最终成片。',
        ])
    if 'shape_proxy' in modes:
        lines.append('非人角色仅用已确认的静态体块、头部和突出部包络表达体量与朝向；肩高、头顶高、体长、角耳尾范围分别记录。不得把占位体块当作最终造型、步态、关节动作或真实表演。')
    if 'existing_model' in modes:
        lines.append('已有非人模型直接复用了项目登记集合中的原始静态网格；模型路径、版本、内容哈希及实际尺寸已经绑定。它仅提供体量、朝向、构图、站位与整体路径，模型外观和静止姿态不代替资产图与分镜规定的最终造型、步态、关节动作或表演。未验证 IK、抓握或接触。')
    if 'articulated_special' in modes:
        lines.append('特殊人体代理仅在已明确选择并记录理由时使用；固定肢体和普通掌体不证明真实表演，未验证 IK、抓握或接触；不得模仿僵直姿态和滑行。')
        for actor in contract['actors']:
            if actor['proxy_mode'] == 'articulated_special':
                lines.append(f"特殊参考理由（{actor['id']}）：{actor['special_proxy_reason']}")
    lines.extend([
        '动作、手势、表情、对白与声音以以下完整分镜原文为准，不以代理形体或根位移代替人物表演。',
        '本地只完成预演与提示词准备，未经付费视频生成。提示词不能保证生成结果不受代理外形或运动影响，仍需检查实际生成结果。',
        '——完整分镜原文开始——',
    ])
    prefix = '\n'.join(lines) + '\n'
    source = spec['storyboard_text']
    prompt = prefix + source + '\n——完整分镜原文结束——\n'
    base = len(prefix.encode('utf-8'))
    shot_coverage = []
    for shot in spec['shots']:
        index = source.index(shot['source_text'])
        start = base + len(source[:index].encode('utf-8'))
        end = start + len(shot['source_text'].encode('utf-8'))
        item = {'id': shot['id'], 'source_text_sha256': text_sha256(shot['source_text']),
                'dialogue_sha256': text_sha256(shot['dialogue']),
                'source_text_utf8_range': [start, end], 'source_text_exact': prompt.encode('utf-8')[start:end] == shot['source_text'].encode('utf-8'),
                'dialogue_exact': shot['dialogue'] in shot['source_text'],
                'explicit_storyboard_fields_present': 'storyboard_fields' in shot}
        if shot['dialogue']:
            dialogue_offset = shot['source_text'].index(shot['dialogue'])
            dialogue_start = start + len(shot['source_text'][:dialogue_offset].encode('utf-8'))
            item['dialogue_utf8_range'] = [dialogue_start, dialogue_start + len(shot['dialogue'].encode('utf-8'))]
        else:
            item['dialogue_utf8_range'] = None
        shot_coverage.append(item)
    coverage = {'schema': 'timeaigc-generation-prompt-coverage/v1',
                'source_spec_sha256': canonical_sha256(spec), 'proxy_contract_sha256': contract['contract_sha256'],
                'storyboard_sha256': text_sha256(source), 'storyboard_utf8_range': [base, base + len(source.encode('utf-8'))],
                'prompt_sha256': text_sha256(prompt), 'shots': shot_coverage,
                'all_source_text_exact': all(item['source_text_exact'] for item in shot_coverage),
                'all_dialogue_exact': all(item['dialogue_exact'] for item in shot_coverage),
                'paid_generation_performed': False,
                'scope_note': 'Exact source preservation is verified; semantic completeness of prose and performance quality still need human review.'}
    return prompt, coverage


def validate_prompt_coverage(prompt_text, spec, contract, coverage):
    expected_prompt, expected_coverage = generate_prompt(spec, contract)
    if prompt_text != expected_prompt or coverage != expected_coverage:
        raise ValueError('generation prompt, source text, proxy mapping or coverage drift detected')
    return coverage


def write_generation_prompt(spec, contract, output_dir):
    prompt, coverage = generate_prompt(spec, contract)
    out = Path(output_dir).resolve()
    files = {'generation_prompt': ('generation-prompt.txt', prompt.encode('utf-8')),
             'source_storyboard': ('storyboard-source.txt', spec['storyboard_text'].encode('utf-8')),
             'prompt_coverage': ('generation-prompt-coverage.json', (json.dumps(coverage, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))}
    if any((out / name).exists() for name, _ in files.values()):
        raise ValueError('prompt output files already exist; choose a fresh version')
    out.mkdir(parents=True, exist_ok=True)
    bindings = {}
    for role, (name, payload) in files.items():
        target = out / name
        with target.open('xb') as stream:
            stream.write(payload)
        bindings[role] = {'path': str(target), 'sha256': hashlib.sha256(payload).hexdigest()}
    return bindings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding='utf-8-sig'))
    contract = json.loads(Path(args.contract).read_text(encoding='utf-8-sig'))
    print(json.dumps(write_generation_prompt(spec, contract, args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
