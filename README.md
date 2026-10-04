# Clockwork Coach

Generates downloadable, exactly 45:00 workout audio tracks: a calm male voice calls every block and transition at fixed timestamps over lyrics-free background music. Everything runs on GitHub Actions at $0, with no computer or server needed.

## Requesting a workout

- **By issue** (how the AI agent does it): open an issue whose title starts with `render` and whose body has optional `template`, `seed` and `music` lines. The workflow comments with the download link and closes the issue. Only the repo owner, or logins in the `RENDER_REQUESTERS` repository variable, can trigger it. See [docs/agent-brief.md](docs/agent-brief.md).
- **By hand:** Actions → render → Run workflow. This also works from the GitHub mobile app.

Each result is a release named `workout-<n>` containing the `.m4a`, a `manifest.json` with every cue and timestamp, and `CREDITS.txt`.

## How it works

```
request → template + seed → plan (validated: exactly 2700 s, warm-up and cool-down present)
        → cues (each fixed in time; must finish inside its block, optional ones dropped if no room)
        → speech (Piper, en_US-norman-medium, cached) + music (Incompetech CC BY, looped per section)
        → mix (sample-exact placement, music ducked under speech, peak ≤ -1 dBFS)
        → AAC .m4a → checks (master = 2700 s of samples, decoded file within 0.1 s, no overlaps)
```

- `library/blocks/*.yaml` holds the exercises. **This is placeholder content and needs review.** Every block must be tagged low impact and quiet.
- `library/templates/*.yaml` holds the workout shapes: circuits, intervals, long steady blocks, and flex (mobility and standing sun salutations).
- Blocks can set `switch:` (spoken exactly at halfway) and `countdown: true` ("Five … One" landing exactly on the switch and the end). The field guide at the top of `library/blocks/warmup.yaml` lists every line the tool adds by itself.
- `library/music/tracks.yaml` lists the music tracks, which are downloaded at render time.
- `clockwork/` contains the generator.

## Local use (optional)

```
pip install -r requirements.txt pytest      # plus ffmpeg on PATH
python -m clockwork check                   # validate library and templates
python -m clockwork plan --template any --seed 7
python -m clockwork render --template circuit-45 --seed 4417 --out out
python -m pytest -q
```

`--tts espeak` uses espeak-ng instead of Piper. Its robotic voice is only meant for offline testing.

## Credits

Music by Kevin MacLeod (incompetech.com), licensed under CC BY 4.0. Voice from Piper TTS (GPL-3.0 engine) using the en_US-norman-medium model, which was trained on public-domain LibriVox recordings. Each workout's `CREDITS.txt` lists the exact tracks used.
