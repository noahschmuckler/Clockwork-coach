"""Command line:

    python -m clockwork render --template circuit-45 --seed 4417 [--music off] [--tts espeak]
    python -m clockwork render --request-file issue_body.txt --label 12
    python -m clockwork check            # validate the library and every template
    python -m clockwork plan --template any --seed 7   # print a plan without rendering
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from . import library, planner, request
from .cues import fmt
from .library import LibraryError


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clockwork")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render a 45:00 workout")
    r.add_argument("--request-file", type=Path, help="YAML request (e.g. an issue body)")
    r.add_argument("--template", default="any")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--music", choices=["on", "off"], default="on")
    r.add_argument("--label", default="local")
    r.add_argument("--out", type=Path, default=Path("out"))
    r.add_argument("--tts", choices=["piper", "espeak", "tone"], default="piper")
    r.add_argument("--download-base", default="", help="URL prefix the file will be downloadable from")

    pl = sub.add_parser("plan", help="print the plan for a template and seed")
    pl.add_argument("--template", default="any")
    pl.add_argument("--seed", type=int, default=0)

    f = sub.add_parser("focus", help="render a focus session from a YAML request")
    f.add_argument("--request-file", type=Path, required=True)
    f.add_argument("--label", default="local")
    f.add_argument("--out", type=Path, default=Path("out"))
    f.add_argument("--tts", choices=["piper", "espeak", "tone"], default="piper")
    f.add_argument("--download-base", default="")
    f.add_argument("--music-config", type=Path, help="tracks.yaml listing focus music (private repo)")
    f.add_argument("--music-dir", type=Path, help="folder holding the music files")

    c = sub.add_parser("check", help="validate library and templates")
    c.add_argument("--seeds", type=int, default=200)

    args = p.parse_args(argv)
    try:
        if args.cmd == "render":
            return _render(args)
        if args.cmd == "focus":
            return _focus(args)
        if args.cmd == "plan":
            lib = library.load()
            tpl = planner.choose_template(lib, args.template, args.seed)
            plan = planner.expand_template(lib, tpl, args.seed)
            planner.validate_plan(plan)
            print(f"template {tpl}, seed {args.seed}")
            for item in plan:
                print(f"{fmt(item.start)}  {item.seconds:4d}s  {item.section:<14} {item.block.id}")
            return 0
        if args.cmd == "check":
            lib = library.load()
            for tpl in sorted(lib.templates):
                for seed in range(args.seeds):
                    planner.validate_plan(planner.expand_template(lib, tpl, seed))
            print(f"ok: {len(lib.blocks)} blocks, {len(lib.templates)} templates x {args.seeds} seeds, {len(lib.tracks)} tracks")
            return 0
    except LibraryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


def _render(args) -> int:
    from .render import render

    args.out.mkdir(parents=True, exist_ok=True)
    error_file = args.out / "error.txt"
    error_file.unlink(missing_ok=True)
    try:
        if args.request_file:
            default_seed = int(args.label) if args.label.isdigit() else 0
            req = request.parse(args.request_file.read_text(), default_seed)
        else:
            req = {"template": args.template, "seed": args.seed, "music": args.music, "plan": None}
        manifest = render(req, args.out, args.label, args.tts, args.download_base)
    except LibraryError as exc:
        error_file.write_text(str(exc) + "\n")
        print(f"rejected: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        error_file.write_text(f"render failed: {exc}\n")
        traceback.print_exc()
        return 1
    print(f"ok: {args.out / manifest['file']} ({manifest['template']}, seed {manifest['seed']}, "
          f"{manifest['checks']['decoded_seconds']}s, {len(manifest['cues'])} cues, "
          f"{len(manifest['dropped_cues'])} dropped)")
    return 0


def _focus(args) -> int:
    from . import focus

    args.out.mkdir(parents=True, exist_ok=True)
    error_file = args.out / "error.txt"
    error_file.unlink(missing_ok=True)
    try:
        m = focus.render(args.request_file.read_text(), args.out, args.label, args.tts, args.download_base,
                         args.music_config, args.music_dir)
    except LibraryError as exc:
        error_file.write_text(str(exc) + "\n")
        print(f"rejected: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        error_file.write_text(f"render failed: {exc}\n")
        traceback.print_exc()
        return 1
    print(f"ok: {args.out / m['file']} ({m['total_seconds'] // 60} min, sound {m['sound_used']}, "
          f"{m['checks']['decoded_seconds']}s, {len(m['cues'])} lines, {len(m['dropped_cues'])} left out)")
    for w in m["warnings"]:
        print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
