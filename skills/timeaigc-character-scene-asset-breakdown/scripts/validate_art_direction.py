#!/usr/bin/env python3
"""Check asset prompt Markdown; semantic continuity still requires manual review."""
from __future__ import annotations
import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Finding:
    level: str
    message: str

PLACEHOLDER_PATTERNS = (
    r"\[(?:用户|角色|场景|画风|时间|地点|Prompt|提示词)[^\]\n]*\]",
    r"<(?:用户|角色|场景|画风|时间|地点)[^>\n]*>",
)
LABEL = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?(MJ\s*英文(?:修改)?提示词|中文提示词|状态修改提示词|参考图生成提示词|网格场景提示词|提示词)(?:\*\*)?\s*[：:](?:\*\*)?\s*(.*)$")
MULTIVIEW = re.compile(r"2\s*[x×]\s*2|四视角|四格|分屏|视图拼接|split[- ]screen|four[- ](?:view|panel)|quadrants", re.I)

def prompt_entries(text: str):
    entries = []
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        match = LABEL.match(line)
        if match:
            if current:
                entries.append(current)
            current = [number, match[1], match[2]]
        elif current and line.strip() and not re.match(r"^\s*(?:#|```|~~~|\*\*|输入图[：:]|说明[：:])", line):
            current[2] += '\n' + line.strip()
        elif current:
            entries.append(current)
            current = None
    if current:
        entries.append(current)
    return entries

def validate(text: str, mode: str = 'full', language: str = 'both', allow_multiview: bool = False) -> list[Finding]:
    findings = []
    if not text.strip():
        return [Finding('ERROR', '输入为空')]
    for pattern in PLACEHOLDER_PATTERNS:
        match = re.search(pattern, text)
        if match:
            findings.append(Finding('ERROR', f'发现未替换占位符：{match[0]}'))
    entries = prompt_entries(text)
    if not entries:
        return findings + [Finding('ERROR', '未识别到提示词正文；请检查提示词标签与正文，或人工核验非标准格式')]
    for index, (line, label, body) in enumerate(entries):
        prefix = f'第 {line} 行'
        english = label.startswith('MJ')
        if english:
            if re.search(r'[—–－]\s*(?:ar|v|edit)\b|-\s+-\s*(?:ar|v|edit)\b|--(?:ar|v)\s*[：:]', body):
                findings.append(Finding('ERROR', f'{prefix}：MJ 参数须使用连续 --，参数名与值用空格分隔'))
            if re.search(r'\S--(?:ar|v|edit)\b', body):
                findings.append(Finding('ERROR', f'{prefix}：MJ 参数前缺少空格'))
            ratio = re.search(r'--(?:ar|aspect)\s+([^\s]+)', body)
            if ratio and not re.fullmatch(r'[1-9]\d*:[1-9]\d*', ratio[1]):
                findings.append(Finding('ERROR', f'{prefix}：画幅须为半角整数比例，后面不能带标点'))
            if not ratio:
                findings.append(Finding('WARNING', f'{prefix}：未识别 --ar 画幅，请核对目标入口及画幅说明'))
            if re.search(r'--(?:v|version)\s+\d+(?:\.\d+)?[，,。;；]?(?:\s+)?[。.]$', body):
                findings.append(Finding('ERROR', f'{prefix}：MJ 尾缀末尾不能追加句号'))
        if not body.strip():
            findings.append(Finding('ERROR', f'{prefix}：提示词正文为空'))
        if re.search(r'同上|参考上一张', body):
            findings.append(Finding('ERROR', f'{prefix}：存在不明确的复用指代'))
        grid = label == '网格场景提示词' or (english and index > 0 and entries[index - 1][1] == '网格场景提示词')
        if '空场景' in body:
            findings.append(Finding('ERROR', f'{prefix}：场景提示词应使用“无人场景”'))
        if not allow_multiview and not grid and MULTIVIEW.search(body):
            findings.append(Finding('WARNING', f'{prefix}：发现多视角或分屏词，复核是否为旧规范残留或冗余负面指令'))
        if language == 'both' and not english:
            if index + 1 == len(entries) or not entries[index + 1][1].startswith('MJ'):
                findings.append(Finding('ERROR', f'{prefix}：缺少紧随其后的 MJ 英文提示词'))
        if language == 'both' and english and (index == 0 or entries[index - 1][1].startswith('MJ')):
            findings.append(Finding('ERROR', f'{prefix}：缺少对应中文提示词'))
        if label in {'状态修改提示词', '参考图生成提示词', '网格场景提示词'}:
            if not re.search(r'输入|基于|以.+(?:图|形象|场景)', body):
                findings.append(Finding('WARNING', f'{prefix}：未识别明确输入图，请复核图像名称'))
            if not re.search(r'保留|保持|维持', body):
                findings.append(Finding('WARNING', f'{prefix}：未识别保留约束'))
        if grid:
            if not re.search(r'2\s*[x×*]\s*2', body, re.I):
                findings.append(Finding('ERROR', f'{prefix}：网格场景缺少 2x2 布局'))
            positions = (r'top[ -]left', r'top[ -]right', r'bottom[ -]left', r'bottom[ -]right') if english else ('左上', '右上', '左下', '右下')
            if not all(re.search(pos, body, re.I) for pos in positions):
                findings.append(Finding('WARNING', f'{prefix}：请逐格说明四个位置的区域与空间关系'))
    # Do not require IDs, legacy headings, fixed aspect ratios or candidate counts.
    # Directional and incremental outputs may legitimately omit whole sections.
    return findings

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='UTF-8 Markdown 文件，或 - 从标准输入读取')
    parser.add_argument('--mode', choices=('full', 'prompts', 'character', 'scene'), default='full', help='兼容旧调用模式；各模式均按实际出现的提示词块检查')
    parser.add_argument('--language', choices=('both', 'zh', 'en'), default='both')
    parser.add_argument('--allow-multiview', action='store_true')
    args = parser.parse_args()
    try:
        text = sys.stdin.read() if args.source == '-' else Path(args.source).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        print(f'ERROR: 无法读取输入：{exc}', file=sys.stderr)
        return 2
    findings = validate(text, args.mode, args.language, args.allow_multiview)
    for finding in findings:
        print(f'{finding.level}: {finding.message}')
    errors = sum(f.level == 'ERROR' for f in findings)
    warnings = sum(f.level == 'WARNING' for f in findings)
    print(f'SUMMARY: {errors} error(s), {warnings} warning(s)')
    return 1 if errors else 0

if __name__ == '__main__':
    raise SystemExit(main())
