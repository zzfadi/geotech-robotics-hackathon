"""Time Dream round trips and ask the hybrid router what it would do on this network.

Mock (no key, no network, nothing billed; latencies are synthetic and labelled so):

    python3 -m vision.dream_latency --mock --mock-median-ms 712 --max-calls 10 \
        --telemetry vision/local/sessions

Real (billed Dream calls; needs DREAM_API_KEY in the environment and a captured
observation, the same inputs as DreamSim/probe.py):

    DREAM_LOG=off uv run --no-project --python 3.12 --with dreamlabs-client==1.1.3 \
        --with dreamlabs==1.0.37 --with numpy --with pillow python -m vision.dream_latency \
        --state state.json --overhead overhead.jpg --left wrist_l.jpg --right wrist_r.jpg \
        --max-calls 10 --telemetry vision/local/sessions

Each call installs the observation with ``set_observation`` and times
``get_action`` (so the time includes the Dream client's own IK solve, as the
loop sees it). A reply over 850 ms is logged ``late``; an exception, a timeout or
a malformed chunk is ``failed``. Every result feeds the same ``ExecutorRouter``
the coordinator uses (Dream when the median of the last 5 is at most 850 ms and
fewer than 2 calls in a row failed), and the output says which executor that
router would pick on this network. The probe never moves anything: returned
chunks are checked for shape and discarded.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import random
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .hybrid import HANDOFF_PROMPT, ExecutorRouter

ROOT = Path(__file__).resolve().parents[1]
HARD_CAP = 50                      # real calls per run; raising it needs a code change
MOCK_CAP = 100000
FRAME_SIZE = (320, 176)
JAW_GAP_M = 0.104825
ROLES = (("overhead", "head"), ("wrist_l", "wrist_l"), ("wrist_r", "wrist_r"))
CHECKPOINT = "dreamlabs/dream-1-beta-flash"

REASONS = {
    "dream_on_time": "The median of the last {n} is {median} ms, at or under {late} ms",
    "dream_unmeasured": "No round trip has been measured yet",
    "dream_slow": "The median of the last {n} is {median} ms, over {late} ms",
    "dream_failing": "{failures} Dream requests in a row failed",
    "dream_unavailable": "Dream is not available on this machine",
    "real_arm_runs_local": "The real arm runs the local grasp unless Dream is allowed on it",
}


class Refused(Exception):
    """A precondition for real calls is missing; nothing was sent."""


class VirtualClock:
    """Monotonic seconds that advance only when something sleeps (mock runs)."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


class MockDreamError(RuntimeError):
    pass


