# Changelog

The newest entry comes first. Agents should read this before using the tool after an update. Usage rules are in [`agent-brief.md`](agent-brief.md).

## 2026-10-04: flexibility additions (v0.2)

### What changed
- **New blocks** (based on Instinct's proposal, revised):
  - 8 mobility stretches (family `mobility`, fixed 60 s, 30 s a side).
  - 2 standing sun salutations (family `flow`, fixed 90 s): `sun-salutation-standing` and `sun-salutation-wall`.
  - `sun-salutation-lunge` (family `flow-lunge`, 120 s). No template picks it, so it's custom-plan only until Noah reviews it.
  - 4 posterior-chain cool-down stretches (family `stretch`, fixed 90 s).
- **New template `flex-45`:** mobility warm-up plus a sun salutation, lighter circuits, a mid-session flow and a long stretch cool-down. It always uses both 90 s flows and never repeats one.
- **Warm-ups:** `circuit-45` and `steady-45` now include mobility stretches in the warm-up.
- **New block fields:**
  - `switch:` is spoken exactly at the halfway point.
  - `countdown: true` speaks "Five. Four. Three. Two. One." one word per second, with "One" exactly 1 s before the switch and 1 s before the block ends.
  - Countdown blocks get a short "Next up, X." instead of "Ten seconds. Next up, X.", so the warning isn't doubled.
- **Existing blocks:** halfway lines such as "Switch legs." and "Switch direction." now use `switch:`, so they land exactly at halfway.
- **Cue priority:** a block's own written cues now take priority over the "Easier option" line, so choreographed sequences aren't cut. The start line, switch and countdown are required: if one can't fit, the render fails instead of silently dropping it.
- **Safety wording:**
  - Chair hamstring stretches now say "Hold a wall for balance".
  - Forward folds now say "Come up slowly, one vertebra at a time" before they end.
- **Documentation:** the field guide at the top of `library/blocks/warmup.yaml` now lists every line the tool speaks by itself, with the priority order.

### Bugs fixed
- **Commas silently cut cue text.** In YAML, `{at: 300, text: Stay relaxed. Soft, quiet feet.}` was read as "Stay relaxed. Soft". It affected one existing march cue. All such lines are now quoted, and the loader rejects malformed cues.
- **Countdown voice garbled.** Piper mangles single words spoken on their own ("Four." came out as 3.3 s of audio). The countdown is now spoken as one sentence and split at the pauses. If a split ever fails, the countdown still ends on time and the result shows a ⚠️ warning.
- Two test renders (issues #4 and #5) failed on the countdown bug before the fix. Both failed safely: nothing was published, and each issue was closed with the reason.

### Verified
- **Real-voice `flex-45` render:** issue #6, `workout-6`.
  - Exactly 45:00 when decoded, with 163 spoken lines and none dropped.
  - All 13 countdowns land on their marks.
- **Automated tests check:**
  - every template totals 2700 s;
  - switch and countdown timing;
  - no dropped flow or mobility lines;
  - no repeated flow in `flex-45`.

### Next steps
1. **Noah:** listen to `workout-6` (stretches start at 02:20). Judge the countdown pacing and how much talking there is.
2. **Noah:** decide whether `sun-salutation-lunge` joins `flex-45`.
3. **Noah:** review the exercise library, which is still a placeholder.
4. **Later:** pre-workout energy/sleep selection, more templates, possibly a better voice.

## 2026-10-04: first working version (v0.1)

- Workouts are requested by opening a GitHub issue titled `render…` and are built on GitHub Actions at no cost.
- Results are published as releases, and the download link is posted on the issue.
- Templates: `circuit-45`, `intervals-45`, `steady-45`.
- Voice: Piper, `en_US-norman-medium`. Music: Kevin MacLeod, CC BY 4.0.
- **Every output is checked:**
  - exactly 2700 s of audio, and the decoded file within 0.1 s of that;
  - no overlapping cues;
  - no clipping.
- Bug fixed: a YAML quoting error in the workflow file stopped the first request (issue #1) from triggering.
- Instinct's first request rendered and was delivered in about 90 seconds.
