import pytest

from clockwork import cues, library, planner, render, request
from clockwork.library import LibraryError
from clockwork.tts import SAMPLE_RATE, Speaker

LIB = library.load()


@pytest.mark.parametrize("template", sorted(LIB.templates))
def test_every_template_totals_45_minutes(template):
    for seed in range(50):
        plan = planner.expand_template(LIB, template, seed)
        planner.validate_plan(plan)
        assert plan[-1].end == planner.TOTAL_SECONDS


def test_same_seed_same_plan_different_seed_varies():
    a = [i.block.id for i in planner.expand_template(LIB, "circuit-45", 4417)]
    b = [i.block.id for i in planner.expand_template(LIB, "circuit-45", 4417)]
    variants = {tuple(i.block.id for i in planner.expand_template(LIB, "circuit-45", s)) for s in range(20)}
    assert a == b
    assert len(variants) > 10


def test_any_template_is_deterministic():
    assert planner.choose_template(LIB, "any", 5) == planner.choose_template(LIB, "any", 5)
    assert {planner.choose_template(LIB, "any", s) for s in range(30)} == set(LIB.templates)


def test_custom_plan_wrong_total_rejected():
    plan = planner.plan_from_custom(LIB, [{"block": "march-in-place", "seconds": 600}])
    with pytest.raises(LibraryError, match="exactly 2700"):
        planner.validate_plan(plan)


def test_custom_plan_out_of_bounds_and_no_cooldown_rejected():
    raw = [{"block": "march-in-place", "seconds": 300}, {"block": "wall-plank", "seconds": 2400}]
    with pytest.raises(LibraryError) as exc:
        planner.validate_plan(planner.plan_from_custom(LIB, raw))
    assert "outside its allowed" in str(exc.value)
    assert "cool-down" in str(exc.value)


def test_unknown_block_rejected():
    with pytest.raises(LibraryError, match="unknown block"):
        planner.plan_from_custom(LIB, [{"block": "burpees", "seconds": 60}])


def test_valid_custom_plan_accepted():
    raw = [
        {"block": "session-open", "seconds": 20},
        {"block": "march-in-place", "seconds": 600},
        {"block": "shadow-boxing", "seconds": 600},
        {"block": "recovery-walk", "seconds": 300},
        {"block": "step-touch", "seconds": 600},
        {"block": "slow-march-down", "seconds": 280},
        {"block": "deep-breathing", "seconds": 280},
        {"block": "session-close", "seconds": 20},
    ]
    planner.validate_plan(planner.plan_from_custom(LIB, raw))


def test_request_parsing():
    r = request.parse("```yaml\ntemplate: steady-45\nseed: 12\nmusic: off\n```", 99)
    assert r == {"template": "steady-45", "seed": 12, "music": "off", "plan": None}
    assert request.parse("", 7)["seed"] == 7
    with pytest.raises(LibraryError):
        request.parse("tempo: fast", 1)
    with pytest.raises(LibraryError):
        request.parse("seed: -3", 1)


@pytest.mark.parametrize("template", sorted(LIB.templates))
def test_cues_fit_without_overlap(template):
    speaker = Speaker("tone")
    for seed in range(5):
        plan = planner.expand_template(LIB, template, seed)
        placed, _ = _place(speaker, plan)
        for cue in placed:
            item = plan[cue.block_index]
            assert item.start <= cue.time and cue.end <= item.end
        # Every block boundary is announced.
        starts = {c.block_index for c in placed if c.kind == "start"}
        assert starts == set(range(len(plan)))


def _place(speaker, plan):
    cands = cues.candidates(plan)
    clips = render.speak_all(speaker, {c.text for c in cands})
    return cues.place(cands, {t: len(a) / SAMPLE_RATE for t, a in clips.items()})


@pytest.mark.parametrize("template", sorted(LIB.templates))
def test_switch_and_countdown_land_exactly(template):
    speaker = Speaker("tone")
    for seed in range(5):
        plan = planner.expand_template(LIB, template, seed)
        placed, dropped = _place(speaker, plan)
        for i, item in enumerate(plan):
            b = item.block
            mine = [c for c in placed if c.block_index == i]
            if b.switch:
                assert [c.time for c in mine if c.kind == "switch"] == [item.start + item.seconds / 2]
            if b.countdown:
                targets = ([item.seconds / 2] if b.switch else []) + [item.seconds]
                counts = [c for c in mine if c.kind == "countdown"]
                assert [c.time for c in counts] == [item.start + t - cues.COUNTDOWN_SECONDS for t in targets]
                assert all(c.end <= c.time + cues.COUNTDOWN_SECONDS for c in counts)


@pytest.mark.parametrize("template", sorted(LIB.templates))
def test_choreographed_lines_are_never_dropped(template):
    # Sun salutations and mobility stretches are sequences: losing a line breaks them.
    speaker = Speaker("tone")
    for seed in range(10):
        plan = planner.expand_template(LIB, template, seed)
        _, dropped = _place(speaker, plan)
        for d in dropped:
            item = next(i for i in plan if i.start <= d["time"] < i.end)
            if {"flow", "mobility"} & set(item.block.families):
                assert d["kind"] not in ("authored", "heads-up"), (item.block.id, d)


def test_flex_never_repeats_a_flow():
    for seed in range(50):
        flows = [i.block.id for i in planner.expand_template(LIB, "flex-45", seed) if "flow" in i.block.families]
        assert len(flows) == 2 and len(set(flows)) == 2
