#!/usr/bin/env python3
"""Validate structural and timing constraints in Seedance 2.5 shot breakdowns."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


SEGMENT_RE = re.compile(r"^\s*#\s*片段(?:序号)?\s*(?:[：:]|\s)\s*(.+?)\s*$")
SHOT_RE = re.compile(
    r"^\s*(?P<start>\d+(?:\.\d+)?)\s*-\s*"
    r"(?P<end>\d+(?:\.\d+)?)\s*秒\s*[：:]\s*(?P<body>.+?)\s*$"
)
CAMERA_RE = re.compile(r"^【镜头语言[：:].+?】")
QUOTE_RE = re.compile(r"“[^”]*”|‘[^’]*’|\"[^\"]*\"|'[^']*'")
SPEECH_TAG_RE = re.compile(r"【(?:对白|OS|旁白)(?:/OS)?】")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
READABLE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]")
SPEECH_END_RE = re.compile(r"声音连续至\s*(\d+(?:\.\d+)?)\s*秒")
SPEECH_CONTINUITY_RE = re.compile(r"声音连续|声音继续|台词跨镜|对白跨镜|跨镜对白")
REQUIRED_FIELDS = ("影调氛围", "出场角色", "所在场景")
FORBIDDEN_FIELDS = ("影像风格",)


@dataclass
class Shot:
    line_no: int
    start: float
    end: float
    body: str


@dataclass
class Segment:
    name: str
    line_no: int
    fields: set[str] = field(default_factory=set)
    forbidden_fields: set[str] = field(default_factory=set)
    shots: list[Shot] = field(default_factory=list)


def parse_segments(text: str) -> list[Segment]:
    """Read standalone prompts or fenced prompts; keep multiline shot bodies."""
    segments: list[Segment] = []
    current: Segment | None = None
    lines = text.splitlines()
    fenced = any(
        re.search(r"^\s*#\s*片段", block, re.M)
        for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    )
    in_fence = False
    for line_no, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current = None
            continue
        if fenced and not in_fence:
            continue
        heading = SEGMENT_RE.match(line)
        if heading:
            current = Segment(name=heading.group(1).strip(), line_no=line_no)
            segments.append(current)
            continue
        if re.match(r"^\s*#{1,6}\s", line):
            current = None
        if current is None:
            continue
        shot_match = SHOT_RE.match(line)
        if shot_match:
            current.shots.append(Shot(
                line_no=line_no,
                start=float(shot_match.group("start")),
                end=float(shot_match.group("end")),
                body=shot_match.group("body"),
            ))
            continue
        if not current.shots:
            for required in REQUIRED_FIELDS:
                if re.match(rf"^\s*{required}\s*[：:]", line):
                    current.fields.add(required)
            for forbidden in FORBIDDEN_FIELDS:
                if re.match(rf"^\s*{forbidden}\s*[：:]", line):
                    current.forbidden_fields.add(forbidden)
        elif line.strip():
            current.shots[-1].body += "\n" + line.strip()
    return segments


def speech_spans(body: str) -> list[tuple[int, int, str]]:
    """Only tagged quoted speech counts; labels and speaker prefixes are excluded."""
    tags = list(SPEECH_TAG_RE.finditer(body))
    result = []
    for i, tag in enumerate(tags):
        limit = tags[i + 1].start() if i + 1 < len(tags) else len(body)
        quote = QUOTE_RE.search(body, tag.end(), limit)
        if quote is not None:
            result.append((tag.start(), quote.end(), quote.group()[1:-1]))
    return result


def visual_text(body: str) -> str:
    for start, end, _ in reversed(speech_spans(body)):
        body = body[:start] + body[end:]
    return CAMERA_RE.sub("", body, count=1)


def cjk_visual_count(body: str) -> int:
    return len(CJK_RE.findall(visual_text(body)))


def spoken_cjk_count(body: str) -> int:
    # Retain the helper name for callers; the documented count includes letters/digits.
    return sum(len(READABLE_RE.findall(words)) for _, _, words in speech_spans(body))


def validate(
    segments: list[Segment],
    duration_min: float,
    preferred_max: float,
    hard_max: float,
    tolerance: float,
    visual_min: int = 0,
    visual_max: int = 180,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not segments:
        errors.append("未找到片段标题；应使用“# 片段 01：名称”。")
        return errors, warnings

    for segment in segments:
        label = f"片段“{segment.name}”（标题行 {segment.line_no}）"
        missing = [item for item in REQUIRED_FIELDS if item not in segment.fields]
        if missing:
            errors.append(f"{label}缺少顶部字段：{'、'.join(missing)}。")
        if segment.forbidden_fields:
            errors.append(f"{label}不得包含顶部字段：{'、'.join(sorted(segment.forbidden_fields))}。")

        if not segment.shots:
            errors.append(f"{label}没有可解析的镜头。")
            continue

        if abs(segment.shots[0].start) > tolerance:
            errors.append(f"{label}首镜从 {segment.shots[0].start:g} 秒开始；应从 0 秒开始。")

        previous_end: float | None = None
        for shot in segment.shots:
            shot_label = f"{label}第 {shot.line_no} 行"
            if shot.end <= shot.start:
                errors.append(f"{shot_label}结束时间必须晚于开始时间。")

            if previous_end is not None:
                delta = shot.start - previous_end
                if delta > tolerance:
                    errors.append(f"{shot_label}与上一镜之间有 {delta:g} 秒空洞。")
                elif delta < -tolerance:
                    errors.append(f"{shot_label}与上一镜重叠 {-delta:g} 秒。")
            previous_end = shot.end

            if not CAMERA_RE.match(shot.body):
                errors.append(f"{shot_label}没有以前置【镜头语言：…】开头。")

            spans = speech_spans(shot.body)
            if len(spans) != len(SPEECH_TAG_RE.findall(shot.body)):
                errors.append(f"{shot_label}对白标签缺少配对引号原文；请检查格式。")
            visual_count = cjk_visual_count(shot.body)
            if visual_count == 0:
                errors.append(f"{shot_label}缺少视觉执行描述。")
            elif visual_count < visual_min or visual_count > visual_max:
                warnings.append(
                    f"{shot_label}视觉描述约 {visual_count} 个中文字符；"
                    f"参考范围 {visual_min}–{visual_max}，以信息完整和精炼为准，不凑字或删关键动作。"
                )

            duration = shot.end - shot.start
            spoken_count = spoken_cjk_count(shot.body)
            estimated_speech = spoken_count * 0.3
            narrative = visual_text(shot.body)
            continuous = bool(SPEECH_CONTINUITY_RE.search(narrative))
            endpoints = list(SPEECH_END_RE.finditer(narrative))
            available = duration
            if spoken_count and len(spans) == 1 and len(endpoints) == 1:
                speech_end = float(endpoints[0].group(1))
                if speech_end <= shot.start or speech_end > segment.shots[-1].end + tolerance:
                    errors.append(f"{shot_label}声音连续结束时间超出本段可用范围。")
                else:
                    available = speech_end - shot.start
            elif spoken_count and continuous:
                warnings.append(
                    f"{shot_label}含跨镜声音，需人工核对实际覆盖范围及各句顺序；不按单镜强判超时。"
                )
                available = None
            if spoken_count == 0 and not continuous and duration > 3 + tolerance:
                warnings.append(
                    f"{shot_label}未标记对白且时长 {duration:g} 秒；请核对是否有持续动作或情绪变化。"
                )
            if available is not None and estimated_speech > available + 0.5:
                warnings.append(
                    f"{shot_label}对白基线约 {estimated_speech:.1f} 秒，"
                    f"超过可用声音时长 {available:g} 秒；请核对开口时刻、覆盖镜头和停顿。"
                )

        segment_end = segment.shots[-1].end
        if segment_end < duration_min - tolerance:
            errors.append(
                f"{label}时间轴结束于 {segment_end:g} 秒；"
                f"标准片段应至少为 {duration_min:g} 秒。"
            )
        elif segment_end > hard_max + tolerance:
            errors.append(
                f"{label}时间轴结束于 {segment_end:g} 秒，超过 Seedance 2.5 "
                f"{hard_max:g} 秒硬上限。"
            )
        elif segment_end > preferred_max + tolerance:
            warnings.append(
                f"{label}时间轴为 {segment_end:g} 秒，属于延长片段；"
                "请确认步骤一已标注必要性并保留生成余量。"
            )

        if segment_end < 15:
            shot_min, shot_max = 4, 9
        elif segment_end < 27:
            shot_min, shot_max = 6, 12
        else:
            shot_min, shot_max = 8, 16
        if not shot_min <= len(segment.shots) <= shot_max:
            errors.append(
                f"{label}时间轴为 {segment_end:g} 秒，包含 {len(segment.shots)} 个镜头；"
                f"应为 {shot_min}–{shot_max} 个。"
            )

    return errors, warnings


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查 Seedance 2.5 分镜的字段、30 秒上限、时间轴、镜头数与描述字数。"
    )
    parser.add_argument("input", nargs="?", default="-", help="UTF-8 Markdown 文件；默认从 stdin 读取")
    parser.add_argument("--duration-min", type=float, default=27.0, help="标准片段最短秒数")
    parser.add_argument("--preferred-max", type=float, default=28.0, help="标准片段建议最长秒数")
    parser.add_argument("--hard-max", type=float, default=30.0, help="单条视频硬上限秒数")
    parser.add_argument("--visual-min", type=int, default=0, help="视觉字数提醒下限；默认不设最低字数")
    parser.add_argument("--visual-max", type=int, default=180, help="视觉字数提醒上限；超出只提示精简")
    parser.add_argument("--tolerance", type=float, default=0.05, help="时间比较容差")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if (
        args.duration_min <= 0
        or args.preferred_max < args.duration_min
        or args.hard_max < args.preferred_max
        or args.hard_max > 30
    ):
        print("ERROR: 时长范围无效。", file=sys.stderr)
        return 2
    if args.visual_min < 0 or args.visual_max <= 0 or args.visual_max < args.visual_min:
        print("ERROR: 视觉描述字数范围无效。", file=sys.stderr)
        return 2

    try:
        text = read_input(args.input)
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 无法读取输入：{exc}", file=sys.stderr)
        return 2

    errors, warnings = validate(
        parse_segments(text),
        duration_min=args.duration_min,
        preferred_max=args.preferred_max,
        hard_max=args.hard_max,
        tolerance=args.tolerance,
        visual_min=args.visual_min,
        visual_max=args.visual_max,
    )

    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARNING: {message}")

    if errors:
        print(f"FAIL: {len(errors)} 个错误，{len(warnings)} 个警告。")
        return 1
    print(f"PASS: 0 个错误，{len(warnings)} 个警告。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
