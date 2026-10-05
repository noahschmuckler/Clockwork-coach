# Focus sessions: spec v1

Status: **built and verified** (October 5, 2026). The first real session, issue #1 in `clockwork-focus`, was 54:00 exactly with 16 lines and none dropped. This spec refines Instinct's "Focus Block Prompter" draft, with Noah's decisions included. It is written for the coding agent that builds the tool and for Instinct, which will use it. It contains nothing personal, so it can live in this public repo.

## 1. What it is
A second mode of the Clockwork Coach generator. It renders one spoken-cue audio file for each focus block. Each block is the unstructured time between meetings or after clinical work, typically 45–60 minutes, Pomodoro style, with short breaks built in.

The voice says what to work on, gives time checks, announces breaks and says what comes next. Noah compares his progress against the plan in Google Tasks. The audio never decides that a task is done.

The engine is the same as for workouts: the Piper `norman` voice, cues at fixed offsets, music ducked under speech, `.m4a` output, `manifest.json`, and failures that publish nothing. The differences are a quest planner instead of the workout library and a session length of your choosing instead of 2700 s.

## 2. Where things live

| Location | Visibility | Holds |
|---|---|---|
| `noahschmuckler/Clockwork-coach` (this repo) | public | All code, including the new focus mode. Focus wording sets and break definitions in `library/focus/`. This spec. |
| `noahschmuckler/clockwork-focus` (new) | **private** | The render workflow for focus sessions, the request issues, the output releases (`focus-<n>`), Noah's personal music (§6), and Instinct's focus brief. |

- The private repo's workflow installs the public generator and renders from its own issues. Task names can then be spoken in the audio and kept private.
- **Workouts are unchanged.** They stay in the public repo, and their tests must keep passing.

**Cost.** Private repos get 2,000 free Actions minutes a month. A render takes about 2 minutes, so the limit is roughly 1,000 sessions a month. Expected use is a few a day. If usage ever outgrows the free tier, the extra cost is a separate decision.

**Downloads.** Release links in a private repo need a GitHub login. Noah signs in to github.com once in Safari on his phone.

## 3. Requesting a session
Open one new issue in `clockwork-focus` per session.
- **Title:** must start with `render-focus`, for example `render-focus: 1pm block`.
- **Body:** YAML.

```yaml
block_start: "13:00"        # label only: used for clock times in the reply, never spoken
intensity: normal           # light | normal | firm
sound: default              # default | <track id> | noise | off
items:
  - {quest: q1, say: "Board memo", minutes: 20, brief: "Finish the outline."}
  - {break: walk, minutes: 4}
  - {quest: q2, say: "Inbox to zero", minutes: 15}
  - {break: stretch, minutes: 3}
  - {quest: q3, say: "Chart review", minutes: 15}
wrap_up_minutes: 2
seed: 7                     # optional; default is the issue number
```

### Fields

| Field | Values | Default | Meaning |
|---|---|---|---|
| `items` | list of quests and breaks | required | Played in order. Must include at least one quest. |
| `quest` | `q1`, `q2`, … | required on quests | Stable id. Instinct keeps the mapping from id to Google Task privately. Ids must be unique. |
| `say` | short text, 60 characters or fewer | `Quest N` | What the voice calls the quest. Real task names are fine, because the repo is private. |
| `minutes` | whole number: quests 2–90, breaks 1–15 | required | Length of the slot. Every spoken line falls inside it. |
| `brief` | one sentence, 150 characters or fewer | none | Spoken once, right after the quest's name. |
| `checkins` | any of `halfway`, `left:N`, `in:N` (N in minutes) | see §4 | Time checks inside a quest. `[]` means silence. |
| `prompts` | list of `{at_minute, text}` | none | A custom line N minutes into the quest. |
| `break` | `walk`, `stretch`, `water`, `breathe` | n/a | Built-in break types (§5). |
| `wrap_up_minutes` | 0–10 | 0 | Unassigned time at the end for wrapping up. |
| `intensity` | `light`, `normal`, `firm` | `normal` | Selects one of three fixed wording sets. Never free text. |
| `sound` | `default`, a track id, `noise`, `off` | `default` | `default` is Noah's chosen focus track (§6). `noise` is generated brown noise. `off` is voice only. |
| `seed` | whole number | issue number | Picks the stretches and where the music starts. The same request and seed always give the same file. |
| `block_start` | `"HH:MM"` | none | Used only for clock times in the reply's timeline. |

**Limits:**
- Total length of 3 hours or less, with at most 20 items.
- Unknown fields, duplicate ids, URLs and control characters are rejected.
- Nothing in the request is ever run as code.

