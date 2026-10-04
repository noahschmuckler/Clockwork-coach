"""Build one workout: plan -> cues -> speech -> music -> mix -> checks -> files."""

from __future__ import annotations

import json
from pathlib import Path

from . import cues as cuelib
from . import library, mixer, music, planner
from .tts import SAMPLE_RATE, Speaker

VERSION = "0.1"


def build_plan(lib: library.Library, request: dict) -> tuple[str, int | None, list[planner.PlanItem]]:
    if request.get("plan") is not None:
        plan = planner.plan_from_custom(lib, request["plan"])
        template, seed = "custom", None
    else:
        seed = request["seed"]
        template = planner.choose_template(lib, request["template"], seed)
        plan = planner.expand_template(lib, template, seed)
    planner.validate_plan(plan)
    return template, seed, plan


def render(request: dict, out_dir: Path, label: str, tts_backend: str = "piper", download_base: str = "") -> dict:
    lib = library.load()
    template, seed, plan = build_plan(lib, request)

    speaker = Speaker(tts_backend)
    cands = cuelib.candidates(plan)
    clips = speak_all(speaker, {c.text for c in cands})
    durations = {text: len(a) / SAMPLE_RATE for text, a in clips.items()}
    placed, dropped = cuelib.place(cands, durations)

    voice = mixer.voice_track(placed, clips)
    bed, music_sections, warnings = None, [], []
    if request["music"] == "on":
        available = music.fetch(lib.tracks)
        if available:
            bed, music_sections = music.build_bed(lib, plan, available, f"{template}:{seed}")
            missing = [s["name"] for s in music_sections if s["track"] is None]
            if missing:
                warnings.append(f"no music available for: {', '.join(missing)}")
        else:
            warnings.append("music could not be downloaded; this file is voice only")
    warnings += speaker.warnings
    master = mixer.mix(voice, bed, placed)

    filename = f"clockwork-{label}.m4a"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / filename
    mixer.encode(master, out_file, f"Clockwork Coach {label} ({template})")
    measured = mixer.checks(master, out_file)

    used_tracks = {s["track"] for s in music_sections if s["track"]}
    tracks = [t for t in lib.tracks if t.id in used_tracks]
    manifest = {
        "version": VERSION,
        "label": label,
        "file": filename,
        "template": template,
        "seed": seed,
        "music": request["music"],
        "voice": speaker.voice,
        "total_seconds": planner.TOTAL_SECONDS,
        "checks": measured,
        "warnings": warnings,
        "library_status": "PLACEHOLDER exercise library - review before relying on it",
        "blocks": [
            {"start": cuelib.fmt(i.start), "end": cuelib.fmt(i.end), "seconds": i.seconds, "block": i.block.id,
             "name": i.block.name, "role": i.block.role, "section": i.section}
            for i in plan
        ],
        "music_sections": [
            {"section": s["name"], "mood": s["mood"], "start": cuelib.fmt(s["start"]), "end": cuelib.fmt(s["end"]), "track": s["track"]}
            for s in music_sections
        ],
        "cues": [
            {"time": round(c.time, 2), "at": cuelib.fmt(c.time), "seconds": round(c.duration, 2), "kind": c.kind, "text": c.text}
            for c in placed
        ],
        "dropped_cues": dropped,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out_dir / "CREDITS.txt").write_text(credits(tracks, speaker.voice))
    (out_dir / "summary.md").write_text(summary(manifest, plan, download_base + filename if download_base else ""))
    return manifest


def speak_all(speaker: Speaker, texts: set[str]) -> dict:
    return {t: speaker.countdown(cuelib.COUNTDOWN_WORDS) if t == cuelib.COUNTDOWN_TEXT else speaker.say(t)
            for t in sorted(texts)}


def credits(tracks: list[library.Track], voice: str) -> str:
    lines = ["Clockwork Coach - credits", ""]
    if tracks:
        lines.append("Music (looped, crossfaded, volume-adjusted and mixed under speech):")
        for t in tracks:
            lines += [f'"{t.title}" {t.artist} (incompetech.com)',
                      f"Licensed under Creative Commons: By Attribution 4.0 License",
                      f"{t.license_url}", f"Source: {t.page}", ""]
    else:
        lines += ["No music in this file.", ""]
    lines.append(f"Voice: Piper text-to-speech ({voice}). Voice model trained on public-domain LibriVox recordings."
                 if voice.startswith("en_US") else f"Voice: {voice}.")
    return "\n".join(lines) + "\n"


def summary(manifest: dict, plan: list[planner.PlanItem], url: str) -> str:
    seed = manifest["seed"] if manifest["seed"] is not None else "-"
    out = []
    if url:
        out += [f"**Workout ready:** [{manifest['file']}]({url})", ""]
    out.append(f"Template `{manifest['template']}` · seed `{seed}` · music {manifest['music']} · 45:00")
    for w in manifest["warnings"]:
        out.append(f"\n⚠️ {w}")
    out += ["", "| Starts | Section | Activities |", "|---|---|---|"]
    for sec in music.sections(plan):
        rows: dict[str, list] = {}  # block id -> [name, seconds, times used], in order of first use
        for item in plan:
            if item.section != sec["name"] or item.block.role == "session":
                continue
            if item.block.role == "rest" and not item.block.intro:
                continue
            rows.setdefault(item.block.id, [item.block.name, item.seconds, 0])[2] += 1
        acts = ", ".join(f"{n} {s}s" + (f" ×{k}" if k > 1 else "") for n, s, k in rows.values())
        out.append(f"| {cuelib.fmt(sec['start'])} | {sec['name']} | {acts} |")
    out += ["", "_Exercise library is a placeholder pending Noah's review. Stop if anything hurts._"]
    return "\n".join(out) + "\n"
