"""The hybrid loop's decision layer: moments in, turn-gate decisions and executor plans out.

The local detector decides *when* the arm may act (a steady offered can) and
*when it must stop* (an extra hand, a jerked can, a detector failure). The
router decides *who* moves the arm for that turn:

* Dream, for the full movement, when Dream is available on this target and the
  median of the last five round trips is at or under 850 ms (Dream workshop);
* local, when the network is slow or Dream keeps failing: the scripted grasp,
  later the edge model (``edge_model/``);
* either way, the last centimetre near the can is the local assist's job.

On the real arm the default is local: the team's call is that Dream-1 is not
needed on the physical arm and Dream stays in the simulation. Nothing here
moves an arm or calls Dream; adapters consume the emitted plans.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Deque, List, Optional

from turn_gate import Observation, TurnGate

from .detections import FrameDetections, finite
from .moments import MomentTracker

HANDOFF_PROMPT = "Take the can from the person's hand."


@dataclass(frozen=True)
class Plan:
    executor: str          # "dream" or "local"
    mode: str              # "dream_async", "local_scripted", "local_edge_model"
    finish: str            # who does the last centimetre
    reason: str
    median_round_trip_ms: Optional[float]
    prompt: str = HANDOFF_PROMPT


class ExecutorRouter:
    """Pick Dream or local per turn from the target and recent Dream round trips."""

    def __init__(
        self,
        *,
        target: str = "sim",
        dream_available: bool = True,
        dream_on_real: bool = False,
        late_ms: float = 850.0,
        window: int = 5,
        max_failures: int = 2,
        local_mode: str = "local_scripted",
    ) -> None:
        if target not in ("sim", "real"):
            raise ValueError("target must be 'sim' or 'real'")
        if local_mode not in ("local_scripted", "local_edge_model"):
            raise ValueError("local_mode must be local_scripted or local_edge_model")
        if not finite(late_ms) or late_ms <= 0 or window < 1 or max_failures < 1:
            raise ValueError("router thresholds must be positive")
        self.target, self.dream_available, self.dream_on_real = target, dream_available, dream_on_real
        self.late_ms, self.max_failures, self.local_mode = float(late_ms), max_failures, local_mode
        self._round_trips: Deque[float] = deque(maxlen=window)
        self._failures = 0

    def record_round_trip(self, seconds: Optional[float], ok: bool = True) -> None:
        """Record one Dream request. A failed request counts as a failure, not a time."""
        if not ok or seconds is None or not finite(seconds) or seconds < 0:
            self._failures += 1
            return
        self._failures = 0
        self._round_trips.append(seconds * 1000.0)

    @property
    def median_ms(self) -> Optional[float]:
        return statistics.median(self._round_trips) if self._round_trips else None

    def choose(self) -> Plan:
        median = self.median_ms
        finish = "local_assist"

        def local(reason: str) -> Plan:
            return Plan("local", self.local_mode, finish, reason, median)

        if self.target == "real" and not self.dream_on_real:
            return local("real_arm_runs_local")
        if not self.dream_available:
            return local("dream_unavailable")
        if self._failures >= self.max_failures:
            return local("dream_failing")
        if median is not None and median > self.late_ms:
            return local("dream_slow")
        reason = "dream_on_time" if median is not None else "dream_unmeasured"
        return Plan("dream", "dream_async", finish, reason, median)


class HandoffCoordinator:
    """Serialize frames, the gate and the router; emit JSON-ready records.

    The gate's default clear debounce is shortened to 0.2 s because the tracker
    already requires a 0.4 s steady offer before it reports ``motion_ok``.
    """

    def __init__(
        self,
        *,
        tracker: Optional[MomentTracker] = None,
        router: Optional[ExecutorRouter] = None,
        clock: Callable[[], float] = time.monotonic,
        clear_for_seconds: float = 0.2,
        stale_after_seconds: float = 0.25,
    ) -> None:
        self.tracker = tracker or MomentTracker()
        self.router = router or ExecutorRouter()
        self.gate = TurnGate(clock=clock, clear_for_seconds=clear_for_seconds,
                             stale_after_seconds=stale_after_seconds)
        self.plan: Optional[Plan] = None
        self.phase = "waiting"

    def _actions(self, gate_events) -> List[dict]:
        actions = []
        for event in gate_events:
            if event.kind == "robot_turn_granted":
                self.plan = self.router.choose()
                self.phase = "reach"
                actions.append({"action": "start_handoff", "grant_id": event.grant_id,
                                **asdict(self.plan)})
            elif event.kind == "robot_turn_revoked":
                actions.append({"action": "abort", "grant_id": event.grant_id,
                                "reason": event.reason})
                self.plan, self.phase = None, "waiting"
            elif event.kind == "robot_turn_completed":
                self.plan, self.phase = None, "waiting"
        return actions

    def on_frame(self, frame: FrameDetections) -> dict:
        update = self.tracker.update(frame)
        gate_events = list(self.gate.observe(Observation(
            frame.captured_at,
            None if update.motion_ok is None else not update.motion_ok,
            valid=update.valid,
        )))
        actions = self._actions(gate_events)
        kinds = {e.kind for e in update.events}
        if "offer_steady" in kinds and self.gate.snapshot().state != "ROBOT":
            requested = list(self.gate.request_turn())
            gate_events += requested
            actions += self._actions(requested)
        grant = self.gate.snapshot().active_grant_id
        if "can_released" in kinds and grant is not None and self.phase == "reach":
            self.phase = "retreat"
            actions.append({"action": "retreat_with_can", "grant_id": grant})
        return self._record("frame", update=update, gate_events=gate_events, actions=actions)

    def tick(self) -> dict:
        gate_events = list(self.gate.tick())
        return self._record("tick", gate_events=gate_events, actions=self._actions(gate_events))

    def robot_done(self, grant_id: int) -> dict:
        gate_events = list(self.gate.complete_turn(grant_id))
        return self._record("robot_done", gate_events=gate_events,
                            actions=self._actions(gate_events))

    def _record(self, kind, *, update=None, gate_events=(), actions=()) -> dict:
        record = {"type": kind, "phase": self.phase,
                  "gate": asdict(self.gate.snapshot()),
                  "gate_events": [asdict(e) for e in gate_events],
                  "actions": list(actions)}
        if update is not None:
            record["moment"] = {
                "captured_at": update.captured_at, "state": update.state,
                "reason": update.reason, "motion_ok": update.motion_ok,
                "valid": update.valid, "can_speed": update.can_speed,
                "events": [asdict(e) for e in update.events],
            }
        return record
