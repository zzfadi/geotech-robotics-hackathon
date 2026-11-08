"""Tally handoff trials and apply the morning rule.

The rule: the pickup from the taped mark is the live beat, unless the
hand-to-arm handoff reached 8 of its last 10 live trials. The mark itself
should also be 8 of 10 before it is locked (target about 10:15).

  python3 -m handoff.trials                    # arm/local/handoff/trials.jsonl
  python3 -m handoff.trials --last 10 some.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from .run import TRIALS

NEED, OF = 8, 10


def tally(rows, last=OF):
    out = {}
    for mode in ("hand", "mark"):
        runs = [r for r in rows if r.get("mode") == mode and not r.get("dry_run")]
        recent = runs[-last:]
        wins = sum(r.get("outcome") == "success" for r in recent)
        reasons = collections.Counter(r.get("reason") for r in recent if r.get("outcome") != "success")
        out[mode] = {"live_trials": len(runs), "last": len(recent), "successes": wins,
                     "failures_by_reason": dict(reasons.most_common())}
    h, m = out["hand"], out["mark"]
    if h["last"] < OF:
        verdict = "demo the taped mark: hand handoff has only %d live trials, needs %d of %d" % (
            h["last"], NEED, OF)
    elif h["successes"] >= NEED:
        verdict = "hand handoff passes (%d of %d): demo it" % (h["successes"], h["last"])
    else:
        verdict = "hand handoff is %d of %d, under %d of %d: demo the taped mark" % (
            h["successes"], h["last"], NEED, OF)
    out["verdict"] = verdict
    if m["last"] >= OF and m["successes"] >= NEED:
        out["mark"]["status"] = "mark passes (%d of %d): ready to lock" % (m["successes"], m["last"])
    else:
        out["mark"]["status"] = "mark is %d of %d live trials, needs %d of %d before it is locked" % (
            m["successes"], m["last"], NEED, OF)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log", nargs="?", type=Path, default=TRIALS)
    p.add_argument("--last", type=int, default=OF)
    a = p.parse_args(argv)
    rows = [json.loads(l) for l in a.log.read_text().splitlines() if l.strip()] if a.log.exists() else []
    print(json.dumps(tally(rows, a.last), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
