# Team dashboard

Live at **https://zzfadi.github.io/geotech-robotics-hackathon/**. It shows each workstream's owner and progress, a countdown to the demo deadline (Sunday, Nov 8, 3 PM PT), every posted update (newest first, filterable by GitHub user, workstream and status), and the latest commits on `main`.

Everything is credited to the **GitHub username that pushed it to `main`**, read from GitHub's push history when the site builds. Nobody types their own name.

GitHub Actions rebuilds and redeploys it on every push to `main` (`.github/workflows/dashboard.yml`).

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
  "workstream_update": { "status": "in-progress", "progress": 40 }
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
| `workstream_update` | no | Moves the workstream card: any of `status` (`not-started`, `in-progress`, `at-risk`, `blocked`, `done`), `progress` (0-100) and `owner`. The newest one wins. |

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

To add a workstream or change its name or goal, edit `dashboard/data/workstreams.json`. To claim an owner or move progress, prefer a `workstream_update` in your update, so the change shows up in the feed too. Project facts (goal, deadline, team) live in `dashboard/data/project.json`.

## Preview locally

```sh
python3 dashboard/build.py
python3 -m http.server 8000 -d dashboard
```

Then open http://localhost:8000. `build.py` writes `dashboard/data/feed.json`, which is generated and not committed. Locally it shows git author names; the live site shows GitHub usernames.
