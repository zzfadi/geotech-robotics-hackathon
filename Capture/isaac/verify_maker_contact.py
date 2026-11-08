"""Verify contact onset against station witnesses and independent clear-hand controls."""

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from contact_fixture import CYCLE
GEOMETRY_TOLERANCE_M = 1e-4


def summarize(station, receipt):
    def read(path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def key(r):
        return r["subject"], r["evaluation"]["session"], r["evaluation"]["tick"]

    expected = {key(r): r["sha256"] for r in read(station)}
    rows = read(receipt)
    if not rows or rows[0]["event"] != "target" or rows[-1]["event"] != "end":
        raise ValueError(
            "Missing scene identification or successful end of trial: "
            + str(rows[-1:])[-500:]
        )
    target = rows[0]
    center = target["center_m"]
    steps = [r for r in rows if r["event"] == "step"]
    bad = sum(
        expected.get(key(r)) != r["sha256"]
        or r["capture_ref"]["session"] != target["trial"]
        for r in steps
    )
    groups = defaultdict(list)
    for r in steps:
        groups[int(r["capture_ref"]["seq"]) // CYCLE].append(r)
    sweeps = []
    controls = []
    for cycle, frames in sorted(groups.items()):

        def phase(r):
            return int(r["capture_ref"]["seq"]) % CYCLE

        clear = [
            r
            for r in frames
            if 5 <= phase(r) <= 35 and center[1] - r["hand_m"][1] > 0.20
        ]
        controls.extend(clear)
        approach = [r for r in frames if 40 <= phase(r) <= 89]
        hits = [r for r in approach if r["impulse_Ns"] > 1e-6]
        if len(clear) < 8 or not approach or max(map(phase, frames)) < 90:
            continue
        ref = [statistics.median(r["wrist_pose"][i] for r in clear) for i in range(3)]
        drift = max(math.dist(ref, r["wrist_pose"][:3]) for r in clear)
        excursion = max(math.dist(ref, r["wrist_pose"][:3]) for r in approach)
        result = dict(
            cycle=cycle,
            control_ticks=len(clear),
            clear_drift_mm=1000 * drift,
            approach_excursion_mm=1000 * excursion,
            peak_impulse_Ns=max(r["impulse_Ns"] for r in approach),
        )
        if hits:
            first = hits[0]
            result.update(
                first_response_tick=first["tick"],
                evaluation_tick=first["evaluation"]["tick"],
                delay_ms=first["evaluation_to_response_ms"],
                clock_rtt_ms=first["clock_rtt_ms"],
                first_impulse_Ns=first["impulse_Ns"],
                hand_target_error_m=math.dist(first["hand_actual_m"], first["hand_m"]),
            )
        sweeps.append(result)
    positive = [s for s in sweeps if "delay_ms" in s]
    clear_hits = sum(r["impulse_Ns"] > 1e-6 for r in controls)
    verified = [
        s
        for s in positive
        if s["hand_target_error_m"] < GEOMETRY_TOLERANCE_M
        and s["approach_excursion_mm"] > s["clear_drift_mm"] + 0.01
        and s["delay_ms"] > 0
    ]
    ages = sorted(s["delay_ms"] for s in verified)
    return dict(
        passed=len(verified) >= 3
        and len(verified) == len(sweeps)
        and not bad
        and not clear_hits,
        target=target,
        physics_ticks_with_pose=len(steps),
        matching_pose_failures=bad,
        clear_ticks=len(controls),
        clear_contact_ticks=clear_hits,
        sweeps=sweeps,
        verified_sweeps=len(verified),
        median_delay_ms=statistics.median(ages) if ages else None,
        range_delay_ms=[min(ages), max(ages)] if ages else None,
        raw_sha256={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (station, receipt)
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("station", type=Path)
    p.add_argument("receipt", type=Path)
    args = p.parse_args()
    result = summarize(args.station, args.receipt)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
