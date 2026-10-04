"""Text to speech with an on-disk cache, so repeated cues are only synthesized once."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

from .library import ROOT

SAMPLE_RATE = 22050  # Piper medium voices, and espeak-ng, both produce 22.05 kHz
CACHE_DIR = ROOT / ".cache"

PIPER_VOICE = "en_US-norman-medium"  # public-domain LibriVox data, trained from scratch
PIPER_VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/norman/medium/"
PIPER_LENGTH_SCALE = 1.05  # a touch slower than default, for clarity
PIPER_NOISE_SCALE = 0.5
PIPER_NOISE_W_SCALE = 0.6
VOICE_PEAK = 0.9


def _trim(audio: np.ndarray, threshold: float = 0.01, pad: float = 0.03) -> np.ndarray:
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return audio[:0]
    p = int(pad * SAMPLE_RATE)
    return audio[max(0, loud[0] - p): loud[-1] + p + 1]


COUNTDOWN_WORD_SECONDS = 0.8  # each countdown word must fit inside its one-second slot


def _tight_trim(audio: np.ndarray, below_peak_db: float = 30.0) -> np.ndarray:
    """Trim breaths and tails: keep 10 ms frames within `below_peak_db` of the loudest frame."""
    f = int(0.01 * SAMPLE_RATE)
    n = len(audio) // f
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * f].reshape(n, f) ** 2, axis=1))
    loud = np.flatnonzero(rms >= rms.max() * 10 ** (-below_peak_db / 20))
    return audio[loud[0] * f: (loud[-1] + 1) * f]


def _split_words(audio: np.ndarray, n: int, below_peak_db: float = 32.0) -> list[np.ndarray] | None:
    """Split speech into n pieces at its quietest gaps; None if it doesn't have n clear parts."""
    f = int(0.01 * SAMPLE_RATE)
    frames = len(audio) // f
    rms = np.sqrt(np.mean(audio[: frames * f].reshape(frames, f) ** 2, axis=1))
    voiced = rms >= rms.max() * 10 ** (-below_peak_db / 20)
    runs, i = [], 0
    while i < frames:
        if voiced[i]:
            j = i
            while j < frames and voiced[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    # Join runs split by tiny dips (under 50 ms) and drop clicks (under 50 ms).
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < 5:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    merged = [r for r in merged if r[1] - r[0] >= 5]
    # Too many pieces: join across the shortest gaps until there are n.
    while len(merged) > n:
        k = min(range(len(merged) - 1), key=lambda k: merged[k + 1][0] - merged[k][1])
        merged[k][1] = merged.pop(k + 1)[1]
    if len(merged) != n:
        return None
    pad = 2  # 20 ms either side
    return [audio[max(0, a - pad) * f: min(frames, b + pad) * f].copy() for a, b in merged]


def _fit(audio: np.ndarray, seconds: float) -> np.ndarray:
    """Speed a clip up (pitch unchanged) so it lasts at most `seconds`."""
    if len(audio) <= seconds * SAMPLE_RATE:
        return audio
    tempo = len(audio) / (seconds * SAMPLE_RATE) * 1.02
    if tempo > 2.0:
        raise RuntimeError(f"countdown audio is {len(audio) / SAMPLE_RATE:.2f}s, can't fit it to {seconds:.2f}s")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-",
         "-af", f"atempo={tempo:.4f}", "-f", "f32le", "-"],
        input=audio.astype(np.float32).tobytes(), check=True, capture_output=True,
    ).stdout
    out = np.frombuffer(raw, dtype=np.float32)[: int(seconds * SAMPLE_RATE)].copy()
    fade = min(len(out), int(0.01 * SAMPLE_RATE))
    out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return out


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getframerate() != SAMPLE_RATE or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"{path}: expected 22050 Hz mono 16-bit audio")
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def _write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    tmp.replace(path)


