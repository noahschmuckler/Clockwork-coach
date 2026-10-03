Hi. I'm changing the plan for the Audio Workout Coach. Here is what changed, and what you need on your side to use the tool on your own.

## What changed from v0.3

- **No local computer and no operator.** The tool runs entirely on GitHub, in the repo `noahschmuckler/Clockwork-coach` (public). GitHub Actions does the rendering with Python and FFmpeg. Releases hold the finished files. GitHub Pages is an optional download page.
- **No ElevenLabs in V1.** Voice comes from an open-source TTS engine (Piper) that runs inside the Action, using one fixed male voice. The voice only has to be clear and correctly timed, not polished. ElevenLabs is a possible phase 2 upgrade, so don't spend those credits or handle that key for now.
- **You drive it.** You ask for a workout, the tool builds it, and you send me the download link. You don't need to set up any server.
- **Still true from v0.3:** a 2,700-second (45:00) timeline built from an approved block library, deterministic for a given seed, a validator that rejects bad timing rather than stretching blocks, no-jump/quiet constraints, low-volume voice-first mix, lyrics-free CC BY music with credits, and the release checks.

## What you need

1. **A GitHub fine-grained personal access token** that I'll create and give you:
   - Repository access: only `noahschmuckler/Clockwork-coach`
   - Permissions: Actions = Read and write, Contents = Read-only, Metadata = Read-only
   - Store it as a secret on your side. Never put it in a message, file, log or workflow input.
   - It has an expiry date. When calls start returning 401, tell me and I'll issue a new one.
2. **Outbound HTTPS** to `api.github.com`, `github.com` and `objects.githubusercontent.com`.
3. **A way to deliver a link to me** (text or message). The file is about 20–40 MB, so a link is better than an attachment.

You do not need: an ElevenLabs key, Python, FFmpeg, a server, a phone app, or any paid service.

## How to request a workout (contract, subject to small changes once built)

**1. Start the render:**

```
POST https://api.github.com/repos/noahschmuckler/Clockwork-coach/actions/workflows/render.yml/dispatches
Authorization: Bearer <token>
Accept: application/vnd.github+json

{
  "ref": "main",
  "inputs": {
    "request_id": "w-20261004-0630",
    "template": "standard-45",
    "seed": "4417",
    "music": "on"
  }
}
```

- `request_id`: a unique ID you generate (letters, digits and dashes). It becomes the release tag, which is how you find the result. Reusing an ID returns the existing file and doesn't render again.
- `template`: a preset name from `library/templates/` in the repo. Read that folder to see what's available.
- `seed`: any integer. The same template and seed always give the same workout. Use a new seed when you want variety.
- `music`: `on` or `off`.
- Optional `plan`: a full timeline JSON (see "Custom plans" below). When you send it, the template and seed are ignored.

A successful request returns `204 No Content`.

**2. Wait for it.** A render should take about 3–10 minutes. Poll every 30–60 seconds for up to 20 minutes:

```
GET https://api.github.com/repos/noahschmuckler/Clockwork-coach/releases/tags/<request_id>
```

- `404` means it isn't ready yet.
- `200` means it's done. The response's `assets` list contains `workout.m4a`, `manifest.json` (the cue timeline) and `CREDITS.txt`.
- To check for failure, list runs with `GET .../actions/runs?event=workflow_dispatch&per_page=10` and find the run named `render <request_id>`. If its `conclusion` is `failure`, read the job log. The validator's rejection reason is printed there in plain text. Fix the request and resend once with a new `request_id`. Don't retry in a loop.

**3. Deliver.** Send me the `browser_download_url` of `workout.m4a`, along with one line summarizing the plan from `manifest.json` (template, seed, and the block list). I download it on Wi-Fi before the workout and play it offline.

## Custom plans (optional)

You may build a timeline yourself instead of using a template, but you only assemble it. You don't invent content.

- Use only block IDs that exist in `library/blocks/`. Each block defines the activity, its easier alternative, its cue text, its allowed duration range and its tags. The tool records the voice for whatever cue text a block contains.
- Durations must fall inside each block's allowed range and add up to exactly 2,700 seconds, including warm-up, transitions, rest and cool-down.
- The validator is the authority. If it rejects your plan, fix the plan. Never try to work around a rule.
- To add or change exercises or wording, open a pull request against `library/`. I review and merge those myself. Don't render from unmerged content.

## Rules

- The repo is **public**, so workflow inputs and logs are visible to anyone. Never put health details, sleep or energy data, my name, my location or credentials into inputs, plans or pull requests. (Pre-workout energy/sleep adaptation is phase 2, and it will map to a template name, not to raw data.)
- Don't change workflows, settings, secrets or billing. The token's scope shouldn't allow it anyway.
- One render per workout request. GitHub Actions is free for public repos, but don't batch-render speculatively.
- If I report pain or concerning symptoms, the answer is to stop, not to make a harder workout.

## Not ready yet

The tool hasn't been built. I'll tell you when `render.yml` is live and give you the token then. Until then, if you want to help, draft candidate low-impact, quiet, bodyweight blocks in the schema described above (activity, easier alternative, duration range, cue lines with offsets, tags) so I can review them.
