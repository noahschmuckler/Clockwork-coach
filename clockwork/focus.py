"""Focus sessions: a spoken timer for blocks of focused work (see docs/focus-spec.md).

Request (YAML) -> validated items (quests and breaks) -> spoken lines at fixed
offsets -> music bed (a track, with its ramp arc kept intact, or brown noise)
-> stereo mix rendered in chunks -> AAC .m4a -> checks.

Reuses the workout engine's voice, cue placement and countdowns. Unlike workouts
the length is whatever the items add up to, and output is stereo at 44.1 kHz so
music (including isochronic-tone tracks) passes through untouched.
"""

from __future__ import annotations

import json
import math
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from . import cues as cuelib
from . import library
from .library import LibraryError
from .render import speak_all
from .tts import SAMPLE_RATE as VOICE_RATE
from .tts import Speaker

VERSION = "0.1"
FOCUS_DIR = library.LIBRARY_DIR / "focus"

OUT_RATE = 44100
AAC_BITRATE = "128k"
CHUNK_SECONDS = 10
PEAK_CEILING = 0.89  # about -1 dBFS
DECODE_TOLERANCE = 0.1

MAX_TOTAL_MINUTES = 180
MAX_ITEMS = 20
QUEST_MINUTES = (2, 90)
BREAK_MINUTES = (1, 15)
WRAP_UP_MINUTES = (0, 10)
SAY_MAX, BRIEF_MAX, PROMPT_MAX = 60, 150, 150
QUIET_START_SECONDS = 30  # no check-ins or prompts in a slot's first 30 s
FINAL_GAP = 0.3  # "Session complete." ends this long before the file does

MUSIC_RMS_DB = -21.0  # focus music sits louder than workout music: the pulses must stay audible
NOISE_RMS_DB = -25.0
DUCK_DB = 6.0  # music drop while the voice speaks
DUCK_LEAD, DUCK_RELEASE, DUCK_RAMP = 0.15, 0.5, 0.25
LEVEL_RAMP = 2.0  # seconds to glide between work and break music levels
XFADE = 4.0  # crossfade when looping or splicing music
FADE_IN, FINAL_FADE = 2.0, 4.0
NOISE_SECONDS = 180

