#!/usr/bin/env python3
"""Validate Seedance 2.5 step-one segment plans."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


SEGMENT_RE = re.compile(
    r"^\s*##\s*片段\s*(?P<number>\d+)\s*[｜|]\s*(?P<name>.+?)\s*$"
)
FIELD_RE = re.compile(
    r"^\s*-\s*(?P<name>时长|场景设定|出场角色|角色状态|剧情与对白大纲)"
    r"\s*[：:]\s*(?P<value>.*)$"
)
DURATION_RE = re.compile(r"(?P<duration>\d+(?:\.\d+)?)\s*秒")
QUOTE_RE = re.compile(r"[“\"](?P<text>[^\n]*)[”\"]")
SPEECH_PREFIX_RE = re.compile(
    r"【(?:对白|OS|旁白)】[^“”\"\n]{0,60}(?=[“\"])"
)
SPEECH_RE = re.compile(
    r"【(?P<tag>对白|OS|旁白)】[^“”\"\n]{0,60}"
    r"[“\"](?P<text>[^\n]*)[”\"]"
)
SPEECH_TAG_RE = re.compile(r"【(?:对白|OS|旁白)】")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
READABLE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]")
REQUIRED_FIELDS = ("时长", "场景设定", "出场角色", "角色状态", "剧情与对白大纲")


@dataclass
class Segment:
    number: int
    name: str
    line_no: int
    fields: dict[str, str] = field(default_factory=dict)
    field_lines: dict[str, int] = field(default_factory=dict)


def parse_segments(text: str) -> list[Segment]:
    segments: list[Segment] = []
    current: Segment | None = None
    current_field: str | None = None

    for line_no, line in enumerate(text.splitlines(), start=1):
        heading = SEGMENT_RE.match(line)
        if heading:
            current = Segment(
                number=int(heading.group("number")),
                name=heading.group("name").strip(),
                line_no=line_no,
            )
            segments.append(current)
            current_field = None
            continue

        if re.match(r"^\s*#{1,6}\s+", line):
            current_field = None
            continue

        if current is None:
            continue

        field_match = FIELD_RE.match(line)
        if field_match:
            current_field = field_match.group("name")
            current.fields[current_field] = field_match.group("value").strip()
            current.field_lines[current_field] = line_no
            continue

        if current_field and line.strip():
            current.fields[current_field] = (
                f"{current.fields[current_field]}\n{line.strip()}".strip()
            )

    return segments


def outline_cjk_count(outline: str) -> int:
    without_prefix = SPEECH_PREFIX_RE.sub("", outline)
    without_quotes = QUOTE_RE.sub("", without_prefix)
    without_tags = SPEECH_TAG_RE.sub("", without_quotes)
    return len(CJK_RE.findall(without_tags))


def speech_metrics(outline: str) -> tuple[int, int]:
    matches = list(SPEECH_RE.finditer(outline))
    readable_count = sum(len(READABLE_RE.findall(match.group("text"))) for match in matches)
    return len(matches), readable_count


def validate(
    segments: list[Segment],
    duration_min: float,
    preferred_max: float,
    hard_max: float,
    outline_min: int,
    outline_max: int,
    seconds_per_char: float,
    speech_headroom: float,
) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    reports: list[str] = []

    if not segments:
        errors.append("未找到步骤一片段标题；应使用“## 片段 01｜名称”。")
        return errors, warnings, reports

    expected_number = 1
    for segment in segments:
        label = f"片段 {segment.number:02d}“{segment.name}”（标题行 {segment.line_no}）"
        if segment.number != expected_number:
            errors.append(
                f"{label}编号不连续；此处应为片段 {expected_number:02d}。"
            )
            expected_number = segment.number
        expected_number += 1

        missing = [name for name in REQUIRED_FIELDS if not segment.fields.get(name, "").strip()]
        if missing:
            errors.append(f"{label}缺少字段或内容为空：{'、'.join(missing)}。")
            continue

        duration_match = DURATION_RE.search(segment.fields["时长"])
        if not duration_match:
            errors.append(f"{label}无法解析时长；应写成“27.5 秒”。")
            continue

        duration = float(duration_match.group("duration"))
        if duration < duration_min:
            errors.append(
                f"{label}时长为 {duration:g} 秒；应在 "
                f"{duration_min:g}–{preferred_max:g} 秒之间，"
                f"或显式使用不超过 {hard_max:g} 秒的延长片段。"
            )
        elif duration > hard_max:
            errors.append(
                f"{label}时长为 {duration:g} 秒，超过 Seedance 2.5 "
                f"{hard_max:g} 秒硬上限。"
            )
        elif duration > preferred_max:
            if "延长片段" not in segment.fields["时长"]:
                errors.append(
                    f"{label}时长为 {duration:g} 秒，超过标准片段上限 "
                    f"{preferred_max:g} 秒；请在时长字段标注“延长片段”并说明必要性。"
                )
            else:
                warnings.append(
                    f"{label}使用 {duration:g} 秒延长片段；"
                    "请人工确认内容、转场余量和尾部收束确有需要。"
                )

        outline = segment.fields["剧情与对白大纲"]
        narrative_count = outline_cjk_count(outline)
        speech_count, readable_count = speech_metrics(outline)
        speech_seconds = readable_count * seconds_per_char
        tagged_count = len(SPEECH_TAG_RE.findall(outline))

        if narrative_count < outline_min:
            if "【素材不足】" in outline:
                warnings.append(
                    f"{label}非对白叙述约 {narrative_count} 个中文字符，"
                    f"少于 {outline_min}；已标注素材不足，请人工确认。"
                )
            else:
                errors.append(
                    f"{label}非对白叙述约 {narrative_count} 个中文字符；"
                    f"应为 {outline_min}–{outline_max}，或明确标注【素材不足】。"
                )
        elif narrative_count > outline_max:
            errors.append(
                f"{label}非对白叙述约 {narrative_count} 个中文字符；"
                f"应为 {outline_min}–{outline_max}。"
            )

        if tagged_count != speech_count:
            errors.append(
                f"{label}发现 {tagged_count} 个对白/OS/旁白标签，"
                f"但只解析到 {speech_count} 段规范引号原文；"
                "请使用【对白】角色：“原文”等格式。"
            )

        if speech_seconds > duration:
            errors.append(
                f"{label}对白基线约 {speech_seconds:.1f} 秒"
                f"（{readable_count} 个可朗读字符），超过片段时长 {duration:g} 秒。"
            )
        elif speech_count and duration - speech_seconds < speech_headroom:
            warnings.append(
                f"{label}对白基线约 {speech_seconds:.1f} 秒，"
                f"距离片段时长 {duration:g} 秒不足 {speech_headroom:g} 秒；"
                "请人工复核停顿、情绪和动作同步。"
            )

        reports.append(
            f"{label}：非对白叙述 {narrative_count} 字；"
            f"对白 {speech_count} 段/{readable_count} 个可朗读字符/"
            f"基线 {speech_seconds:.1f} 秒；片段 {duration:g} 秒。"
        )

    return errors, warnings, reports


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查步骤一片段规划的字段、时长、非对白字数与对白时长。"
    )
    parser.add_argument("input", nargs="?", default="-", help="UTF-8 Markdown 文件")
    parser.add_argument("--duration-min", type=float, default=27.0, help="标准片段最短秒数")
    parser.add_argument("--preferred-max", type=float, default=28.0, help="标准片段建议最长秒数")
    parser.add_argument("--hard-max", type=float, default=30.0, help="单条视频硬上限秒数")
    parser.add_argument("--outline-min", type=int, default=300, help="非对白叙述最少汉字数")
    parser.add_argument("--outline-max", type=int, default=500, help="非对白叙述最多汉字数")
    parser.add_argument(
        "--seconds-per-char",
        type=float,
        default=0.3,
        help="每个可朗读字符的对白基线秒数",
    )
    parser.add_argument(
        "--speech-headroom",
        type=float,
        default=2.0,
        help="低于此剩余秒数时警告",
    )
    parser.add_argument("--report", action="store_true", help="输出每片段计数报告")
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
    if args.outline_min <= 0 or args.outline_max < args.outline_min:
        print("ERROR: 非对白叙述字数范围无效。", file=sys.stderr)
        return 2
    if args.seconds_per_char <= 0 or args.speech_headroom < 0:
        print("ERROR: 对白计时参数无效。", file=sys.stderr)
        return 2

    try:
        text = read_input(args.input)
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 无法读取输入：{exc}", file=sys.stderr)
        return 2

    errors, warnings, reports = validate(
        parse_segments(text),
        duration_min=args.duration_min,
        preferred_max=args.preferred_max,
        hard_max=args.hard_max,
        outline_min=args.outline_min,
        outline_max=args.outline_max,
        seconds_per_char=args.seconds_per_char,
        speech_headroom=args.speech_headroom,
    )

    if args.report:
        for message in reports:
            print(f"INFO: {message}")
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