class MockDreamClient:
    """Synthetic Dream client: lognormal round trips around a stated median.

    ``medians_ms`` is one median per phase; calls are split evenly across the
    phases, so ``(712, 1100)`` is a network that degrades halfway through.
    Each call sleeps on ``clock`` (a VirtualClock in dry runs, so nothing waits).
    """

    def __init__(self, medians_ms: Sequence[float], *, sigma: float = 0.25, fail_rate: float = 0.0,
                 calls: int = 10, seed: int = 1, clock: Optional[VirtualClock] = None) -> None:
        if not medians_ms or not all(math.isfinite(m) and m > 0 for m in medians_ms):
            raise ValueError("mock medians must be positive")
        if not (math.isfinite(sigma) and sigma >= 0) or not 0 <= fail_rate <= 1:
            raise ValueError("mock sigma must be >= 0 and fail rate in [0, 1]")
        self.medians, self.sigma, self.fail_rate = list(medians_ms), sigma, fail_rate
        self.calls, self.rng = max(1, calls), random.Random(seed)
        self.clock = clock or VirtualClock()
        self.made = 0
        self.observations = 0

    def describe(self) -> str:
        phases = " then ".join("{:g} ms".format(m) for m in self.medians)
        return ("MOCK: synthetic lognormal round trips, median {}, sigma {:g}, fail rate {:g}, seed fixed; "
                "no Dream call was made").format(phases, self.sigma, self.fail_rate)

    def set_observation(self, frames, observation, **_):
        self.observations += 1

    def get_action(self):
        phase = self.medians[min(len(self.medians) - 1, self.made * len(self.medians) // self.calls)]
        self.made += 1
        latency_ms = phase * math.exp(self.rng.gauss(0.0, self.sigma))
        failed = self.rng.random() < self.fail_rate
        self.clock.sleep(latency_ms / 1000.0)
        if failed:
            raise MockDreamError("mock request failed")
        row = [0.0] * 7
        return {"q_l_chunk": [row] * 48, "q_r_chunk": [row] * 48}

    def close(self) -> None:
        pass


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(len(ordered), rank) - 1]


def check_chunk(action) -> None:
    """A usable reply has two chunks of rows with 7 finite values (6 joints plus gripper)."""
    if not isinstance(action, dict):
        raise ValueError("action is not a mapping")
    for key in ("q_l_chunk", "q_r_chunk"):
        rows = action.get(key)
        rows = rows.tolist() if hasattr(rows, "tolist") else rows
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError(key + " missing or empty")
        for row in rows:
            if len(row) != 7 or not all(math.isfinite(float(v)) for v in row):
                raise ValueError(key + " rows need 7 finite values")


def _call_with_timeout(fn: Callable, timeout: Optional[float]):
    if not timeout:
        return fn()
    box: Dict[str, object] = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # handed back to the caller below
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError("no reply within {:g} s".format(timeout))
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


def _scrub(text: str) -> str:
    key = os.environ.get("DREAM_API_KEY")
    if key:
        text = text.replace(key, "<redacted>")
    return text[:160]


def _trips(n: int) -> str:
    return "round trip" if n == 1 else "{} round trips".format(n)


def _reason_text(router: ExecutorRouter, reason: str) -> str:
    median = router.median_ms
    return REASONS.get(reason, reason).format(
        n=_trips(len(router._round_trips)), median="?" if median is None else int(round(median)),
        late=int(router.late_ms), failures=router._failures)


def run_probe(client, frames: dict, observation: dict, *, calls: int, clock: Callable[[], float],
              sleep: Callable[[float], None], router: Optional[ExecutorRouter] = None, writer=None,
              interval: float = 0.0, upload_bytes: Optional[int] = None, model: Optional[str] = None,
              call_timeout: Optional[float] = None, label: str = "") -> dict:
    """Make ``calls`` round trips; return the summary. Writes telemetry when ``writer`` is given."""
    router = router or ExecutorRouter(target="sim")
    late_ms = router.late_ms
    emit = writer.emit if writer is not None else (lambda *a, **k: None)
    rtts: List[float] = []
    late = failed = 0
    loop_mode = None
    verdict: Optional[Tuple[str, str]] = None
    stopped = ""
    made = 0
    for i in range(calls):
        if i and interval:
            sleep(interval)
        rid = "dl%04d" % (i + 1)
        client.set_observation(frames, observation)
        sent = emit("network", phase="sent", request_id=rid, upload_bytes=upload_bytes,
                    frame_size=list(FRAME_SIZE), model=model)
        made += 1
        started = clock()
        error = None
        try:
            check_chunk(_call_with_timeout(client.get_action, call_timeout))
        except TimeoutError as exc:
            error, stopped = "timeout: " + str(exc), "a call timed out; the client state is unknown"
        except Exception as exc:  # any failure is a failed round trip for the router
            error = _scrub("{}: {}".format(type(exc).__name__, exc))
        # Whole microseconds, so float noise never puts an exact 850 ms reply on the wrong side of the line.
        rtt_ms = round((clock() - started) * 1000.0, 3)
        elapsed = rtt_ms / 1000.0
        if error is None:
            rtts.append(rtt_ms)
            if rtt_ms > late_ms:
                late += 1
                emit("network", phase="late", request_id=rid,
                     ts=None if sent is None else sent["ts"] + late_ms / 1000.0)
            emit("network", phase="received", request_id=rid, rtt_ms=round(rtt_ms, 1), model=model)
            router.record_round_trip(elapsed, ok=True)
        else:
            failed += 1
            emit("network", phase="failed", request_id=rid, error=error, model=model)
            router.record_round_trip(None, ok=False)
        median = router.median_ms
        if median is not None:
            want = "sync" if median > late_ms else "async"
            if want != loop_mode:
                emit("mode", mode=want, previous=loop_mode, median_ms=round(median, 1),
                     threshold_ms=late_ms, window=router._round_trips.maxlen,
                     reason="Median of the last {} is {} ms, {} {} ms".format(
                         _trips(len(router._round_trips)), int(round(median)),
                         "over" if want == "sync" else "at or under", int(late_ms)))
                loop_mode = want
        plan = router.choose()
        if (plan.executor, plan.reason) != verdict:
            verdict = (plan.executor, plan.reason)
            emit("decision", by="loop", request_id=rid,
                 what="Next handoff turn goes to " + ("Dream" if plan.executor == "dream"
                                                       else "the local controller"),
                 why=_reason_text(router, plan.reason))
        if stopped:
            break
    plan = router.choose()
    summary = {
        "type": "dream_latency_summary",
        "label": label,
        "calls": made,
        "received": len(rtts),
        "late": late,
        "failed": failed,
        "late_threshold_ms": late_ms,
        "rtt_ms": {"median": _round(statistics.median(rtts)) if rtts else None,
                   "p95": _round(percentile(rtts, 0.95)), "min": _round(min(rtts, default=None)),
                   "max": _round(max(rtts, default=None))},
        "window_median_ms": _round(router.median_ms),
        "verdict": {"executor": plan.executor, "mode": plan.mode, "reason": plan.reason,
                    "why": _reason_text(router, plan.reason), "target": router.target},
        "rule": "Dream if the median of the last {} round trips is at most {:g} ms and fewer than {} "
                "requests in a row failed; local otherwise (vision.hybrid.ExecutorRouter)".format(
                    router._round_trips.maxlen, late_ms, router.max_failures),
        "stopped_early": stopped or None,
    }
    emit("decision", by="loop",
         what="Verdict for this network: " + ("Dream" if plan.executor == "dream" else "local"),
         why="{}; overall median {} ms, p95 {} ms; {} late and {} failed of {} calls".format(
             summary["verdict"]["why"], summary["rtt_ms"]["median"], summary["rtt_ms"]["p95"],
             late, failed, made))
    return summary


# ----------------------------------------------------------------- real observation

def load_state(path: Path) -> dict:
    """The probe's state file: q_left, q_right (six radians) and gap_left_m, gap_right_m."""
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    observation = {}
    for side in ("left", "right"):
        q = state.get("q_" + side)
        if not isinstance(q, list) or len(q) != 6 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in q):
            raise ValueError("q_{} must be six finite radians".format(side))
        gap = state.get("gap_{}_m".format(side))
        if not isinstance(gap, (int, float)) or isinstance(gap, bool) or not 0 <= gap <= JAW_GAP_M:
            raise ValueError("gap_{}_m must be 0 to {} m".format(side, JAW_GAP_M))
        observation["state.{}_arm".format(side)] = [float(v) for v in q]
        observation["state.{}_grip".format(side)] = [float(gap) / JAW_GAP_M]
    return observation


