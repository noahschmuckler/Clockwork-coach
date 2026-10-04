"""Load and validate the block library, templates and music list."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = ROOT / "library"

ROLES = {"session", "warmup", "work", "rest", "cooldown"}
MOODS = {"warm", "drive", "calm"}


class LibraryError(ValueError):
    """The library, a template or a plan breaks a rule."""


@dataclass(frozen=True)
class Cue:
    at: object  # int seconds, negative = from end, or "half"
    text: str


@dataclass(frozen=True)
class Block:
    id: str
    name: str
    role: str
    families: tuple[str, ...]
    min_seconds: int
    max_seconds: int
    intro: str
    easier: str
    cues: tuple[Cue, ...]
    switch: str  # spoken exactly at the halfway point ("Switch sides."), or ""
    countdown: bool  # "Five ... One" landing exactly on the switch and on the block end
    tags: dict = field(hash=False, compare=False)

    def allows(self, seconds: int) -> bool:
        return self.min_seconds <= seconds <= self.max_seconds


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str
    mood: str
    url: str
    page: str
    license: str
    license_url: str
    filename: str


@dataclass
class Library:
    blocks: dict[str, Block]
    templates: dict[str, dict]
    tracks: list[Track]

    def block(self, block_id: str) -> Block:
        try:
            return self.blocks[block_id]
        except KeyError:
            raise LibraryError(f"unknown block id '{block_id}'") from None

    def family(self, families: list[str]) -> list[Block]:
        wanted = set(families)
        return [b for b in self.blocks.values() if wanted & set(b.families)]


def _parse_block(raw: dict, source: str) -> Block:
    where = f"{source}: block '{raw.get('id', '?')}'"
    for key in ("id", "name", "role", "families", "seconds", "intro", "tags"):
        if key not in raw:
            raise LibraryError(f"{where} is missing '{key}'")
    unknown = set(raw) - {"id", "name", "role", "families", "seconds", "intro", "easier", "cues", "switch", "countdown", "tags"}
    if unknown:
        raise LibraryError(f"{where} has unknown fields {sorted(unknown)}")
    if raw["role"] not in ROLES:
        raise LibraryError(f"{where} has role '{raw['role']}', expected one of {sorted(ROLES)}")
    tags = raw["tags"] or {}
    # Quiet-room and joint-protection constraints from the spec, enforced for every block.
    if tags.get("impact") != "low":
        raise LibraryError(f"{where} must be tagged impact: low")
    if tags.get("noise") != "quiet":
        raise LibraryError(f"{where} must be tagged noise: quiet")
    secs = raw["seconds"]
    lo, hi = int(secs["min"]), int(secs["max"])
    if not 0 < lo <= hi:
        raise LibraryError(f"{where} has an invalid seconds range {lo}-{hi}")
    cues = []
    for c in raw.get("cues") or []:
        if not isinstance(c, dict) or set(c) != {"at", "text"}:
            # Usually an unquoted comma inside {at: .., text: ..}: quote the text.
            raise LibraryError(f"{where} has a malformed cue {c!r}; quote text that contains commas")
        at = c["at"]
        if not (at == "half" or isinstance(at, int)):
            raise LibraryError(f"{where} cue 'at' must be an integer or 'half', got {at!r}")
        if not str(c.get("text", "")).strip():
            raise LibraryError(f"{where} has a cue with no text")
        cues.append(Cue(at=at, text=str(c["text"]).strip()))
    switch = str(raw.get("switch") or "").strip()
    countdown = raw.get("countdown", False)
    if not isinstance(countdown, bool):
        raise LibraryError(f"{where}: countdown must be true or false")
    if countdown and lo < (40 if switch else 30):
        raise LibraryError(f"{where}: a countdown needs a minimum of {40 if switch else 30} seconds")
    if raw["role"] not in ("session", "rest") and not str(raw["name"]).strip():
        raise LibraryError(f"{where} needs a spoken name")
    return Block(
        id=str(raw["id"]),
        name=str(raw["name"] or "").strip(),
        role=raw["role"],
        families=tuple(raw["families"] or ()),
        min_seconds=lo,
        max_seconds=hi,
        intro=str(raw["intro"] or "").strip(),
        easier=str(raw.get("easier") or "").strip(),
        cues=tuple(cues),
        switch=switch,
        countdown=countdown,
        tags=tags,
    )


def load(library_dir: Path = LIBRARY_DIR) -> Library:
    blocks: dict[str, Block] = {}
    for path in sorted((library_dir / "blocks").glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text()) or []:
            block = _parse_block(raw, path.name)
            if block.id in blocks:
                raise LibraryError(f"duplicate block id '{block.id}' in {path.name}")
            blocks[block.id] = block

    templates: dict[str, dict] = {}
    for path in sorted((library_dir / "templates").glob("*.yaml")):
        tpl = yaml.safe_load(path.read_text())
        if tpl.get("id") != path.stem:
            raise LibraryError(f"{path.name}: id must match the file name '{path.stem}'")
        for section in tpl.get("sections", []):
            if section.get("mood") not in MOODS:
                raise LibraryError(f"{path.name}: section '{section.get('name')}' needs a mood in {sorted(MOODS)}")
        templates[tpl["id"]] = tpl

    tracks: list[Track] = []
    tracks_file = library_dir / "music" / "tracks.yaml"
    if tracks_file.exists():
        for raw in yaml.safe_load(tracks_file.read_text()) or []:
            if raw.get("mood") not in MOODS:
                raise LibraryError(f"tracks.yaml: track '{raw.get('id')}' needs a mood in {sorted(MOODS)}")
            tracks.append(Track(**raw))

    return Library(blocks=blocks, templates=templates, tracks=tracks)
