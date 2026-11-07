# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "pillow>=11", "psutil>=6", "tzdata"]
# ///
"""Persist an idle Luna2 session, commit Unreal content, and push main.

Run once from a 15-minute host scheduled task, or at a completed milestone.
--dry-run checks prerequisites without persistence, staging, commits or pushes.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
import uuid

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[1]
POLICY = json.loads((PROJECT / "Config/CheckpointPolicy.json").read_text())
STATE = PROJECT / "Saved/Checkpoints/status.json"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def preflight():
    if git("branch", "--show-current") != POLICY["branch"]:
        raise RuntimeError("Checkpoint requires main")
    if git("diff", "--cached", "--name-only"):
        raise RuntimeError("Existing staged work: leave the index untouched")
    if git("ls-files", "-u"):
        raise RuntimeError("Resolve merge conflicts before checkpointing")
    git("fetch", POLICY["remote"], POLICY["branch"])
    behind = int(git("rev-list", "--count", "HEAD..origin/main"))
    if behind:
        raise RuntimeError("Local main is behind origin/main; coordinate synchronization")


def candidate_paths():
    root = POLICY["content_root"]
    tracked = git("diff", "--name-only", "-z", "HEAD", "--", root)
    new = git("ls-files", "--others", "--exclude-standard", "-z", "--", root)
    paths = sorted(set(filter(None, (tracked + "\0" + new).split("\0"))))
    if any(not p.startswith(root) or Path(p).suffix.lower() not in {".uasset", ".umap"} for p in paths):
        raise RuntimeError("Content includes non-asset files; review them manually")
    return paths


def hub_idle():
    with urllib.request.urlopen(POLICY["hub_url"] + "/api/state", timeout=10) as response:
        state = json.load(response)
    # The hub retains the last task title after completion; it is not an active task ID.
    # Unknown states defer. The editor task/animation/dirty guards remain authoritative.
    busy = [name for name, agent in state["agents"].items()
            if agent.get("state") not in {"idle", "done"}]
    if busy:
        raise RuntimeError("Agents busy: " + ", ".join(busy))


def editor_call(client, expression):
    result = client.copresence("run_python", {
        "code": "from copresence import checkpoints\nresult = " + expression,
        "description": "Host checkpoint boundary",
    })
    if result.get("error"):
        raise RuntimeError(result["error"])
    return ast.literal_eval(result["value"])


def ready(info):
    if info["agent"] != POLICY["host_agent"] or info["session"] != POLICY["session"]:
        raise RuntimeError("Wrong host editor/session")
    if info["task"] or info["animation"] or info["content"]:
        raise RuntimeError("Active task, animation or unshared dirty content; defer checkpoint")


def checkpoint(dry_run=False):
    preflight()
    hub_idle()
    os.environ["COPRESENCE_HUB_URL"] = POLICY["hub_url"]
    from copresence_mcp import UnrealMcp
    client = UnrealMcp(POLICY["editor_mcp_url"])
    before = editor_call(client, "checkpoints.inspect()")
    ready(before)
    if dry_run:
        return {"state": "ready", "files": candidate_paths(), "pending_packages": before["pending"]}
    if before["pending"] or before["maps"]:
        editor_call(client, "checkpoints.request()")
        for _ in range(60):
            time.sleep(1)
            after = editor_call(client, "checkpoints.inspect()")
            operation = after["operation"]
            if operation["state"] == "error":
                raise RuntimeError(operation["error"])
            if operation["state"] == "saved":
                break
        else:
            raise RuntimeError("Persistence timed out; no Git checkpoint")
    hub_idle()
    after = editor_call(client, "checkpoints.inspect()")
    ready(after)
    if after["maps"] or after["content"] or after["pending"]:
        raise RuntimeError("New edits or unsaved packages; defer Git checkpoint")
    preflight()
    paths = candidate_paths()
    if paths:
        update = None
        if (REPO / "dashboard/build.py").exists():
            now = datetime.now(ZoneInfo("America/Los_Angeles"))
            update = Path("dashboard/updates") / (now.strftime("%Y-%m-%d-%H%M") + "-luna2-unreal-checkpoint-" + uuid.uuid4().hex[:8] + ".json")
            (REPO / update).write_text(json.dumps({
                "when": now.isoformat(),
                "workstream": "vrxr", "status": "done", "what": "Persisted Unreal content checkpoint",
                "why": "15-minute or milestone recovery baseline; push follows this commit.",
                "where": paths, "details": "Concert session rollover is separately coordinated; this commit does not clear live history."
            }, indent=2))
            subprocess.run([sys.executable, "dashboard/build.py", "--check"], cwd=REPO, check=True)
        # Stage only persisted Unreal content, never source/config/Saved or someone's index.
        git("add", "--", *paths)
        if update:
            git("add", "--", update.as_posix())
        git("diff", "--cached", "--check")
        git("commit", "-m", "Checkpoint Unreal content " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
    ahead = int(git("rev-list", "--count", "origin/main..HEAD"))
    if ahead and POLICY["push"]:
        git("push", POLICY["remote"], "HEAD:main")
        git("fetch", POLICY["remote"], POLICY["branch"])
    elif POLICY["push"]:
        # An empty project is not evidence that unattended GitHub write access works.
        git("push", "--dry-run", POLICY["remote"], "HEAD:main")
    commit = git("rev-parse", "HEAD")
    if git("rev-parse", "origin/main") != commit:
        raise RuntimeError("Push not verified; local checkpoint retained for retry")
    return {"state": "pushed" if paths or ahead else "unchanged", "commit": commit,
            "files": paths, "session_rollover_required": bool(paths),
            "note": "Git persistence does not clear active Concert history"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    os.environ["GCM_INTERACTIVE"] = "never"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # OS-backed lock releases on crash; a stale directory cannot disable checkpoints.
    lock = STATE.parent / "checkpoint.lock"
    with lock.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0); handle.write(b"0"); handle.flush(); handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SystemExit("Checkpoint already running")
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = checkpoint(args.dry_run)
        except Exception as error:
            result = {"state": "deferred", "error": str(error)}
            try:
                if git("diff", "--name-only", "origin/main...HEAD", "--", POLICY["content_root"]):
                    result["session_rollover_required"] = True
            except subprocess.CalledProcessError:
                pass
        # Preserve a required rollover across unchanged runs and push failures.
        previous = json.loads(STATE.read_text()) if STATE.exists() else {}
        if previous.get("session_rollover_required"):
            result["session_rollover_required"] = True
        result["checked_at"] = time.time()
        if not args.dry_run:
            temp = STATE.with_suffix(".tmp")
            temp.write_text(json.dumps(result, indent=2))
            temp.replace(STATE)
        print(json.dumps(result, indent=2))
        return 1 if result["state"] == "deferred" else 0


if __name__ == "__main__":
    raise SystemExit(main())
