#!/usr/bin/env python3
"""Validate Markdown output produced by the comic visual asset art-direction skill."""

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


FULL_HEADINGS = (
    "第一部分：剧本宏观分析",
    "第二部分：角色视觉档案",
    "第三部分：场景视觉档案",
    "第四部分：视觉提示词库",
)

CHARACTER_REQUIRED = (
    ("全身", "缺少全身构图"),
    ("正视镜头", "缺少正视镜头"),
    ("正面站立", "缺少正面站立"),
    ("中性", "缺少中性无明显情绪"),
    ("脚部", "缺少脚部完整可见"),
    ("高级灰", "缺少高级灰背景"),
    ("9:16", "缺少 9:16 画幅"),
)

SCENE_REQUIRED = (
    (r"2\s*[x×]\s*2", "缺少 2x2 网格"),
    ("顶视鸟瞰图", "缺少顶视鸟瞰图"),
    ("正面视图", "缺少正面视图"),
    ("左侧视图", "缺少左侧视图"),
    ("右侧视图", "缺少右侧视图"),
    ("同一", "缺少四格一致性说明"),
    ("地面", "缺少地面与材质"),
    ("光影", "缺少光影"),
    ("前景", "缺少前景"),
    ("背景", "缺少背景"),
    ("无人物", "缺少无人物约束"),
    ("16:9", "缺少 16:9 画幅"),
)

PLACEHOLDER_PATTERNS = (
    r"\[(?:用户|角色|场景|画风|时间|地点|Prompt|提示词)[^\]\n]*\]",
    r"<(?:用户|角色|场景|画风|时间|地点)[^>\n]*>",
    r"(?:^|[\s：:])(?:\.{3}|…{2,})(?:$|\s)",
)


def read_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def find_section(text: str, title: str, next_title: str | None = None) -> str:
    start = text.find(title)
    if start < 0:
        return ""
    if next_title is None:
        return text[start:]
    end = text.find(next_title, start + len(title))
    return text[start:] if end < 0 else text[start:end]


def prompt_entries(section: str) -> list[tuple[str, str]]:
    heading = re.compile(r"(?m)^####\s+(.+?)\s*$")
    matches = list(heading.finditer(section))
    entries: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        entries.append((match.group(1).strip(), section[match.end() : end].strip()))
    return entries


def contains(value: str, pattern: str) -> bool:
    if pattern.startswith("2\\s"):
        return re.search(pattern, value, flags=re.IGNORECASE) is not None
    return pattern in value


def validate_placeholders(text: str, findings: list[Finding]) -> None:
    for pattern in PLACEHOLDER_PATTERNS:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            findings.append(Finding("ERROR", f"发现未替换占位符：{match.group(0).strip()}"))


def validate_character_prompts(text: str, findings: list[Finding], required: bool) -> None:
    section = find_section(text, "A. 角色立绘提示词", "B. 场景概念图提示词")
    if not section:
        if required:
            findings.append(Finding("ERROR", "缺少“A. 角色立绘提示词”部分"))
        return

    entries = prompt_entries(section)
    if not entries:
        findings.append(Finding("ERROR", "角色提示词部分没有以四级标题列出的资产"))
        return

    for title, body in entries:
        if "提示词" not in body:
            findings.append(Finding("ERROR", f"{title}：缺少“提示词”字段"))
        for token, message in CHARACTER_REQUIRED:
            if token not in body:
                findings.append(Finding("ERROR", f"{title}：{message}"))
        if "无其他人物" not in body:
            findings.append(Finding("WARNING", f"{title}：建议明确“无其他人物”"))
        if "建议生成数量" not in body:
            findings.append(Finding("WARNING", f"{title}：缺少独立的建议生成数量"))


def validate_scene_order(title: str, body: str, findings: list[Finding]) -> None:
    ordered_groups = (
        ("地面",),
        ("光影",),
        ("前景",),
        ("中央", "中景"),
        ("背景",),
        ("天空", "顶部", "天花", "穹顶"),
    )
    cursor = -1
    for group in ordered_groups:
        found = [
            (body.find(token, cursor + 1), token)
            for token in group
            if body.find(token, cursor + 1) >= 0
        ]
        if not found:
            if any(token in body for token in group):
                findings.append(
                    Finding(
                        "ERROR",
                        f"{title}：结构层级“{'/'.join(group)}”出现过早，"
                        "应按地面→光影→前景→中景/中央→背景→天空/顶部排列",
                    )
                )
            else:
                findings.append(Finding("ERROR", f"{title}：缺少结构层级“{'/'.join(group)}”"))
            return
        cursor, _ = min(found)


def validate_scene_prompts(text: str, findings: list[Finding], required: bool) -> None:
    section = find_section(text, "B. 场景概念图提示词")
    if not section:
        if required:
            findings.append(Finding("ERROR", "缺少“B. 场景概念图提示词”部分"))
        return

    entries = prompt_entries(section)
    if not entries:
        findings.append(Finding("ERROR", "场景提示词部分没有以四级标题列出的资产"))
        return

    for title, body in entries:
        if "提示词" not in body:
            findings.append(Finding("ERROR", f"{title}：缺少“提示词”字段"))
        for pattern, message in SCENE_REQUIRED:
            if not contains(body, pattern):
                findings.append(Finding("ERROR", f"{title}：{message}"))
        if not any(token in body for token in ("天空", "顶部", "天花", "穹顶")):
            findings.append(Finding("ERROR", f"{title}：缺少天空或顶部结构"))
        if not all(token in body for token in ("无人群", "无人影", "无人体剪影")):
            findings.append(Finding("WARNING", f"{title}：无人物负面约束不完整"))
        validate_scene_order(title, body, findings)


def validate_ids(text: str, findings: list[Finding]) -> None:
    for prefix, label in (("C", "角色"), ("S", "场景")):
        values = [int(value) for value in re.findall(rf"(?m)^###\s+{prefix}(\d{{2}})\b", text)]
        if not values:
            continue
        unique = sorted(set(values))
        expected = list(range(unique[0], unique[-1] + 1))
        if unique[0] != 1 or unique != expected:
            findings.append(Finding("WARNING", f"{label}基础资产编号不连续：{unique}"))


def validate(text: str, mode: str) -> list[Finding]:
    findings: list[Finding] = []

    if mode == "full":
        for heading in FULL_HEADINGS:
            if heading not in text:
                findings.append(Finding("ERROR", f"缺少完整输出标题：{heading}"))

    validate_placeholders(text, findings)
    validate_ids(text, findings)

    require_characters = mode in {"full", "prompts", "character"}
    require_scenes = mode in {"full", "prompts", "scene"}
    validate_character_prompts(text, findings, require_characters)
    validate_scene_prompts(text, findings, require_scenes)

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="UTF-8 Markdown 文件路径；使用 - 从标准输入读取")
    parser.add_argument(
        "--mode",
        choices=("full", "prompts", "character", "scene"),
        default="full",
        help="校验完整交付、完整提示词库、仅角色或仅场景输出",
    )
    args = parser.parse_args()

    try:
        text = read_text(args.source)
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 无法读取输入：{exc}", file=sys.stderr)
        return 2

    findings = validate(text, args.mode)
    errors = [item for item in findings if item.level == "ERROR"]
    warnings = [item for item in findings if item.level == "WARNING"]

    for item in findings:
        print(f"{item.level}: {item.message}")
    print(f"SUMMARY: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
