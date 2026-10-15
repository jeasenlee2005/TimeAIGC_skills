#!/usr/bin/env python3
"""Append a review-only Chinese text panel to an existing clean PNG sequence.

No scene is rendered and no video encoder is invoked. Source frame 00001.png is
timeline frame 0. Shots use integer, zero-based, half-open [start_frame,end_frame)
intervals. The full clean image is copied at (0,0), without resizing or cropping.
Both RGB and RGBA sources retain their original pixel channels exactly.

Example:
  python dual_video.py --spec scene-v2.json --clean-frames frames \
    --output review_frames --font C:/Windows/Fonts/msyh.ttc

Use a fresh output directory for each run. The report explicitly marks every
output frame as review-only; only the original clean sequence may be a video
generation reference. MP4 encoding is deliberately a separate downstream step.
Compact mode (default) chooses the smallest fixed panel that fits the complete
film and timed, lossless dialogue pages. It fails if a shot is too short for the configured reading
budget. Use --mode expanded for an unchanged-timeline full-text review that may
require pausing. Neither mode extends the original timeline or edits dialogue.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFont


# Basic Chinese line-breaking prohibitions for soft wraps. Explicit source
# newlines remain authoritative and are never moved or removed.
NO_LINE_START = frozenset("、。，．！？：；）》】」』〕〉〗〙〛’”»％‰…,.!?;:%)]}")
NO_LINE_END = frozenset("（【《〈「『〔〖〘〚‘“«([{")
# A deliberately conservative review heuristic, not a universal speech model.
# Counts include punctuation and speaker labels; actual delivery needs a read.
DIALOGUE_WARNING_CPS = 6.0
USAGE_HINT = "预览视频仅供观看/上传生成请用干净版"


class ValidationError(ValueError):
    """Input or output safety validation failed."""


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer (booleans are invalid)")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _positive_number(value: Any, name: str, maximum: float = 120.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a finite positive number")
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValidationError(f"{name} must be greater than zero and at most {maximum}")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if len(value) > 100_000:
        raise ValidationError(f"{name} exceeds the explicit 100000-character limit")
    # Newlines are retained exactly. Tabs and invisible controls are rejected
    # rather than rendered inconsistently or silently removed.
    for char in value:
        if (ord(char) < 32 and char not in "\r\n") or 0x7F <= ord(char) < 0xA0:
            raise ValidationError(f"{name} contains an unsupported control character")
        if 0xD800 <= ord(char) <= 0xDFFF:
            raise ValidationError(f"{name} contains an invalid Unicode surrogate")
    return value


def validate_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValidationError("spec must be a JSON object")
    required = ("width", "height", "fps", "frame_count", "shots")
    missing = [name for name in required if name not in spec]
    if missing:
        raise ValidationError("missing spec fields: " + ", ".join(missing))
    _integer(spec["width"], "width", 64, 8192)
    _integer(spec["height"], "height", 64, 8192)
    _integer(spec["fps"], "fps", 1, 120)
    count = _integer(spec["frame_count"], "frame_count", 1, 100_000)
    shots = spec["shots"]
    if not isinstance(shots, list) or not shots:
        raise ValidationError("shots must be a nonempty list")
    seen: set[str] = set()
    expected_start = 0
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise ValidationError(f"shots[{index}] must be an object")
        for name in ("id", "start_frame", "end_frame", "summary", "dialogue"):
            if name not in shot:
                raise ValidationError(f"shots[{index}] is missing {name}")
        shot_id = _text(shot["id"], f"shots[{index}].id")
        if not shot_id or len(shot_id) > 80 or "\n" in shot_id or "\r" in shot_id:
            raise ValidationError("shot ids must be nonempty single-line strings of at most 80 characters")
        if shot_id in seen:
            raise ValidationError(f"duplicate shot id: {shot_id}")
        seen.add(shot_id)
        start = _integer(shot["start_frame"], f"{shot_id}.start_frame", 0, count - 1)
        end = _integer(shot["end_frame"], f"{shot_id}.end_frame", 1, count)
        if start != expected_start:
            raise ValidationError(f"{shot_id} starts at {start}; expected {expected_start} (no gaps or overlaps)")
        if end <= start:
            raise ValidationError(f"{shot_id} has an empty or reversed interval")
        expected_start = end
        _text(shot["summary"], f"{shot_id}.summary")
        _text(shot["dialogue"], f"{shot_id}.dialogue")
    if expected_start != count:
        raise ValidationError(f"shots end at {expected_start}; frame_count is {count}")
    return spec


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pixel_hash(im: Image.Image) -> str:
    prefix = f"{im.mode}:{im.width}:{im.height}:".encode("ascii")
    return _sha_bytes(prefix + im.tobytes())


def _line_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    if not text:
        return 0
    left, _top, right, _bottom = font.getbbox(text)
    return max(float(font.getlength(text)), float(right - left))


def wrap_exact(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[dict[str, Any]]:
    """Pixel-width wrapping with reversible source spans, including CR/LF.

    Each line stores text and the exact explicit newline immediately following
    it. Soft wraps have newline="". Joining text+newline recreates the source.
    Whitespace, punctuation and trailing newlines are never stripped. At soft
    wraps, move the boundary backwards to keep closing punctuation off the
    beginning and opening punctuation off the end of a rendered line.
    """
    if max_width <= 0:
        raise ValidationError("text column has no usable width")
    lines: list[dict[str, Any]] = []
    start = 0
    current = ""
    cursor = 0
    while cursor < len(text):
        char = text[cursor]
        if char in "\r\n":
            newline = "\r\n" if char == "\r" and text[cursor:cursor + 2] == "\r\n" else char
            lines.append({"text": current, "newline": newline,
                          "source_start": start, "source_end": cursor + len(newline)})
            cursor += len(newline)
            start = cursor
            current = ""
            continue
        if _line_width(char, font) > max_width:
            raise ValidationError(f"a glyph does not fit the text column: {char!r}")
        if current and _line_width(current + char, font) > max_width:
            boundary = cursor
            while boundary > start:
                preceding = text[start:boundary].rstrip()
                # Look through spaces beyond the current cursor as well: a
                # soft wrap immediately before spaces must not strand the
                # closing punctuation that follows those spaces.
                following_index = boundary
                while (following_index < len(text) and text[following_index].isspace()
                       and text[following_index] not in "\r\n"):
                    following_index += 1
                following = text[following_index:following_index + 1]
                if ((preceding and preceding[-1] in NO_LINE_END)
                        or (following and following[0] in NO_LINE_START)):
                    boundary -= 1
                else:
                    break
            if boundary == start:
                raise ValidationError("text column is too narrow for a punctuation group; "
                                      "increase width or reduce the explicit font size")
            lines.append({"text": text[start:boundary], "newline": "",
                          "source_start": start, "source_end": boundary})
            start = boundary
            current = text[start:cursor]
            if _line_width(current + char, font) > max_width:
                raise ValidationError("text column is too narrow for a punctuation group")
        current += char
        cursor += 1
    lines.append({"text": current, "newline": "", "source_start": start, "source_end": cursor})
    if "".join(line["text"] + line["newline"] for line in lines) != text:
        raise AssertionError("line wrapping lost source text")
    return lines


def dialogue_metrics(dialogue: str, start_frame: int, end_frame: int, fps: int) -> dict[str, Any]:
    """Report dense dialogue without rewriting text or its authorized timeline."""
    duration = (end_frame - start_frame) / fps
    count = sum(not char.isspace() for char in dialogue)
    cps = count / duration
    warning = cps > DIALOGUE_WARNING_CPS
    return {
        "source_characters": len(dialogue),
        "non_whitespace_characters": count,
        "duration_seconds": duration,
        "characters_per_second": round(cps, 3),
        "warning_threshold_characters_per_second": DIALOGUE_WARNING_CPS,
        "warning": warning,
        "warning_message": (f"对白 {count} 个非空白字符 / {duration:.3f} 秒 = {cps:.2f} 字符/秒，"
                            "超过本工具的粗略提示阈值；请暂停阅读说明版，并另行核对朗读或表演时长。"
                            "此警告不修改原分镜时长。") if warning else None,
        "counting_note": "按 Unicode 字符计数，排除空白，包含标点和说话人标记；6 字符/秒仅为审查提示阈值，不是普适语速标准。",
        "timing_modified": False,
    }


def _layout_shot(shot: dict[str, Any], spec: dict[str, Any], font: ImageFont.FreeTypeFont,
                 small_font: ImageFont.FreeTypeFont, padding: int,
                 show_usage_hint: bool = False) -> dict[str, Any]:
    width = spec["width"]
    usable = width - padding * 2
    body_ascent, body_descent = font.getmetrics()
    small_ascent, small_descent = small_font.getmetrics()
    body_step = body_ascent + body_descent + max(2, font.size // 8)
    small_step = small_ascent + small_descent + max(2, small_font.size // 8)
    gap = 3
    y = padding
    draw_rows: list[dict[str, Any]] = []
    text_sections: dict[str, Any] = {}

    def add_lines(lines: list[dict[str, Any]], section: str, small: bool = False) -> None:
        nonlocal y
        chosen_font = small_font if small else font
        ascent = small_ascent if small else body_ascent
        step = small_step if small else body_step
        for line in lines:
            baseline = y + ascent
            bbox = chosen_font.getbbox(line["text"], anchor="ls")
            absolute_bbox = [padding + bbox[0], spec["height"] + baseline + bbox[1],
                             padding + bbox[2], spec["height"] + baseline + bbox[3]]
            row = dict(line, section=section, small=small, x=padding,
                       panel_baseline=baseline, bbox=absolute_bbox)
            if absolute_bbox[0] < 0 or absolute_bbox[2] > width:
                raise ValidationError("font glyphs exceed the allocated horizontal bounds")
            draw_rows.append(row)
            y += step

    start, end = shot["start_frame"], shot["end_frame"]
    header = f"{shot['id']}  ·  {start / spec['fps']:.2f}–{end / spec['fps']:.2f} 秒"
    add_lines(wrap_exact(header, small_font, usable), "review_header", small=True)
    if show_usage_hint:
        add_lines(wrap_exact(USAGE_HINT, small_font, usable), "usage_hint", small=True)
    y += gap
    summary_lines = wrap_exact(shot["summary"], font, usable)
    if shot["summary"]:
        add_lines(summary_lines, "summary")
        y += gap
    text_sections["summary"] = {"source": shot["summary"], "lines": summary_lines,
                                "reconstructed": "".join(x["text"] + x["newline"] for x in summary_lines),
                                "exact": True}
    dialogue_lines = wrap_exact(shot["dialogue"], font, usable)
    # Whitespace-only dialogue is retained and shown, with an explicit silence
    # marker as a separate row; the marker is never inserted into source text.
    if not shot["dialogue"].strip():
        add_lines(wrap_exact("（无对白）", font, usable), "silence_marker")
    if shot["dialogue"]:
        add_lines(dialogue_lines, "dialogue")
    text_sections["dialogue"] = {"source": shot["dialogue"], "lines": dialogue_lines,
                                 "reconstructed": "".join(x["text"] + x["newline"] for x in dialogue_lines),
                                 "exact": True}
    return {"id": shot["id"], "start_frame": start, "end_frame": end,
            "silent": not shot["dialogue"].strip(), "required_panel_height": y + padding,
            "dialogue_metrics": dialogue_metrics(shot["dialogue"], start, end, spec["fps"]),
            "text": text_sections, "rows": draw_rows}


def _page_record(shot: dict[str, Any], index: int, count: int, lines: list[dict[str, Any]],
                 start: int, end: int, fps: int, required_frames: int,
                 reading_rule_applied: bool) -> dict[str, Any]:
    source_start = lines[0]["source_start"] if lines else 0
    source_end = lines[-1]["source_end"] if lines else 0
    source_text = shot["dialogue"][source_start:source_end]
    reconstructed = "".join(line["text"] + line["newline"] for line in lines)
    if source_text != reconstructed:
        raise AssertionError("dialogue page lost source text")
    characters = sum(not char.isspace() for char in source_text)
    return {"page_index": index, "page_number": index + 1, "page_count": count,
            "start_frame": start, "end_frame": end,
            "duration_seconds": (end - start) / fps,
            "source_start": source_start, "source_end": source_end,
            "source_text": source_text, "reconstructed": reconstructed,
            "source_exact": True, "lines": lines,
            "non_whitespace_characters": characters,
            "characters_per_second": round(characters * fps / (end - start), 6),
            "required_reading_frames": required_frames,
            "reading_rule_applied": reading_rule_applied}


def _compact_vertical_metrics(font: ImageFont.FreeTypeFont, small_font: ImageFont.FreeTypeFont,
                              summary_lines: int, hint_lines: int) -> tuple[int, int, int, int]:
    """Shared capacity thresholds; changing panel height never changes wrapping."""
    padding = max(5, font.size // 3)
    body_step = sum(font.getmetrics()) + max(2, font.size // 8)
    small_step = sum(small_font.getmetrics()) + max(2, small_font.size // 8)
    body_y = padding + (1 + hint_lines) * small_step + 3 + summary_lines * small_step
    if summary_lines:
        body_y += 3
    return padding, body_step, small_step, body_y


def _compact_height_candidates(spec: dict[str, Any], font: ImageFont.FreeTypeFont,
                               small_font: ImageFont.FreeTypeFont, max_panel_height: int,
                               show_usage_hint: bool = False) -> list[int]:
    """Every height where any shot gains a dialogue line, in ascending order.

    At intervening integer heights all shots keep the same capacity, pagination,
    text positions and reading budget. Testing these thresholds is exhaustive
    without assuming that an individual pagination strategy is monotone.
    """
    usable = spec["width"] - 2 * max(5, font.size // 3)
    hint_count = len(wrap_exact(USAGE_HINT, small_font, usable)) if show_usage_hint else 0
    # Keep the limit as a final diagnostic attempt when it cannot fit even one
    # line. It must never be used as a precondition for testing smaller panels.
    candidates = {max_panel_height}
    for shot in spec["shots"]:
        summary_count = len(wrap_exact(shot["summary"], small_font, usable)) if shot["summary"] else 0
        padding, body_step, _small_step, body_y = _compact_vertical_metrics(
            font, small_font, summary_count, hint_count)
        candidates.update(range(body_y + padding + body_step, max_panel_height + 1, body_step))
    return sorted(candidates)


def _auto_compact_layouts(spec: dict[str, Any], font: ImageFont.FreeTypeFont,
                          small_font: ImageFont.FreeTypeFont, max_panel_height: int,
                          max_chars_per_second: float, min_page_seconds: float,
                          show_usage_hint: bool = False) -> tuple[int, list[dict[str, Any]]]:
    """Find the globally smallest feasible fixed panel, without binary search."""
    last_error = None
    for height in _compact_height_candidates(spec, font, small_font, max_panel_height, show_usage_hint):
        try:
            layouts = [_layout_compact_shot(shot, spec, font, small_font, height,
                                            max_chars_per_second, min_page_seconds, show_usage_hint)
                       for shot in spec["shots"]]
        except ValidationError as error:
            last_error = error
        else:
            return height, layouts
    assert last_error is not None
    raise last_error


def _layout_compact_shot(shot: dict[str, Any], spec: dict[str, Any], font: ImageFont.FreeTypeFont,
                         small_font: ImageFont.FreeTypeFont, panel_height: int,
                         max_chars_per_second: float, min_page_seconds: float,
                         show_usage_hint: bool = False) -> dict[str, Any]:
    """Plan fixed-height pages and their exact intervals before rendering files.

    The reading budget measures dialogue Unicode characters excluding whitespace,
    including speaker labels/punctuation. Summary is repeated on all pages and is
    limited to two small lines. Silent shots have one marker for their entire
    original interval and do not acquire a minimum dialogue reading time.
    """
    padding = max(5, font.size // 3)
    usable = spec["width"] - 2 * padding
    body_ascent, body_descent = font.getmetrics()
    small_ascent, small_descent = small_font.getmetrics()
    gap = 3
    summary_lines = wrap_exact(shot["summary"], small_font, usable)
    displayed_summary = summary_lines if shot["summary"] else []
    if len(displayed_summary) > 2:
        raise ValidationError(f"{shot['id']}: compact summary needs {len(displayed_summary)} lines "
                              "(maximum 2); use --mode expanded to retain the full text")
    # Reserve one header line. Page counts are checked against its actual width
    # after pagination; a long shot id is never shortened silently.
    hint_lines = wrap_exact(USAGE_HINT, small_font, usable) if show_usage_hint else []
    padding, body_step, small_step, body_y = _compact_vertical_metrics(
        font, small_font, len(displayed_summary), len(hint_lines))
    line_capacity = (panel_height - padding - body_y) // body_step
    if line_capacity < 1:
        raise ValidationError(f"{shot['id']}: compact panel has no room for dialogue; "
                              "increase --panel-height or use --mode expanded")
    dialogue_lines = wrap_exact(shot["dialogue"], font, usable)
    silent = not shot["dialogue"].strip()
    # wrap_exact's final zero-length line accounts for a trailing newline but
    # owns no characters. Retain it in the audit, not as an otherwise empty page.
    page_lines = list(dialogue_lines)
    while len(page_lines) > 1 and page_lines[-1]["source_start"] == page_lines[-1]["source_end"]:
        page_lines.pop()
    fps = spec["fps"]
    minimum_frames = math.ceil(Fraction(str(min_page_seconds)) * fps)
    rate = Fraction(str(max_chars_per_second))

    def required_frames(characters: int) -> int:
        by_rate = (characters * fps * rate.denominator + rate.numerator - 1) // rate.numerator
        return max(minimum_frames, by_rate)

    if silent:
        chunks = [dialogue_lines]
    else:
        # Choose line boundaries that minimize the required integer reading
        # frames, then page count. Blindly filling each page can leave a very
        # short final page whose minimum duration rejects an otherwise feasible
        # shot. This bounded dynamic program rebalances without changing text.
        prefix = [0]
        for line in page_lines:
            prefix.append(prefix[-1] + sum(not char.isspace() for char in line["text"]))
        costs = [(0, 0)] + [(10**18, 10**18)] * len(page_lines)
        previous = [0] * (len(page_lines) + 1)
        for end in range(1, len(page_lines) + 1):
            for start in range(max(0, end - line_capacity), end):
                candidate = (costs[start][0] + required_frames(prefix[end] - prefix[start]),
                             costs[start][1] + 1)
                if candidate < costs[end]:
                    costs[end] = candidate
                    previous[end] = start
        chunks = []
        end = len(page_lines)
        while end:
            start = previous[end]
            chunks.append(page_lines[start:end])
            end = start
        chunks.reverse()
    required: list[int] = []
    for chunk in chunks:
        text = "".join(line["text"] + line["newline"] for line in chunk)
        characters = sum(not char.isspace() for char in text)
        required.append(required_frames(characters) if not silent else 0)
    available = shot["end_frame"] - shot["start_frame"]
    needed = sum(required)
    if needed > available:
        raise ValidationError(f"{shot['id']}: compact pagination needs {needed} frames "
                              f"({needed / fps:.3f}s) for {len(chunks)} page(s), but the original shot "
                              f"has {available} frames ({available / fps:.3f}s); "
                              f"limits are {max_chars_per_second:g} dialogue characters/s and "
                              f"{min_page_seconds:g}s minimum per page. Use --mode expanded; "
                              "the original timeline and dialogue will not be changed")
    # Distribute spare integer frames deterministically and keep every page at
    # or above its own budget. This covers the shot exactly without float times.
    extra_each, extra_remainder = divmod(available - needed, len(chunks))
    pages: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    next_frame = shot["start_frame"]
    for page_index, chunk in enumerate(chunks):
        duration = required[page_index] + extra_each + (page_index < extra_remainder)
        page = _page_record(shot, page_index, len(chunks), chunk, next_frame,
                            next_frame + duration, fps, required[page_index], not silent)
        rows: list[dict[str, Any]] = []

        def add(line: dict[str, Any], section: str, top: int, small: bool) -> None:
            chosen = small_font if small else font
            baseline = top + (small_ascent if small else body_ascent)
            bbox = chosen.getbbox(line["text"], anchor="ls")
            rows.append(dict(line, section=section, small=small, page_index=page_index,
                             x=padding, panel_baseline=baseline,
                             bbox=[padding + bbox[0], spec["height"] + baseline + bbox[1],
                                   padding + bbox[2], spec["height"] + baseline + bbox[3]]))

        header = (f"{shot['id']}  ·  {shot['start_frame'] / fps:.2f}–{shot['end_frame'] / fps:.2f} 秒"
                  f"  ·  {page_index + 1}/{len(chunks)}")
        header_lines = wrap_exact(header, small_font, usable)
        if len(header_lines) != 1:
            raise ValidationError(f"{shot['id']}: compact header does not fit one line; use --mode expanded")
        add(header_lines[0], "review_header", padding, True)
        for index, line in enumerate(hint_lines):
            add(line, "usage_hint", padding + (index + 1) * small_step, True)
        summary_y = padding + (1 + len(hint_lines)) * small_step + gap
        for line in displayed_summary:
            add(line, "summary", summary_y, True)
            summary_y += small_step
        if silent:
            marker_lines = wrap_exact("（无对白）", font, usable)
            if len(marker_lines) > line_capacity:
                raise ValidationError(f"{shot['id']}: compact silence marker does not fit; use --mode expanded")
            for index, line in enumerate(marker_lines):
                add(line, "silence_marker", body_y + index * body_step, False)
        else:
            for index, line in enumerate(chunk):
                add(line, "dialogue", body_y + index * body_step, False)
        page["rows"] = rows
        pages.append(page)
        all_rows.extend(rows)
        next_frame += duration
    if next_frame != shot["end_frame"] or "".join(page["source_text"] for page in pages) != shot["dialogue"]:
        raise AssertionError("compact pages do not cover the original shot and dialogue exactly")
    return {"id": shot["id"], "start_frame": shot["start_frame"], "end_frame": shot["end_frame"],
            "silent": silent, "required_panel_height": panel_height,
            "dialogue_metrics": dialogue_metrics(shot["dialogue"], shot["start_frame"], shot["end_frame"], fps),
            "text": {
                "summary": {"source": shot["summary"], "lines": summary_lines,
                            "reconstructed": "".join(x["text"] + x["newline"] for x in summary_lines), "exact": True},
                "dialogue": {"source": shot["dialogue"], "lines": dialogue_lines,
                             "reconstructed": "".join(x["text"] + x["newline"] for x in dialogue_lines), "exact": True}},
            "rows": all_rows, "pages": pages, "dialogue_lines_per_page": line_capacity,
            "required_reading_frames": needed, "available_frames": available}


def _path_is_inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _validate_paths(spec_path: Path, clean_dir: Path, output_dir: Path) -> None:
    if not clean_dir.is_dir():
        raise ValidationError(f"clean frame directory does not exist: {clean_dir}")
    if output_dir == Path(output_dir.anchor):
        raise ValidationError("output cannot be a filesystem root")
    if _path_is_inside(output_dir, clean_dir) or _path_is_inside(clean_dir, output_dir):
        raise ValidationError("output and clean input directories must not overlap")
    if _path_is_inside(spec_path, output_dir):
        raise ValidationError("output cannot contain the input spec")
    if (clean_dir / "dual-video-report.json").exists() or (clean_dir / "INCOMPLETE.json").exists():
        raise ValidationError("clean input appears to be a review-only or incomplete output sequence")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValidationError("output must be a new or empty directory; existing files are never overwritten")


def _preflight_frames(clean_dir: Path, spec: dict[str, Any]) -> tuple[list[Path], str]:
    expected_names = {f"{index + 1:05d}.png" for index in range(spec["frame_count"])}
    found_names = {path.name for path in clean_dir.iterdir() if path.suffix.lower() == ".png"}
    if found_names != expected_names:
        missing = sorted(expected_names - found_names)[:5]
        extra = sorted(found_names - expected_names)[:5]
        raise ValidationError(f"PNG sequence must exactly match frame_count; missing={missing}, extra={extra}")
    paths = [clean_dir / f"{index + 1:05d}.png" for index in range(spec["frame_count"])]
    mode = ""
    for path in paths:
        if path.is_symlink():
            raise ValidationError(f"frame symlinks are not accepted: {path.name}")
        try:
            with Image.open(path) as im:
                im.load()
                if im.format != "PNG" or im.size != (spec["width"], spec["height"]):
                    raise ValidationError(f"invalid format or dimensions in {path.name}: {im.format}, {im.size}")
                if im.mode not in ("RGB", "RGBA"):
                    raise ValidationError(f"{path.name} must be RGB or RGBA; got {im.mode}")
                if mode and im.mode != mode:
                    raise ValidationError("all source frames must have the same pixel mode")
                mode = im.mode
        except (OSError, Image.DecompressionBombError) as error:
            raise ValidationError(f"unreadable frame {path.name}: {error}") from error
    return paths, mode


def compose(spec_path: str | Path, clean_dir: str | Path, output_dir: str | Path,
            font_path: str | Path, font_size: int | None = None,
            max_panel_height: int = 4096, *, mode: str = "compact", panel_height: int | str | None = None,
            max_chars_per_second: float = 6.0, min_page_seconds: float = 2.0,
            show_usage_hint: bool = False) -> dict[str, Any]:
    """Validate, compose and verify every output PNG; return the audit report."""
    raw_output = Path(output_dir).expanduser()
    # Reject explicitly symlinked output parents before resolving the path.
    for candidate in (raw_output, *raw_output.parents):
        if candidate.is_symlink():
            raise ValidationError("symlinked output directories or parents are not accepted")
    spec_path = Path(spec_path).expanduser().resolve(strict=True)
    clean_dir = Path(clean_dir).expanduser().resolve(strict=True)
    output_dir = raw_output.resolve()
    font_path = Path(font_path).expanduser().resolve(strict=True)
    _validate_paths(spec_path, clean_dir, output_dir)
    with spec_path.open("r", encoding="utf-8-sig") as stream:
        spec = validate_spec(json.load(stream))
    size = _integer(font_size if font_size is not None else max(16, spec["width"] // 40),
                    "font_size", 8, 96)
    _integer(max_panel_height, "max_panel_height", 1, 16384)
    if mode not in ("compact", "expanded"):
        raise ValidationError("mode must be compact or expanded")
    requested_panel_height = (None if panel_height is None or panel_height == "auto"
                              else _integer(panel_height, "panel_height", 1, 16384))
    if type(show_usage_hint) is not bool:
        raise ValidationError("show_usage_hint must be boolean")
    max_chars_per_second = _positive_number(max_chars_per_second, "max_chars_per_second")
    min_page_seconds = _positive_number(min_page_seconds, "min_page_seconds")
    font = ImageFont.truetype(str(font_path), size)
    small_font = ImageFont.truetype(str(font_path), max(10, size * 3 // 4))
    minimum_feasible_panel_height = None
    if mode == "expanded":
        layouts = [_layout_shot(shot, spec, font, small_font, max(5, size // 3), show_usage_hint)
                   for shot in spec["shots"]]
        required_height = max(layout["required_panel_height"] for layout in layouts)
        if requested_panel_height is not None and requested_panel_height < required_height:
            raise ValidationError(f"expanded panel needs {required_height}px to retain all text; "
                                  f"requested panel_height={requested_panel_height} would crop content")
        panel_height = requested_panel_height if requested_panel_height is not None else required_height
        for shot, layout in zip(spec["shots"], layouts):
            page = _page_record(shot, 0, 1, layout["text"]["dialogue"]["lines"],
                                shot["start_frame"], shot["end_frame"], spec["fps"], 0, False)
            page["rows"] = layout["rows"]
            layout["pages"] = [page]
    else:
        def compact_at(height):
            return [_layout_compact_shot(shot, spec, font, small_font, height,
                                          max_chars_per_second, min_page_seconds, show_usage_hint)
                    for shot in spec["shots"]]

        if requested_panel_height is not None:
            panel_height = requested_panel_height
            layouts = compact_at(panel_height)
        else:
            panel_height, layouts = _auto_compact_layouts(
                spec, font, small_font, max_panel_height,
                max_chars_per_second, min_page_seconds, show_usage_hint)
            minimum_feasible_panel_height = panel_height
    # Review height is even to suit common video encoders; never shrink content.
    panel_height += (spec["height"] + panel_height) % 2
    if panel_height > max_panel_height:
        raise ValidationError(f"complete text requires a {panel_height}px panel, exceeding "
                              f"max_panel_height={max_panel_height}; increase the explicit limit or split text by time upstream")
    review_height = spec["height"] + panel_height
    for layout in layouts:
        for row in layout["rows"]:
            left, top, right, bottom = row["bbox"]
            if not (0 <= left <= right <= spec["width"] and spec["height"] <= top <= bottom <= review_height):
                raise ValidationError(f"text bbox exceeds the review panel: {layout['id']} {row['section']}")
    paths, pixel_mode = _preflight_frames(clean_dir, spec)
    # All input, timing and text checks happen before any output is created.
    output_dir.mkdir(parents=True, exist_ok=True)
    incomplete_path = output_dir / "INCOMPLETE.json"
    with incomplete_path.open("x", encoding="utf-8") as stream:
        json.dump({"status": "incomplete", "role": "review-only",
                   "allowed_for_generation": False}, stream)
    if any(path.name != incomplete_path.name for path in output_dir.iterdir()):
        raise ValidationError("output changed during preparation; refusing to overwrite files")
    background = (15, 21, 30, 255) if pixel_mode == "RGBA" else (15, 21, 30)
    foreground = (238, 242, 247, 255) if pixel_mode == "RGBA" else (238, 242, 247)
    muted = (132, 184, 210, 255) if pixel_mode == "RGBA" else (132, 184, 210)
    panels: dict[tuple[str, int], Image.Image] = {}
    for layout in layouts:
        for page in layout["pages"]:
            panel = Image.new(pixel_mode, (spec["width"], panel_height), background)
            draw = ImageDraw.Draw(panel)
            for row in page["rows"]:
                draw.text((row["x"], row["panel_baseline"]), row["text"], anchor="ls",
                          font=small_font if row["small"] else font,
                          fill=muted if row["small"] else foreground)
            panels[(layout["id"], page["page_index"])] = panel
    frames: list[dict[str, Any]] = []
    shot_index = 0
    page_index = 0
    for index, path in enumerate(paths):
        while index >= layouts[shot_index]["end_frame"]:
            shot_index += 1
            page_index = 0
        layout = layouts[shot_index]
        while index >= layout["pages"][page_index]["end_frame"]:
            page_index += 1
        with Image.open(path) as src:
            src.load()
            if src.size != (spec["width"], spec["height"]) or src.mode != pixel_mode:
                raise ValidationError(f"source changed after preflight: {path.name}")
            canvas = Image.new(pixel_mode, (spec["width"], review_height), background)
            canvas.paste(src, (0, 0))
            canvas.paste(panels[(layout["id"], page_index)], (0, spec["height"]))
            target = output_dir / path.name
            temporary = output_dir / (path.name + ".tmp")
            if target.exists() or temporary.exists():
                raise ValidationError(f"refusing to overwrite {target.name}")
            canvas.save(temporary, format="PNG", compress_level=6)
            temporary.replace(target)
            # Check persisted files, not just the in-memory operation.
            with Image.open(target) as persisted:
                persisted.load()
                upper = persisted.crop((0, 0, spec["width"], spec["height"]))
                exact = upper.mode == src.mode and upper.tobytes() == src.tobytes()
                if not exact:
                    raise AssertionError(f"source pixels changed in review frame {path.name}")
                frames.append({"frame_index": index, "file": path.name, "shot_id": layout["id"],
                               "page_index": page_index,
                               "clean_file_sha256": _sha_file(path), "review_file_sha256": _sha_file(target),
                               "clean_pixels_sha256": _pixel_hash(src),
                               "review_upper_pixels_sha256": _pixel_hash(upper),
                               "upper_region_exact": exact})
    report: dict[str, Any] = {
        "schema": "timeaigc-dual-video-report/v2", "status": "passed",
        "role": "review-only", "allowed_for_generation": False,
        "source_role": "clean-reference", "timeline": "integer-frames, zero-based, half-open",
        "spec_path": str(spec_path), "spec_sha256": _sha_file(spec_path),
        "clean_frames": str(clean_dir), "review_frames": str(output_dir),
        "font_path": str(font_path), "font_sha256": _sha_file(font_path),
        "font_size": size, "small_font_size": small_font.size,
        "width": spec["width"], "source_height": spec["height"],
        "review_height": review_height, "panel_height": panel_height,
        "layout_mode": mode,
        "pagination_settings": {
            "panel_height_requested": requested_panel_height,
            "panel_height_selection": "auto" if requested_panel_height is None else "explicit",
            "minimum_feasible_panel_height": minimum_feasible_panel_height,
            "usage_hint_shown": show_usage_hint,
            "usage_hint_text": USAGE_HINT if show_usage_hint else None,
            "max_chars_per_second": max_chars_per_second,
            "min_page_seconds": min_page_seconds,
            "reading_limits_enforced": mode == "compact",
            "counting_rule": "dialogue Unicode characters excluding whitespace, including speaker labels and punctuation",
            "summary_rule": "compact summary is repeated in full on every page, maximum two small lines; not counted as dialogue",
            "silent_shot_rule": "one silence marker for the original interval; dialogue reading limits do not apply",
            "timing_modified": False,
        },
        "fps": spec["fps"], "frame_count": spec["frame_count"], "pixel_mode": pixel_mode,
        "source_region": [0, 0, spec["width"], spec["height"]],
        "cut_frames": [shot["start_frame"] for shot in spec["shots"][1:]],
        "page_change_frames": [page["start_frame"] for layout in layouts for page in layout["pages"]
                               if page["start_frame"] > 0],
        "all_upper_regions_pixel_exact": all(frame["upper_region_exact"] for frame in frames),
        "all_source_text_preserved": all(section["source"] == section["reconstructed"]
                                         for layout in layouts for section in layout["text"].values()),
        "all_page_text_preserved": all("".join(page["source_text"] for page in layout["pages"])
                                        == layout["text"]["dialogue"]["source"] for layout in layouts),
        "all_page_intervals_cover_shots": all(
            layout["pages"][0]["start_frame"] == layout["start_frame"]
            and layout["pages"][-1]["end_frame"] == layout["end_frame"]
            and all(a["end_frame"] == b["start_frame"] for a, b in zip(layout["pages"], layout["pages"][1:]))
            for layout in layouts),
        "all_text_bounds_inside_panel": True,
        "line_break_policy": "Chinese soft-wrap line-start/line-end prohibitions; explicit source newlines preserved",
        "warnings": [{"type": "dialogue_density", "shot_id": layout["id"],
                      "message": layout["dialogue_metrics"]["warning_message"]}
                     for layout in layouts if layout["dialogue_metrics"]["warning"]],
        "encoding_note": "Pixel identity is verified before lossy video encoding. Encode both sequences at this same fps/frame_count; validate encoded media separately.",
        "shots": layouts, "frames": frames,
    }
    report_path = output_dir / "dual-video-report.json"
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    incomplete_path.unlink()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--clean-frames", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="new or empty review PNG directory (never overwrites existing files)")
    parser.add_argument("--font", required=True, type=Path, help="Chinese-capable TTF/TTC/OTF font")
    parser.add_argument("--font-size", type=int, default=None)
    parser.add_argument("--mode", choices=("compact", "expanded"), default="compact")
    def panel_height_argument(value):
        if value.lower() == "auto":
            return None
        try:
            height = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("panel height must be auto or a positive integer") from error
        if height < 1:
            raise argparse.ArgumentTypeError("panel height must be positive")
        return height
    parser.add_argument("--panel-height", type=panel_height_argument, default=None,
                        help="auto (default) chooses the tightest fixed height; a positive pixel value overrides it if all content fits")
    parser.add_argument("--show-usage-hint", action="store_true",
                        help="explicitly show the single supported usage hint; hidden by default")
    parser.add_argument("--max-chars-per-second", type=float, default=6.0,
                        help="compact dialogue reading budget, excluding whitespace (default 6)")
    parser.add_argument("--min-page-seconds", type=float, default=2.0,
                        help="compact minimum duration of each non-silent dialogue page (default 2)")
    parser.add_argument("--max-panel-height", type=int, default=4096)
    args = parser.parse_args(argv)
    try:
        report = compose(args.spec, args.clean_frames, args.output, args.font,
                         args.font_size, args.max_panel_height, mode=args.mode, panel_height=args.panel_height,
                         max_chars_per_second=args.max_chars_per_second, min_page_seconds=args.min_page_seconds,
                         show_usage_hint=args.show_usage_hint)
    except (ValidationError, OSError, json.JSONDecodeError) as error:
        print(f"dual_video: {error}", file=sys.stderr)
        return 2
    print(json.dumps({key: report[key] for key in
                      ("status", "role", "layout_mode", "review_frames", "width", "source_height", "review_height",
                       "panel_height", "fps", "frame_count", "all_upper_regions_pixel_exact",
                       "all_source_text_preserved")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