## 4. What the voice says
All lines are placed at fixed offsets on one precomputed timeline. Every line falls inside its own item's time.

| When | Line (normal wording) |
|---|---|
| 0:00 | "Focus session. Three quests, fifty-five minutes. First: Board memo. Finish the outline." |
| Halfway through a quest of 20 minutes or more | "Halfway through Board memo." |
| 5 minutes left in a quest of 15 minutes or more | "Five minutes left on Board memo." |
| Quest → break | "Time's up for Board memo. Four-minute break: stand up, walk, refill your water." |
| 1 minute before a break ends | "One minute. Head back." |
| Break → quest | "Back to work. Next: Inbox to zero." |
| Quest → quest | "Time's up for Inbox to zero. Next: Chart review." |
| Wrap-up starts | "Wrap up. Save your work and write down your next step." |
| End | "Session complete." This line ends exactly at the total length. |

- **Never "done":** the audio says "Time's up", not "done". Elapsed time never means a task is complete.
- **Custom check-ins:** a quest's own `checkins` replace the defaults. A check-in or prompt that would land in the first 30 seconds of a slot, or collide with another line, is dropped, and the reply lists it.
- **Required lines:** start, transition and break lines are required. If one can't fit, the render fails with the line's text and how many seconds were missing. It is never shortened silently.

**Wording sets** (transition line / five-minute line):

| Set | Transition | Five-minute line |
|---|---|---|
| light | "Time's up for X. When you're ready, move to Y." | "About five minutes left on X." |
| normal | "Time's up for X. Next: Y." | "Five minutes left on X." |
| firm | "Time. Close out X now. Next: Y." | "Five minutes. Start wrapping up X." |

The sets live in `library/focus/wording.yaml`.

**No chime in v1.** The design leaves room to add an optional soft generated chime before transitions later.

## 5. Breaks

| `break:` | What happens | Sound |
|---|---|---|
| `walk` | "Stand up, walk, refill your water." Then "One minute. Head back." | Music drops 6 dB |
| `stretch` | One 60 s mobility stretch per minute, chosen by the seed from the workout library's `mobility` family, with each block's switch and countdown | Music drops 6 dB |
| `water` | "Stand up, drink some water, and look at something far away." | Music drops 6 dB |
| `breathe` | The workout library's breathing block | Music drops 12 dB |

A typical 60-minute block has 2–3 quests separated by `walk` or `stretch` breaks of 3–5 minutes. Instinct proposes the split and Noah approves it.

## 6. Music

**Noah's preference:** a driving beat with ambient changes around a theme. Not relaxing; it works every time. The reference is a specific YouTube video.

**We will not download audio from YouTube.** There are three reasons:
1. YouTube's terms of service forbid downloading outside YouTube's own features.
2. YouTube blocks downloads from GitHub's servers, so it would fail unpredictably anyway.
3. The track is someone's copyrighted work. That's why a private repo is required at all.

**Bring-your-own track** (the supported route):
1. Find the artist and title, usually in the video description. Focus-music producers commonly sell DRM-free files on Bandcamp, Apple's iTunes Store, Amazon MP3 or their own site.
2. Buy or download a copy Noah is entitled to. This is typically a few dollars, and it is the only exception to $0, so Noah approves it.
   - Streaming-only or DRM-locked copies (YouTube Premium offline, Spotify) can't be used.
3. Upload it once to the private repo as an asset on a release named `music-library`. Use a release asset rather than a Git commit, because long tracks exceed Git's file limits.
4. Register it in `music/tracks.yaml` in the private repo with: `id`, title, artist, where it was obtained, and `default: true`.

**Noah's track:** "Upbeat Study Music - Deep Focus For Complex Tasks" by Jason Lewis (Mind Amend). The music is by Tomas Novoa: the tracks Arrecife, Cienaga, Brotes, Prisma and Tundra. The tracks carry **isochronic** tones, so they work on a phone speaker and headphones are optional. Buy the download version from mindamend.com. The credits name both Jason Lewis and Tomas Novoa.

**The track is a session with an arc, not a loop.** From the artist's description:
- the pulse ramps from 10 Hz to 18 Hz over the first 6 minutes;
- it holds at 18 Hz;
- it ramps back down over the final 5 minutes.

