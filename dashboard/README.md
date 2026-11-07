# Team dashboard

Live at **https://zzfadi.github.io/geotech-robotics-hackathon/**.

- **Overview** (`index.html`): countdown to the demo (Sunday, Nov 8, 3 PM PT), overall progress, what needs attention (blocked tracks, overdue milestones, tracks with no owner), one card per track, the milestone timeline, activity by hour, who's shipping, and the latest updates and commits.
- **Track pages** (`track.html?id=<track>`): one per track, with its owner, milestones, progress next to every other track and the clock, its activity, who's on it, and its updates and commits.

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
  "workstream_update": { "status": "in-progress", "milestones_done": ["detect"] }
}
```

| Field | Required | What to put |
| --- | --- | --- |
| `when` | yes | ISO time with offset. Pacific is `-08:00` this weekend. |
| `workstream` | yes | One id from the list below. |
| `status` | yes | `done`, `in-progress`, `blocked`, `decision` or `note`. |
| `what` | yes | One line: what changed. |
| `why` | yes | One or two sentences: why it matters. |
| `where` | no | Files or folders (repo paths), commit SHAs, `#12` for a PR, or full URLs. They become links. |
| `details` | no | Anything else worth knowing. |
| `workstream_update` | no | Moves the track: `milestones_done` (milestone ids from `workstreams.json` that this update finished), `status` (`not-started`, `in-progress`, `at-risk`, `blocked`, `done`), `owner`, or `progress` (0-100). The newest value wins. |

### Workstream ids

| id | Workstream |
| --- | --- |
| `planning` | Planning & coordination |
| `arm-station` | Arm station + portal-makermods |
| `dream1` | Dream-1 integration |
| `turn-gate` | Turn gate (hand in workspace) |
| `voice-agent` | Voice agent (LiveKit) |
| `vrxr` | VR/XR data + Unreal |
| `demo` | Demo rehearsal & submission |

### Tracks and milestones

Tracks live in `dashboard/data/workstreams.json`. Each has an `id`, `name`, `short` (nav label), `owner`, `status`, `goal`, an optional `risk` (one line that shows under Needs attention), `paths` (repo path prefixes, so commits there count toward the track) and `milestones`, each with an `id`, `title`, `due` (ISO time) and `done`.

A track's progress is the share of its milestones that are done, unless someone sets `progress` explicitly. So the simplest way to move a track forward is to finish a milestone and say so in your update with `"workstream_update": { "milestones_done": ["<id>"] }`. To claim a track, use `"owner"` the same way, so it shows in the feed too.

Edit `workstreams.json` directly to add or rename tracks, add milestones, or move a due time. The milestone times there now are a first draft for the team to adjust. Project facts (goal, deadline, team) live in `dashboard/data/project.json`.

## Preview locally

```sh
python3 dashboard/build.py
python3 -m http.server 8000 -d dashboard
```

Then open http://localhost:8000. `build.py` writes `dashboard/data/feed.json`, which is generated and not committed. Locally it shows git author names; the live site shows GitHub usernames.
