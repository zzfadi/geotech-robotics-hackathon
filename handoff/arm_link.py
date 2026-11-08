"""The arm side: the real guarded server, or a stand-in for tests and replay.

SocketArm speaks arm/tools/pickup_server.py's one-JSON-line protocol over
localhost, one connection per command like pickup_client.py does, so an
operator can still send ``clear`` or ``rest`` from another terminal.

FakeArm is a kinematic stand-in with a fake clock. It moves joints at the
requested speed, closes on a can only when the scripted world says one is
between the jaws, warms joints 2 and 3 while extended, and can trip. It is
not physics and its results are never hardware evidence.
"""
from __future__ import annotations

import json
import socket
import time
from typing import Callable, List, Optional, Sequence


class ArmError(RuntimeError):
    def __init__(self, message: str, reply: Optional[dict] = None):
        super().__init__(message)
        self.reply = reply or {}


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


class FakeClock:
    def __init__(self, start: float = 0.0, limit: float = 600.0):
        self.t = float(start)
        self.limit = limit

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)
        if self.t > self.limit:
            raise TimeoutError("replay ran past %.0f s of simulated time" % self.limit)


class SocketArm:
    def __init__(self, port: int = 8765, host: str = "127.0.0.1", timeout: float = 120.0):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the arm server only listens on localhost; tunnel to it")
        self.host, self.port, self.timeout = host, port, timeout

    def call(self, cmd: dict) -> dict:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall((json.dumps(cmd) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as exc:
            raise ArmError("arm server unreachable: %s" % exc) from exc
        try:
            reply = json.loads(buf)
        except ValueError as exc:
            raise ArmError("arm server sent no reply") from exc
        if not reply.get("ok", False):
            raise ArmError(reply.get("error") or "arm server refused", reply)
        return reply

    def status(self) -> dict:
        return self.call({"cmd": "status"})

    def move(self, q: Sequence[float], speed: float) -> dict:
        return self.call({"cmd": "move", "q": [round(float(x), 3) for x in q], "speed": speed})

    def grip(self, deg: float, wait: float) -> dict:
        return self.call({"cmd": "grip", "deg": deg, "wait": wait})

    def rest(self) -> dict:
        return self.call({"cmd": "rest"})


class FakeArm:
    """Replies shaped like pickup_server's, driven by a FakeClock."""

    def __init__(self, clock: FakeClock, q: Sequence[float], *,
                 can_between_jaws: Callable[[float], bool] = lambda t: False,
                 can_present_after_grasp: Callable[[float], bool] = lambda t: True,
                 contact_deg: float = -33.0, grip_open: float = -90.0,
                 temps_c: Sequence[float] = (40.0, 41.0),
                 heat_c_per_s: float = 0.025, trip_at: Optional[float] = None,
                 slip_at: Optional[float] = None):
        self.clock = clock
        self.q = [float(x) for x in q] + [grip_open]
        self.can_between_jaws = can_between_jaws
        self.can_present_after_grasp = can_present_after_grasp
        self.contact_deg = contact_deg
        self.temps = list(temps_c)
        self.heat = heat_c_per_s
        self.trip_at = trip_at
        self.slip_at = slip_at
        self.trip: Optional[str] = None
        self.holding = False
        self.calls: List[dict] = []
        self._last_t = clock.now()

    def _advance(self, seconds: float) -> None:
        self.clock.sleep(seconds)
        self._warm()

    def _warm(self) -> None:
        dt = self.clock.now() - self._last_t
        self._last_t = self.clock.now()
        # Extended arm (shoulder far from rest) heats; folded arm cools.
        extended = abs(self.q[1] + 55.6) > 10
        rate = self.heat if extended else -self.heat
        self.temps = [max(30.0, t + rate * dt) for t in self.temps]
        if self.trip_at is not None and self.clock.now() >= self.trip_at and not self.trip:
            self.trip = "motor 3 torque residual (scripted)"
        if (self.holding and self.slip_at is not None and self.clock.now() >= self.slip_at):
            self.holding = False
            self.q[6] = -2.6

    def _state(self) -> dict:
        return {"ok": True, "arm_state": "ENABLED", "fault": None, "trip": self.trip,
                "q_deg": [round(x, 3) for x in self.q],
                "temp_c": [40.0, self.temps[0], self.temps[1], 38.0, 36.0, 35.0, 33.0]}

    def _log(self, cmd: dict) -> None:
        self.calls.append(dict(cmd, t=round(self.clock.now(), 3)))

    def _check_trip(self) -> None:
        self._warm()
        if self.trip:
            raise ArmError("tripped: " + self.trip, self._state())

    def status(self) -> dict:
        self._log({"cmd": "status"})
        self._warm()
        return self._state()

    def move(self, q: Sequence[float], speed: float) -> dict:
        self._log({"cmd": "move", "q": list(q), "speed": speed})
        self._check_trip()
        speed = min(15.0, max(1.0, float(speed)))
        dist = max(abs(a - b) for a, b in zip(q, self.q[:6]))
        self._advance(dist / speed + 0.08)
        self._check_trip()
        self.q[:6] = [float(x) for x in q]
        return self._state()

    def grip(self, deg: float, wait: float) -> dict:
        self._log({"cmd": "grip", "deg": deg, "wait": wait})
        self._check_trip()
        closing = deg > self.q[6]
        if closing and self.can_between_jaws(self.clock.now()):
            self.q[6] = self.contact_deg
            self.holding = True
        elif closing:
            self.q[6] = max(deg - 0.1, -2.6)
            self.holding = False
        else:
            self.q[6] = deg
            self.holding = False
        self._advance(wait)
        if self.holding and not self.can_present_after_grasp(self.clock.now()):
            self.holding = False
            self.q[6] = -2.6
        # Like pickup_server: only a grip reply says whether the SDK is regulating on contact.
        return dict(self._state(), gripper_holding=self.holding)

    def rest(self) -> dict:
        self._log({"cmd": "rest"})
        self._advance(3.0)
        self.q = [-16.5, -55.6, 20.9, 44.1, 10.25, 1.46, -90.0]
        self.holding = False
        return dict(self._state(), return_verified=True, release_acknowledged=True)
