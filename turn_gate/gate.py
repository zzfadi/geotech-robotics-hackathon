"""A small, clock-injected turn gate. This is not a physical safety system.

No hardware command is sent here. An adapter must evaluate ``tick()`` and consume
revocations before dispatching commands, bind work to the current grant ID, and
cancel/clear its own queued and repeated commands when a grant is revoked.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class Observation:
    """Actual capture time in the gate's monotonic clock domain, not receipt time.

    Samples must strictly increase. Republished/cached frames must retain their
    original capture timestamp. ``valid`` covers source tracking/calibration
    validity; a missing or indeterminate hand observation is not clear space.
    """

    observed_at: float
    hand_present: Optional[bool]
    valid: bool = True


@dataclass(frozen=True)
class GateSnapshot:
    """Cached state at evaluated_at; call tick() to evaluate freshness again."""

    state: str
    reason: str
    request_pending: bool
    active_grant_id: Optional[int]
    evaluated_at: Optional[float]
    last_observed_at: Optional[float]
    clear_since: Optional[float]


@dataclass(frozen=True)
class GateEvent:
    """A bounded transition record, with grant_id identifying the affected turn.

    A revoked/completed event carries the former grant ID. IDs are local to one
    TurnGate instance; adapters must also distinguish process/session lifetimes.
    """

    kind: str
    sequence: int
    at: float
    state: str
    reason: str
    grant_id: Optional[int]
    request_pending: bool


class TurnGate:
    """Grant a robot turn only on explicit request and observed clear duration.

    Each public action returns zero or one event; no event history is retained.
    Only observe() advances the clear interval. tick() never grants a turn.
    An interrupted turn requires a new request and a new clear interval.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        clear_for_seconds: float = 0.5,
        stale_after_seconds: float = 0.25,
    ) -> None:
        for name, value in (
            ("clear_for_seconds", clear_for_seconds),
            ("stale_after_seconds", stale_after_seconds),
        ):
            if not self._number(value) or value <= 0:
                raise ValueError(name + " must be finite and greater than zero")
        self._clock = clock
        self.clear_for_seconds = float(clear_for_seconds)
        self.stale_after_seconds = float(stale_after_seconds)
        self._state = "HOLD"
        self._reason = "awaiting_observation"
        self._request_pending = False
        self._active_grant_id: Optional[int] = None
        self._next_grant_id = 1
        self._sequence = 0
        self._evaluated_at: Optional[float] = None
        self._clock_high_water: Optional[float] = None
        self._sample_high_water: Optional[float] = None
        self._observation: Optional[Observation] = None
        self._clear_since: Optional[float] = None

    @staticmethod
    def _number(value: object) -> bool:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value)
        except (OverflowError, TypeError):
            return False

    def snapshot(self) -> GateSnapshot:
        """Return cached state only; this does not read the clock or grant work."""
        return GateSnapshot(
            self._state,
            self._reason,
            self._request_pending,
            self._active_grant_id,
            self._evaluated_at,
            self._observation.observed_at if self._observation else None,
            self._clear_since,
        )

    def _hold(self, reason: str, *, forget_observation: bool = True) -> None:
        self._state = "HOLD"
        self._reason = reason
        self._request_pending = False
        self._active_grant_id = None
        self._clear_since = None
        if forget_observation:
            self._observation = None

    def _begin(self) -> Tuple[GateSnapshot, float, bool]:
        before = self.snapshot()
        now = self._clock()
        if not self._number(now):
            self._hold("invalid_clock")
            return before, self._evaluated_at or 0.0, False
        now = float(now)
        self._evaluated_at = now
        if self._clock_high_water is not None and now < self._clock_high_water:
            self._hold("clock_moved_backwards")
            return before, now, False
        self._clock_high_water = now
        if (
            self._observation is not None
            and now - self._observation.observed_at > self.stale_after_seconds
        ):
            self._hold("stale_observation")
        return before, now, True

    def _finish(
        self, before: GateSnapshot, now: float, *, completed: bool = False
    ) -> Tuple[GateEvent, ...]:
        after = self.snapshot()
        signature = lambda s: (
            s.state, s.reason, s.request_pending, s.active_grant_id
        )
        if signature(before) == signature(after):
            return ()
        if before.state == "ROBOT" and after.state == "HOLD":
            kind = "robot_turn_completed" if completed else "robot_turn_revoked"
            grant_id = before.active_grant_id
        elif after.state == "ROBOT":
            kind = "robot_turn_granted"
            grant_id = after.active_grant_id
        else:
            kind = "turn_requested" if after.request_pending else "holding"
            grant_id = None
        self._sequence += 1
        return (GateEvent(
            kind, self._sequence, now, after.state, after.reason,
            grant_id, after.request_pending,
        ),)

    def _consider_grant(self) -> None:
        if self._state == "ROBOT":
            return
        observed_duration = (
            self._observation.observed_at - self._clear_since
            if self._observation is not None and self._clear_since is not None
            else 0.0
        )
        if observed_duration < self.clear_for_seconds and not math.isclose(
            observed_duration, self.clear_for_seconds, rel_tol=1e-12, abs_tol=0.0
        ):
            self._reason = "clear_debounce"
        elif not self._request_pending:
            self._reason = "awaiting_turn_request"
        else:
            self._state = "ROBOT"
            self._reason = "robot_turn"
            self._active_grant_id = self._next_grant_id
            self._next_grant_id += 1
            self._request_pending = False

    def observe(self, observation: Observation) -> Tuple[GateEvent, ...]:
        before, now, healthy_clock = self._begin()
        if not healthy_clock:
            return self._finish(before, now)
        captured = observation.observed_at
        if not self._number(captured):
            self._hold("invalid_timestamp")
        elif captured > now:
            self._hold("future_observation")
        elif self._sample_high_water is not None and captured <= self._sample_high_water:
            self._hold(
                "duplicate_observation" if captured == self._sample_high_water
                else "out_of_order_observation"
            )
        else:
            self._sample_high_water = float(captured)
            if now - captured > self.stale_after_seconds:
                self._hold("stale_observation")
            elif observation.valid is not True or type(observation.hand_present) is not bool:
                self._hold("invalid_observation")
            else:
                self._observation = observation
                if observation.hand_present:
                    self._hold("hand_present", forget_observation=False)
                else:
                    if self._clear_since is None:
                        self._clear_since = float(captured)
                    self._consider_grant()
        return self._finish(before, now)

    def request_turn(self) -> Tuple[GateEvent, ...]:
        before, now, healthy_clock = self._begin()
        if healthy_clock and self._state != "ROBOT":
            if self._observation is None:
                self._request_pending = False
            elif self._observation.hand_present:
                self._hold("hand_present", forget_observation=False)
            else:
                self._request_pending = True
                self._consider_grant()
        return self._finish(before, now)

    def complete_turn(self, grant_id: int) -> Tuple[GateEvent, ...]:
        """Complete only the active grant; ignore late completion of older work."""
        before, now, healthy_clock = self._begin()
        completed = (
            healthy_clock and self._state == "ROBOT"
            and type(grant_id) is int and grant_id == self._active_grant_id
        )
        if completed:
            self._hold("turn_completed")
        return self._finish(before, now, completed=completed)

    def tick(self) -> Tuple[GateEvent, ...]:
        """Reevaluate expiry before dispatch; elapsed time never grants a turn."""
        before, now, _ = self._begin()
        return self._finish(before, now)
