import json
import shutil
import subprocess

import pytest

from clockwork import cues, focus, library
from clockwork.library import LibraryError
from clockwork.render import speak_all
from clockwork.tts import SAMPLE_RATE, Speaker

BREAKS = focus.load_breaks()
WORDING = focus.load_wording()
LIB = library.load()
X = int(focus.XFADE * focus.OUT_RATE)

REQUEST = """
block_start: "13:00"
items:
  - {quest: q1, say: "Board memo", minutes: 20, brief: "Finish the outline."}
  - {break: walk, minutes: 4}
  - {quest: q2, say: "Inbox to zero", minutes: 15}
  - {break: stretch, minutes: 3}
  - {quest: q3, say: "Chart review", minutes: 14}
wrap_up_minutes: 2
"""


def parse(body, seed=1):
    return focus.parse_request(body, seed, set(BREAKS))


def placed_for(req, speaker=None):
    speaker = speaker or Speaker("tone")
    items, total = req["items"], req["total"]
    stretches = focus.stretch_slots(LIB, items, req["seed"], BREAKS)
    cands, skipped, final = focus.candidates(items, total, WORDING[req["intensity"]], BREAKS, stretches)
    clips = speak_all(speaker, {c.text for c in cands} | {final})
    dur = {t: len(a) / SAMPLE_RATE for t, a in clips.items()}
    cands.append(cues.Candidate(total - dur[final] - focus.FINAL_GAP, final, "final",
                                total - focus.FINAL_GAP + 1e-6, block_index=len(items) - 1))
    placed, dropped = cues.place(cands, dur, total=total)
    return items, stretches, placed, dropped + skipped


# ---------------------------------------------------------------- request validation

def test_valid_request_timeline():
    req = parse(REQUEST)
    items = req["items"]
    assert [i.kind for i in items] == ["quest", "break", "quest", "break", "quest", "wrapup"]
    assert req["total"] == (20 + 4 + 15 + 3 + 14 + 2) * 60
    assert [i.start for i in items] == [0, 1200, 1440, 2340, 2520, 3360]
    assert req["intensity"] == "normal" and req["sound"] == "default" and req["seed"] == 1


@pytest.mark.parametrize("body, message", [
    ("items: [{quest: q1, minutes: 10}]\ncolour: red", "unknown request fields"),
    ("items: [{quest: q1, minutes: 10}, {quest: q1, minutes: 10}]", "duplicate quest id"),
    ("items: [{quest: q1, minutes: 10, say: 'see https://x.org'}]", "link"),
    ("items: [{quest: q1, minutes: 10, say: '" + "x" * 61 + "'}]", "limit is 60"),
    ("items: [{quest: q1, minutes: 1}]", "from 2 to 90"),
    ("items: [{quest: q1, minutes: 90}, {quest: q2, minutes: 90}, {quest: q3, minutes: 5}]", "limit is 180"),
    ("items: [{break: walk, minutes: 5}]", "at least one quest"),
    ("items: [{break: nap, minutes: 5}, {quest: q1, minutes: 5}]", "break must be one of"),
    ("items: [{quest: q1, minutes: 10, checkins: [left:12]}]", "between 1 and 9"),
    ("items: [{quest: q1, minutes: 10, checkins: [soon]}]", "unknown check-in"),
    ("items: [{quest: q1, minutes: 10, colour: red}]", "unknown fields"),
    ("intensity: brutal\nitems: [{quest: q1, minutes: 10}]", "intensity"),
    ("just some words", "items"),
])
def test_rejections(body, message):
    with pytest.raises(LibraryError, match=message):
        parse(body)


def test_unquoted_block_start_is_understood():
    # YAML 1.1 reads 13:00 as a sexagesimal number; accept it anyway.
    assert parse("block_start: 13:00\nitems: [{quest: q1, minutes: 5}]")["block_start"] == "13:00"


# ---------------------------------------------------------------- spoken lines

def test_lines_stay_inside_items_and_final_ends_on_time():
    req = parse(REQUEST)
    items, _, placed, _ = placed_for(req)
    for c in placed:
        it = items[c.block_index]
        assert it.start <= c.time and c.end <= it.end + 1e-6, c
    final = [c for c in placed if c.kind == "final"]
    assert len(final) == 1 and abs(final[0].end - (req["total"] - focus.FINAL_GAP)) < 1e-6
    starts = {c.block_index for c in placed if c.kind == "start"}
    assert starts == set(range(len(items)))


def test_default_checkins_follow_thresholds():
    req = parse(REQUEST)
    items, _, placed, _ = placed_for(req)
    by_item = {i: [c for c in placed if c.block_index == i and c.kind == "checkin"] for i in range(len(items))}
    assert len(by_item[0]) == 2  # 20 min: halfway + five left
    assert [c.time for c in by_item[0]] == [600, 900]
    assert len(by_item[2]) == 1 and by_item[2][0].time == 1440 + 600  # 15 min: five left only
    assert by_item[4] == []  # 14 min: silent


def test_never_says_done():
    items, _, placed, _ = placed_for(parse(REQUEST))
    assert not any(" done" in c.text.lower() for c in placed if c.kind != "final")
    assert any("Time's up for Board memo" in c.text for c in placed)


