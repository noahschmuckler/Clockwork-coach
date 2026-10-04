"""Turn a template + seed (or a custom plan) into a validated 2,700-second timeline."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .library import MOODS, Block, Library, LibraryError

TOTAL_SECONDS = 2700
MIN_WARMUP_SECONDS = 180
MIN_COOLDOWN_SECONDS = 180
MAX_CONTINUOUS_WORK_SECONDS = 900
DEFAULT_REST_BLOCK = "rest-breathe"


@dataclass
class PlanItem:
    block: Block
    seconds: int
    section: str
    mood: str
    start: int = 0

    @property
    def end(self) -> int:
        return self.start + self.seconds


def choose_template(lib: Library, template: str, seed: int) -> str:
    if template == "any":
        return random.Random(f"any:{seed}").choice(sorted(lib.templates))
    if template not in lib.templates:
        raise LibraryError(f"unknown template '{template}'. Available: {', '.join(sorted(lib.templates))}, any")
    return template


def expand_template(lib: Library, template_id: str, seed: int) -> list[PlanItem]:
    tpl = lib.templates[template_id]
    rng = random.Random(f"{template_id}:{seed}")
    used: set[str] = set()
    items: list[PlanItem] = []

    def pick(families, seconds: int) -> Block:
        fams = [families] if isinstance(families, str) else list(families)
        pool = sorted(
            (b for b in lib.family(fams) if b.allows(seconds) and b.role != "session"),
            key=lambda b: b.id,
        )
        if not pool:
            raise LibraryError(f"no block in families {fams} allows {seconds} seconds")
        fresh = [b for b in pool if b.id not in used]
        if not fresh:
            # Library too small for full variety: allow a repeat, but never back to back.
            prev = items[-1].block.id if items else None
            fresh = [b for b in pool if b.id != prev] or pool
        choice = rng.choice(fresh)
        used.add(choice.id)
        return choice

    for section in tpl["sections"]:
        name, mood = section["name"], section["mood"]
        for step in section["steps"]:
            if "block" in step:
                block = lib.block(step["block"])
                used.add(block.id)
                items.append(PlanItem(block, int(step["seconds"]), name, mood))
            elif "pick" in step:
                for _ in range(int(step.get("count", 1))):
                    items.append(PlanItem(pick(step["pick"], int(step["seconds"])), int(step["seconds"]), name, mood))
            elif "circuit" in step:
                c = step["circuit"]
                work, rest = int(c["work"]), int(c["rest"])
                rest_block = lib.block(c.get("rest_block", DEFAULT_REST_BLOCK))
                stations = [pick(fam, work) for fam in c["stations"]]
                for _ in range(int(c["rounds"])):
                    for station in stations:
                        items.append(PlanItem(station, work, name, mood))
                        items.append(PlanItem(rest_block, rest, name, mood))
            else:
                raise LibraryError(f"template '{template_id}': unknown step {step}")
    return _assign_starts(items)


def plan_from_custom(lib: Library, raw_items: list) -> list[PlanItem]:
    """A plan written out block by block (for example by the agent)."""
    if not isinstance(raw_items, list) or not raw_items:
        raise LibraryError("plan must be a non-empty list of {block, seconds} entries")
    default_mood = {"session": "warm", "warmup": "warm", "work": "drive", "rest": "drive", "cooldown": "calm"}
    items = []
    for i, raw in enumerate(raw_items, 1):
        if not isinstance(raw, dict) or "block" not in raw or "seconds" not in raw:
            raise LibraryError(f"plan entry {i} needs 'block' and 'seconds'")
        block = lib.block(str(raw["block"]))
        seconds = raw["seconds"]
        if not isinstance(seconds, int):
            raise LibraryError(f"plan entry {i} ({block.id}): seconds must be a whole number")
        mood = raw.get("mood", default_mood[block.role])
        if mood not in MOODS:
            raise LibraryError(f"plan entry {i} ({block.id}): mood must be one of {sorted(MOODS)}")
        items.append(PlanItem(block, seconds, str(raw.get("section", block.role.title())), mood))
    return _assign_starts(items)


def _assign_starts(items: list[PlanItem]) -> list[PlanItem]:
    t = 0
    for item in items:
        item.start = t
        t += item.seconds
    return items


def validate_plan(plan: list[PlanItem]) -> None:
    """Raise LibraryError listing every rule the plan breaks."""
    errors = []
    for i, item in enumerate(plan, 1):
        b = item.block
        if not b.allows(item.seconds):
            errors.append(f"entry {i} ({b.id}): {item.seconds}s is outside its allowed {b.min_seconds}-{b.max_seconds}s")
    total = sum(item.seconds for item in plan)
    if total != TOTAL_SECONDS:
        errors.append(f"total is {total}s; it must be exactly {TOTAL_SECONDS}s (45:00)")

    body = [item for item in plan if item.block.role != "session"]
    warm = 0
    for item in body:
        if item.block.role != "warmup":
            break
        warm += item.seconds
    if warm < MIN_WARMUP_SECONDS:
        errors.append(f"plan must open with at least {MIN_WARMUP_SECONDS}s of warm-up blocks (has {warm}s)")
    cool = 0
    for item in reversed(body):
        if item.block.role != "cooldown":
            break
        cool += item.seconds
    if cool < MIN_COOLDOWN_SECONDS:
        errors.append(f"plan must end with at least {MIN_COOLDOWN_SECONDS}s of cool-down blocks (has {cool}s)")

    run = 0
    for item in plan:
        run = run + item.seconds if item.block.role == "work" else 0
        if run > MAX_CONTINUOUS_WORK_SECONDS:
            errors.append(f"more than {MAX_CONTINUOUS_WORK_SECONDS}s of work without rest, ending at {item.block.id}")
            break

    if errors:
        raise LibraryError("plan rejected:\n- " + "\n- ".join(errors))
