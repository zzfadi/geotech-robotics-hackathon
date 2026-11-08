"""Write a vision.run session as control center telemetry (geotech.telemetry/1).

The detector session becomes plain facts the control center can show: camera
health, every handoff moment and coordinator action as a one-sentence decision,
recorded Dream round trips, and the router's mode against the 850 ms line.
Nothing here moves an arm; the actions are what an adapter would be told.

The camera event reports only what this loop knows: whether the frame read
worked (``present``). Colours, orientation, view and aspect are ``unknown``
because nothing here checks them.
"""

from __future__ import annotations

from typing import Callable, Optional

from .detections import FrameDetections
from .hybrid import ExecutorRouter

# moment -> (by, what, routine)
MOMENTS = {
    "hand_entered": ("loop", "A hand is in view; hold", True),
    "offer_started": ("loop", "A can is being offered; wait for it to hold still", True),
    "offer_steady": ("loop", "The offer is steady; ask for the robot's turn", False),
    "can_released": ("loop", "The person let go of the can", False),
    "offer_withdrawn": ("safety", "The offer was withdrawn; stop", False),
    "hand_returned": ("safety", "A hand went back to the released can; stop", False),
    "observation_invalid": ("safety", "The detector frame is unusable; fail closed", False),
    "hand_withdrawn": ("loop", "No hand in the keep-out zone", True),
    "observation_recovered": ("loop", "Detector frames are usable again", True),
}

WHY = {
    "empty_hand": "A hand without a can is in the keep-out zone",
    "multiple_hands": "More than one hand is in view",
    "hand_holding_can_outside_zone": "The can is held outside the handoff zone",
    "steadying": "One hand holds a can in the handoff zone; it must stay still first",
    "can_moving": "One hand holds a can in the handoff zone, but the can is moving",
    "offer_steady": "The held can stayed still in the handoff zone for the steady interval",
    "hand_let_go": "The hand stayed off the can for the release interval",
    "can_without_hand": "The can is in view and no hand is",
    "can_jerked_away": "The held can moved faster than the jerk limit",
    "can_lost": "The can stayed out of view longer than the occlusion allowance",
    "hand_returned_to_can": "The hand is on the can again after letting go",
    "no_hand_in_keep_out": "The keep-out zone is clear",
    "read_failed": "The camera read failed",
    "non_increasing_capture_time": "A frame arrived out of capture order",
    "holding_offer": "The can is still held",
}

GATE_WHY = {
    "hand_present": "The detector no longer allows motion (a hand or offer problem)",
    "stale_observation": "No fresh detector frame arrived in time",
    "invalid_observation": "The detector frame was invalid",
    "invalid_timestamp": "A frame had an invalid capture time",
    "future_observation": "A frame claimed a capture time in the future",
    "clock_moved_backwards": "The clock moved backwards",
    "invalid_clock": "The clock returned an invalid time",
}

PLAN_WHY = {
    "real_arm_runs_local": "The real arm always runs the local routine; Dream drives only the sim arms",
    "dream_unavailable": "Dream is switched off for this run",
    "dream_failing": "Recent Dream requests failed",
    "dream_slow": "The median of the last Dream round trips is over {late} ms",
    "dream_on_time": "The median of the last Dream round trips is at or under {late} ms",
    "dream_unmeasured": "No Dream round trip has been measured yet",
}


def _executor(plan_executor: str) -> str:
    return "Dream" if plan_executor == "dream" else "the local controller"


