"""Place voice cues at exact sample offsets, duck the music under speech, encode and verify."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .cues import PlacedCue
from .planner import TOTAL_SECONDS
from .tts import SAMPLE_RATE

DUCK_DB = 6.0  # extra music reduction while speaking (bed is ~15 dB under voice before ducking)
DUCK_LEAD = 0.15  # start ducking this long before speech
DUCK_RELEASE = 0.5  # stay ducked this long after speech
PEAK_CEILING = 0.89  # about -1 dBFS
AAC_BITRATE = "64k"
DECODE_TOLERANCE = 0.1  # seconds


def voice_track(placed: list[PlacedCue], clips: dict[str, np.ndarray]) -> np.ndarray:
    total = TOTAL_SECONDS * SAMPLE_RATE
    track = np.zeros(total, dtype=np.float32)
    for cue in placed:
        clip = clips[cue.text]
        i = round(cue.time * SAMPLE_RATE)
        if i + len(clip) > total:
            raise RuntimeError(f"cue '{cue.text}' runs past the end")
        track[i: i + len(clip)] += clip
    return track


def duck_envelope(placed: list[PlacedCue]) -> np.ndarray:
    """0 = no speech, 1 = speech; smoothed so the music glides down and back up."""
    total = TOTAL_SECONDS * SAMPLE_RATE
    env = np.zeros(total, dtype=np.float32)
    for cue in placed:
        a = max(0, round((cue.time - DUCK_LEAD) * SAMPLE_RATE))
        b = min(total, round((cue.end + DUCK_RELEASE) * SAMPLE_RATE))
        env[a:b] = 1.0
    w = int(0.12 * SAMPLE_RATE)
    kernel = np.ones(w, dtype=np.float32) / w
    return np.convolve(env, kernel, mode="same").astype(np.float32)


def mix(voice: np.ndarray, bed: np.ndarray | None, placed: list[PlacedCue]) -> np.ndarray:
    out = voice.copy()
    if bed is not None:
        duck = np.float32(10 ** (-DUCK_DB / 20))
        out += bed * (1 - (1 - duck) * duck_envelope(placed))
    peak = float(np.max(np.abs(out)))
    if peak > PEAK_CEILING:
        out *= np.float32(PEAK_CEILING / peak)
    return out


def encode(master: np.ndarray, path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-",
         "-c:a", "aac", "-b:a", AAC_BITRATE, "-movflags", "+faststart",
         "-metadata", f"title={title}", "-metadata", "artist=Clockwork Coach", str(path)],
        input=master.astype(np.float32).tobytes(), check=True,
    )


def decoded_seconds(path: Path) -> float:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        check=True, capture_output=True,
    ).stdout
    return len(raw) / 4 / SAMPLE_RATE


def checks(master: np.ndarray, out_file: Path) -> dict:
    """Release gates from the spec. Raises on failure; returns the measurements."""
    expected = TOTAL_SECONDS * SAMPLE_RATE
    if len(master) != expected:
        raise RuntimeError(f"master has {len(master)} samples, expected {expected}")
    peak = float(np.max(np.abs(master)))
    if peak > 1.0:
        raise RuntimeError(f"master clips (peak {peak:.3f})")
    decoded = decoded_seconds(out_file)
    if abs(decoded - TOTAL_SECONDS) > DECODE_TOLERANCE:
        raise RuntimeError(f"encoded file decodes to {decoded:.3f}s, expected {TOTAL_SECONDS}s ±{DECODE_TOLERANCE}")
    return {
        "master_samples": len(master),
        "sample_rate": SAMPLE_RATE,
        "master_peak": round(peak, 4),
        "decoded_seconds": round(decoded, 3),
        "file_bytes": out_file.stat().st_size,
    }
