"""Decide what the coach says and exactly when.

Every cue gets a fixed start time and must finish inside its own block. Cues
never move a block boundary: an optional cue that doesn't fit is dropped, and a
required cue that doesn't fit fails the render (shorten its text instead).
"""

from __future__ import annotations

from dataclasses import dataclass

from .library import LibraryError
from .planner import TOTAL_SECONDS, PlanItem

GAP_SECONDS = 0.4  # minimum silence between two cues
COUNTDOWN_GAP_SECONDS = 0.1  # a countdown may run right up to the line that follows it
HEADS_UP_SECONDS = 10
EASIER_AT_SECONDS = 12

# "Five" .. "One" spoken one second apart, so "One" starts exactly 1 s before the target.
COUNTDOWN_WORDS = ["Five.", "Four.", "Three.", "Two.", "One."]
COUNTDOWN_TEXT = " ".join(COUNTDOWN_WORDS)
COUNTDOWN_SECONDS = len(COUNTDOWN_WORDS)

# Lower number wins when two cues collide. start, switch and countdown are required:
# if one can't fit the render fails rather than dropping it.
PRIORITY = {"start": 0, "switch": 1, "countdown": 1, "heads-up": 2, "authored": 3, "easier": 4,
            "checkpoint": 5, "minutes-left": 6}
REQUIRED = {"start", "switch", "countdown"}

# (seconds into the session, line). Each may slide later by up to 30 s to find a gap.
CHECKPOINTS = [
    (600, "Ten minutes done."),
    (1350, "Halfway there."),
    (2100, "Ten minutes to go."),
    (2400, "Five minutes to go."),
]
CHECKPOINT_SLIDE_SECONDS = 30

_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
          "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen"]


def words(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


@dataclass
class Candidate:
    time: float  # earliest start, seconds from session start
    text: str
    kind: str
    window_end: float  # cue must finish by this time
    slide: float = 0.0  # how far later it may move to find a gap
    block_index: int = 0

    @property
    def required(self) -> bool:
        return self.kind in REQUIRED

    @property
    def priority(self) -> int:
        return PRIORITY[self.kind]


@dataclass
class PlacedCue:
    time: float
    duration: float
    text: str
    kind: str
    block_index: int

    @property
    def end(self) -> float:
        return self.time + self.duration


def _next_name(plan: list[PlanItem], i: int) -> str | None:
    for item in plan[i + 1:]:
        if item.block.role == "session":
            return None
        if item.block.name:
            return item.block.name
    return None


def candidates(plan: list[PlanItem]) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for i, item in enumerate(plan):
        b, start, end, d = item.block, float(item.start), float(item.end), item.seconds
        first_use = b.id not in seen
        seen.add(b.id)
        nxt = _next_name(plan, i)
        nxt_item = plan[i + 1] if i + 1 < len(plan) else None

        # Start of block: always spoken.
        if b.role == "session":
            text = b.intro
        elif b.role == "rest" and not b.intro:
            text = f"Rest. Next up, {nxt}." if nxt else "Rest."
        elif first_use:
            text = f"{b.name}. {b.intro}".strip()
        else:
            text = f"{b.name}. Go."
        if text:
            out.append(Candidate(start, text, "start", end, block_index=i))

        if b.role == "session":
            continue

        # Exactly-timed side switch and countdowns.
        if b.switch:
            out.append(Candidate(start + d / 2, b.switch, "switch", end, block_index=i))
        if b.countdown:
            targets = ([d / 2] if b.switch else []) + [d]
            for target in targets:
                out.append(Candidate(start + target - COUNTDOWN_SECONDS, COUNTDOWN_TEXT, "countdown",
                                     start + target, block_index=i))

        # Warning before the next block. With a countdown the numbers carry the timing,
        # so the warning just names what's next, a little earlier.
        if b.countdown and nxt_item is not None and nxt:
            out.append(Candidate(end - 11, f"Next up, {nxt}.", "heads-up", end - COUNTDOWN_SECONDS, block_index=i))
        elif d >= 30 and nxt_item is not None:
            if nxt_item.block.role == "rest" and not nxt_item.block.intro:
                text = "Ten seconds."
            elif nxt:
                text = f"Ten seconds. Next up, {nxt}."
            else:
                text = "Ten seconds."
            out.append(Candidate(end - HEADS_UP_SECONDS, text, "heads-up", end, block_index=i))

        # Easier alternative, once, the first time a block appears.
        if first_use and b.easier and d >= 40:
            out.append(Candidate(start + EASIER_AT_SECONDS, f"Easier option: {b.easier}", "easier", end, slide=8, block_index=i))

        # Block-specific lines.
        for cue in b.cues:
            if cue.at == "half":
                at = d / 2
            elif cue.at < 0:
                at = d + cue.at
            else:
                at = cue.at
            if 0 < at < d:
                out.append(Candidate(start + at, cue.text, "authored", end, slide=5, block_index=i))

        # Orientation in long blocks.
        if d >= 150:
            for k in range((d - 20) // 60, 0, -1):
                line = "One minute left in this one." if k == 1 else f"{words(k).capitalize()} minutes left in this one."
                out.append(Candidate(end - 60 * k, line, "minutes-left", end, slide=4, block_index=i))

    for t, line in CHECKPOINTS:
        for i, item in enumerate(plan):
            if item.start <= t < item.end:
                out.append(Candidate(float(t), line, "checkpoint", float(item.end), slide=CHECKPOINT_SLIDE_SECONDS, block_index=i))
                break
    return out


def place(cands: list[Candidate], durations: dict[str, float]) -> tuple[list[PlacedCue], list[dict]]:
    """Fit cues into the timeline by priority. Returns (placed, dropped)."""
    placed: list[PlacedCue] = []
    dropped: list[dict] = []
    errors: list[str] = []

    def free(t: float, dur: float, kind: str) -> bool:
        for p in placed:
            gap = COUNTDOWN_GAP_SECONDS if "countdown" in (kind, p.kind) else GAP_SECONDS
            if not (t + dur + gap <= p.time or t >= p.end + gap):
                return False
        return True

    for c in sorted(cands, key=lambda c: (c.priority, c.time)):
        dur = durations[c.text]
        t, chosen = c.time, None
        while t <= c.time + c.slide + 1e-9:
            if t + dur <= c.window_end and free(t, dur, c.kind):
                chosen = t
                break
            t += 0.5
        if chosen is None:
            if c.required:
                errors.append(f"'{c.text}' ({dur:.1f}s at {fmt(c.time)}) does not fit before {fmt(c.window_end)}")
            else:
                dropped.append({"time": round(c.time, 2), "text": c.text, "kind": c.kind})
            continue
        placed.append(PlacedCue(chosen, dur, c.text, c.kind, c.block_index))

    if errors:
        raise LibraryError("required cues do not fit; shorten their text:\n- " + "\n- ".join(errors))
    placed.sort(key=lambda p: p.time)
    check_cues(placed)
    return placed, dropped


def check_cues(placed: list[PlacedCue]) -> None:
    for a, b in zip(placed, placed[1:]):
        if a.end > b.time:
            raise LibraryError(f"cue overlap at {fmt(b.time)}: '{a.text}' / '{b.text}'")
    if placed and placed[-1].end > TOTAL_SECONDS:
        raise LibraryError("last cue runs past 45:00")


def fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"