def encode_frames(paths: Dict[str, Path]) -> Tuple[dict, Dict[str, bytes]]:
    """Resize each JPEG to 320x176 like DreamSim/probe.py. Needs Pillow."""
    from PIL import Image  # optional dependency, present in the real-mode environment

    frames, raw = {}, {}
    for role, path in paths.items():
        buffer = io.BytesIO()
        Image.open(path).convert("RGB").resize(FRAME_SIZE, Image.Resampling.LANCZOS).save(
            buffer, format="JPEG", quality=90)
        raw[role] = buffer.getvalue()
        frames["video." + role] = base64.b64encode(raw[role]).decode("ascii")
    return frames, raw


def _dreamsim_helpers():
    """DreamSim/probe.py's policy config and model.py's derived Maker Arm, by import."""
    folder = str(ROOT / "DreamSim")
    sys.path.insert(0, folder)
    try:
        from model import prepare  # type: ignore
        from probe import configure  # type: ignore
    finally:
        sys.path.remove(folder)
    return configure, prepare


def real_client(checkpoint: str, model_dir: Path):  # pragma: no cover - needs the Dream service
    from dream import DreamClient  # optional dependency

    configure, prepare = _dreamsim_helpers()
    arm = prepare(model_dir)
    client = DreamClient()
    try:
        configure(client, arm, checkpoint)
        print("policy configured; warming the custom CAD IK (not timed)", file=sys.stderr, flush=True)
        if not client.wait_for_ik(180):
            raise TimeoutError("custom IK warm-up exceeded 180 s")
    except BaseException:
        client.close()
        raise
    return client


