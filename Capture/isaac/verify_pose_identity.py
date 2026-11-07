"""Compare a short paired capture of producer and evaluated Hub JSONL records."""

import argparse
import json
import math

from pose_stream import is_pose


def capture_key(frame, metadata):
    return (
        frame["subject"],
        frame["space"],
        metadata["session"],
        metadata["seq"],
        metadata["station_ns"],
    )


def compare_capture(source, evaluated):
    """Test a specific asserted capture reference against every transmitted joint."""
    if not is_pose(evaluated) or source.get("version") != 1:
        raise ValueError("Expected original producer v1 and evaluated export v2")
    if capture_key(source, source) != capture_key(evaluated, evaluated["capture_ref"]):
        raise ValueError("Different subject, space or capture reference")
    if len(source["bones"]) != len(evaluated["bones"]):
        return {"matches": False, "reason": "skeleton size"}

    metres = degrees = confidence = 0.0
    for original, received in zip(source["bones"], evaluated["bones"]):
        if len(original) != 10 or original[:2] != received[:2]:
            return {"matches": False, "reason": "skeleton layout"}
        if not all(type(x) in (int, float) and math.isfinite(x) for x in original[2:]):
            raise ValueError("Invalid source data")
        x, y, z, qx, qy, qz, qw, c = original[2:]
        # Project's XR station frame -> right-handed simulation metres (+X forward,+Y left,+Z up).
        metres = max(metres, math.dist((-z, -x, y), received[2:5]))
        q = (-qz, -qx, qy, qw)
        r = received[5:9]
        norm = math.hypot(*q) * math.hypot(*r)
        if norm == 0:
            raise ValueError("Invalid source quaternion")
        dot = abs(sum(a * b for a, b in zip(q, r)) / norm)
        degrees = max(degrees, math.degrees(2 * math.acos(min(1.0, dot))))
        confidence = max(confidence, abs(c - received[9]))
    return {
        "matches": metres <= 1e-5 and degrees <= 0.01 and confidence <= 1e-5,
        "max_position_m": metres,
        "max_rotation_deg": degrees,
        "max_confidence": confidence,
    }


def verify(originals, exports):
    # Rehearsal files contain one producer packet per capture ID. Ambiguous source IDs invalidate proof.
    known = {}
    for raw in originals:
        source = json.loads(raw)
        key = capture_key(source, source)
        if key in known and known[key]["bones"] != source["bones"]:
            raise ValueError(
                "Conflicting producer poses for the same capture reference"
            )
        known[key] = source
    counts = {"matched": 0, "different": 0, "missing_witness": 0}
    for raw in exports:
        frame = json.loads(raw)
        if not is_pose(frame):
            raise ValueError("Invalid evaluated frame")
        source = known.get(capture_key(frame, frame["capture_ref"]))
        if source is None:
            counts["missing_witness"] += 1
        else:
            proof = compare_capture(source, frame)
            counts["matched" if proof["matches"] else "different"] += 1
            if not proof["matches"]:
                print(
                    json.dumps(
                        {
                            "subject": frame["subject"],
                            "evaluation": frame["evaluation"],
                            "capture_ref": frame["capture_ref"],
                            "comparison": proof,
                        }
                    )
                )
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("originals", type=argparse.FileType("r", encoding="utf-8"))
    parser.add_argument("evaluated", type=argparse.FileType("r", encoding="utf-8"))
    arguments = parser.parse_args()
    with arguments.originals, arguments.evaluated:
        totals = verify(arguments.originals, arguments.evaluated)
    print(json.dumps(totals))
    raise SystemExit(
        0
        if totals["matched"]
        and not totals["different"]
        and not totals["missing_witness"]
        else 1
    )
