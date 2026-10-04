"""Parse a render request, e.g. the body of a GitHub issue.

Accepted form (YAML, optionally inside a ``` fence):

    template: circuit-45     # or any, intervals-45, steady-45 ...; default any
    seed: 4417              # whole number; default comes from the issue number
    music: on               # on | off; default on
    plan:                   # optional, replaces template + seed
      - {block: session-open, seconds: 20}
      - ...
"""

from __future__ import annotations

import re

import yaml

from .library import LibraryError

KEYS = {"template", "seed", "music", "plan"}


def parse(body: str, default_seed: int) -> dict:
    text = body or ""
    fence = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    text = text.strip()
    try:
        data = yaml.safe_load(text) if text else {}
    except yaml.YAMLError as exc:
        raise LibraryError(f"request is not valid YAML: {exc}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise LibraryError("request must be 'key: value' lines (template, seed, music, plan)")
    unknown = set(data) - KEYS
    if unknown:
        raise LibraryError(f"unknown request fields {sorted(unknown)}; allowed: {sorted(KEYS)}")

    music = data.get("music", "on")
    if isinstance(music, bool):  # YAML reads on/off as booleans
        music = "on" if music else "off"
    if music not in ("on", "off"):
        raise LibraryError("music must be on or off")

    seed = data.get("seed", default_seed)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise LibraryError("seed must be a whole number from 0 to 2147483647")

    return {
        "template": str(data.get("template", "any")).strip(),
        "seed": seed,
        "music": music,
        "plan": data.get("plan"),
    }
