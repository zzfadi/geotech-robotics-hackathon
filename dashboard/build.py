#!/usr/bin/env python3
"""Validate dashboard data and build data/feed.json for the page.

Usage:
  python3 dashboard/build.py          # validate and write dashboard/data/feed.json
  python3 dashboard/build.py --check  # validate only (run before you commit)

No dependencies beyond the Python 3 standard library.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
UPDATES = ROOT / "updates"
DATA = ROOT / "data"

KINDS = {"agent", "human"}
UPDATE_STATUSES = {"done", "in-progress", "blocked", "decision", "note"}
WS_STATUSES = {"not-started", "in-progress", "at-risk", "blocked", "done"}
REQUIRED = ["when", "who", "kind", "workstream", "status", "what", "why"]
ALLOWED = set(REQUIRED) | {"for", "where", "details", "workstream_update"}
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
    ids = set()
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
        p = ws.get("progress", 0)
        if not isinstance(p, int) or not 0 <= p <= 100:
            errors.append(f"{where}: progress must be a whole number 0-100")
        if ws.get("id") in ids:
            errors.append(f"{where}: duplicate id '{ws.get('id')}'")
        ids.add(ws.get("id"))
    return ids


def validate_update(path, entry, ws_ids, errors):
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
        if key not in ALLOWED:
            errors.append(f"{rel}: unknown field '{key}' (allowed: {sorted(ALLOWED)})")
    when = None
    try:
        when = parse_when(entry.get("when", ""))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{rel}: 'when' is not an ISO time ({exc})")
    if entry.get("kind") not in KINDS:
        errors.append(f"{rel}: kind must be 'agent' or 'human'")
    if entry.get("status") not in UPDATE_STATUSES:
        errors.append(f"{rel}: status must be one of {sorted(UPDATE_STATUSES)}")
    if entry.get("workstream") not in ws_ids:
        errors.append(f"{rel}: workstream must be one of {sorted(ws_ids)}")
    where = entry.get("where", [])
    if not isinstance(where, list) or not all(isinstance(w, str) for w in where):
        errors.append(f"{rel}: 'where' must be a list of text (paths, URLs or commit SHAs)")
    wu = entry.get("workstream_update")
    if wu is not None:
        if not isinstance(wu, dict) or not set(wu) <= {"status", "progress", "owner"}:
            errors.append(f"{rel}: workstream_update may only have status, progress, owner")
        else:
            if "status" in wu and wu["status"] not in WS_STATUSES:
                errors.append(f"{rel}: workstream_update.status must be one of {sorted(WS_STATUSES)}")
            if "progress" in wu and (not isinstance(wu["progress"], int) or not 0 <= wu["progress"] <= 100):
                errors.append(f"{rel}: workstream_update.progress must be a whole number 0-100")
    return when


def git_commits(limit=40):
    fmt = "%x1e%H%x1f%h%x1f%an%x1f%aI%x1f%s"
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "log", f"-n{limit}", f"--format={fmt}", "--name-only"],
            capture_output=True, text=True, check=True,
        ).stdout
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
        commits.append({
            "sha": parts[0], "short": parts[1], "author": parts[2],
            "when": parts[3], "subject": parts[4],
            "files": len(files), "areas": areas[:4],
        })
    return commits


def main():
    check_only = "--check" in sys.argv
    errors = []
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
        when = validate_update(path, entry, ws_ids, errors)
        if when is not None:
            entry = dict(entry, id=path.stem, _ts=when.timestamp())
            updates.append(entry)

    if errors:
        print("Dashboard data has problems:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1

    updates.sort(key=lambda e: e["_ts"], reverse=True)

    # Apply workstream_update entries, oldest first, on top of workstreams.json.
    by_id = {ws["id"]: dict(ws, updated=None) for ws in workstreams}
    for entry in sorted(updates, key=lambda e: e["_ts"]):
        ws = by_id[entry["workstream"]]
        ws["updated"] = entry["when"]
        for key, value in (entry.get("workstream_update") or {}).items():
            ws[key] = value
    for entry in updates:
        entry.pop("_ts", None)

    print(f"OK: {len(updates)} updates, {len(workstreams)} workstreams.")
    if check_only:
        return 0

    feed = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project,
        "workstreams": list(by_id.values()),
        "updates": updates,
        "commits": git_commits(),
    }
    (DATA / "feed.json").write_text(json.dumps(feed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Wrote dashboard/data/feed.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
