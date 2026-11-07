"""Replay JSONL input locally without opening devices or network connections."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from .gate import Observation, TurnGate


def replay(
    records: Iterable[Mapping[str, object]],
    *,
    clear_for_seconds: float = 0.5,
    stale_after_seconds: float = 0.25,
) -> Iterator[dict]:
    now = 0.0
    gate = TurnGate(
        clock=lambda: now,
        clear_for_seconds=clear_for_seconds,
        stale_after_seconds=stale_after_seconds,
    )
    for record in records:
        now = record["at"]
        action = record["action"]
        if action == "observe":
            events = gate.observe(Observation(
                observed_at=record["observed_at"],
                hand_present=record.get("hand_present"),
                valid=record.get("valid", True),
            ))
        elif action == "request_turn":
            events = gate.request_turn()
        elif action == "complete_turn":
            events = gate.complete_turn(record["grant_id"])
        elif action == "tick":
            events = gate.tick()
        else:
            raise ValueError("unknown action: " + str(action))
        yield {
            "action": action,
            "events": [asdict(event) for event in events],
            "snapshot": asdict(gate.snapshot()),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="JSONL trace file")
    parser.add_argument("--clear-for", type=float, default=0.5)
    parser.add_argument("--stale-after", type=float, default=0.25)
    args = parser.parse_args()
    line_number = 0

    def records() -> Iterator[dict]:
        nonlocal line_number
        with args.trace.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("each line must be a JSON object")
                yield item

    try:
        for output in replay(
            records(), clear_for_seconds=args.clear_for,
            stale_after_seconds=args.stale_after,
        ):
            print(json.dumps(output, allow_nan=False, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("replay failed at line {}: {}".format(line_number, exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
