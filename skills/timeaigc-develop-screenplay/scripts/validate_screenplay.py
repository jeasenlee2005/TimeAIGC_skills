#!/usr/bin/env python3
"""Validate structural fields, numbering, placeholders, and duration totals."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


SCENE_MARKER_RE = re.compile(r"【场次】[：:]?\*{0,2}\s*第?\s*(\d+)\s*场")
UNIT_MARKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?"
    r"(第[一二三四五六七八九十百0-9]+(?:幕|章|节|单元)|节拍\s*[0-9一二三四五六七八九十百]+)",
    re.MULTILINE,
)
DURATION_FIELD_RE = re.compile(
    r"(?:预估|预计)?(?:总)?时长[】\]）)]?\*{0,2}\s*[：:]?\s*\*{0,2}\s*"
    r"([0-9]+(?::[0-9]{1,2})|[0-9]+(?:\.[0-9]+)?\s*分钟(?:\s*[0-9]+\s*秒)?|"
    r"[0-9]+(?:\.[0-9]+)?\s*分(?:钟)?\s*[0-9]*\s*秒?|[0-9]+(?:\.[0-9]+)?\s*秒)"
)
PLACEHOLDER_RE = re.compile(
    r"(?:TODO|TBD|待补充|待填写|请填写|填入用户|占位符|\[(?:X|填写|待补)[^\]]*\])",
    re.IGNORECASE,
)


@dataclass
class Finding:
    level: str
    message: str


def parse_duration(value: str) -> float | None:
    value = re.sub(r"\s+", "", value)
    if re.fullmatch(r"\d+:\d{1,2}", value):
        minutes, seconds = value.split(":")
        return int(minutes) * 60 + int(seconds)

    minute_match = re.search(r"(\d+(?:\.\d+)?)分(?:钟)?", value)
    second_match = re.search(r"(\d+(?:\.\d+)?)秒", value)
    if minute_match or second_match:
        minutes = float(minute_match.group(1)) if minute_match else 0.0
        seconds = float(second_match.group(1)) if second_match else 0.0
        return minutes * 60 + seconds
    return None


def duration_from_text(text: str) -> float | None:
    match = DURATION_FIELD_RE.search(text)
    return parse_duration(match.group(1)) if match else None


def split_by_matches(text: str, matches: list[re.Match[str]]) -> list[tuple[re.Match[str], str]]:
    blocks: list[tuple[re.Match[str], str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match, text[match.start():end]))
    return blocks


def validate_script(text: str) -> tuple[list[Finding], list[float]]:
    findings: list[Finding] = []
    matches = list(SCENE_MARKER_RE.finditer(text))
    if not matches:
        return [Finding("ERROR", "未找到任何 `【场次】` 标记。")], []

    numbers = [int(match.group(1)) for match in matches]
    expected = list(range(numbers[0], numbers[0] + len(numbers)))
    if numbers != expected:
        findings.append(
            Finding("ERROR", f"场次编号不连续：实际为 {numbers}，期望为 {expected}。")
        )
    if numbers[0] != 1:
        findings.append(Finding("WARNING", f"首个场次为第 {numbers[0]} 场，不是第 1 场。"))

    durations: list[float] = []
    required_labels = ("【场景】", "【人物】", "【预计时长】", "【具体内容】")
    for match, block in split_by_matches(text, matches):
        scene_number = int(match.group(1))
        for label in required_labels:
            if label not in block:
                findings.append(Finding("ERROR", f"第 {scene_number} 场缺少字段 {label}。"))
        duration = duration_from_text(block)
        if duration is None:
            findings.append(Finding("ERROR", f"第 {scene_number} 场的时长无法解析。"))
        elif duration <= 0:
            findings.append(Finding("ERROR", f"第 {scene_number} 场的时长必须大于 0。"))
        else:
            durations.append(duration)

        content_index = block.find("【具体内容】")
        if content_index >= 0:
            content = block[content_index + len("【具体内容】"):].strip("*：: \r\n")
            if not content:
                findings.append(Finding("ERROR", f"第 {scene_number} 场没有具体内容。"))

    return findings, durations


def validate_outline(text: str) -> tuple[list[Finding], list[float]]:
    findings: list[Finding] = []
    matches = list(UNIT_MARKER_RE.finditer(text))
    if not matches:
        return [Finding("ERROR", "未找到幕、章、单元或节拍标题。")], []

    durations: list[float] = []
    required_labels = ("预估时长", "涉及人物", "涉及场景", "核心事件")
    for match, block in split_by_matches(text, matches):
        title = match.group(1)
        for label in required_labels:
            if label not in block:
                findings.append(Finding("ERROR", f"{title} 缺少字段“{label}”。"))
        if "转折或状态变化" not in block:
            findings.append(Finding("WARNING", f"{title} 未写明“转折或状态变化”。"))
        duration = duration_from_text(block)
        if duration is None:
            findings.append(Finding("ERROR", f"{title} 的时长无法解析。"))
        elif duration <= 0:
            findings.append(Finding("ERROR", f"{title} 的时长必须大于 0。"))
        else:
            durations.append(duration)

    return findings, durations


def declared_total_duration(text: str) -> float | None:
    for line in text.splitlines():
        if "总时长" in line or "目标时长" in line:
            duration = duration_from_text(line)
            if duration is not None:
                return duration
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="UTF-8 Markdown screenplay or outline")
    parser.add_argument(
        "--mode",
        choices=("auto", "outline", "script"),
        default="auto",
        help="Validation mode; auto detects scene markers",
    )
    parser.add_argument("--target-seconds", type=float, help="Expected total duration in seconds")
    parser.add_argument(
        "--tolerance-seconds",
        type=float,
        default=1.0,
        help="Allowed difference from target duration (default: 1)",
    )
    parser.add_argument("--report", action="store_true", help="Print summary even when valid")
    args = parser.parse_args()

    try:
        text = args.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"ERROR: 文件不存在：{args.path}")
        return 2
    except UnicodeDecodeError:
        print(f"ERROR: 文件不是有效的 UTF-8：{args.path}")
        return 2

    mode = args.mode
    if mode == "auto":
        mode = "script" if SCENE_MARKER_RE.search(text) else "outline"

    if mode == "script":
        findings, durations = validate_script(text)
    else:
        findings, durations = validate_outline(text)

    if PLACEHOLDER_RE.search(text):
        findings.append(Finding("ERROR", "文档中仍有 TODO、待填写或占位符。"))

    target = args.target_seconds
    if target is None:
        target = declared_total_duration(text)

    total = sum(durations)
    if target is not None and abs(total - target) > args.tolerance_seconds:
        findings.append(
            Finding(
                "ERROR",
                f"分项时长合计 {total:g} 秒，与目标 {target:g} 秒相差 "
                f"{abs(total - target):g} 秒。",
            )
        )

    for finding in findings:
        print(f"{finding.level}: {finding.message}")

    errors = sum(finding.level == "ERROR" for finding in findings)
    warnings = sum(finding.level == "WARNING" for finding in findings)
    if args.report or findings:
        target_text = f"，目标 {target:g} 秒" if target is not None else ""
        print(
            f"SUMMARY: mode={mode}，单元数={len(durations)}，"
            f"分项时长合计={total:g} 秒{target_text}，"
            f"errors={errors}，warnings={warnings}"
        )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
