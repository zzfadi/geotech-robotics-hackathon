#!/usr/bin/env python3
"""Validate dashboard data and build data/feed.json for the page.

Usage:
  python3 dashboard/build.py          # validate and write dashboard/data/feed.json

Authors are GitHub usernames of whoever pushed each commit to main, read from the
repository activity API in GitHub Actions. Locally (no GITHUB_TOKEN) they fall back
to git author names.
  python3 dashboard/build.py --check  # validate only (run before you commit)

No dependencies beyond the Python 3 standard library.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
UPDATES = ROOT / "updates"
DATA = ROOT / "data"

UPDATE_STATUSES = {"done", "in-progress", "blocked", "decision", "note"}
WS_STATUSES = {"not-started", "in-progress", "at-risk", "blocked", "done"}
REQUIRED = ["when", "workstream", "status", "what", "why"]
ALLOWED = set(REQUIRED) | {"where", "details", "workstream_update"}
# Older entries named their author by hand. Authors now come from GitHub, so these are ignored.
LEGACY = {"who", "kind", "for"}
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-z0-9_.-]+\.json$")


def parse_when(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("needs a time zone offset, like -08:00")
    return dt


def load_json(path, errors):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{path.relative_to(REPO)}: not valid JSON ({exc})")
        return None


def validate_workstreams(items, errors):
    ids = {}
    if not isinstance(items, list):
        errors.append("data/workstreams.json: must be a list")
        return ids
    for i, ws in enumerate(items):
        where = f"data/workstreams.json[{i}]"
        for key in ("id", "name", "owner", "status"):
            if not isinstance(ws.get(key), str) or not ws.get(key).strip():
                errors.append(f"{where}: '{key}' is required")
        if ws.get("status") not in WS_STATUSES:
            errors.append(f"{where}: status must be one of {sorted(WS_STATUSES)}")
        if "progress" in ws and (not isinstance(ws["progress"], int) or not 0 <= ws["progress"] <= 100):
            errors.append(f"{where}: progress must be a whole number 0-100")
        paths = ws.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            errors.append(f"{where}: paths must be a list of repo path prefixes")
        ms_ids = set()
        for j, ms in enumerate(ws.get("milestones", [])):
            mw = f"{where}.milestones[{j}]"
            if not isinstance(ms, dict) or not isinstance(ms.get("id"), str) or not isinstance(ms.get("title"), str):
                errors.append(f"{mw}: needs an 'id' and a 'title'")
                continue
            if ms["id"] in ms_ids:
                errors.append(f"{mw}: duplicate milestone id '{ms['id']}'")
            ms_ids.add(ms["id"])
            if "done" in ms and not isinstance(ms["done"], bool):
                errors.append(f"{mw}: done must be true or false")
            if "due" in ms:
                try:
                    parse_when(ms["due"])
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{mw}: due is not an ISO time ({exc})")
        if ws.get("id") in ids:
            errors.append(f"{where}: duplicate id '{ws.get('id')}'")
        ids[ws.get("id")] = ms_ids
    return ids


def validate_update(path, entry, ws_ids, errors, warnings):
    rel = path.relative_to(REPO)
    if not NAME_RE.match(path.name):
        errors.append(f"{rel}: file name must look like YYYY-MM-DD-HHMM-who-short-title.json (lowercase)")
    if not isinstance(entry, dict):
        errors.append(f"{rel}: must be a JSON object")
        return None
    for key in REQUIRED:
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            errors.append(f"{rel}: '{key}' is required and must be text")
    for key in entry:
        if key in LEGACY:
            warnings.append(f"{rel}: '{key}' is no longer used (the author comes from GitHub); you can delete it")
        elif key not in ALLOWED:
            errors.append(f"{rel}: unknown field '{key}' (allowed: {sorted(ALLOWED)})")
    when = None
    try:
        when = parse_when(entry.get("when", ""))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{rel}: 'when' is not an ISO time ({exc})")
    if entry.get("status") not in UPDATE_STATUSES:
        errors.append(f"{rel}: status must be one of {sorted(UPDATE_STATUSES)}")
    if entry.get("workstream") not in ws_ids:
        errors.append(f"{rel}: workstream must be one of {sorted(ws_ids)}")
    where = entry.get("where", [])
    if not isinstance(where, list) or not all(isinstance(w, str) for w in where):
        errors.append(f"{rel}: 'where' must be a list of text (paths, URLs or commit SHAs)")
    wu = entry.get("workstream_update")
    if wu is not None:
        if not isinstance(wu, dict) or not set(wu) <= {"status", "progress", "owner", "milestones_done"}:
            errors.append(f"{rel}: workstream_update may only have status, progress, owner, milestones_done")
        else:
            if "status" in wu and wu["status"] not in WS_STATUSES:
                errors.append(f"{rel}: workstream_update.status must be one of {sorted(WS_STATUSES)}")
            if "progress" in wu and (not isinstance(wu["progress"], int) or not 0 <= wu["progress"] <= 100):
                errors.append(f"{rel}: workstream_update.progress must be a whole number 0-100")
            done = wu.get("milestones_done", [])
            known = ws_ids.get(entry.get("workstream"), set())
            if not isinstance(done, list) or not all(isinstance(m, str) for m in done):
                errors.append(f"{rel}: workstream_update.milestones_done must be a list of milestone ids")
            else:
                for m in done:
                    if m not in known:
                        errors.append(f"{rel}: milestone '{m}' isn't in workstream '{entry.get('workstream')}' "
                                      f"(ids: {sorted(known)})")
    return when


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def github_get_all(path, max_pages=10):
    """GET a paginated GitHub API list. Needs GITHUB_TOKEN and GITHUB_REPOSITORY (set in Actions)."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None
    url = f"https://api.github.com/repos/{repo}/{path}"
    items = []
    for _ in range(max_pages):
        req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "team-dashboard-build"})
        with urlopen(req, timeout=20) as res:
            items.extend(json.loads(res.read().decode("utf-8")))
            link = res.headers.get("Link") or ""
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not nxt:
            break
        url = nxt.group(1)
    return items


