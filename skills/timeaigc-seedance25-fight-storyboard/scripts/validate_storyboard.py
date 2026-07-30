#!/usr/bin/env python3
"""Validate the deterministic structure and timing of a 16-shot fight storyboard."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


SHOT_RE = re.compile(
    r"^###\s*镜头\s*(\d{1,2})\s*[｜|]\s*"
    r"(\d{1,2}:\d{2}(?:\.\d+)?)\s*[–—-]\s*"
    r"(\d{1,2}:\d{2}(?:\.\d+)?)\s*$",
    re.MULTILINE,
)
TARGET_RE = re.compile(r"^-\s*目标时长[：:]\s*(\d+(?:\.\d+)?)\s*秒\s*$", re.MULTILINE)
TITLE_RE = re.compile(r"^#\s*剧情段落\s+\d+\s*[｜|]\s*16\s*动态镜头分镜\s*$", re.MULTILINE)
SECTION_RE = re.compile(r"^##\s*16\s*动态镜头\s*$", re.MULTILINE)
FIELDS = (
    "画面风格",
    "景别",
    "视角",
    "运镜",
    "画面动作",
    "连续性",
    "场景与空间",
    "视觉反馈与环境互动",
    "氛围",
)
PLACEHOLDERS = ("……", "...", "待补", "待定", "TBD", "<placeholder>")
DIALOGUE_MARKERS = ("【对白】", "【旁白】", "【OS】")


@dataclass
class Finding:
    level: str
    message: str


def parse_time(value: str) -> float:
    minutes, seconds = value.split(":", 1)
    return int(minutes) * 60 + float(seconds)


def extract_shot_body(text: str, match: re.Match[str], next_start: int) -> str:
    return text[match.end() : next_start].strip()


def validate(text: str) -> list[Finding]:
    findings: list[Finding] = []
    matches = list(SHOT_RE.finditer(text))

    if not TITLE_RE.search(text):
        findings.append(Finding("ERROR", "缺少“剧情段落 N｜16 动态镜头分镜”标题。"))
    if not SECTION_RE.search(text):
        findings.append(Finding("ERROR", "缺少“## 16 动态镜头”章节。"))

    if len(matches) != 16:
        findings.append(Finding("ERROR", f"镜头标题数量为 {len(matches)}，必须严格为 16。"))

    numbers = [int(match.group(1)) for match in matches]
    if numbers != list(range(1, 17)):
        findings.append(Finding("ERROR", f"镜头编号必须连续为 01–16，当前为 {numbers}。"))
    elif any(len(match.group(1)) != 2 for match in matches):
        findings.append(Finding("ERROR", "镜头编号必须使用两位数字 01–16。"))

    target_match = TARGET_RE.search(text)
    target = float(target_match.group(1)) if target_match else 30.0
    if not target_match:
        findings.append(Finding("WARNING", "未找到“目标时长”字段，按 30 秒校验。"))

    if matches:
        starts = [parse_time(match.group(2)) for match in matches]
        ends = [parse_time(match.group(3)) for match in matches]
        if abs(starts[0]) > 0.01:
            findings.append(Finding("ERROR", f"镜头 01 必须从 00:00 开始，当前为 {matches[0].group(2)}。"))

        for index, (start, end) in enumerate(zip(starts, ends), start=1):
            if end <= start:
                findings.append(Finding("ERROR", f"镜头 {index:02d} 结束时间必须晚于开始时间。"))
            if index > 1 and abs(start - ends[index - 2]) > 0.01:
                findings.append(
                    Finding(
                        "ERROR",
                        f"镜头 {index - 1:02d} 与镜头 {index:02d} 时间轴不连续。",
                    )
                )

        if abs(ends[-1] - target) > 0.05:
            findings.append(
                Finding(
                    "ERROR",
                    f"最后镜头结束于 {ends[-1]:g} 秒，必须等于目标时长 {target:g} 秒。",
                )
            )

    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = extract_shot_body(text, match, next_start)
        shot_number = int(match.group(1))

        field_positions = [body.find(field + "：") for field in FIELDS]
        if any(position < 0 for position in field_positions):
            missing = [field for field, position in zip(FIELDS, field_positions) if position < 0]
            findings.append(
                Finding("ERROR", f"镜头 {shot_number:02d} 缺少字段：{'、'.join(missing)}。")
            )
        elif field_positions != sorted(field_positions):
            findings.append(Finding("ERROR", f"镜头 {shot_number:02d} 字段顺序不符合规范。"))

        if not (body.startswith("[") and "]" in body):
            findings.append(Finding("ERROR", f"镜头 {shot_number:02d} 提示词必须写在方括号内。"))

        feedback_index = body.find("视觉反馈与环境互动：")
        atmosphere_index = body.find("；氛围：")
        if feedback_index >= 0:
            feedback_end = atmosphere_index if atmosphere_index > feedback_index else len(body)
            feedback = body[feedback_index:feedback_end]
            if "【" not in feedback or "】" not in feedback:
                findings.append(
                    Finding(
                        "ERROR",
                        f"镜头 {shot_number:02d} 的视觉反馈与环境互动必须使用【】。",
                    )
                )

        if shot_number > 1:
            continuity_index = body.find("连续性：")
            space_index = body.find("；场景与空间：")
            continuity = (
                body[continuity_index:space_index]
                if continuity_index >= 0 and space_index > continuity_index
                else ""
            )
            if not any(term in continuity for term in ("承接", "延续", "沿着", "接续", "上一镜")):
                findings.append(
                    Finding(
                        "WARNING",
                        f"镜头 {shot_number:02d} 的连续性字段未明确承接上一镜。",
                    )
                )

    for marker in DIALOGUE_MARKERS:
        if marker in text:
            findings.append(Finding("ERROR", f"正式分镜中不得出现台词标记 {marker}。"))

    if re.search(r"“[^”\n]{1,100}”", text):
        findings.append(Finding("WARNING", "检测到中文引号内容，请确认不是被禁止的台词原文。"))

    for placeholder in PLACEHOLDERS:
        if placeholder in text:
            findings.append(Finding("ERROR", f"检测到未替换占位符：{placeholder}"))

    if "激烈打斗" in text:
        findings.append(Finding("WARNING", "检测到空泛描述“激烈打斗”，请改成可执行动作。"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="UTF-8 Markdown storyboard file")
    parser.add_argument("--report", action="store_true", help="Print a validation summary")
    args = parser.parse_args()

    try:
        text = args.file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 无法按 UTF-8 读取文件：{exc}", file=sys.stderr)
        return 2

    findings = validate(text)
    for finding in findings:
        print(f"{finding.level}: {finding.message}")

    errors = sum(finding.level == "ERROR" for finding in findings)
    warnings = sum(finding.level == "WARNING" for finding in findings)
    if args.report or findings:
        print(f"SUMMARY: {errors} error(s), {warnings} warning(s)")
    if not findings:
        print("OK: 16 镜结构、字段和时间轴校验通过。")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
