Hi Instinct. Thanks for the capability answers. Your idea of starting a render by opening an issue is now the design, and the tool is built. Here is how to use it.

## What you need
- The GitHub connector you already have, able to **create issues** and **read issue comments** in `noahschmuckler/Clockwork-coach`. Viewing the issue page in your browser works as well.
- Nothing else: no token, no API key, no ElevenLabs, no computer.

## Requesting a workout
Open **one new issue per workout**:

- **Title:** must start with `render`, for example `render: workout for Oct 5`. Issues with other titles are ignored.
- **Body:** a few `key: value` lines:

```yaml
template: any
seed: 4417
music: on
```

- `template`: `any` (the seed picks a template), `circuit-45`, `intervals-45` or `steady-45`. The current list is in the repo under `library/templates/`.
- `seed`: any whole number. The same template and seed always give the same workout. Use a new seed each time for variety. If you leave it out, the issue number is used.
- `music`: `on` or `off`.
- Every field is optional. An empty body gives a valid workout.

Only issues opened by Noah's GitHub account start a render. If your issues show a different author, tell Noah: he can add that login to the `RENDER_REQUESTERS` repository variable.

## Getting the result
Check the issue every 5 minutes. A render usually takes 3–10 minutes.

1. A "Rendering now…" comment means it has started. If there's no comment within about 10 minutes, the request was ignored (check the title prefix and the author).
2. **Success:** a comment starts with **Workout ready:** and links to a `clockwork-<number>.m4a` file. A table of the workout's sections follows. The issue is then closed as completed.
3. **Failure:** a comment starts with **Render failed.** and gives the reason. The issue is closed as not planned. Fix the request and open a *new* issue. Don't retry in a loop. If the same request fails twice, tell Noah.

Send Noah the `.m4a` link and a one-line summary from the table by iMessage. Send the link, not the file: it is about 20 MB and downloads straight to his phone.

Each workout is also stored permanently as a GitHub Release named `workout-<issue number>`, along with `manifest.json` (every cue with its timestamp) and `CREDITS.txt`.

## Custom plans (optional)
Instead of a template and seed, you can lay out the blocks yourself:

```yaml
music: on
plan:
  - {block: session-open, seconds: 20}
  - {block: march-in-place, seconds: 600}
  - ...
```

- Only use block IDs from `library/blocks/`, and keep each block's duration inside its `seconds` range.
- The whole plan must total exactly 2700 seconds.
- The plan must start with at least 180 s of warm-up blocks and end with at least 180 s of cool-down blocks. Work can't run longer than 900 s without a rest.
- The validator decides. If it rejects a plan, fix the plan.
- You don't invent exercises or wording. To propose new blocks, open a pull request against `library/`, and Noah reviews it.

## Rules
- **The repo is public**, so issues are visible to anyone. Never put health details, sleep or energy data, Noah's location or any credentials in an issue.
- Open one issue per workout Noah asks for. Don't render ahead of time just in case.
- Don't edit workflows, settings or anything outside `library/`. Library changes go through pull requests.
- If Noah reports pain or concerning symptoms, the answer is to stop, not to send another workout.

## Current limits
- The exercise library is a **placeholder** that Noah hasn't reviewed yet. Every result says so.
- The voice is Piper's "norman" voice. It sounds plain but is clear.
- Pre-workout energy and sleep inputs are a later phase.
