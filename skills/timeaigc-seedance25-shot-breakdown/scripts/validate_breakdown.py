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
QUOTE_RE = re.compile(r"[“\"]([^”\"]*)[”\"]")
SPEECH_PREFIX_RE = re.compile(r"【(?:对白|OS|旁白)(?:/OS)?】[^“”\"\n]{0,40}(?=[“\"])")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
REQUIRED_FIELDS = ("影调氛围", "出场角色", "所在场景")
FORBIDDEN_FIELDS = ("影像风格",)
POSITION_PATTERNS = {
    "left": re.compile(r"画面左|左侧|左边|左上|左下"),
    "right": re.compile(r"画面右|右侧|右边|右上|右下"),
    "top": re.compile(r"画面上|上方|上部|顶部|左上|右上"),
    "bottom": re.compile(r"画面下|下方|下部|底部|左下|右下"),
    "center": re.compile(r"画面中央|画面中心|正中|中轴"),
    "foreground": re.compile(r"前景"),
    "background": re.compile(r"背景|后景"),
}


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
    segments: list[Segment] = []
    current: Segment | None = None

    for line_no, line in enumerate(text.splitlines(), start=1):
        heading = SEGMENT_RE.match(line)
        if heading:
            current = Segment(name=heading.group(1).strip(), line_no=line_no)
            segments.append(current)
            continue

        if current is None:
            continue

        shot_match = SHOT_RE.match(line)
        if shot_match:
            current.shots.append(
                Shot(
                    line_no=line_no,
                    start=float(shot_match.group("start")),
                    end=float(shot_match.group("end")),
                    body=shot_match.group("body"),
                )
            )
            continue

        if not current.shots:
            for required in REQUIRED_FIELDS:
                if re.match(rf"^\s*{required}\s*[：:]", line):
                    current.fields.add(required)
            for forbidden in FORBIDDEN_FIELDS:
                if re.match(rf"^\s*{forbidden}\s*[：:]", line):
                    current.forbidden_fields.add(forbidden)

    return segments


def cjk_visual_count(body: str) -> int:
    without_camera = CAMERA_RE.sub("", body, count=1)
    without_speech_prefix = SPEECH_PREFIX_RE.sub("", without_camera)
    without_spoken_words = QUOTE_RE.sub("", without_speech_prefix)
    return len(CJK_RE.findall(without_spoken_words))


def spoken_cjk_count(body: str) -> int:
    return sum(len(CJK_RE.findall(match)) for match in QUOTE_RE.findall(body))


def composition_problem(body: str) -> str | None:
    positions = {name for name, pattern in POSITION_PATTERNS.items() if pattern.search(body)}

    if re.search(r"(?<!非)对称构图", body):
        horizontal = {"left", "right"} <= positions
        vertical = {"top", "bottom"} <= positions
        if not (horizontal or vertical):
            return "对称构图必须写明左右两侧或上下区域的具体内容。"

    if "三角构图" in body and len(positions) < 3:
        return "三角构图必须写明至少三个视觉支点的位置。"

    if "留白构图" in body:
        has_space_material = re.search(r"暗墙|雾气|天空|走廊|空区|负空间|阴影|虚焦|环境", body)
        if len(positions) < 2 or not has_space_material:
            return "留白构图必须写明主体位置、留白区域及承载留白的环境内容。"

    if "过肩" in body:
        has_foreground_shoulder = re.search(r"前景.{0,16}(肩|肩背|轮廓)|(肩|肩背|轮廓).{0,16}前景", body)
        if not has_foreground_shoulder or len(positions) < 2:
            return "过肩镜头必须写明前景肩背位置、遮挡关系和焦点人物位置。"

    return None


def validate(
    segments: list[Segment],
    duration_min: float,
    preferred_max: float,
    hard_max: float,
    tolerance: float,
    visual_min: int = 120,
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

            visual_count = cjk_visual_count(shot.body)
            if not visual_min <= visual_count <= visual_max:
                errors.append(
                    f"{shot_label}视觉执行描述约 {visual_count} 个中文字符；"
                    f"应为 {visual_min}–{visual_max}。"
                )

            composition_issue = composition_problem(shot.body)
            if composition_issue:
                errors.append(f"{shot_label}{composition_issue}")
            if "中心构图" in shot.body and not re.search(
                r"仪式|秩序|压迫|正面对峙|对峙|孤立|喜剧|反差", shot.body
            ):
                warnings.append(
                    f"{shot_label}使用中心构图但未写明叙事理由；"
                    "请确认是否应改为三分、非对称、留白或前景遮挡。"
                )

            duration = shot.end - shot.start
            spoken_count = spoken_cjk_count(shot.body)
            estimated_speech = spoken_count * 0.3
            if spoken_count == 0 and duration > 3 + tolerance:
                warnings.append(
                    f"{shot_label}无对白但时长 {duration:g} 秒；通常不应超过 3 秒。"
                )
            if estimated_speech > duration + 0.5:
                warnings.append(
                    f"{shot_label}对白基线约 {estimated_speech:.1f} 秒，"
                    f"超过镜头时长 {duration:g} 秒。"
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
    parser.add_argument("--visual-min", type=int, default=120, help="单镜头视觉描述最少中文字符数")
    parser.add_argument("--visual-max", type=int, default=180, help="单镜头视觉描述最多中文字符数")
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
    if args.visual_min <= 0 or args.visual_max < args.visual_min:
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
