# Clockwork Coach: agent brief

This is the reference for any AI agent (currently Instinct) that requests workouts for Noah. It is kept up to date and replaces earlier versions.

**Start here:** read [`CHANGELOG.md`](CHANGELOG.md) for what changed recently, bugs that were fixed and next steps. Check it whenever Noah says the tool was updated, and before proposing library changes.

## What you need
- The GitHub connector, able to **create issues** and **read issue comments** in `noahschmuckler/Clockwork-coach`. Viewing the issue page in a browser works as well.
- Nothing else: no token, API key, ElevenLabs account or computer.

## 1. Requesting a workout
Open **one new issue per workout**.

- **Title:** must start with `render`, for example `render: workout for Oct 5`. Issues with other titles are ignored.
- **Body:** a few `key: value` lines, all optional:

```yaml
template: any
seed: 4417
music: on
```

| Field | Values | Default |
|---|---|---|
| `template` | `any`, `circuit-45`, `intervals-45`, `steady-45`, `flex-45` | `any` (the seed picks a template) |
| `seed` | any whole number | the issue number |
| `music` | `on`, `off` | `on` |

- Only issues opened by Noah's GitHub account trigger a render. Your issues have worked so far. If that ever stops, tell Noah. He can add another login to the `RENDER_REQUESTERS` repository variable.

## 2. Getting the result
A render takes about 1.5 minutes. Check the issue about 5 minutes after opening it.

1. A **"Rendering now…"** comment means it started. If there's no comment within 10 minutes, the request was ignored: check the `render` title prefix.
2. **Success:** a comment starting **Workout ready:** links to `clockwork-<n>.m4a`, followed by a table of sections. The issue closes as *completed*.
3. **Failure:** a comment starting **Render failed.** gives the reason. The issue closes as *not planned*, and nothing is published. Fix the request and open a **new** issue. Don't retry in a loop. If the same request fails twice, tell Noah and include the error text.
4. **Warnings:** a success comment may include a ⚠️ line, such as music that couldn't be downloaded. The workout is still usable. Pass the warning on to Noah.

Send Noah the `.m4a` link plus a one-line summary taken from the table, by iMessage. Send the link, not the file: it's about 22 MB and downloads straight to his phone.

Every workout is kept permanently as a GitHub Release named `workout-<issue number>`. It holds the audio, `manifest.json` (every spoken line with its timestamp) and `CREDITS.txt`.

## 3. Choosing workouts (best practices)

| Template | Shape | Good for |
|---|---|---|
| `circuit-45` | 8:20 warm-up with 4 mobility stretches; two 12-minute circuits (3 rounds of 4 short stations with rest); recovery; 10:40 cool-down | Default variety day |
| `intervals-45` | Warm-up; two rounds of 2-minute intervals; a 5-station finisher; cool-down | Longer efforts with fewer changes |
| `steady-45` | Warm-up with mobility; one 10-minute and one 8-minute steady cardio block; short strength circuit; cool-down | Days for a steady rhythm |
| `flex-45` | Mobility warm-up plus a standing sun salutation; lighter circuits; a mid-session flow; long posterior-chain stretch cool-down | Easier, stretch-focused days |

- **Variety:** use a new seed every time. The same template and seed always give the same workout. Keep your own record of the seeds you've used; don't put that record in the repo.
- **Rotation:** `template: any` rotates through all templates. Choose a specific template when Noah asks for something particular.
- **Easier days:** after a rough night or a hard day, ask for `flex-45`, but never write the reason in the issue.
- **Repeating a favourite:** if Noah wants a past workout again, resend its existing link. Releases are permanent, so there's no need to render it again.
- **Music:** `music: off` gives voice only. Offer it if Noah finds the music distracting or wants it as quiet as possible.

## 4. Custom plans (optional)
Instead of a template and seed, you can lay out the blocks yourself:

```yaml
music: on
plan:
  - {block: session-open, seconds: 20}
  - {block: march-in-place, seconds: 600}
  - ...
```

- Use only block IDs that exist in `library/blocks/`, and keep each block's duration inside its `seconds` range. Many stretch and flow blocks have one fixed length.
- The total must be exactly **2700 seconds**.
- The plan must open with at least 180 s of warm-up blocks and end with at least 180 s of cool-down blocks. Work can't run longer than 900 s without a rest.
- The validator decides. If it rejects a plan, fix the plan rather than working around the rule.
- `sun-salutation-lunge` (120 s) can only be used through a custom plan for now, because no template includes it yet.

## 5. Proposing library changes
You don't invent exercises or wording in a render request. To add or change blocks or templates, open a **pull request** against `library/`, and Noah reviews and merges it. Renders always use the merged library on `main`.

Before opening the pull request, check it against this list:
- Read the field guide at the top of `library/blocks/warmup.yaml`. It lists the lines the tool **already speaks by itself**: the block start, "Easier option", "Ten seconds. Next up", minutes left in long blocks, and session checkpoints. Don't write those as cues.
- For a side change exactly at halfway, use `switch: Switch sides.`. For "Five … One" landing exactly on the switch and on the block end, use `countdown: true`. **Never hand-write a countdown or a "Ten seconds" line.**
- **Quote any cue text that contains a comma:** `{at: 20, text: "Slow breath, arch your back."}`. Without quotes, YAML cuts the line at the comma. The validator now rejects that.
- For choreographed sequences (flows):
  - Give the block one fixed length.
  - Put the easier option inside `intro`.
  - End the last cue at least 12 s before the block ends.
  - Space cues at least 6 s apart.
- **Keep intros short in short blocks.** The start line, switch and countdown are never dropped, so if they can't fit, the render fails. Rough guide: about 14 characters per second of speech.
- Every block must be tagged `impact: low` and `noise: quiet`. No jumping, no floor work and no dropping to the floor.
- Every template must total exactly 2700 s. Show the arithmetic in comments.
- The PR runs the **test** check automatically. If it fails, read the log and fix the PR.
- Write generic content only. Never mention Noah's health in the repo.

## 6. Rules
- **The repo is public.** Never put health details, sleep or energy data, Noah's location or any credentials in an issue, PR or file.
- Render only when Noah asks for a workout. Don't render ahead of time just in case.
- Don't edit workflows, settings or code. Your changes are limited to pull requests against `library/`.
- If Noah reports pain or concerning symptoms, tell him to stop, and don't send another workout.

## 7. Known limits
- The exercise library is a **placeholder** that Noah hasn't fully reviewed. Every result says so.
- The voice is Piper's "norman" voice: plain, but clear.
- Timing is exact only while the file plays uninterrupted. Pausing it moves the finish time.
- Inputs for pre-workout energy and sleep are a later phase. For now, express them through your choice of template.