# ----------------------------------------------------------------- CLI

def _medians(text: str) -> List[float]:
    try:
        values = [float(v) for v in text.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("comma-separated milliseconds, e.g. 712 or 712,1100")
    if not values:
        raise argparse.ArgumentTypeError("at least one median")
    return values


def _session_start(writer, purpose: str, router: ExecutorRouter, note: str) -> None:
    writer.emit("session", phase="start", robot="none moved (Dream latency probe)", purpose=purpose,
                limits={"rtt_threshold_ms": router.late_ms, "window": router._round_trips.maxlen},
                note=note)
    writer.emit("task", phase="start", name="Dream latency probe", detail=note)


def _session_end(writer, purpose: str, summary: dict, note: str) -> None:
    outcome = "stopped" if summary.get("stopped_early") else (
        "failed" if summary["received"] == 0 else "success")
    writer.emit("task", phase="end", name="Dream latency probe", outcome=outcome,
                detail="{} calls, {} late, {} failed".format(summary["calls"], summary["late"], summary["failed"]))
    writer.emit("session", phase="end", robot="none moved (Dream latency probe)", purpose=purpose, note=note)


def main(argv=None, *, make_client=None, environ=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mock", action="store_true", help="synthetic latencies; no key, no network")
    parser.add_argument("--mock-median-ms", type=_medians, default=[712.0],
                        help="median per phase, comma-separated (default 712, the workshop median)")
    parser.add_argument("--mock-sigma", type=float, default=0.25, help="lognormal sigma (default 0.25)")
    parser.add_argument("--mock-fail-rate", type=float, default=0.0)
    parser.add_argument("--mock-seed", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=10,
                        help="number of Dream calls, a hard cap (real mode at most %d)" % HARD_CAP)
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between calls")
    parser.add_argument("--call-timeout", type=float, default=30.0, help="seconds; a timeout stops the probe")
    parser.add_argument("--target", choices=("sim", "real"), default="sim",
                        help="router target; on 'real' the router stays local by team decision")
    parser.add_argument("--late-ms", type=float, default=850.0)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--overhead", type=Path)
    parser.add_argument("--left", type=Path, help="wrist_l JPEG")
    parser.add_argument("--right", type=Path, help="wrist_r JPEG")
    parser.add_argument("--prompt", default=HANDOFF_PROMPT)
    parser.add_argument("--checkpoint", default=CHECKPOINT)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "vision" / "local" / "dream_model")
    parser.add_argument("--telemetry", type=Path, help="write a control_center session under this folder")
    parser.add_argument("--session", help="session id (default generated)")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ

    if args.max_calls < 1 or args.max_calls > (MOCK_CAP if args.mock else HARD_CAP):
        print("refusing: --max-calls must be 1 to {}".format(MOCK_CAP if args.mock else HARD_CAP), file=sys.stderr)
        return 2
    # Checked before any client exists, so a bad value can never stop a real run after a billed call.
    if not (math.isfinite(args.interval) and args.interval >= 0
            and math.isfinite(args.call_timeout) and args.call_timeout > 0):
        print("refusing: --interval must be >= 0 and --call-timeout > 0 seconds", file=sys.stderr)
        return 2
    try:
        router = ExecutorRouter(target=args.target, late_ms=args.late_ms)
        if args.mock:
            vclock = VirtualClock()
            client = MockDreamClient(args.mock_median_ms, sigma=args.mock_sigma, fail_rate=args.mock_fail_rate,
                                     calls=args.max_calls, seed=args.mock_seed, clock=vclock)
    except ValueError as exc:
        print("refusing: {}".format(exc), file=sys.stderr)
        return 2
    raw_frames: Dict[str, bytes] = {}
    if args.mock:
        clock, sleep = vclock, vclock.sleep
        frames, observation = {}, {"prompt": args.prompt}
        note, purpose, model, upload, label = client.describe(), "demo", "mock", None, "mock"
        wall0 = time.time()
        writer_clock = lambda: wall0 + vclock()
    else:
        try:
            if not environ.get("DREAM_API_KEY"):
                raise Refused("DREAM_API_KEY is not set in this environment. Real mode makes billed Dream "
                              "calls; inject the key from the authorized store, or use --mock for a dry run.")
            missing = [n for n in ("state", "overhead", "left", "right") if getattr(args, n) is None]
            if missing:
                raise Refused("real mode needs a captured observation: --" + ", --".join(missing))
            observation = load_state(args.state)
            observation["prompt"] = args.prompt
            frames, raw_frames = encode_frames({"overhead": args.overhead, "wrist_l": args.left,
                                                "wrist_r": args.right})
        except Refused as exc:
            print("refusing: " + str(exc), file=sys.stderr)
            return 2
        except (OSError, ValueError, ImportError) as exc:
            print("refusing: could not prepare the observation: {}".format(exc), file=sys.stderr)
            return 2
        print("COST WARNING: up to {} real Dream inference calls ({}) on the account behind DREAM_API_KEY. "
              "--max-calls is the cap; Ctrl-C stops it. Nothing moves.".format(args.max_calls, args.checkpoint),
              file=sys.stderr, flush=True)
        try:
            client = (make_client or real_client)(args.checkpoint, args.model_dir)
        except Exception as exc:
            print("could not start the Dream client: {}".format(_scrub(str(exc))), file=sys.stderr)
            return 2
        clock, sleep, writer_clock = time.monotonic, time.sleep, None
        note = ("Real Dream round trips from one captured observation, {} calls max; nothing moves"
                .format(args.max_calls))
        purpose, model, label = "live", args.checkpoint, "real"
        upload = sum(len(b) for b in raw_frames.values())

    writer = None
    if args.telemetry:
        from control_center.telemetry import Writer, new_session_id

        writer = Writer(str(args.telemetry), session=args.session or new_session_id("dream-latency-" + label),
                        component="dream-latency", clock=writer_clock)
        _session_start(writer, purpose, router, note)
        for role, frame_role in ROLES:
            if role in raw_frames:
                full, rel = writer.frame_path(role + ".jpg")
                Path(full).write_bytes(raw_frames[role])
                writer.emit("frame", role=frame_role, path=rel, width=FRAME_SIZE[0], height=FRAME_SIZE[1],
                            request_id="dl0001", bytes=len(raw_frames[role]))
    try:
        summary = run_probe(client, frames, observation, calls=args.max_calls, clock=clock, sleep=sleep,
                            router=router, writer=writer, interval=args.interval, upload_bytes=upload,
                            model=model, call_timeout=None if args.mock else args.call_timeout, label=label)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        client.close()
    summary["note"] = note
    if writer is not None:
        _session_end(writer, purpose, summary, note)
        summary["session"] = writer.session
        summary["events"] = os.path.join(writer.dir, "events.jsonl")
        writer.close()
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
