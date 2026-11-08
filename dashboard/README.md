# Team dashboard

Live at **https://zzfadi.github.io/geotech-robotics-hackathon/**. It is written for the whole team, including people who don't read code.

- **Overview** (`index.html`): what we are building in one sentence, the countdown to the 3 PM demo, **Today** (the Sunday run of day, with each item marked agreed or proposed), one card per track (what it is in plain words, a status word and colour, the owner and the single next step), **Needs a decision**, and the latest updates.
- **How it works** (`design.html`): three pictures drawn from `data/design.json`: the demo step by step, the whole system, and the machines and who looks after them. Every box says **working**, **in progress** or **planned**. Then the team and a glossary.
- **All updates** (`updates.html`): every update and commit, filterable by track and by person, plus the milestone timeline, activity by hour and who's shipping.
- **Track pages** (`track.html?id=<track>`): one per track, with its plain description, next step, risk, owner, milestones, progress next to every other track, its activity, who's on it, and its updates and commits.

Everything is credited to the **GitHub username that pushed it to `main`**, read from GitHub's push history when the site builds. Nobody types their own name. Commits are matched to tracks by the `paths` each track lists, and by the update files they add.

GitHub Actions rebuilds and redeploys the site on every push to `main` (`.github/workflows/dashboard.yml`).

## How to post an update

Everyone posts the same way: **add one new JSON file** to `dashboard/updates/`. Never edit or delete someone else's entry. One file per update means two people posting at once never conflict.

1. Create `dashboard/updates/YYYY-MM-DD-HHMM-<short-title>.json`, using Pacific time and lowercase, for example `2026-11-07-1430-turn-gate-prototype.json`.
2. Fill it in (format below).
3. Run `python3 dashboard/build.py --check`. It prints `OK` or tells you exactly what to fix.
4. Commit it to `main`, ideally in the same commit as the work it describes, and push.

### Format

```json
{
  "when": "2026-11-07T14:30:00-08:00",
  "workstream": "turn-gate",
  "status": "in-progress",
  "what": "Hand-in-workspace detector running on the webcam feed",
  "why": "The arm has to pause the moment a hand enters the workspace.",
  "where": ["turn_gate/detector.py", "a1b2c3d", "https://github.com/zzfadi/geotech-robotics-hackathon/pull/3"],
  "details": "Optional extra context, one or two sentences.",
  "workstream_update": { "status": "in-progress", "milestones_done": ["detect"], "next": "Tune it on the real table" }
}
```

| Field | Required | What to put |
| --- | --- | --- |
| `when` | yes | ISO time with offset. Pacific is `-08:00` this weekend. |
| `workstream` | yes | One id from the list below. |
| `status` | yes | `done`, `in-progress`, `blocked`, `decision` or `note`. |
| `what` | yes | One line: what changed. Write it so a non-engineer understands it; it shows on the overview. |
| `why` | yes | One or two sentences: why it matters. |
| `where` | no | Files or folders (repo paths), commit SHAs, `#12` for a PR, or full URLs. They become links. |
| `details` | no | Anything else worth knowing. |
| `workstream_update` | no | Moves the track: `milestones_done` (milestone ids from `workstreams.json` that this update finished), `status` (`not-started`, `in-progress`, `at-risk`, `blocked`, `done`), `owner`, `progress` (0-100), or `next` (the track's one next step, shown on its card). The newest value wins. |

### Workstream ids

| id | Workstream |
| --- | --- |
| `planning` | Planning & coordination |
| `arm-station` | Arm station: the can hand-off |
| `dream1` | Dream AI in the simulation |
| `turn-gate` | Turn gate: the camera check |
| `voice-agent` | Voice agent (LiveKit) |
| `vrxr` | Simulation + human capture |
| `demo` | Demo rehearsal & submission |

The ids never change, so old updates keep working when a track is renamed.

### Tracks and milestones

Tracks live in `dashboard/data/workstreams.json`. Each has an `id`, `name`, `short` (nav label), `owner`, `status`, `goal`, `plain` (one line anyone understands, shown on the overview card), `next` (the single next step), an optional `risk` (one line, shown on the card and the track page), `paths` (repo path prefixes, so commits there count toward the track) and `milestones`, each with an `id`, `title`, `due` (ISO time) and `done`.

A track's progress is the share of its milestones that are done, unless someone sets `progress` explicitly. So the simplest way to move a track forward is to finish a milestone and say so in your update with `"workstream_update": { "milestones_done": ["<id>"] }`. To claim a track, use `"owner"` the same way, so it shows in the feed too. To change the next step without editing the file, use `"next"`.

Edit `workstreams.json` directly to add or rename tracks, add milestones, or move a due time. Don't mark a milestone done there unless an update says it happened.

### Today and Needs a decision

Both live in `dashboard/data/project.json`, next to the project facts (goal, deadline, team):

- `summary`: the one sentence at the top of the overview.
- `today`: the run of day. Each item has `at` (ISO time), optional `end`, `what`, optional `detail`, and `state`: `proposed` (not yet agreed), `agreed`, `done` or `cancelled`. When the team confirms an item, change its `state` to `agreed`.
- `decisions`: open questions, each with `q` (the question), `why` (one line of context) and `source` (where it comes from, usually `docs/plan.md`). Remove one once it's settled and post the answer as a `decision` update.

### How it works (`data/design.json`)

Anyone can fix the words. Each diagram has an `id`, `title`, `lead`, `cols` (columns on a wide screen), optional `gap`, optional `notes`, `list_edges` (also list the arrows as text), `nodes` and `edges`.

- A **node** has an `id`, `title`, optional `text`, `icon` (`person`, `camera`, `brain`, `arm`, `retreat`, `stop`, `mark`, `grid`, `screen`, `spark`, `headset`, `desktop`, `server`, `board`, `laptop`, `cloud`, `voice`), optional `owner`, its place (`col`, `row`, optional `span`) and a `status`: `working`, `in-progress` or `planned`. A group box can list `items` instead, each with a `label`, `status` and optional `text`.
- An **edge** is an arrow: `from` and `to` (node ids), optional `label` (two or three words) and `status` (planned arrows are dotted, in-progress ones dashed).
- `glossary` is a list of `term` and `plain`.

Keep statuses honest: **working** only when it has been shown to work, and say where (for example "in software" or "in the simulation"). On a phone the boxes stack in the order they are listed. `build.py --check` validates the file.

## Preview locally

```sh
python3 dashboard/build.py
python3 -m http.server 8000 -d dashboard
```

Then open http://localhost:8000. `build.py` writes `dashboard/data/feed.json`, which is generated and not committed. Locally it shows git author names; the live site shows GitHub usernames. No external scripts or fonts are loaded; everything is in `app.js` and `style.css`.