class DetectorTelemetry:
    """Feed it the coordinator's records; it emits events on a control_center Writer.

    ``wall`` maps the coordinator's clock to unix seconds (replay uses a virtual
    clock); leave it None to stamp events with the writer's own clock.
    """

    def __init__(self, writer, *, router: ExecutorRouter, camera_role: str = "head",
                 wall: Optional[Callable[[float], float]] = None, camera_every: float = 1.0) -> None:
        self.writer, self.router, self.role = writer, router, camera_role
        self.wall, self.camera_every = wall, camera_every
        self._camera_ok: Optional[bool] = None
        self._camera_at: Optional[float] = None
        self._frames_since: list = []
        self._loop_mode: Optional[str] = None
        self._plan: Optional[tuple] = None
        self._trips = 0
        self._task_open = False
        self._start_ts: Optional[float] = None
        self._last_at: Optional[float] = None
        self.counts = {"frames": 0, "decisions": 0}

    def _ts(self, at: Optional[float]) -> Optional[float]:
        return None if self.wall is None or at is None else self.wall(at)

    def emit(self, kind: str, at: Optional[float] = None, **fields) -> dict:
        if at is not None:
            self._last_at = at if self._last_at is None else max(self._last_at, at)
        event = self.writer.emit(kind, ts=self._ts(at), **fields)
        if kind == "decision":
            self.counts["decisions"] += 1
        return event

    def start(self, *, purpose: str, robot: str, note: str, at: Optional[float] = None) -> None:
        event = self.emit("session", at, phase="start", robot=robot, purpose=purpose, note=note,
                          limits={"rtt_threshold_ms": self.router.late_ms,
                                  "window": self.router._round_trips.maxlen})
        self._start_ts = event["ts"]
        self._router_changed(at)

    def end(self, *, purpose: str, robot: str, note: str, at: Optional[float] = None) -> None:
        """Close the session; with no ``at`` it ends at the latest time seen."""
        at = self._last_at if at is None else at
        if self._task_open:
            self.emit("task", at, phase="end", name="Can handoff", outcome="stopped",
                      detail="The session ended during a turn")
            self._task_open = False
        self.emit("session", at, phase="end", robot=robot, purpose=purpose, note=note)

    # ----------------------------------------------------------- inputs

    def round_trip(self, seconds, ok: bool, at: Optional[float] = None) -> None:
        """A recorded Dream round trip (replay ``dream_round_trip``); the router already has it."""
        self._trips += 1
        if at is not None:
            self._last_at = at if self._last_at is None else max(self._last_at, at)
        rid = "rt%04d" % self._trips
        received = self._ts(at)
        sent = None
        if received is not None and ok and seconds is not None:
            sent = max(self._start_ts or received, received - float(seconds))
        self.writer.emit("network", ts=sent if sent is not None else received, phase="sent", request_id=rid)
        if ok and seconds is not None:
            if seconds * 1000.0 > self.router.late_ms:
                self.writer.emit("network", ts=received, phase="late", request_id=rid)
            self.writer.emit("network", ts=received, phase="received", request_id=rid,
                             rtt_ms=round(seconds * 1000.0, 1))
        else:
            self.writer.emit("network", ts=received, phase="failed", request_id=rid, error="recorded failure")
        median = self.router.median_ms
        if median is not None:
            want = "sync" if median > self.router.late_ms else "async"
            if want != self._loop_mode:
                self.emit("mode", at, mode=want, previous=self._loop_mode, median_ms=round(median, 1),
                          threshold_ms=self.router.late_ms, window=self.router._round_trips.maxlen,
                          reason="Median of the last {} is {} ms, {} {} ms".format(
                              "round trip" if len(self.router._round_trips) == 1
                              else "{} round trips".format(len(self.router._round_trips)), int(round(median)),
                              "over" if want == "sync" else "at or under", int(self.router.late_ms)))
                self._loop_mode = want
        self._router_changed(at)

    def frame(self, record: dict, frames: Optional[FrameDetections], at: Optional[float] = None) -> None:
        self.counts["frames"] += 1
        if frames is not None:
            self._camera(frames, at)
        self.record(record, at)

    def record(self, record: dict, at: Optional[float] = None) -> None:
        moment = record.get("moment")
        if moment:
            for event in moment["events"]:
                self._moment(event, at)
        for action in record.get("actions", ()):
            self._action(action, at)
        for event in record.get("gate_events", ()):
            if event["kind"] == "robot_turn_completed" and self._task_open:
                self.emit("task", at, phase="end", name="Can handoff", outcome="success",
                          detail="Turn {} completed".format(event.get("grant_id")))
                self._task_open = False

    # ----------------------------------------------------------- mapping

    def _camera(self, frames: FrameDetections, at: Optional[float]) -> None:
        present = not (not frames.valid and frames.error == "read_failed")
        stamp = frames.captured_at if at is None else at
        self._frames_since.append(stamp)
        due = self._camera_at is None or stamp - self._camera_at >= self.camera_every
        if present == self._camera_ok and not due:
            return
        window = [t for t in self._frames_since if stamp - t <= 1.0]
        self._frames_since = window
        fps = None
        if len(window) > 1 and window[-1] > window[0]:
            fps = round((len(window) - 1) / (window[-1] - window[0]), 1)
        age = None if at is None else round(max(0.0, at - frames.captured_at) * 1000.0, 1)
        checks = {"present": "ok" if present else "fail", "colors": "unknown", "orientation": "unknown",
                  "role_view": "unknown", "aspect": "unknown"}
        self.emit("camera", at, role=self.role, ok=present, checks=checks, fps=fps, age_ms=age,
                  note=None if present else "The camera read failed")
        self._camera_ok, self._camera_at = present, stamp

    def _moment(self, event: dict, at: Optional[float]) -> None:
        kind = event["kind"]
        if kind not in MOMENTS:
            return
        by, what, routine = MOMENTS[kind]
        reason = event.get("reason", "")
        why = WHY.get(reason, "Detector reason: " + reason if reason else "Detector state " + event["state"])
        self.emit("decision", at, by=by, what=what, why=why, step=kind, routine=True if routine else None)

    def _action(self, action: dict, at: Optional[float]) -> None:
        kind = action["action"]
        grant = action.get("grant_id")
        if kind == "start_handoff":
            why = PLAN_WHY.get(action.get("reason"), action.get("reason", "")).format(late=int(self.router.late_ms))
            median = action.get("median_round_trip_ms")
            if median is not None:
                why += " (median {} ms)".format(int(round(median)))
            self.emit("decision", at, by="loop",
                      what="Start the handoff with {} ({})".format(_executor(action["executor"]), action["mode"]),
                      why=why + "; the last centimetre stays with the local assist", step="reach")
            self.emit("task", at, phase="start", name="Can handoff", step="reach", steps=["reach", "retreat"],
                      detail="Turn {}, {}".format(grant, action["mode"]))
            self._task_open = True
        elif kind == "abort":
            self.emit("decision", at, by="safety", what="Abort turn {}".format(grant),
                      why=GATE_WHY.get(action.get("reason"), "Turn gate: {}".format(action.get("reason"))))
            if self._task_open:
                self.emit("task", at, phase="end", name="Can handoff", outcome="stopped",
                          detail="Turn {} aborted".format(grant))
                self._task_open = False
        elif kind == "retreat_with_can":
            self.emit("decision", at, by="loop", what="Retreat with the can",
                      why="The person let go during the reach", step="retreat")
            if self._task_open:
                self.emit("task", at, phase="step", name="Can handoff", step="retreat", step_index=1,
                          steps=["reach", "retreat"])

    def _router_changed(self, at: Optional[float]) -> None:
        plan = self.router.choose()
        if (plan.executor, plan.reason) == self._plan:
            return
        self._plan = (plan.executor, plan.reason)
        why = PLAN_WHY.get(plan.reason, plan.reason).format(late=int(self.router.late_ms))
        if plan.median_round_trip_ms is not None:
            why += " (median {} ms)".format(int(round(plan.median_round_trip_ms)))
        self.emit("decision", at, by="loop", what="Next handoff turn goes to " + _executor(plan.executor),
                  why=why)