REQUEST_KEYS = {"block_start", "intensity", "sound", "items", "wrap_up_minutes", "seed"}
QUEST_KEYS = {"quest", "say", "minutes", "brief", "checkins", "prompts"}
BREAK_KEYS = {"break", "minutes"}
TRACK_KEYS = {"id", "title", "artist", "file", "source", "default", "arc"}
_URL = re.compile(r"https?://|www\.|\.(com|org|net|io|ly)\b", re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CHECKIN = re.compile(r"^(halfway|left:(\d+)|in:(\d+))$")

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    rest = n % 100
    return _ONES[n // 100] + " hundred" + (" and " + number_words(rest) if rest else "")


# ---------------------------------------------------------------- request

@dataclass
class Item:
    kind: str  # quest | break | wrapup
    seconds: int
    id: str = ""
    say: str = ""
    brief: str = ""
    checkins: list | None = None  # None = defaults
    prompts: list = field(default_factory=list)
    break_type: str = ""
    start: int = 0

    @property
    def end(self) -> int:
        return self.start + self.seconds

    @property
    def label(self) -> str:
        if self.kind == "quest":
            return self.say
        if self.kind == "break":
            return f"{self.break_type.capitalize()} break"
        return "Wrap-up"


def _text(value, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LibraryError(f"{what} must be non-empty text")
    if _CTRL.search(value):
        raise LibraryError(f"{what} contains control characters")
    text = " ".join(value.split())
    if len(text) > limit:
        raise LibraryError(f"{what} is {len(text)} characters; the limit is {limit}")
    if _URL.search(text):
        raise LibraryError(f"{what} looks like it contains a link; links aren't allowed")
    return text


def _minutes(value, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise LibraryError(f"{what} must be a whole number of minutes from {lo} to {hi}")
    return value


def parse_request(body: str, default_seed: int, break_types: set[str]) -> dict:
    text = body or ""
    fence = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = yaml.safe_load(text.strip()) if text.strip() else None
    except yaml.YAMLError as exc:
        raise LibraryError(f"request is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise LibraryError("request must be YAML with an 'items' list (see docs/focus-spec.md)")
    unknown = set(data) - REQUEST_KEYS
    if unknown:
        raise LibraryError(f"unknown request fields {sorted(unknown)}; allowed: {sorted(REQUEST_KEYS)}")

    intensity = data.get("intensity", "normal")
    if intensity not in ("light", "normal", "firm"):
        raise LibraryError("intensity must be light, normal or firm")
    sound = data.get("sound", "default")
    if isinstance(sound, bool):  # YAML reads off as false
        sound = "default" if sound else "off"
    if not isinstance(sound, str) or not re.fullmatch(r"[a-z0-9-]{1,40}", sound):
        raise LibraryError("sound must be default, noise, off, or a track id")
    seed = data.get("seed", default_seed)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise LibraryError("seed must be a whole number from 0 to 2147483647")
    block_start = data.get("block_start")
    if block_start is not None:
        if isinstance(block_start, int):  # YAML 1.1 reads unquoted 13:00 as minutes-of-hours
            block_start = f"{block_start // 60:02d}:{block_start % 60:02d}"
        if not isinstance(block_start, str) or not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", block_start):
            raise LibraryError('block_start must look like "13:00"')
    wrap = _minutes(data.get("wrap_up_minutes", 0), "wrap_up_minutes", *WRAP_UP_MINUTES)

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise LibraryError("items must be a non-empty list of quests and breaks")
    if len(raw_items) > MAX_ITEMS:
        raise LibraryError(f"{len(raw_items)} items; the limit is {MAX_ITEMS}")

    items: list[Item] = []
    seen: set[str] = set()
    quest_no = 0
    for i, raw in enumerate(raw_items, 1):
        where = f"item {i}"
        if not isinstance(raw, dict):
            raise LibraryError(f"{where} must be a quest or a break mapping")
        if "quest" in raw:
            bad = set(raw) - QUEST_KEYS
            if bad:
                raise LibraryError(f"{where} has unknown fields {sorted(bad)}; allowed: {sorted(QUEST_KEYS)}")
            qid = raw["quest"]
            if not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", qid):
                raise LibraryError(f"{where}: quest id must be short letters/digits, like q1")
            if qid in seen:
                raise LibraryError(f"{where}: duplicate quest id '{qid}'")
            seen.add(qid)
            quest_no += 1
            minutes = _minutes(raw.get("minutes"), f"{where} ({qid}) minutes", *QUEST_MINUTES)
            say = _text(raw["say"], f"{where} ({qid}) say", SAY_MAX) if "say" in raw else f"Quest {number_words(quest_no)}"
            brief = _text(raw["brief"], f"{where} ({qid}) brief", BRIEF_MAX) if "brief" in raw else ""
            checkins = None
            if "checkins" in raw:
                if not isinstance(raw["checkins"], list):
                    raise LibraryError(f"{where} ({qid}): checkins must be a list, e.g. [halfway, left:5]")
                checkins = []
                for c in raw["checkins"]:
                    m = _CHECKIN.match(str(c).replace(" ", ""))
                    if not m:
                        raise LibraryError(f"{where} ({qid}): unknown check-in {c!r}; use halfway, left:N or in:N")
                    n = int(m.group(2) or m.group(3) or 0)
                    if m.group(1) != "halfway" and not 1 <= n < minutes:
                        raise LibraryError(f"{where} ({qid}): check-in {c!r} must be between 1 and {minutes - 1} minutes")
                    checkins.append(("halfway", 0) if m.group(1) == "halfway" else (m.group(1).split(":")[0], n))
            prompts = []
            for p in raw.get("prompts") or []:
                if not isinstance(p, dict) or set(p) != {"at_minute", "text"}:
                    raise LibraryError(f"{where} ({qid}): each prompt needs exactly at_minute and text")
                at = p["at_minute"]
                if isinstance(at, bool) or not isinstance(at, (int, float)) or not 0 < at < minutes:
                    raise LibraryError(f"{where} ({qid}): prompt at_minute must be between 0 and {minutes}")
                prompts.append((float(at), _text(p["text"], f"{where} ({qid}) prompt", PROMPT_MAX)))
            items.append(Item("quest", minutes * 60, id=qid, say=say, brief=brief, checkins=checkins, prompts=prompts))
        elif "break" in raw:
            bad = set(raw) - BREAK_KEYS
            if bad:
                raise LibraryError(f"{where} has unknown fields {sorted(bad)}; allowed: {sorted(BREAK_KEYS)}")
            if raw["break"] not in break_types:
                raise LibraryError(f"{where}: break must be one of {sorted(break_types)}")
            minutes = _minutes(raw.get("minutes"), f"{where} break minutes", *BREAK_MINUTES)
            items.append(Item("break", minutes * 60, break_type=raw["break"]))
        else:
            raise LibraryError(f"{where} must have either 'quest' or 'break'")
    if quest_no == 0:
        raise LibraryError("a session needs at least one quest")
    if wrap:
        items.append(Item("wrapup", wrap * 60))
    t = 0
    for item in items:
        item.start = t
        t += item.seconds
    if t > MAX_TOTAL_MINUTES * 60:
        raise LibraryError(f"session is {t // 60} minutes; the limit is {MAX_TOTAL_MINUTES}")
    return {"items": items, "total": t, "intensity": intensity, "sound": sound, "seed": seed,
            "block_start": block_start}


# ---------------------------------------------------------------- spoken lines

def load_wording() -> dict:
    return yaml.safe_load((FOCUS_DIR / "wording.yaml").read_text())


def load_breaks() -> dict:
    return yaml.safe_load((FOCUS_DIR / "breaks.yaml").read_text())


@dataclass
class StretchSlot:
    block: library.Block
    start: int  # seconds from session start; always 60 s long


def stretch_slots(lib: library.Library, items: list[Item], seed: int, breaks: dict) -> dict[int, list[StretchSlot]]:
    """Choose a 60 s mobility block for each minute of every stretch break."""
    rng = random.Random(f"focus-stretch:{seed}")
    pool = sorted((b for b in lib.family(["mobility"]) if b.allows(60)), key=lambda b: b.id)
    if not pool:
        raise LibraryError("the workout library has no 60 s mobility blocks for stretch breaks")
    out: dict[int, list[StretchSlot]] = {}
    used: list[str] = []
    for i, item in enumerate(items):
        if item.kind != "break" or not breaks[item.break_type].get("stretch"):
            continue
        slots = []
        for k in range(item.seconds // 60):
            fresh = [b for b in pool if b.id not in used] or [b for b in pool if not used or b.id != used[-1]]
            b = rng.choice(fresh)
            used.append(b.id)
            slots.append(StretchSlot(b, item.start + 60 * k))
        out[i] = slots
    return out


def _brief(item: Item) -> str:
    return f"{item.say}. {item.brief}" if item.brief else item.say


def candidates(items: list[Item], total: int, wording: dict, breaks: dict,
               stretches: dict[int, list[StretchSlot]]) -> tuple[list[cuelib.Candidate], list[dict], str]:
    """All lines for the session except the final one. Returns (candidates, skipped, final_text)."""
    w = wording
    out: list[cuelib.Candidate] = []
    skipped: list[dict] = []
    quests = sum(1 for it in items if it.kind == "quest")

    def add(t, text, kind, i, window_end, slide=0.0):
        out.append(cuelib.Candidate(float(t), text, kind, float(window_end), slide=slide, block_index=i))

    def break_intro(i: int, item: Item) -> str:
        n = item.seconds // 60
        text = breaks[item.break_type]["intro"].format(N=number_words(n).capitalize(), n=number_words(n))
        slots = stretches.get(i)
        if slots:
            b = slots[0].block
            text += f" First: {b.name}. {b.intro}"
        return text

    for i, item in enumerate(items):
        prev = items[i - 1] if i else None
        if item.kind == "quest":
            label = _brief(item)
            if prev is None:
                text = w["opening"].format(quests=number_words(quests).capitalize(),
                                           quest_word="quest" if quests == 1 else "quests",
                                           minutes=number_words(total // 60), next=label)
            elif prev.kind == "quest":
                text = w["next"].format(prev=prev.say, next=label)
            else:
                text = w["back"].format(next=label)
        elif item.kind == "break":
            intro = break_intro(i, item)
            if prev is None:
                text = intro
            elif prev.kind == "quest":
                text = w["to_break"].format(prev=prev.say, **{"break": intro})
            else:
                text = intro
        else:
            text = w["to_wrap_up"].format(prev=prev.say) if prev and prev.kind == "quest" else w["wrap_up"]
        add(item.start, " ".join(text.split()), "start", i, item.end)

        d = item.seconds
        if item.kind == "quest":
            checkins = item.checkins
            if checkins is None:
                checkins = ([("halfway", 0)] if d >= 20 * 60 else []) + ([("left", 5)] if d >= 15 * 60 else [])
            for kind, n in checkins:
                if kind == "halfway":
                    at, line = d / 2, w["halfway"].format(name=item.say)
                elif kind == "left":
                    at = d - 60 * n
                    line = (w["left_one"] if n == 1 else w["left"]).format(n=number_words(n), name=item.say)
                else:
                    at, line = 60 * n, w["elapsed"].format(n=number_words(n).capitalize(), name=item.say)
                line = line[0].upper() + line[1:]
                if at < QUIET_START_SECONDS:
                    skipped.append({"time": item.start + at, "text": line, "kind": "checkin", "why": "first 30 s"})
                    continue
                add(item.start + at, line, "checkin", i, item.end)
            for at_min, line in item.prompts:
                at = at_min * 60
                if at < QUIET_START_SECONDS:
                    skipped.append({"time": item.start + at, "text": line, "kind": "prompt", "why": "first 30 s"})
                    continue
                add(item.start + at, line, "prompt", i, item.end, slide=5)
        elif item.kind == "break":
            for line in breaks[item.break_type].get("lines") or []:
                at = line["at"] if line["at"] >= 0 else d + line["at"]
                if QUIET_START_SECONDS <= at < d:
                    add(item.start + at, line["text"], "heads-up", i, item.end)
            for k, slot in enumerate(stretches.get(i, [])):
                b, s = slot.block, slot.start
                if k:
                    add(s, f"{b.name}. {b.intro}", "start", i, s + 60)
                if b.switch:
                    add(s + 30, b.switch, "switch", i, s + 60)
                if b.countdown:
                    if b.switch:
                        add(s + 30 - cuelib.COUNTDOWN_SECONDS, cuelib.COUNTDOWN_TEXT, "countdown", i, s + 30)
                    add(s + 60 - cuelib.COUNTDOWN_SECONDS, cuelib.COUNTDOWN_TEXT, "countdown", i, s + 60)
    return out, skipped, w["final"]


# ---------------------------------------------------------------- music plan

@dataclass
class Seg:
    dst: int  # first output sample
    src: int  # first source sample
    n: int  # length in samples
    fade_in: int
    fade_out: int


def concat(chunks: list[tuple[int, int]], x: int) -> tuple[list[Seg], int]:
    """Join (src_start, length) chunks end to end, each overlapping the next by x samples."""
    segs, dst = [], 0
    for k, (src, n) in enumerate(chunks):
        segs.append(Seg(dst, src, n, x if k else 0, x if k < len(chunks) - 1 else 0))
        dst += n - x
    return segs, dst + x


def plan_loop(length: int, total: int, x: int, offset: int = 0) -> list[Seg]:
    """Play from `offset`, looping the whole source with crossfades if it's too short."""
    if length - offset >= total:
        segs, t = concat([(offset, total)], x)
    else:
        if length < 3 * x:
            raise LibraryError("music source is too short to loop")
        chunks = [(offset, length - offset)]
        t = length - offset
        while t + length - x < total:
            chunks.append((0, length))
            t += length - x
        last = total - t + x
        if last < 2 * x:  # too short to crossfade cleanly: borrow from the previous chunk
            src, n = chunks[-1]
            chunks[-1] = (src, n - (2 * x - last))
            last = 2 * x
        chunks.append((0, last))
        segs, t = concat(chunks, x)
    assert t == total, (t, total)
    return segs


def plan_arc(length: int, total: int, ramp_up: int, ramp_down: int, x: int) -> list[Seg]:
    """Keep a session track's arc: ramp-up from 0:00, ramp-down ending exactly at `total`.

    Only the plateau between them is shortened or extended, and every splice is a
    crossfade inside the plateau. Nothing is time-stretched.
    """
    need = total - ramp_up - ramp_down  # plateau needed
    have = length - ramp_up - ramp_down  # plateau in the track
    if have < 3 * x:
        raise LibraryError("this track's steady middle section is too short to fit sessions to")
    if need < x:
        raise LibraryError("session too short for this track's ramp-up and ramp-down")
    if total <= length:
        a = ramp_up + (need + x) // 2
        b = total + x - a
        chunks = [(0, a), (length - b, b)]
    else:
        remaining = total - length
        m = math.ceil(remaining / (have - x))
        nets = [remaining // m + (1 if k < remaining % m else 0) for k in range(m)]
        extra = 0
        if min(nets) < x:
            extra = x * m - remaining
            nets = [x] * m
        chunks = ([(0, length - ramp_down - extra)] + [(ramp_up, n + x) for n in nets]
                  + [(length - ramp_down - x, ramp_down + x)])
    segs, t = concat(chunks, x)
    assert t == total, (t, total)
    assert segs[0].src == 0 and segs[-1].src + segs[-1].n == length
    return segs


# ---------------------------------------------------------------- audio sources

def _ffmpeg_raw(args: list[str], out: Path) -> np.memmap:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-ac", "2", "-ar", str(OUT_RATE), "-f", "f32le", str(out)],
                   check=True)
    return np.memmap(out, dtype=np.float32, mode="r").reshape(-1, 2)


def decode_track(path: Path, tmp: Path) -> np.memmap:
    return _ffmpeg_raw(["-i", str(path)], tmp / "track.f32")


def brown_noise(seed: int, tmp: Path) -> np.memmap:
    src = (f"anoisesrc=color=brown:sample_rate={OUT_RATE}:amplitude=0.5:duration={NOISE_SECONDS}:seed={{}}")
    return _ffmpeg_raw(["-f", "lavfi", "-i", src.format(seed + 1), "-f", "lavfi", "-i", src.format(seed + 2),
                        "-filter_complex", "[0][1]amerge=inputs=2,highpass=f=35,lowpass=f=9000"],
                       tmp / "noise.f32")


def rms_db(source: np.ndarray) -> float:
    step = max(1, len(source) // 2_000_000)  # sample at most ~2M frames
    x = np.asarray(source[::step], dtype=np.float64)
    return 20 * math.log10(max(1e-9, float(np.sqrt(np.mean(x ** 2)))))


def upsample_voice(clip: np.ndarray) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(VOICE_RATE), "-ac", "1", "-i", "-",
         "-ar", str(OUT_RATE), "-f", "f32le", "-"],
        input=clip.astype(np.float32).tobytes(), check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


# ---------------------------------------------------------------- mixer

class Mixer:
    """Renders the session one chunk at a time, so length doesn't drive memory use."""

    def __init__(self, total: int, source, segs: list[Seg], music_gain: float, items: list[Item],
                 breaks: dict, placed: list[cuelib.PlacedCue], voice: dict[str, np.ndarray]):
        self.n = total * OUT_RATE
        self.source, self.segs, self.music_gain = source, segs, music_gain
        self.placed, self.voice = placed, voice
        # Music level breakpoints (seconds, dB): glide between work and break levels.
        times, levels = [0.0], [0.0]
        for a, b in zip(items, items[1:]):
            la = breaks[a.break_type].get("music_db", 0) if a.kind == "break" else 0
            lb = breaks[b.break_type].get("music_db", 0) if b.kind == "break" else 0
            if la != lb:
                times += [b.start - LEVEL_RAMP / 2, b.start + LEVEL_RAMP / 2]
                levels += [la, lb]
        last = items[-1]
        times.append(float(total))
        levels.append(breaks[last.break_type].get("music_db", 0) if last.kind == "break" else 0)
        self.level_t, self.level_db = np.array(times), np.array(levels, dtype=np.float64)

    def chunk(self, a: int, b: int) -> np.ndarray:
        out = np.zeros((b - a, 2), dtype=np.float32)
        t = np.arange(a, b) / OUT_RATE
        if self.source is not None:
            music = np.zeros_like(out)
            for s in self.segs:
                o0, o1 = max(a, s.dst), min(b, s.dst + s.n)
                if o0 >= o1:
                    continue
                idx = np.arange(o0, o1) - s.dst
                g = np.ones(len(idx), dtype=np.float32)
                if s.fade_in:
                    m = idx < s.fade_in
                    g[m] = np.sin(np.pi / 2 * idx[m] / s.fade_in)
                if s.fade_out:
                    m = idx >= s.n - s.fade_out
                    g[m] = np.cos(np.pi / 2 * (idx[m] - (s.n - s.fade_out)) / s.fade_out)
                music[o0 - a: o1 - a] += np.asarray(self.source[s.src + idx[0]: s.src + idx[-1] + 1]) * g[:, None]
            gain = self.music_gain * 10 ** (np.interp(t, self.level_t, self.level_db) / 20)
            gain *= 1 - (1 - 10 ** (-DUCK_DB / 20)) * self._duck(t)
            gain *= np.clip(t / FADE_IN, 0, 1) * np.clip((self.n / OUT_RATE - t) / FINAL_FADE, 0, 1)
            out += music * gain[:, None].astype(np.float32)
        for cue in self.placed:
            i0 = round(cue.time * OUT_RATE)
            clip = self.voice[cue.text]
            o0, o1 = max(a, i0), min(b, i0 + len(clip))
            if o0 < o1:
                out[o0 - a: o1 - a] += clip[o0 - i0: o1 - i0, None]
        return out

    def _duck(self, t: np.ndarray) -> np.ndarray:
        env = np.zeros(len(t), dtype=np.float64)
        lo, hi = t[0] - DUCK_RAMP - DUCK_RELEASE, t[-1] + DUCK_RAMP + DUCK_LEAD
        for cue in self.placed:
            s, e = cue.time - DUCK_LEAD, cue.end + DUCK_RELEASE
            if e < lo or s > hi:
                continue
            dist = np.maximum(np.maximum(s - t, t - e), 0)
            env = np.maximum(env, np.clip(1 - dist / DUCK_RAMP, 0, 1))
        return env

    def render(self, path: Path, title: str) -> dict:
        step = CHUNK_SECONDS * OUT_RATE
        peak = 0.0
        for a in range(0, self.n, step):
            peak = max(peak, float(np.max(np.abs(self.chunk(a, min(self.n, a + step))))))
        scale = min(1.0, PEAK_CEILING / peak) if peak > 0 else 1.0
        path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(OUT_RATE), "-ac", "2", "-i", "-",
             "-c:a", "aac", "-b:a", AAC_BITRATE, "-movflags", "+faststart",
             "-metadata", f"title={title}", "-metadata", "artist=Clockwork Coach", str(path)],
            stdin=subprocess.PIPE)
        frames, final_peak = 0, 0.0
        for a in range(0, self.n, step):
            block = self.chunk(a, min(self.n, a + step)) * np.float32(scale)
            final_peak = max(final_peak, float(np.max(np.abs(block))))
            proc.stdin.write(block.astype(np.float32).tobytes())
            frames += len(block)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed to encode the session")
        return {"frames": frames, "peak": final_peak}


def decoded_seconds(path: Path) -> float:
    """Decode the whole file (streaming, so long files don't fill memory) and count samples."""
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(OUT_RATE),
                             "-f", "f32le", "-"], stdout=subprocess.PIPE)
    n = 0
    while chunk := proc.stdout.read(1 << 20):
        n += len(chunk)
    proc.wait()
    return n / 4 / OUT_RATE


# ---------------------------------------------------------------- tracks

def load_tracks(config: Path | None) -> list[dict]:
    if config is None or not config.exists():
        return []
    tracks = yaml.safe_load(config.read_text()) or []
    for t in tracks:
        bad = set(t) - TRACK_KEYS
        if bad or "id" not in t or "file" not in t:
            raise LibraryError(f"track entry {t.get('id', '?')}: needs id and file; unknown fields {sorted(bad)}")
        if "arc" in t and set(t["arc"]) != {"ramp_up", "ramp_down"}:
            raise LibraryError(f"track {t['id']}: arc needs ramp_up and ramp_down (seconds)")
    return tracks


def resolve_sound(sound: str, tracks: list[dict], music_dir: Path | None) -> tuple[dict | None, list[str]]:
    """Returns (track or None for noise/off, warnings). 'off' is handled by the caller."""
    if sound in ("noise", "off"):
        return None, []
    if sound == "default":
        track = next((t for t in tracks if t.get("default")), None)
        if track is None:
            return None, ["no default focus track is set up yet; using brown noise"]
    else:
        track = next((t for t in tracks if t["id"] == sound), None)
        if track is None:
            raise LibraryError(f"unknown sound '{sound}'. Available: default, noise, off"
                               + "".join(f", {t['id']}" for t in tracks))
    path = (music_dir / track["file"]) if music_dir else None
    if path is None or not path.exists():
        return None, [f"the music file for '{track['id']}' isn't uploaded yet; using brown noise"]
    return {**track, "path": path}, []


# ---------------------------------------------------------------- render

def render(body: str, out_dir: Path, label: str, tts_backend: str = "piper", download_base: str = "",
           music_config: Path | None = None, music_dir: Path | None = None) -> dict:
    breaks = load_breaks()
    default_seed = int(label) if label.isdigit() else 0
    req = parse_request(body, default_seed, set(breaks))
    items, total = req["items"], req["total"]
    wording = load_wording()[req["intensity"]]

    track, warnings = (None, []) if req["sound"] == "off" else resolve_sound(
        req["sound"], load_tracks(music_config), music_dir)
    if track and "arc" in track:
        arc = track["arc"]
        if total < arc["ramp_up"] + arc["ramp_down"] + 60:
            raise LibraryError(f"'{track['id']}' needs a session of at least "
                               f"{(arc['ramp_up'] + arc['ramp_down'] + 60) // 60} minutes "
                               f"for its ramp-up and ramp-down; use sound: noise for shorter sessions")

    lib = library.load()
    stretches = stretch_slots(lib, items, req["seed"], breaks)
    cands, skipped, final_text = candidates(items, total, wording, breaks, stretches)
    speaker = Speaker(tts_backend)
    clips = speak_all(speaker, {c.text for c in cands} | {final_text})
    durations = {t: len(a) / VOICE_RATE for t, a in clips.items()}
    final_at = total - durations[final_text] - FINAL_GAP
    cands.append(cuelib.Candidate(final_at, final_text, "final", total - FINAL_GAP + 1e-6, block_index=len(items) - 1))
    placed, dropped = cuelib.place(cands, durations, total=total)
    for cue in placed:  # every line must sit inside its own item
        item = items[cue.block_index]
        if not (item.start - 1e-6 <= cue.time and cue.end <= item.end + 1e-6):
            raise LibraryError(f"line '{cue.text}' spills out of {item.label}")
    warnings += speaker.warnings

    voice = {t: upsample_voice(a) for t, a in clips.items() if any(c.text == t for c in placed)}
    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        source, segs, music_gain, sound_used = None, [], 0.0, "voice only"
        rng = random.Random(f"focus-music:{req['seed']}")
        if track:
            source = decode_track(track["path"], tmp)
            n, x = total * OUT_RATE, int(XFADE * OUT_RATE)
            if "arc" in track:
                segs = plan_arc(len(source), n, track["arc"]["ramp_up"] * OUT_RATE,
                                track["arc"]["ramp_down"] * OUT_RATE, x)
            else:
                spare = max(0, len(source) - n)
                segs = plan_loop(len(source), n, x, offset=rng.randrange(spare + 1))
            music_gain = 10 ** ((MUSIC_RMS_DB - rms_db(source)) / 20)
            sound_used = track.get("title", track["id"])
        elif req["sound"] != "off":
            source = brown_noise(req["seed"] % 100000, tmp)
            segs = plan_loop(len(source), total * OUT_RATE, int(XFADE * OUT_RATE))
            music_gain = 10 ** ((NOISE_RMS_DB - rms_db(source)) / 20)
            sound_used = "brown noise"
        filename = f"focus-{label}.m4a"
        out_file = out_dir / filename
        mixer = Mixer(total, source, segs, music_gain, items, breaks, placed, voice)
        stats = mixer.render(out_file, f"Focus session {label}")

    expected = total * OUT_RATE
    if stats["frames"] != expected:
        raise RuntimeError(f"rendered {stats['frames']} frames, expected {expected}")
    if stats["peak"] > 1.0:
        raise RuntimeError(f"output clips (peak {stats['peak']:.3f})")
    decoded = decoded_seconds(out_file)
    if abs(decoded - total) > DECODE_TOLERANCE:
        raise RuntimeError(f"encoded file decodes to {decoded:.3f}s, expected {total}s")

    manifest = {
        "version": VERSION,
        "label": label,
        "file": filename,
        "total_seconds": total,
        "intensity": req["intensity"],
        "sound": req["sound"],
        "sound_used": sound_used,
        "seed": req["seed"],
        "block_start": req["block_start"],
        "voice": speaker.voice,
        "checks": {"frames": stats["frames"], "sample_rate": OUT_RATE, "channels": 2,
                   "peak": round(stats["peak"], 4), "decoded_seconds": round(decoded, 3),
                   "file_bytes": out_file.stat().st_size},
        "warnings": warnings,
        "items": [
            {"start": cuelib.fmt(it.start), "end": cuelib.fmt(it.end), "seconds": it.seconds, "kind": it.kind,
             "id": it.id or None, "label": it.label}
            for it in items
        ],
        "stretches": {items[i].label + f" @ {cuelib.fmt(items[i].start)}": [s.block.id for s in v]
                      for i, v in stretches.items()},
        "music_segments": [{"dst": round(s.dst / OUT_RATE, 2), "src": round(s.src / OUT_RATE, 2),
                            "seconds": round(s.n / OUT_RATE, 2)} for s in segs],
        "cues": [{"time": round(c.time, 2), "at": cuelib.fmt(c.time), "seconds": round(c.duration, 2),
                  "kind": c.kind, "text": c.text} for c in placed],
        "dropped_cues": dropped + skipped,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out_dir / "CREDITS.txt").write_text(credits(track, sound_used, speaker.voice))
    (out_dir / "summary.md").write_text(summary(manifest, items, download_base + filename if download_base else ""))
    return manifest


def credits(track: dict | None, sound_used: str, voice: str) -> str:
    lines = ["Clockwork Coach focus session - credits", ""]
    if track:
        lines += [f"Music: \"{track.get('title', track['id'])}\" - {track.get('artist', 'unknown artist')}",
                  f"Source: {track.get('source', 'not recorded')}",
                  "Used for personal listening only; edited (trimmed/looped, crossfaded, volume-adjusted, "
                  "mixed under speech). Not for redistribution.", ""]
    elif sound_used == "brown noise":
        lines += ["Background: brown noise generated with ffmpeg.", ""]
    lines.append(f"Voice: Piper text-to-speech ({voice}), trained on public-domain LibriVox recordings.")
    return "\n".join(lines) + "\n"


def _clock(block_start: str | None, seconds: int) -> str:
    if not block_start:
        return ""
    h, m = map(int, block_start.split(":"))
    t = h * 60 + m + seconds // 60
    return f"{t // 60 % 24:d}:{t % 60:02d}"


def summary(manifest: dict, items: list[Item], url: str) -> str:
    bs = manifest["block_start"]
    out = []
    if url:
        out += [f"**Focus session ready:** [{manifest['file']}]({url})", ""]
    quests = sum(1 for it in items if it.kind == "quest")
    out.append(f"{manifest['total_seconds'] // 60} minutes · {quests} quest{'s' if quests != 1 else ''} · "
               f"sound: {manifest['sound_used']} · {manifest['intensity']}")
    for w in manifest["warnings"]:
        out.append(f"\n⚠️ {w}")
    head = "| Clock | Audio | Item |" if bs else "| Audio | Item |"
    out += ["", head, "|---|---|---|" if bs else "|---|---|"]
    for it in items:
        name = f"**{it.say}** ({it.id})" if it.kind == "quest" else it.label
        audio = f"{cuelib.fmt(it.start)}–{cuelib.fmt(it.end)}"
        out.append(f"| {_clock(bs, it.start)}–{_clock(bs, it.end)} | {audio} | {name} |" if bs
                   else f"| {audio} | {name} |")
    if manifest["dropped_cues"]:
        out += ["", "Lines left out (no room or too early in a slot): "
                + "; ".join(f"“{d['text']}”" for d in manifest["dropped_cues"])]
    start = f" at {bs}" if bs else ""
    out += ["", f"_Press play{start}. Pausing shifts every later line; the audio can't tell when a quest is done._"]
    return "\n".join(out) + "\n"
