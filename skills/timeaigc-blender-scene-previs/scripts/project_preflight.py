"""CLI emits reviewable questions; exit 2 means wait for project-owner input."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from character_registry import (PreflightBlocked, load_project_context,
                                mark_stale_previews, resolve_clip_actors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--scene-id")
    parser.add_argument("--clip-actors", type=Path, help="JSON file containing actor reference list")
    parser.add_argument("--output", type=Path, help="optional NEW JSON report file; existing paths are never overwritten")
    parser.add_argument("--mark-stale", action="store_true", help="append stale events to project index only")
    args = parser.parse_args(argv)
    output_handle = None
    if args.output:
        # Reserve exclusively BEFORE --mark-stale or any other project mutation.
        # Checking exists() then write_text() has a race and follows symlinks;
        # mode x atomically refuses every existing path, including link aliases.
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            output_handle = args.output.open("x", encoding="utf-8")
        except OSError as exc:
            report = {"schema": "timeaigc-preflight-report/v1", "status": "invalid_output_path",
                      "generation_allowed": False, "error": "Report output must be a new file; existing files are never overwritten.",
                      "detail": str(exc), "project_mutations_performed": False}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
    stale_report = None
    try:
        if args.mark_stale:
            stale_report = mark_stale_previews(args.project_root)
        context = load_project_context(args.project_root, scene_id=args.scene_id)
        report = {"schema": "timeaigc-preflight-report/v1", "status": "ready",
                  "generation_allowed": True, "questions": [],
                  "project_id": context["registry"]["project_id"],
                  "whole_project_character_count": len(context["registry"]["characters"]),
                  "source_document": context["source_document"],
                  "scene_id": context["scene_id"], "scene_units": context["scene_units"],
                  "blender_calibration_pending": True}
        if args.clip_actors:
            actors = json.loads(args.clip_actors.read_text(encoding="utf-8-sig"))
            resolved, binding = resolve_clip_actors(args.project_root, actors, scene_id=args.scene_id)
            report.update(resolved_actors=resolved, character_binding=binding,
                          loaded_clip_actor_count=len(resolved))
        code = 0
    except PreflightBlocked as exc:
        report, code = exc.report, 2
    except (ValueError, OSError, RuntimeError) as exc:
        report, code = {"schema": "timeaigc-preflight-report/v1", "status": "invalid_project_data",
                        "generation_allowed": False, "error": str(exc)}, 2
    if stale_report is not None:
        report["stale_check"] = stale_report
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_handle:
        try:
            output_handle.write(text)
            output_handle.flush()
        except OSError as exc:
            print(json.dumps({"schema": "timeaigc-preflight-report/v1", "status": "report_write_failed",
                              "generation_allowed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        finally:
            output_handle.close()
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