def pushers(branch="main"):
    """Map commit SHA -> GitHub username of whoever pushed it to the branch, from the repo activity API."""
    try:
        activity = github_get_all(f"activity?ref=refs/heads/{branch}&per_page=100&direction=asc")
    except Exception as exc:  # noqa: BLE001
        print(f"note: GitHub activity lookup failed ({exc}); using git author names", file=sys.stderr)
        return {}
    if not activity:
        return {}
    by_sha = {}
    for act in sorted(activity, key=lambda a: a.get("timestamp", "")):
        login = (act.get("actor") or {}).get("login")
        before, after = act.get("before", ""), act.get("after", "")
        if not login or not after or set(after) == {"0"}:
            continue
        rng = after if not before or set(before) == {"0"} else f"{before}..{after}"
        try:
            shas = git("rev-list", rng).split()
        except Exception:  # noqa: BLE001
            shas = [after]
        for sha in shas:
            by_sha.setdefault(sha, login)
    return by_sha


def author_of(sha, by_sha, names):
    if sha in by_sha:
        return by_sha[sha], "github"
    return names.get(sha, "unknown"), "git"


def git_commits(by_sha, limit=400):
    fmt = "%x1e%H%x1f%h%x1f%an%x1f%aI%x1f%s"
    try:
        out = git("log", f"-n{limit}", f"--format={fmt}", "--name-only")
    except Exception:  # noqa: BLE001
        return []
    commits = []
    for block in out.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        head, _, files = block.partition("\n")
        parts = head.split("\x1f")
        if len(parts) != 5:
            continue
        files = [f for f in files.splitlines() if f.strip()]
        areas = []
        for f in files:
            segs = f.split("/")
            area = "/".join(segs[:2]) if len(segs) > 2 else (segs[0] if len(segs) > 1 else "(root)")
            if area not in areas:
                areas.append(area)
        author, source = author_of(parts[0], by_sha, {parts[0]: parts[2]})
        commits.append({
            "sha": parts[0], "short": parts[1], "author": author, "author_source": source,
            "when": parts[3], "subject": parts[4],
            "files": len(files), "areas": areas[:4], "_paths": files,
        })
    return commits


def update_adders():
    """Map each update file name -> SHA of the commit that first added it."""
    result = {}
    try:
        out = git("log", "--reverse", "--diff-filter=A", "--no-renames", "--format=%x1e%H", "--name-only", "--", "dashboard/updates")
    except Exception:  # noqa: BLE001
        return result
    for block in out.split("\x1e"):
        sha, _, files = block.strip("\n").partition("\n")
        for f in files.splitlines():
            if f.strip():
                result.setdefault(Path(f.strip()).name, sha.strip())
    return result