class Speaker:
    """backend: 'piper' (real voice), 'espeak' (robotic, local testing), 'tone' (unit tests)."""

    def __init__(self, backend: str = "piper"):
        self.backend = backend
        self.voice = {"piper": PIPER_VOICE, "espeak": "espeak-ng en-us+m3", "tone": "test-tone"}[backend]
        self._piper = None
        self.warnings: list[str] = []
        settings = f"{self.voice}|{PIPER_LENGTH_SCALE}|{PIPER_NOISE_SCALE}|{PIPER_NOISE_W_SCALE}|{VOICE_PEAK}"
        self.cache = CACHE_DIR / "tts" / hashlib.sha1(settings.encode()).hexdigest()[:12]

    def say(self, text: str) -> np.ndarray:
        path = self.cache / (hashlib.sha1(text.encode()).hexdigest() + ".wav")
        if not path.exists():
            audio = _trim(self._synthesize(text))
            if audio.size == 0:
                raise RuntimeError(f"speech engine produced silence for: {text!r}")
            audio = audio * (VOICE_PEAK / max(1e-6, float(np.max(np.abs(audio)))))
            _write_wav(path, audio)
        return _read_wav(path)

    def countdown(self, words: list[str]) -> np.ndarray:
        """One word per second ("Five" "Four" ...), each starting exactly on its second.

        Neural voices garble single words spoken on their own, so the countdown is
        spoken as one sentence ("Five, four, three, two, one.") and split at the
        pauses between words. If the split doesn't find one piece per word, the
        whole sentence is used as-is (sped up if needed) and a warning is recorded.
        """
        step = SAMPLE_RATE
        sentence = ", ".join(w.rstrip(".").lower() for w in words).capitalize() + "."
        audio = self.say(sentence)
        pieces = _split_words(audio, len(words))
        if pieces is None:
            # Still a countdown ending by the target, just not one word per exact second.
            msg = f"countdown could not be split into {len(words)} words; numbers are not exactly on the second"
            print(msg)
            if msg not in self.warnings:
                self.warnings.append(msg)
            span = (len(words) - 1) + COUNTDOWN_WORD_SECONDS
            return _fit(_tight_trim(audio), span)
        parts = [_fit(p, COUNTDOWN_WORD_SECONDS) for p in pieces]
        print("countdown words: " + ", ".join(f"{len(a) / step:.2f}s->{len(b) / step:.2f}s" for a, b in zip(pieces, parts)))
        out = np.zeros(step * (len(words) - 1) + len(parts[-1]), dtype=np.float32)
        for k, p in enumerate(parts):
            out[k * step: k * step + len(p)] = p
        return out

    def _synthesize(self, text: str) -> np.ndarray:
        if self.backend == "tone":
            # Stand-in for tests: ~14 characters per second, like real speech,
            # with a short pause at each comma.
            parts = []
            for phrase in text.split(", "):
                n = int(SAMPLE_RATE * max(0.3, len(phrase) / 14))
                t = np.arange(n) / SAMPLE_RATE
                parts += [(0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), np.zeros(int(0.2 * SAMPLE_RATE), np.float32)]
            return np.concatenate(parts[:-1])
        if self.backend == "espeak":
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "x.wav"
                subprocess.run(["espeak-ng", "-v", "en-us+m3", "-s", "160", "-w", str(out), text], check=True)
                return _read_wav(out)
        return self._piper_say(text)

    def _piper_say(self, text: str) -> np.ndarray:
        from piper import PiperVoice, SynthesisConfig

        if self._piper is None:
            self._piper = PiperVoice.load(str(fetch_voice()))
            if self._piper.config.sample_rate != SAMPLE_RATE:
                raise RuntimeError(f"voice sample rate {self._piper.config.sample_rate} != {SAMPLE_RATE}")
        cfg = SynthesisConfig(
            length_scale=PIPER_LENGTH_SCALE,
            noise_scale=PIPER_NOISE_SCALE,
            noise_w_scale=PIPER_NOISE_W_SCALE,
            normalize_audio=True,
        )
        # Piper yields one chunk per sentence; join them with a short natural pause.
        pause = np.zeros(int(0.25 * SAMPLE_RATE), dtype=np.float32)
        parts = []
        for chunk in self._piper.synthesize(text, syn_config=cfg):
            if parts:
                parts.append(pause)
            parts.append(_trim(chunk.audio_float_array.astype(np.float32)))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def fetch_voice() -> Path:
    voice_dir = CACHE_DIR / "voices"
    model = voice_dir / f"{PIPER_VOICE}.onnx"
    for path in (model, voice_dir / f"{PIPER_VOICE}.onnx.json"):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            print(f"downloading {path.name}")
            tmp = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(PIPER_VOICE_URL + path.name + "?download=true", tmp)
            tmp.replace(path)
    return model