def test_stretch_break_uses_mobility_blocks_with_exact_countdowns():
    req = parse(REQUEST)
    items, stretches, placed, _ = placed_for(req)
    (i, slots), = stretches.items()
    assert items[i].break_type == "stretch" and len(slots) == 3
    assert all("mobility" in s.block.families for s in slots)
    assert len({s.block.id for s in slots}) == 3
    for s in slots:
        mine = sorted(c.time for c in placed if c.kind == "countdown" and s.start <= c.time < s.start + 60)
        expected = ([s.start + 25] if s.block.switch else []) + ([s.start + 55] if s.block.countdown else [])
        assert mine == expected


def test_custom_checkins_and_early_prompt_reported():
    body = "items: [{quest: q1, minutes: 10, checkins: [in:3, left:1], prompts: [{at_minute: 0.2, text: Breathe.}]}]"
    items, _, placed, dropped = placed_for(parse(body))
    texts = [c.text for c in placed]
    assert "Three minutes into Quest one." in texts and "One minute left on Quest one." in texts
    assert any(d["text"] == "Breathe." and d["why"] == "first 30 s" for d in dropped)


def test_firm_wording():
    req = parse("intensity: firm\nitems: [{quest: q1, minutes: 5, say: Memo}, {quest: q2, minutes: 5, say: Mail}]")
    _, _, placed, _ = placed_for(req)
    assert any(c.text.startswith("Time. Close out Memo now.") for c in placed)


# ---------------------------------------------------------------- music fitting

@pytest.mark.parametrize("length_s, total_s", [(3600, 2700), (3600, 3600), (2400, 3000), (1500, 7200), (1500, 1510)])
def test_arc_keeps_ramps_and_splices_only_in_plateau(length_s, total_s):
    sr, ru, rd = focus.OUT_RATE, 360, 300
    L, T = length_s * sr, total_s * sr
    segs = focus.plan_arc(L, T, ru * sr, rd * sr, X)
    assert segs[0].dst == 0 and segs[0].src == 0  # ramp-up from the very start
    last = segs[-1]
    assert last.dst + last.n == T and last.src + last.n == L  # ramp-down ends exactly at the end
    for s in segs:
        assert 0 <= s.src and s.src + s.n <= L
        if s.fade_in:  # every splice point is inside the steady plateau, both in source and output
            assert ru * sr <= s.src and s.src + s.fade_in <= L - rd * sr
            assert ru * sr <= s.dst <= T - rd * sr
        if s.fade_out:
            assert ru * sr <= s.src + s.n - s.fade_out and s.src + s.n <= L - rd * sr


@pytest.mark.parametrize("length_s, total_s, offset_s", [(180, 3540, 0), (180, 181, 0), (600, 300, 120), (100, 100, 0)])
def test_loop_totals_exact(length_s, total_s, offset_s):
    sr = focus.OUT_RATE
    segs = focus.plan_loop(length_s * sr, total_s * sr, X, offset=offset_s * sr)
    assert segs[-1].dst + segs[-1].n == total_s * sr
    assert all(s.src + s.n <= length_s * sr for s in segs)


def test_arc_track_too_short_session_rejected(tmp_path):
    (tmp_path / "t.yaml").write_text("- {id: deep, file: deep.mp3, default: true, arc: {ramp_up: 360, ramp_down: 300}}\n")
    (tmp_path / "deep.mp3").write_bytes(b"x")
    with pytest.raises(LibraryError, match="at least 12 minutes"):
        focus.render("items: [{quest: q1, minutes: 10}]", tmp_path / "out", "1", "tone",
                     music_config=tmp_path / "t.yaml", music_dir=tmp_path)


def test_missing_track_falls_back_to_noise_with_warning(tmp_path):
    (tmp_path / "t.yaml").write_text("- {id: deep, file: deep.mp3, default: true}\n")
    track, warnings = focus.resolve_sound("default", focus.load_tracks(tmp_path / "t.yaml"), tmp_path)
    assert track is None and "isn't uploaded yet" in warnings[0]


# ---------------------------------------------------------------- end to end (needs ffmpeg)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
def test_render_noise_session(tmp_path):
    body = "items: [{quest: q1, minutes: 2, say: Memo}, {break: water, minutes: 1}, {quest: q2, minutes: 2}]"
    m = focus.render(body, tmp_path, "3", "tone")
    assert m["total_seconds"] == 300 and m["sound_used"] == "brown noise"
    assert abs(m["checks"]["decoded_seconds"] - 300) <= focus.DECODE_TOLERANCE
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate",
                            "-of", "json", str(tmp_path / "focus-3.m4a")], capture_output=True, text=True).stdout
    stream = json.loads(probe)["streams"][0]
    assert stream["channels"] == 2 and stream["sample_rate"] == "44100"
    assert (tmp_path / "summary.md").read_text().count("|") > 10


@needs_ffmpeg
def test_render_with_arc_track(tmp_path):
    # A 14-minute stand-in track; the session (13 min) must start at its start and end at its end.
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:duration=840", "-ac", "2",
                    str(tmp_path / "deep.wav")], check=True)
    (tmp_path / "t.yaml").write_text(
        "- {id: deep, title: Test track, file: deep.wav, default: true, arc: {ramp_up: 360, ramp_down: 300}}\n")
    m = focus.render("items: [{quest: q1, minutes: 13}]", tmp_path / "out", "4", "tone",
                     music_config=tmp_path / "t.yaml", music_dir=tmp_path)
    segs = m["music_segments"]
    assert segs[0]["src"] == 0 and segs[0]["dst"] == 0
    assert abs(segs[-1]["src"] + segs[-1]["seconds"] - 840) < 0.01
    assert abs(segs[-1]["dst"] + segs[-1]["seconds"] - 780) < 0.01
    assert m["sound_used"] == "Test track"
