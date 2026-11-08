"""The handoff routine and its taped-mark fallback, as one guarded sequence.

Hand mode ("hold my beer"):
  1. Operator says go. The turn gate must grant: the arm's path is clear.
  2. Approach: jaws open, move to the offer pose in short guarded segments.
     A hand in the path, or a stale/invalid detector feed, pauses the move
     before the next segment; it resumes only on a new operator go and a new
     gate grant.
  3. Offer: the arm is parked and still. Wait until the detector sees the can
     centred between the jaws with a hand still on it, continuously.
  4. Grasp: close. The jaw angle says what happened: on nothing (open, try
     again), something thinner than a can such as fingers (open at once, try
     again), or the can.
  5. Release: keep holding still until the hand is off the can, continuously.
     If the person never lets go, open the jaws and give it back.
  6. Retreat: a fresh gate grant, then a guarded move to the carry pose.
  7. Confirm: hold for a moment, re-read the jaw angle. Success means the can
     is still held at the carry pose; the arm stays holding for the next beat.

Mark mode (fallback): the same gate and guards, but approach, descend over
the taped mark, close, lift and carry. Nobody's hand is involved.

The routine only calls an arm object (handoff.arm_link) and reads a
detections.Fuser. Nothing here is a safety system; the operator stays on the
e-stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from turn_gate.gate import Observation, TurnGate

from .arm_link import ArmError
from .config import Config, Pose
from .detections import Fuser, Scene

TASK_NAMES = {"hand": "Take the can from a person's hand", "mark": "Pick the can from the taped mark"}


class Abort(Exception):
    def __init__(self, reason: str, why: str, outcome: str = "failed", retract: bool = True):
        super().__init__(reason)
        self.reason, self.why, self.outcome, self.retract = reason, why, outcome, retract


@dataclass
class Result:
    mode: str
    outcome: str            # success | failed | stopped
    reason: str
    why: str
    holding: bool
    grasp_attempts: int
    pauses: int
    seconds: float
    seconds_to_grasp: Optional[float]
    final_step: str
    events: List[dict] = field(default_factory=list)

    def summary(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "events"}
        d["seconds"] = round(self.seconds, 2)
        if self.seconds_to_grasp is not None:
            d["seconds_to_grasp"] = round(self.seconds_to_grasp, 2)
        return d


class ScriptedOperator:
    """Operator actions at scripted clock times: go, resume, stop."""

    def __init__(self, clock, actions: Sequence[Sequence]):
        self.clock = clock
        self.actions = sorted((float(t), str(a)) for t, a in actions)

    def poll(self) -> List[str]:
        now = self.clock.now()
        due = [a for t, a in self.actions if t <= now]
        self.actions = [(t, a) for t, a in self.actions if t > now]
        return due


class Routine:
    def __init__(self, cfg: Config, arm, fuser: Fuser, operator, clock, mode: str = "hand",
                 emit: Optional[Callable[[str, dict], None]] = None,
                 before_read: Callable[[], None] = lambda: None):
        if mode not in TASK_NAMES:
            raise ValueError("mode must be hand or mark")
        self.cfg, self.arm, self.fuser, self.operator, self.clock = cfg, arm, fuser, operator, clock
        self.mode = mode
        self._emit_cb = emit
        self._before_read = before_read
        t = cfg.timing
        # The gate evaluates each frame at its own capture time (frames that
        # arrived during a blocking move are replayed in order), then a tick at
        # the real time enforces freshness of the newest one.
        self._gate_t = clock.now()
        self.gate = TurnGate(clock=lambda: self._gate_t, clear_for_seconds=t.clear_for_s,
                             stale_after_seconds=t.stale_after_s)
        self.q: Optional[List[float]] = None
        self.contact_deg: Optional[float] = None
        self.holding = False
        self.grasp_attempts = 0
        self.pauses = 0
        self.step = "idle"
        self.events: List[dict] = []
        self._grant: Optional[int] = None
        self._fed: Optional[float] = None
        self._path_hit: Optional[str] = None
        self._cursor: Dict[str, Optional[float]] = {}
        self._pending_ops: List[str] = []
        self._t0 = clock.now()
        self._grasped_at: Optional[float] = None

    # ----------------------------------------------------------------- output
    def _emit(self, kind: str, **f) -> None:
        ev = dict(t=round(self.clock.now() - self._t0, 3), kind=kind, **f)
        self.events.append(ev)
        if self._emit_cb:
            self._emit_cb(kind, f)

    def _step(self, name: str, detail: str = "") -> None:
        self.step = name
        self._emit("step", step=name, detail=detail)

    def _decide(self, by: str, what: str, why: str) -> None:
        self._emit("decision", by=by, what=what, why=why)

    # --------------------------------------------------------------- operator
    def _poll_operator(self) -> None:
        for action in self.operator.poll():
            if action == "stop":
                raise Abort("operator_stop", "The operator stopped the routine.",
                            outcome="stopped", retract=False)
            self._pending_ops.append(action)

    def _take_op(self, *names: str) -> bool:
        for i, a in enumerate(self._pending_ops):
            if a in names:
                del self._pending_ops[i]
                return True
        return False

    def _sleep(self, seconds: float) -> None:
        self.clock.sleep(seconds)
        self._poll_operator()

    # ------------------------------------------------------------------- arm
    def _take(self, reply: dict) -> dict:
        q = reply.get("q_deg")
        if isinstance(q, list) and len(q) >= 7:
            self.q = [float(x) for x in q[:7]]
        temps = [t for t in (reply.get("temp_c") or [])[:6] if isinstance(t, (int, float))]
        if temps and max(temps) >= self.cfg.temp_abort_c:
            hot = max(range(len(temps)), key=lambda i: temps[i])
            raise Abort("motor_hot", "Motor %d reached %.0f C; the server trips at 52 C." % (
                hot + 1, temps[hot]))
        return reply

    def _call(self, fn, *args) -> dict:
        try:
            return self._take(fn(*args))
        except ArmError as exc:
            if exc.reply:
                try:
                    self._take(exc.reply)
                except Abort:
                    pass
            raise Abort("arm_error", "The arm server refused: %s" % exc, retract=False) from exc

    def _status(self) -> dict:
        return self._call(self.arm.status)

    def _grip(self, deg: float, wait: float) -> dict:
        return self._call(self.arm.grip, deg, wait)

    def _open(self) -> None:
        self._grip(self.cfg.gripper.open_deg, self.cfg.gripper.open_wait_s)
        self.holding = False
        self.contact_deg = None

    def _still_holding(self) -> bool:
        if self.q is None or self.contact_deg is None:
            return False
        g, grip = self.q[6], self.cfg.gripper
        return g < grip.empty_deg and g - self.contact_deg <= grip.slip_deg

    # ------------------------------------------------------------ turn gate
    def _pump(self) -> None:
        """Feed every new path frame to the gate; remember any hand or bad frame."""
        self._before_read()
        now = self.clock.now()
        events = []
        for sc in self.fuser.history("path", self._fed):
            self._fed = sc.captured_at
            if sc.captured_at > now:
                continue
            if not sc.valid:
                self._path_hit = self._path_hit or "the detector feed was invalid"
            elif sc.hand_in_path is not False:
                self._path_hit = self._path_hit or (
                    "a hand came into the arm's path" if sc.hand_in_path else
                    "the detector did not report the path")
            self._gate_t = max(self._gate_t, sc.captured_at)
            events += self.gate.observe(Observation(
                sc.captured_at, sc.hand_in_path if sc.valid else None, sc.valid))
        self._gate_at_now()
        events += self.gate.tick()
        for e in events:
            if e.kind in ("robot_turn_granted", "robot_turn_revoked", "robot_turn_completed"):
                self._emit("gate", event=e.kind, reason=e.reason, grant=e.grant_id)

    def _gate_at_now(self) -> None:
        self._gate_t = max(self._gate_t, self.clock.now())

    def _motion_allowed(self) -> Optional[str]:
        """None when the next segment may go; otherwise why not."""
        self._pump()
        snap = self.gate.snapshot()
        hit, self._path_hit = self._path_hit, None
        if snap.state != "ROBOT" or snap.active_grant_id != self._grant:
            return {"hand_present": "a hand came into the arm's path",
                    "stale_observation": "the detector feed went stale",
                    "invalid_observation": "the detector feed was invalid",
                    }.get(snap.reason, hit or "the turn gate is holding (%s)" % snap.reason)
        if hit:
            # A bad frame the gate did not act on: still a pause.
            self._gate_at_now()
            self.gate.complete_turn(self._grant)
            return hit
        return None

    def _acquire(self, timeout: float, operator_first: bool, purpose: str) -> None:
        deadline = self.clock.now() + timeout
        waiting_for_op = operator_first
        said = False
        while True:
            self._poll_operator()
            if waiting_for_op:
                if self._take_op("go", "resume"):
                    waiting_for_op = False
                    self._decide("operator", "Asked the arm to %s" % purpose, "The operator said go.")
            if not waiting_for_op:
                self._pump()
                snap = self.gate.snapshot()
                if snap.state == "ROBOT":
                    self._grant = snap.active_grant_id
                    self._path_hit = None
                    return
                if not snap.request_pending:
                    self._gate_at_now()
                    self.gate.request_turn()
                if not said and snap.reason == "hand_present":
                    self._decide("safety", "Waiting before the arm moves",
                                 "A hand is in the arm's path; it moves once the path is clear.")
                    said = True
            if self.clock.now() > deadline:
                if waiting_for_op:
                    raise Abort("no_go", "Nobody said go.", outcome="stopped", retract=False)
                raise Abort("path_never_cleared", "The arm's path never cleared for %.0f s." % timeout,
                            retract=False)
            self._sleep(0.05)

    def _release_turn(self) -> None:
        if self._grant is not None:
            self._gate_at_now()
            self.gate.complete_turn(self._grant)
            self._grant = None

    def _move_chain(self, poses: Sequence[Pose], phase: str) -> None:
        seg = self.cfg.timing.segment_deg
        for pose in poses:
            while True:
                if self.q is None:
                    self._status()
                cur = self.q[:6]
                remaining = max(abs(a - b) for a, b in zip(pose.q, cur))
                if remaining <= 0.5:
                    break
                frac = min(1.0, seg / remaining)
                target = [c + (g - c) * frac for c, g in zip(cur, pose.q)]
                self._poll_operator()
                why = self._motion_allowed()
                if why:
                    self.pauses += 1
                    self._grant = None
                    self._decide("safety", "Paused the %s before the next step" % phase, why[0].upper() + why[1:] + ".")
                    self._step("paused", phase)
                    self._acquire(self.cfg.timing.resume_timeout_s,
                                  self.cfg.resume_needs_operator, "resume the " + phase)
                    self._step(phase, "resumed")
                    continue
                reply = self._call(self.arm.move, target, pose.speed)
                self._emit("state", q=[round(x, 2) for x in self.q[:6]], grip=round(self.q[6], 2))

    # ------------------------------------------------------------- watching
    def _start_watch(self, role: str) -> None:
        self._before_read()
        hist = self.fuser.history(role, None)
        self._cursor[role] = max(self.clock.now(), hist[-1].captured_at if hist else 0.0)

    def _wait_for(self, role: str, predicate: Callable[[Scene], bool], debounce: float,
                  timeout: float, on_poll: Optional[Callable[[], None]] = None) -> bool:
        """True once predicate holds on every fresh frame for debounce seconds."""
        start = last_poll = self.clock.now()
        since = last_ok = None
        stale = self.cfg.timing.stale_after_s
        while True:
            self._poll_operator()
            self._before_read()
            for sc in self.fuser.history(role, self._cursor.get(role)):
                self._cursor[role] = sc.captured_at
                if predicate(sc):
                    since = sc.captured_at if since is None else since
                    last_ok = sc.captured_at
                else:
                    since = last_ok = None
            now = self.clock.now()
            if last_ok is not None and now - last_ok > stale:
                since = last_ok = None   # the feed stalled: continuity is broken
            if since is not None and last_ok - since >= debounce - 1e-9:
                return True
            if on_poll and now - last_poll >= self.cfg.timing.status_every_s:
                last_poll = now
                on_poll()
            if now - start > timeout:
                return False
            self._sleep(0.03)

    def _check_holding(self) -> None:
        self._status()
        if not self._still_holding():
            self.holding = False
            raise Abort("pulled_or_slipped", "The jaws closed past the can: it was pulled back or slipped.")

    # ---------------------------------------------------------------- grasp
    def _close(self) -> str:
        grip = self.cfg.gripper
        self.grasp_attempts += 1
        reply = self._grip(grip.close_deg, grip.close_wait_s)
        g = self.q[6]
        if g >= grip.empty_deg:
            return "empty"
        if grip.contact_min_deg <= g <= grip.contact_max_deg and reply.get("gripper_holding") is not False:
            self.contact_deg = g
            self.holding = True
            self._grasped_at = self.clock.now()
            self._emit("contact", what="grasp", object="can", detail="jaws stopped at %.1f deg" % g)
            return "can"
        if grip.contact_min_deg <= g <= grip.contact_max_deg:
            return "no_regulation"
        return "thin" if g > grip.contact_max_deg else "wide"

    # ------------------------------------------------------------------ run
    def run(self) -> Result:
        self._emit("task", phase="start", name=TASK_NAMES[self.mode])
        outcome, reason, why = "success", "held", ""
        try:
            self._status()
            self._open()
            self._step("waiting_for_go")
            self._acquire(self.cfg.timing.trigger_timeout_s, True,
                          "take the can" if self.mode == "hand" else "pick the can from the mark")
            if self.mode == "hand":
                self._run_hand()
            else:
                self._run_mark()
            self._step("confirm")
            self.clock.sleep(self.cfg.timing.hold_check_s)
            self._check_holding()
            self._release_turn()
            why = "The can is held at the carry pose (jaws at %.1f deg)." % self.q[6]
            self._decide("controller", "Holding the can", why)
            self._step("done")
        except Abort as a:
            outcome, reason, why = a.outcome, a.reason, a.why
            self._decide("safety" if a.outcome != "stopped" else "operator", "Stopped: " + a.reason, a.why)
            if a.reason == "pulled_or_slipped":
                self._emit("contact", what="drop", object="can", detail=a.why)
            self._cleanup(a)
        self._emit("task", phase="end", name=TASK_NAMES[self.mode], outcome=outcome, detail=reason)
        return Result(self.mode, outcome, reason, why, self.holding, self.grasp_attempts, self.pauses,
                      self.clock.now() - self._t0,
                      None if self._grasped_at is None else self._grasped_at - self._t0,
                      self.step, self.events)

    def _run_hand(self) -> None:
        cfg, t = self.cfg, self.cfg.timing
        self._step("approach")
        self._move_chain(cfg.chain(cfg.hand_approach), "approach")
        self._release_turn()   # parked and still: nothing moves until the can is in the jaws
        for attempt in range(1, cfg.max_grasp_attempts + 1):
            self._step("offer", "attempt %d" % attempt)
            self._decide("loop", "Waiting for the can between the jaws",
                         "The arm is parked with its jaws open at the handoff spot.")
            self._start_watch("jaws")
            if not self._wait_for("jaws", lambda s: s.presenting, t.present_debounce_s,
                                  t.present_timeout_s, on_poll=self._status):
                raise Abort("no_can_presented", "No can was held between the jaws for %.0f s." %
                            t.present_timeout_s)
            self._step("grasp", "attempt %d" % attempt)
            result = self._close()
            if result == "can":
                break
            self._open()
            self._decide("controller", "Opened the jaws and offered again", {
                "empty": "The jaws closed on nothing.",
                "thin": "The jaws closed further than a can allows, maybe on fingers.",
                "wide": "The jaws stopped wider than a can.",
                "no_regulation": "The gripper did not report holding on contact.",
            }[result])
        else:
            raise Abort("no_grasp", "No grasp in %d attempts." % cfg.max_grasp_attempts)
        self._step("release")
        self._decide("loop", "Holding still until the hand lets go",
                     "Pulling away while the person still holds the can would tug it.")
        self._start_watch("jaws")
        if not self._wait_for("jaws", lambda s: s.released, t.release_debounce_s,
                              t.release_timeout_s, on_poll=self._check_holding):
            self._open()
            self._decide("controller", "Gave the can back", "The person kept holding it.")
            raise Abort("person_kept_holding", "The hand stayed on the can for %.0f s." % t.release_timeout_s)
        self._check_holding()
        self._emit("contact", what="release", object="hand", detail="the person let go of the can")
        self._step("retreat")
        self._acquire(t.clear_timeout_s, False, "carry the can back")
        self._move_chain(cfg.chain(cfg.hand_retreat), "retreat")

    def _run_mark(self) -> None:
        cfg = self.cfg
        self._step("approach")
        self._move_chain(cfg.chain(cfg.mark_approach), "approach")
        self._step("descend")
        self._move_chain(cfg.chain(cfg.mark_descend), "descent")
        if cfg.mark_check_can_before_close:
            self._before_read()
            sc = self.fuser.scene(self.clock.now(), ("jaws",))
            if sc is not None and sc.valid and sc.can_in_jaws is False:
                raise Abort("can_not_at_mark", "The wrist camera sees no can between the jaws.")
        self._step("grasp")
        result = self._close()
        if result != "can":
            raise Abort("missed_can", "Closing at the mark found %s." % {
                "empty": "nothing", "thin": "something thinner than a can",
                "wide": "something wider than a can", "no_regulation": "no regulated contact"}[result])
        self._step("lift")
        self._move_chain(cfg.chain(cfg.mark_lift), "lift")
        self._check_holding()
        self._step("retreat")
        self._move_chain(cfg.chain(cfg.mark_retreat), "retreat")

    # -------------------------------------------------------------- cleanup
    def _cleanup(self, a: Abort) -> None:
        self._grant = None
        if not a.retract:
            self._step("halted", "the arm holds where it is")
            return
        try:
            if self.holding and not self._still_holding():
                self.holding = False
            if not self.holding:
                self._open()
            else:
                self._step("halted", "holding the can; take it from the jaws, then rest")
                return
            ready = self.cfg.chain(self.cfg.hand_approach if self.mode == "hand" else self.cfg.mark_approach)[0]
            if self.q and max(abs(a_ - b) for a_, b in zip(self.q[:6], ready.q)) > 0.5:
                self._step("reset", "back to the ready pose")
                # Back out the way it came (reverse of the approach), under the same gate.
                names = self.cfg.hand_approach if self.mode == "hand" else self.cfg.mark_approach
                back = self.cfg.chain(list(reversed(names)))
                self._acquire(self.cfg.timing.clear_timeout_s, False, "reset to the ready pose")
                self._move_chain(back, "reset")
                self._release_turn()
            self._step("halted", "at the ready pose, jaws open")
        except Abort as inner:
            self._decide("safety", "Reset stopped: " + inner.reason, inner.why)
            self._step("halted", "the arm holds where it is")
