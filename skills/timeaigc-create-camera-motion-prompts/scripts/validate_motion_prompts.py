#!/usr/bin/env python3
"""Validate the structure and controlled camera vocabulary of prompt output."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ALLOWED_TERMS = (
    "固定镜头",
    "摇镜头",
    "俯仰镜头",
    "推镜头",
    "拉镜头",
    "跟镜头",
    "环绕镜头",
    "横移镜头",
    "升降镜头",
    "变焦镜头",
    "移焦镜头",
    "手持镜头",
    "摇臂镜头",
    "航拍镜头",
    "FPV 镜头",
    "FPV镜头",
    "索道摄影",
)

SPATIAL_OR_ROTATIONAL_TERMS = (
    "摇镜头",
    "俯仰镜头",
    "推镜头",
    "拉镜头",
    "跟镜头",
    "环绕镜头",
    "横移镜头",
    "升降镜头",
    "手持镜头",
    "摇臂镜头",
    "航拍镜头",
    "FPV 镜头",
    "FPV镜头",
    "索道摄影",
)

AMBIGUOUS_CAMERA_NAMES = (
    "推拉拍摄",
    "旋转运镜",
    "镜头飞舞",
    "慢推镜头",
    "快推镜头",
    "甩镜头",
    "轨道镜头",
)

PLACEHOLDER_RE = re.compile(r"\[[^\]]*待填[^\]]*\]|<[^>]+>|TODO|占位符", re.IGNORECASE)
HEADING_RE = re.compile(r"(?m)^###\s*方案\s*(\d+)\s*[｜|:：]\s*(.+?)\s*$")
FENCE_RE = re.compile(r"(?ms)^```[^\n]*\n(.*?)^```\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="UTF-8 Markdown file to validate")
    parser.add_argument(
        "--expected",
        type=int,
        default=3,
        help="expected number of prompt variants (default: 3)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print successful checks as well as errors and warnings",
    )
    return parser.parse_args()


def add(items: list[str], level: str, message: str) -> None:
    items.append(f"{level}: {message}")


def main() -> int:
    args = parse_args()
    findings: list[str] = []

    if args.expected < 1:
        add(findings, "ERROR", "--expected 必须大于零")
    if not args.path.is_file():
        add(findings, "ERROR", f"文件不存在：{args.path}")
        print("\n".join(findings))
        return 1

    try:
        text = args.path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        add(findings, "ERROR", f"文件不是有效的 UTF-8：{exc}")
        print("\n".join(findings))
        return 1

    headings = HEADING_RE.findall(text)
    blocks = [block.strip() for block in FENCE_RE.findall(text)]

    if len(headings) != args.expected:
        add(
            findings,
            "ERROR",
            f"方案标题数量为 {len(headings)}，期望 {args.expected}",
        )
    else:
        numbers = [int(number) for number, _ in headings]
        expected_numbers = list(range(1, args.expected + 1))
        if numbers != expected_numbers:
            add(
                findings,
                "ERROR",
                f"方案编号应连续为 {expected_numbers}，实际为 {numbers}",
            )

    if len(blocks) != args.expected:
        add(
            findings,
            "ERROR",
            f"代码块数量为 {len(blocks)}，期望 {args.expected}",
        )

    if PLACEHOLDER_RE.search(text):
        add(findings, "ERROR", "输出中残留占位符")

    for index, block in enumerate(blocks, start=1):
        label = f"方案{index}"
        normalized = block.lstrip("\ufeff")

        if not normalized:
            add(findings, "ERROR", f"{label} 的代码块为空")
            continue
        if "\n\n" in normalized or "\r\n\r\n" in normalized:
            add(findings, "ERROR", f"{label} 不是单段提示词")
        if re.search(r"(^|\n)\s*(?:[-*+] |\d+[.)] )", normalized):
            add(findings, "ERROR", f"{label} 的代码块内包含列表")
        if "**" in normalized or "__" in normalized:
            add(findings, "ERROR", f"{label} 的代码块内包含 Markdown 强调")
        if not any(term in normalized for term in ALLOWED_TERMS):
            add(findings, "ERROR", f"{label} 未使用受控运镜术语")

        if "固定镜头" in normalized:
            if not normalized.startswith("固定镜头"):
                add(findings, "ERROR", f"{label} 的“固定镜头”未写在开头")
            conflicts = [
                term for term in SPATIAL_OR_ROTATIONAL_TERMS if term in normalized
            ]
            if conflicts:
                add(
                    findings,
                    "ERROR",
                    f"{label} 同时使用固定镜头与不兼容术语：{', '.join(conflicts)}",
                )

        ambiguous = [term for term in AMBIGUOUS_CAMERA_NAMES if term in normalized]
        if ambiguous:
            add(
                findings,
                "WARNING",
                f"{label} 使用了非受控或含义不清的运镜名称：{', '.join(ambiguous)}",
            )

        english_probe = re.sub(r"\bFPV\b", "", normalized, flags=re.IGNORECASE)
        english_words = sorted(set(re.findall(r"[A-Za-z]+", english_probe)))
        if english_words:
            add(
                findings,
                "WARNING",
                f"{label} 含英文字符，请确认来自用户原文：{', '.join(english_words)}",
            )

    errors = [item for item in findings if item.startswith("ERROR:")]
    warnings = [item for item in findings if item.startswith("WARNING:")]

    if findings:
        print("\n".join(findings))
    if args.report:
        print(
            f"SUMMARY: headings={len(headings)}, blocks={len(blocks)}, "
            f"errors={len(errors)}, warnings={len(warnings)}"
        )
    elif not findings:
        print("OK")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