The track entry in `music/tracks.yaml` records this as `arc: {ramp_up: 360, ramp_down: 300}`. Any session using an arc track is fitted like this:
- **Ramp-up:** always played from the start of the track, at the start of the session.
- **Ramp-down:** the track's last 5 minutes always end exactly when the session ends, so the wind-down covers the wrap-up.
- **Plateau:** the 18 Hz middle is cut or extended to fill the remaining time. Extending loops plateau material with crossfades. This works because the pulse rate is the same throughout the plateau.
- **Breaks:** the music keeps running underneath, at a lower level. Session timing never shifts the arc.
- **Short sessions:** if a session is shorter than 11 minutes plus a 1-minute plateau, the render fails with a clear message. Use a non-arc sound for those.

**Volume:** the pulses have to stay audible to work, so focus music sits louder than workout music. It ducks only about 6 dB, and only while the voice is speaking.

**Time of day:** beta tones act a bit like coffee. Instinct should not use this track for sessions that start after about 7 pm; use `noise` instead.

**Processing rules for brainwave tracks:**
- **Output stereo for focus sessions** (AAC 128 kbps, with the voice in the centre). Isochronic tones survive mono, but binaural tracks don't, and the workout pipeline is mono.
- **Never time-stretch or pitch-shift a music track.** Speed changes alter the pulse rate.
- **Duck only while the voice is speaking.** The pulse continues underneath at a reduced level.

**How a track without an arc is used:**
- If the track is longer than the session, a seeded segment is used, so different sessions hear different parts.
- If it's shorter, it loops with crossfades.
- Speech ducks the music by about 6 dB, and only while the voice is speaking.
- The track never leaves the private repo, and outputs stay in private releases.

**Until a track is installed:** `default` falls back to `noise`, and the reply says so. Lyric-free Incompetech tracks with a steady pulse can be added to the public list as alternatives.

## 7. Instinct's workflow
1. Read the open tasks Noah names for the block. The lists are "Instinct - next actions" and "TODO".
2. Propose items and minutes. Check that the total fits the calendar gap, because the generator doesn't know the calendar.
3. **Noah approves the plan before any render.**
4. Open the `render-focus` issue in the private repo.
5. Choose `sound`. Use `default` (the Mind Amend track) for daytime sessions, and `noise` for sessions starting after about 7 pm or shorter than 12 minutes.
6. After about 5 minutes, read the result. Text Noah the link and the timeline, with clock times taken from `block_start`.
7. Complete a Google Task only when Noah reports it done. The audio never marks a task done, and elapsed time never does either.

The result, failure and warning handling is the same as for workouts. See [`agent-brief.md`](agent-brief.md) §2.

**Instinct's access:** its GitHub connector needs access to `clockwork-focus`. Noah grants this once.

## 8. Build checklist (for the coding agent)
- [ ] `clockwork/focus.py` holds the quest/break planner, validator and cue generation. It reuses `cues.place`, `tts.Speaker`, `mixer` and the manifest and checks. Workout code paths are unchanged.
- [ ] `library/focus/wording.yaml` (the three sets) and `library/focus/breaks.yaml`.
- [ ] `python -m clockwork focus --request-file … --music-dir …` exists. Brown noise is generated with ffmpeg (`anoisesrc=color=brown`) and needs no license.
- [ ] The music layer supports segment-or-loop (never time-stretch), per-item level changes and a configurable duck depth.
- [ ] Arc-preserving fit for tracks marked `arc:`. Test: the ramp-up starts at 0:00, the ramp-down ends exactly at the session end, and the plateau is extended or trimmed only by crossfades.
- [ ] Focus output is stereo (voice centred, music stereo as supplied) at AAC 128 kbps. The workout output stays mono.
- [ ] The mixer works in chunks, so 3-hour sessions fit a private-repo runner's memory.
- [ ] Tests cover: totals; quest boundaries landing exactly; default check-in thresholds; required lines failing loudly; dropped optional lines being reported; validator rejections (unknown fields, duplicate ids, URLs, limits); the stretch break reusing mobility blocks.
- [ ] Private repo:
  - [ ] a workflow that installs this repo at `main` and has the same author guard and comment/close/release behaviour, with the prefix `render-focus` and tag `focus-<n>`;
  - [ ] `music/tracks.yaml`;
  - [ ] the `music-library` release;
  - [ ] `docs/focus-brief.md` for Instinct.
- [ ] Changelog entry and README section in this repo.

## 9. Known limits
- **Fixed file:** pausing pushes every later cue out of step, and seeking skips cues. The audio starts when Noah presses play, so press play at the start of the block.
- The file can't know when a quest finishes early or runs over.
- **Phase 2:** a small web player (pause / skip / extend / done) that plays the same cue plan from `manifest.json`. It's worth building if interruptions turn out to be the main problem.
