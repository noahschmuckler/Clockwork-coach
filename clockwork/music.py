"""Download, decode, loop and lay out background music under the timeline."""

from __future__ import annotations

import random
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from .library import Library, Track
from .planner import TOTAL_SECONDS, PlanItem
from .tts import CACHE_DIR, SAMPLE_RATE

BED_RMS_DB = -29.0  # music level while nobody is speaking (voice peaks at about -1 dBFS)
MOOD_TRIM_DB = {"warm": 0.0, "drive": 1.0, "calm": -3.0}
SECTION_XFADE = 4.0  # seconds, centred on each section boundary
LOOP_XFADE = 1.5
FINAL_FADE = 8.0
USER_AGENT = "clockwork-coach/0.1 (personal workout audio; github.com/noahschmuckler/Clockwork-coach)"


def fetch(tracks: list[Track]) -> dict[str, Path]:
    """Download any missing tracks. Returns the tracks that are available."""
    have = {}
    for track in tracks:
        path = CACHE_DIR / "music" / track.filename
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                req = urllib.request.Request(track.url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                if len(data) < 100_000:
                    raise RuntimeError(f"only {len(data)} bytes")
                path.with_suffix(".part").write_bytes(data)
                path.with_suffix(".part").replace(path)
                print(f"downloaded {track.title}")
            except Exception as exc:  # network or site problem: render continues without this track
                print(f"warning: could not download '{track.title}': {exc}")
                continue
        have[track.id] = path
    return have


def decode(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    audio = np.frombuffer(raw, dtype=np.float32).copy()
    loud = np.flatnonzero(np.abs(audio) > 0.003)
    return audio[loud[0]: loud[-1] + 1] if loud.size else audio


def _rms_db(audio: np.ndarray) -> float:
    return 20 * np.log10(max(1e-9, float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))))


def _loop(audio: np.ndarray, n: int, offset: int) -> np.ndarray:
    """Repeat audio to length n with crossfades, starting `offset` samples in."""
    x = int(LOOP_XFADE * SAMPLE_RATE)
    if len(audio) <= 2 * x:
        raise ValueError("track too short to loop")
    fade_in = np.linspace(0, 1, x, dtype=np.float32)
    out = np.zeros(n + len(audio), dtype=np.float32)
    pos, first = -(offset % (len(audio) - x)), True
    while pos < n:
        piece = audio.copy()
        if not first:
            piece[:x] *= fade_in
        piece[-x:] *= fade_in[::-1]
        lo = max(pos, 0)
        out[lo: pos + len(piece)] += piece[lo - pos:]
        pos += len(audio) - x
        first = False
    return out[:n]


def sections(plan: list[PlanItem]) -> list[dict]:
    out: list[dict] = []
    for item in plan:
        if out and out[-1]["name"] == item.section:
            out[-1]["end"] = item.end
        else:
            out.append({"name": item.section, "mood": item.mood, "start": item.start, "end": item.end})
    return out


def build_bed(lib: Library, plan: list[PlanItem], available: dict[str, Path], seed_key: str) -> tuple[np.ndarray, list[dict]]:
    """Return (music bed for the full 45:00, list of sections with the track used)."""
    total = TOTAL_SECONDS * SAMPLE_RATE
    bed = np.zeros(total, dtype=np.float32)
    rng = random.Random(f"music:{seed_key}")
    decoded: dict[str, np.ndarray] = {}
    used = []
    prev = None
    half = SECTION_XFADE / 2
    for sec in sections(plan):
        choices = sorted((t for t in lib.tracks if t.mood == sec["mood"] and t.id in available), key=lambda t: t.id)
        if not choices:
            used.append({**sec, "track": None})
            continue
        fresh = [t for t in choices if t.id != prev] or choices
        track = rng.choice(fresh)
        prev = track.id
        if track.id not in decoded:
            audio = decode(available[track.id])
            gain_db = BED_RMS_DB + MOOD_TRIM_DB[sec["mood"]] - _rms_db(audio)
            decoded[track.id] = audio * np.float32(10 ** (gain_db / 20))
        a = max(0.0, sec["start"] - half)
        b = min(float(TOTAL_SECONDS), sec["end"] + half)
        i0, i1 = int(a * SAMPLE_RATE), int(b * SAMPLE_RATE)
        piece = _loop(decoded[track.id], i1 - i0, offset=rng.randrange(0, SAMPLE_RATE * 30))
        x = int(SECTION_XFADE * SAMPLE_RATE)
        ramp = np.sqrt(np.linspace(0, 1, x, dtype=np.float32))  # equal-power crossfade
        if sec["start"] > 0:
            piece[:x] *= ramp
        else:
            piece[: 2 * SAMPLE_RATE] *= np.linspace(0, 1, 2 * SAMPLE_RATE, dtype=np.float32)
        if sec["end"] < TOTAL_SECONDS:
            piece[-x:] *= ramp[::-1]
        bed[i0:i1] += piece
        used.append({**sec, "track": track.id})
    f = int(FINAL_FADE * SAMPLE_RATE)
    bed[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
    return bed, used