def update_authors(by_sha):
    """Map each update file name -> (author, source), from the commit that first added it."""
    result = {}
    try:
        out = git("log", "--reverse", "--diff-filter=A", "--no-renames", "--format=%x1e%H%x1f%an", "--name-only",
                  "--", "dashboard/updates")
    except Exception:  # noqa: BLE001
        return result
    for block in out.split("\x1e"):
        head, _, files = block.strip("\n").partition("\n")
        if "\x1f" not in head:
            continue
        sha, name = head.split("\x1f", 1)
        for f in files.splitlines():
            f = f.strip()
            if f and Path(f).name not in result:
                result[Path(f).name] = author_of(sha, by_sha, {sha: name})
    return result


def main():
    check_only = "--check" in sys.argv
    errors, warnings = [], []
    project = load_json(DATA / "project.json", errors) or {}
    workstreams = load_json(DATA / "workstreams.json", errors) or []
    ws_ids = validate_workstreams(workstreams, errors)

    updates = []
    for path in sorted(UPDATES.glob("*")):
        if path.name.startswith(".") or path.name == "README.md":
            continue
        if path.suffix != ".json":
            errors.append(f"{path.relative_to(REPO)}: updates must be .json files")
            continue
        entry = load_json(path, errors)
        if entry is None:
            continue
        when = validate_update(path, entry, ws_ids, errors, warnings)
        if when is not None:
            entry = {k: v for k, v in entry.items() if k not in LEGACY}
            entry = dict(entry, id=path.stem, file=path.name, _ts=when.timestamp())
            updates.append(entry)

    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    if errors:
        print("Dashboard data has problems:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1

    updates.sort(key=lambda e: e["_ts"], reverse=True)

    # Apply workstream_update entries, oldest first, on top of workstreams.json.
    by_id = {}
    for ws in workstreams:
        ws = dict(ws, updated=None, milestones=[dict(m, done=bool(m.get("done"))) for m in ws.get("milestones", [])])
        by_id[ws["id"]] = ws
    for entry in sorted(updates, key=lambda e: e["_ts"]):
        ws = by_id[entry["workstream"]]
        ws["updated"] = entry["when"]
        for key, value in (entry.get("workstream_update") or {}).items():
            if key == "milestones_done":
                for ms in ws["milestones"]:
                    if ms["id"] in value and not ms["done"]:
                        ms["done"], ms["done_at"] = True, entry["when"]
            else:
                ws[key] = value
    for ws in by_id.values():
        # An explicit progress (workstreams.json or a workstream_update) wins; otherwise count milestones.
        ws["progress_source"] = "set" if "progress" in ws or not ws["milestones"] else "milestones"
        if "progress" not in ws:
            total = len(ws["milestones"])
            ws["progress"] = round(100 * sum(m["done"] for m in ws["milestones"]) / total) if total else 0
    for entry in updates:
        entry.pop("_ts", None)

    print(f"OK: {len(updates)} updates, {len(workstreams)} workstreams.")
    if check_only:
        return 0

    by_sha = pushers(project.get("branch", "main"))
    authors = update_authors(by_sha)
    adders = update_adders()
    for entry in updates:
        name = entry.pop("file")
        entry["author"], entry["author_source"] = authors.get(name, ("not pushed yet", "local"))
        entry["sha"] = adders.get(name)
    print(f"Authors: {len(by_sha)} commits matched to GitHub pushers" if by_sha else "Authors: git author names (no GitHub token)")

    # Which tracks each commit touched: path prefixes, plus the workstream of any update it added.
    ws_by_update_sha = {}
    for entry in updates:
        if entry.get("sha"):
            ws_by_update_sha.setdefault(entry["sha"], set()).add(entry["workstream"])
    commits = git_commits(by_sha)
    for c in commits:
        tracks = set(ws_by_update_sha.get(c["sha"], set()))
        for path in c.pop("_paths"):
            if path.startswith("dashboard/updates/"):
                continue
            for ws in by_id.values():
                if any(path == p or path.startswith(p) for p in ws.get("paths", [])):
                    tracks.add(ws["id"])
        c["tracks"] = [ws_id for ws_id in by_id if ws_id in tracks]

    times = [parse_when(c["when"]) for c in commits] + [parse_when(u["when"]) for u in updates]
    if times and "start" not in project:
        project["start"] = min(times).isoformat()

    feed = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project,
        "workstreams": list(by_id.values()),
        "updates": updates,
        "commits": commits,
    }
    (DATA / "feed.json").write_text(json.dumps(feed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Wrote dashboard/data/feed.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
