"""Table 10 robot toolset for the CoPresence agents (editor Python; local, untracked).

Lets the in-editor agent (e.g. Denys Mac Codex, in Agent Chat or a voice call) turn a person's
words into Table 10 robot actions:
  "move the left arm 10 cm up"   -> move_gripper(side="left", up_cm=10)
  "grab the can" / "put it down"  -> dream_task(prompt="pick up the can" / "put the can down")
  "open the right gripper"        -> set_gripper(side="right", opening_percent=100)

Motion safety is the VR handle's (Saved/XR/table10_vr_grab.py): whole-arm damped-least-squares IK
inside the Maker Arm limits (URDF joint limits intersected with the real motors' SDK limits,
minus 1 deg), every link placed from forward kinematics with the base fixed, joints capped at
5 deg per 30 Hz tick. Tools/maker_arm_real_link.py adopts the pose, so in drive mode the real
arms follow under the bridge's guards. Dream tasks go to the Table 10 Dream runner through
Saved/Table10Dream/request.json (owned by the Table 10 session).

    import sys; sys.path.insert(0, '<project>/Saved/XR'); import table10_robot_tools
"""
import json
import math
import re
import time
import uuid
from pathlib import Path

import unreal
import toolset_registry

import table10_vr_grab as grab

TABLE10_OFFSET = (2460.0, 0.0, 0.0)   # Table 10 world = original station + this (cm)
# Asking to pass the can between the arms, which the Dream runner can't do yet
HANDOVER = re.compile(r"\b(pass|give|hand|handover|handoff|transfer|swap|move)\b.*(\b(other|another|second|left|right)\b"
                      r"(\s+\w+)?\s+(arm|gripper|hand)|\bbetween\b.*\b(arms|grippers|hands)\b)")
MAX_MOVE_CM = 25.0
MAX_TURN_DEG = 90.0
SIDES = ("left", "right")


def _project():
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def _dream_dir():
    return _project() / "Saved" / "Table10Dream"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def _side(side):
    side = (side or "").strip().lower()
    if side not in SIDES:
        raise ValueError('side must be "left" or "right" (the arm on the robot\'s left is at world -Y)')
    return side


def _clamp(v, limit):
    return max(-limit, min(limit, float(v)))


def _local(p):
    return [round(p.x - TABLE10_OFFSET[0], 1), round(p.y - TABLE10_OFFSET[1], 1), round(p.z - TABLE10_OFFSET[2], 1)]


def _arm_pose(side):
    import maker_arm_real_link as rl
    import maker_arm_real_mapping as mapping
    actors = grab._arm_actors(side)
    poses = {}
    for link, actor in actors.items():
        t = actor.get_actor_transform()
        poses[link] = ([t.translation.x, t.translation.y, t.translation.z],
                       [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w])
    q, gap, residual = mapping.extract(poses, rl.MOUNT_YAW[side], rl.TABLE10_BASES[side], 0.0)
    return actors, q, gap, residual


def _write_request(prompt, side="", task=""):
    status = _read_json(_dream_dir() / "status.json") or {}
    if status.get("state") in ("queued", "starting", "running") and time.time() - float(status.get("unix", 0)) < 120:
        return {"queued": False, "reason": f"the Table 10 Dream runner is busy ({status.get('state')}): {status.get('detail')}"}
    _dream_dir().mkdir(parents=True, exist_ok=True)
    request = {"id": uuid.uuid4().hex, "prompt": prompt, "phrase": prompt, "unix": time.time(),
               "source": "agent", "table": 10}
    if side:
        request["side"] = side
    if task:
        request["task"] = task
    partial = _dream_dir() / "request.tmp"
    partial.write_text(json.dumps(request))
    partial.replace(_dream_dir() / "request.json")
    return {"queued": True, "id": request["id"], "prompt": prompt}


# ---- planned motions through the Table 10 runtime ------------------------------------------------
# Tools/maker_arm_table10_dream.py plays joint rows at 20 Hz and carries the can kinematically while a
# gripper squeezes it. Agent moves go through it too, so the can follows the arm holding it and the
# runtime's arm state stays the one the Dream runner reads.
HZ = 20.0
JOINT_SPEED = math.radians(18)      # planned joint moves: fastest joint at 18 deg/s on average (eased: <30 peak)
LINE_SPEED_CM = 5.0                  # straight-line moves (lift, lower, approach, back off)
LINE_STEP_CM = 1.0                   # IK waypoint spacing along a straight line
HOME_Q = [0.0, -1.3267562769368857, 1.5947274948757348, -1.4680311465744582, 0.0, 0.0]  # DreamSim/fleet_reset.py
OPEN = 0.95                          # jaw fraction for open
HANDOVER_MEET_X = (40.0, 35.0, 45.0, 30.0)   # table-local x of the meeting point, tried in order (y = 0)
HANDOVER_LIFT_CM = 15.0              # can centre above its resting height at the meeting point


def _t10():
    import maker_arm_table10_dream as t10
    return t10


def _runtime():
    rt = getattr(_t10(), "_rt", None)
    return rt if rt is not None and rt.callback is not None else None



def _start_runtime():
    """Starts the Table 10 runtime (Tools/maker_arm_table10_dream.py) when an editor came up without it:
    agent motions go through it so the can is carried. It must start outside a transaction, so on the
    next tick; the agent asks again a moment later."""
    if not getattr(unreal, "_table10_runtime_starting", False):
        unreal._table10_runtime_starting = True
        state = {}

        def _go(_delta):
            unreal.unregister_slate_post_tick_callback(state["h"])
            unreal._table10_runtime_starting = False
            try:
                _t10().start(str(_dream_dir()))
            except Exception as error:
                unreal.log_error(f"Table 10 runtime start: {error!r}")
        state["h"] = unreal.register_slate_post_tick_callback(_go)
    return {"started": False, "reason": "the Table 10 motion runtime wasn't running; it is starting now, "
                                        "so ask again in a second"}

def _runner_busy():
    status = _read_json(_dream_dir() / "status.json") or {}
    if status.get("state") in ("queued", "starting", "running") and time.time() - float(status.get("unix", 0)) < 120:
        return f"the Table 10 Dream runner is busy ({status.get('state')}): {status.get('detail')}"
    return ""


def _drain(_delta):
    """Starts the next queued agent request once the arms are still (one per tick)."""
    rt = _runtime()
    if not _queue or rt is None or rt.motion is not None or _pending(rt) or _runner_busy():
        return
    fn, args = _queue.pop(0)
    try:
        unreal.log("Table 10 robot tools: queued request -> " + str(fn(*args))[:300])
    except Exception as error:
        unreal.log_warning(f"Table 10 robot tools: queued request failed: {error!r}")


def _enqueue(fn, *args):
    """Runs a request when the current motion ends. It is planned then, from the arms as they are, so a
    request said while another arm is still moving doesn't drag that arm back to an old pose."""
    _queue.append((fn, args))
    previous = getattr(unreal, "_table10_tools_drain", None)
    if previous is None:
        unreal._table10_tools_drain = unreal.register_slate_post_tick_callback(_drain)
    return {"queued": True, "position": len(_queue),
            "note": "the arms are finishing the previous move; this starts right after it"}


_queue = []


def _reset_drain():
    previous = getattr(unreal, "_table10_tools_drain", None)
    if previous is not None:
        try:
            unreal.unregister_slate_post_tick_callback(previous)
        except Exception:
            pass
        unreal._table10_tools_drain = None


def _pending(rt):
    """An agent motion handed to the runtime that it hasn't started yet (it reads command.json on its
    next tick); a second request in the same frame would otherwise overwrite it."""
    return bool(_motion) and rt.seq != _motion.get("seq") and time.time() - _motion.get("started", 0) < 2.0


def _waits(rt):
    """True when the arms are only finishing an agent motion (queue the request then); the Dream runner
    and real-arm modes are checked by _busy as before."""
    return ((rt.motion is not None and str(rt.seq or "").startswith("agent-")) or _pending(rt)) and not _runner_busy()


def _busy(rt, sides=SIDES):
    if rt.motion is not None or _pending(rt):
        return "the Table 10 arms are still moving; wait for that motion to finish"
    for side in sides:
        mode = grab.real_arm_mode(side)
        if mode is not None and mode != "drive":
            return (f"the real {side} arm leads right now (its service is in '{mode}' mode), so Unreal can't move "
                    "it; the person can switch it to drive on the Table 10 web page")
    return _runner_busy()


def _real_note():
    """Says when these moves also move the physical arms."""
    driven = [s for s in SIDES if grab.real_arm_mode(s) == "drive"]
    return f"the real {' and '.join(driven)} arm{'s' if len(driven) > 1 else ''} follow{'' if len(driven) > 1 else 's'} this move" if driven else ""


def _latch(rt, side):
    """Jaw fraction that keeps squeezing what this side holds, else its current opening."""
    import maker_arm_real_mapping as mapping
    if rt.held == side:
        return max(rt.grip_gap - .006, 0.) / mapping.JAW_MAX_M
    return rt.gap[side] / mapping.JAW_MAX_M


class _Plan:
    """Joint rows for both arms, phase by phase, eased, at 20 Hz."""

    def __init__(self, rt):
        self.state = {s: (list(rt.q[s]), _latch(rt, s)) for s in SIDES}
        self.rows = {s: [] for s in SIDES}

    def lines(self, by_side, cm):
        """Several arms along their straight-line waypoints at the same time (shorter lists hold their end)."""
        n = max(len(qs) for qs in by_side.values() if qs) if any(by_side.values()) else 0
        if not n:
            return
        total = max(cm / LINE_SPEED_CM, .5)
        weights = [1.0] * n
        weights[0] = weights[-1] = 2.0
        scale = total / sum(weights)
        for i, w in enumerate(weights):
            targets = {s: (qs[min(i, len(qs) - 1)], None) for s, qs in by_side.items() if qs}
            self.phase(seconds=w * scale, ease=False, **targets)

    def line(self, side, qs, cm):
        """One arm along IK waypoints of a straight line `cm` long at LINE_SPEED_CM, eased only at its two
        ends (each waypoint is its own phase, linear in between, so the motion doesn't stutter)."""
        if not qs:
            return
        total = max(cm / LINE_SPEED_CM, .5)
        weights = [1.0] * len(qs)
        weights[0] = weights[-1] = 2.0              # gentle start and stop
        scale = total / sum(weights)
        for q, w in zip(qs, weights):
            self.phase(seconds=w * scale, ease=False, **{side: (q, None)})

    def phase(self, seconds=None, ease=True, **targets):
        ends = {}
        for s in SIDES:
            q0, j0 = self.state[s]
            q1, j1 = targets.get(s, (None, None))
            ends[s] = (list(q1) if q1 is not None else q0, j0 if j1 is None else j1)
        if seconds is None:
            dq = max(max(abs(a - b) for a, b in zip(self.state[s][0], ends[s][0])) for s in SIDES)
            seconds = max(.6, dq / JOINT_SPEED)
        n = max(2, int(round(seconds * HZ)))
        for s in SIDES:
            (q0, j0), (q1, j1) = self.state[s], ends[s]
            for k in range(1, n + 1):
                e = .5 - .5 * math.cos(math.pi * k / n) if ease else k / n
                self.rows[s].append([a + (b - a) * e for a, b in zip(q0, q1)] + [j0 + (j1 - j0) * e])
        self.state = ends

    def seconds(self):
        return round(len(self.rows["left"]) / HZ, 1)


def _play(plan, label):
    """Hands the rows to the runtime, which plays them over the next seconds (this doesn't wait)."""
    seq = "agent-" + uuid.uuid4().hex[:8]
    report = _dream_dir() / f"agent-action-{seq}.json"
    report.write_text(json.dumps({"action": {"q_l_chunk": plan.rows["left"], "q_r_chunk": plan.rows["right"]},
                                  "controller_source": label}))
    # Keep the 20 newest reports; by age (the names are random), and never the one just written
    reports = sorted((r for r in _dream_dir().glob("agent-action-*.json") if r != report),
                     key=lambda r: r.stat().st_mtime)
    for old in reports[:-20]:
        old.unlink(missing_ok=True)
    partial = _dream_dir() / "command.tmp"
    partial.write_text(json.dumps({"seq": seq, "report": str(report)}))
    partial.replace(_dream_dir() / "command.json")
    _motion.update(seq=seq, label=label, started=time.time(), seconds=plan.seconds())
    return seq


_motion = {}


def _motion_status(rt):
    if not _motion:
        return None
    playing = rt is not None and rt.motion is not None and rt.seq == _motion["seq"]
    return {"label": _motion["label"], "state": "running" if playing else "done", "seconds": _motion["seconds"],
            "can_held_by": rt.held if rt is not None else None}


def _link7(side, q, gap):
    import maker_arm_real_link as rl
    import maker_arm_real_mapping as mapping
    p, r = mapping.fk_ue(q, gap, rl.MOUNT_YAW[side], rl.TABLE10_BASES[side])["link_007"]
    return unreal.Vector(*p), unreal.Quat(*r)


def _solve(side, seed, gap, position, quat, iterations=80):
    import maker_arm_real_link as rl
    import maker_arm_real_mapping as mapping
    q, err_mm, err_deg = mapping.ik(seed, gap, rl.MOUNT_YAW[side], rl.TABLE10_BASES[side],
                                    [position.x, position.y, position.z], [quat.x, quat.y, quat.z, quat.w],
                                    mapping.drive_limits(), iterations=iterations)
    return list(q), err_mm, err_deg


def _straight(side, q0, gap, p_from, r_from, p_to, r_to, max_mm=6.0, max_deg=5.0):
    """IK waypoints every LINE_STEP_CM from one wrist pose to another along a straight line, each seeded
    from the last. Stops at the last waypoint the arm reaches within the Maker Arm limits.
    Returns (joint waypoints, cm covered, cm asked)."""
    total = (p_to - p_from).length()
    n = max(1, int(math.ceil(total / LINE_STEP_CM)))
    qs, q, done = [], list(q0), 0.0
    for i in range(1, n + 1):
        f = i / n
        p = p_from + (p_to - p_from) * f
        r = unreal.MathLibrary.r_lerp(r_from.rotator(), r_to.rotator(), f, True).quaternion()
        q_next, err_mm, err_deg = _solve(side, q, gap, p, r, iterations=40)
        if err_mm > max_mm or err_deg > max_deg:
            break
        qs.append(q_next)
        q, done = q_next, total * f
    return qs, done, total


def _spun(p7, r7, axis_point, deg):
    """The wrist pose turned about the vertical axis through axis_point (an upright can turns on itself)."""
    spin = unreal.Rotator(0, 0, deg).quaternion()
    return axis_point + spin.rotate_vector(p7 - axis_point), spin * r7


def _place(rt):
    """Lowers the can the arm holds straight down until it stands on the table, opens gently, backs off
    straight up and returns the arm to idle (planned, no Dream call)."""
    side = rt.held
    c = rt.cup.get_actor_location()
    p7, r7 = _link7(side, rt.q[side], rt.gap[side])
    drop = (rt.spec["rest_z"] + .2) - c.z
    best = None
    for deg in sorted(range(-40, 45, 10), key=abs):
        p_from, r_from = _spun(p7, r7, c, deg) if deg else (p7, r7)
        if deg:
            # Turn in place first (about the can's axis), then go straight down
            turn, _t_done, _t_total = _straight(side, rt.q[side], rt.gap[side], p7, r7, p_from, r_from)
            if not turn or (_link7(side, turn[-1], rt.gap[side])[0] - p_from).length() > .6:
                continue
            start_q = turn[-1]
        else:
            turn, start_q = [], rt.q[side]
        down, done, total = _straight(side, start_q, rt.gap[side], p_from, r_from,
                                      p_from + unreal.Vector(0, 0, drop), r_from)
        if total - done < .5:
            best = (turn, down, abs(drop), r_from)
            break
        if best is None or done > best[2]:
            best = (turn, down, done, r_from)
    turn, down, lowered, r_hold = best
    if lowered < abs(drop) - .5:
        return {"started": False, "reason": f"the {side} arm can only lower the can {lowered:.0f} of {abs(drop):.0f} cm "
                                            "here inside the Maker Arm limits; move it nearer the arm's base first"}
    import maker_arm_real_mapping as mapping
    plan = _Plan(rt)
    if turn:
        plan.line(side, turn, 3.0)
    plan.line(side, down, abs(drop))
    plan.phase(seconds=.4)                                                  # settle on the table
    plan.phase(seconds=1.0, **{side: (None, OPEN)})                        # open gently: the can stands
    p_low = _link7(side, plan.state[side][0], mapping.JAW_MAX_M * OPEN)[0]
    back, _b, _bt = _straight(side, plan.state[side][0], mapping.JAW_MAX_M * OPEN, p_low, r_hold,
                              p_low + unreal.Vector(0, 0, 10.0), r_hold)
    plan.line(side, back, 10.0)                                            # straight up, clear of the can
    plan.phase(**{side: (HOME_Q, OPEN)})                                    # back to idle
    _play(plan, f"planned put-down ({side} arm, lowered {abs(drop):.0f} cm; no Dream call; editor-world kinematic carry)")
    return {"started": True, "side": side, "lowered_cm": round(abs(drop), 1), "seconds": plan.seconds(), "real_arms": _real_note(),
            "note": "follow it with dream_status"}


def _grasp_offset(side, q, gap):
    """Grasp centre in the wrist (link_007) frame, from the URDF's grasp_center frame."""
    import maker_arm_real_link as rl
    import maker_arm_real_mapping as mapping
    from yam_kinematics import ue_pose
    frames = mapping.mounted_frames(q, gap, yaw=rl.MOUNT_YAW[side])
    g, _ = ue_pose(frames["grasp_center"])
    b = rl.TABLE10_BASES[side]
    p7, r7 = _link7(side, q, gap)
    return r7.unrotate_vector(unreal.Vector(g[0] + b[0], g[1] + b[1], g[2] + b[2]) - p7)


def _side_grasp(rt, side, grasp, seed):
    """Joints that put this side's grasp centre at `grasp`, approaching from its own side of the table with
    the jaws horizontal around the upright can, plus a pre-grasp 8 cm back along the approach."""
    import maker_arm_real_mapping as mapping
    gap = mapping.JAW_MAX_M * OPEN
    offset = _grasp_offset(side, rt.q[side], rt.gap[side])
    reach = offset.length()
    toward = -1.0 if side == "right" else 1.0      # the right arm (+Y) reaches toward -Y, the left toward +Y
    best = None
    for tilt in (25, 40, 10, 55, 0, 70):
        t = math.radians(tilt)
        approach = unreal.Vector(0.0, toward * math.cos(t), -math.sin(t))
        for jaw in (unreal.Vector(1, 0, 0), unreal.Vector(-1, 0, 0)):
            quat = unreal.MathLibrary.make_rot_from_yz(jaw, approach * -1.0).quaternion()
            wrist = grasp - approach * reach
            for start in (seed, HOME_Q, rt.q[side]):
                q, err_mm, err_deg = _solve(side, start, gap, wrist, quat)
                if err_mm < 6 and err_deg < 4:
                    pre, pre_mm, pre_deg = _solve(side, q, gap, wrist - approach * 8.0, quat)
                    if pre_mm < 10 and pre_deg < 6:
                        score = err_mm + pre_mm + 3 * err_deg
                        if best is None or score < best[0]:
                            best = (score, q, pre, approach)
                    break
        if best is not None and best[0] < 4:
            break
    return None if best is None else {"grasp": best[1], "pre": best[2], "approach": best[3]}


def _pickup(rt, side="", lift_cm=10.0):
    """Planned pickup (no Dream call) with either arm: open, go to a pre-grasp beside the can, straight in
    with the jaws around the can's middle, close gently, then lift it straight up."""
    import maker_arm_real_link as rl
    import maker_arm_real_mapping as mapping
    if rt.held in SIDES:
        return {"started": False, "reason": f"the {rt.held} arm is already holding the can"}
    c = rt.cup.get_actor_location()
    if side not in SIDES:
        side = min(SIDES, key=lambda s: math.hypot(c.x - rl.TABLE10_BASES[s][0], c.y - rl.TABLE10_BASES[s][1]))
    take = _side_grasp(rt, side, unreal.Vector(c.x, c.y, c.z), rt.q[side])
    if take is None:
        return {"started": False, "reason": f"the {side} arm can't reach around the can where it stands inside the "
                                            "Maker Arm limits; try the other arm"}
    width = 2 * rt.spec["radius_cm"][0] / 100.0
    close = max(width - .008, 0.) / mapping.JAW_MAX_M
    open_gap = mapping.JAW_MAX_M * OPEN
    pre_p, quat = _link7(side, take["pre"], open_gap)
    grasp_p, _q = _link7(side, take["grasp"], open_gap)
    approach, _d, _t = _straight(side, take["pre"], open_gap, pre_p, quat, grasp_p, quat)
    held_gap = width - .002
    up, lifted, _total = _straight(side, take["grasp"], held_gap, grasp_p, quat,
                                   grasp_p + unreal.Vector(0, 0, lift_cm), quat)
    plan = _Plan(rt)
    plan.phase(**{side: (take["pre"], OPEN)})                    # beside the can, jaws open
    plan.line(side, approach or [take["grasp"]], 8.0)             # straight in around it
    plan.phase(seconds=1.0, **{side: (None, close)})             # close gently
    plan.phase(seconds=.4)                                        # let the grip settle
    plan.line(side, up, lifted)                                   # straight up
    _play(plan, f"planned pickup ({side} arm; no Dream call; editor-world kinematic carry)")
    return {"started": True, "side": side, "lift_cm": round(lifted, 1), "seconds": plan.seconds(), "real_arms": _real_note(),
            "note": "planned pickup (no Dream call); follow it with dream_status"}


def _dream_unavailable():
    """Dream refused for lack of capacity in the last few minutes (DA-2000 NO_CAPACITY)."""
    status = _read_json(_dream_dir() / "status.json") or {}
    return (status.get("state") == "failed" and "capacity" in (status.get("detail") or "").lower()
            and time.time() - float(status.get("unix", 0)) < 300)


def _handover(rt, to_side=""):
    """Planned handover (no Dream call): the arm holding the can carries it to the middle of the table, the
    other arm grasps it lower (or higher) from its own side, the first arm opens, and the runtime's carry
    passes the can over; then the first arm backs off and returns to its idle pose."""
    import maker_arm_real_mapping as mapping
    giver = rt.held
    if giver not in SIDES:
        return {"started": False, "reason": "no arm is holding the can; pick it up first"}
    taker = to_side if to_side in SIDES and to_side != giver else ("right" if giver == "left" else "left")
    c = rt.cup.get_actor_location()
    p7, r7 = _link7(giver, rt.q[giver], rt.gap[giver])
    giver_grasp_z = rt._grasp_center(giver, rt.q[giver], rt.gap[giver])[2] - c.z
    half = rt.spec["half_height_cm"]
    tz = -3.0 if giver_grasp_z > -1.0 else 3.0          # the other half of the can body from the giver's grip
    tz = max(-(half - 1.5), min(half - 1.5, giver_grasp_z + tz * 1.4)) if abs(giver_grasp_z) > 2 else tz
    z = max(c.z, rt.spec["rest_z"] + HANDOVER_LIFT_CM)
    for x in HANDOVER_MEET_X:
        meet = unreal.Vector(TABLE10_OFFSET[0] + x, TABLE10_OFFSET[1], z)
        # The upright can may turn about its own axis on the way, so the wrist turns with it to whatever
        # heading the arm reaches best (the arm's base joint turns as the can moves toward the middle)
        carry = None
        for turn_deg in sorted(range(-60, 65, 5), key=abs):
            spin = unreal.Rotator(0, 0, turn_deg).quaternion()
            q_try, e_mm, e_deg = _solve(giver, rt.q[giver], rt.gap[giver], meet + spin.rotate_vector(p7 - c), spin * r7)
            if carry is None or e_mm + 3 * e_deg < carry[0]:
                carry = (e_mm + 3 * e_deg, q_try)
                if carry[0] < 2:
                    break
        q_carry = carry[1]
        # Where the can really ends up in that pose: the runtime carries it at a fixed offset from the wrist
        carry_wrist, carry_quat = _link7(giver, q_carry, rt.gap[giver])
        can_at = unreal.MathLibrary.compose_transforms(
            rt.rel, unreal.Transform(carry_wrist, carry_quat.rotator(), unreal.Vector(1, 1, 1)))
        up = can_at.rotation.rotate_vector(unreal.Vector(0, 0, 1))
        if math.degrees(math.acos(max(-1.0, min(1.0, up.z)))) > 15:
            continue
        take = _side_grasp(rt, taker, can_at.translation + up * tz, rt.q[taker])
        if take is None:
            continue
        # The giver's own approach axis, to back away along it after letting go
        away = carry_quat.rotate_vector(unreal.Vector(0, 0, 1))
        q_back, b_mm, _ = _solve(giver, q_carry, rt.gap[giver], carry_wrist + away * 8.0, carry_quat)
        width = 2 * rt.spec["radius_cm"][0] / 100.0
        close = max(width - .008, 0.) / mapping.JAW_MAX_M
        plan = _Plan(rt)
        plan.phase(**{giver: (q_carry, None), taker: (None, OPEN)})               # carry to the middle; open
        plan.phase(**{taker: (take["pre"], OPEN)})                                  # taker to its pre-grasp
        plan.phase(seconds=1.2, **{taker: (take["grasp"], OPEN)})                   # in around the can
        plan.phase(seconds=.8, **{taker: (None, close)})                            # taker squeezes
        plan.phase(seconds=.3)                                                       # both hold
        plan.phase(seconds=.6, **{giver: (None, OPEN)})                             # giver lets go: handover
        plan.phase(seconds=.8, **{giver: (q_back if b_mm < 10 else None, OPEN)})    # giver backs off
        plan.phase(**{giver: (HOME_Q, OPEN)})                                        # giver to idle
        _play(plan, f"planned handover {giver} -> {taker} (no Dream call; editor-world kinematic carry)")
        return {"started": True, "from": giver, "to": taker, "meeting_point_table_cm": _local(can_at.translation),
                "seconds": plan.seconds(), "real_arms": _real_note(), "note": "follow it with dream_status"}
    return {"started": False, "reason": f"the {taker} arm can't reach the can in the middle of the table from the "
                                        f"{giver} arm's grip inside the Maker Arm limits"}


@unreal.uclass()
class Table10RobotTools(unreal.ToolsetDefinition):
    """Table 10 robot arms: the pair of MakerMods Maker Arms at the human-interaction table
    (MakerInteraction_T09). Use these to act on what the person asks the robot to do: move a
    gripper by some centimetres or degrees, open or close a gripper, or run a Dream task such
    as picking up or putting down the can. Directions are Table 10's: toward_person is away
    from the robot bases across the table (+X), toward_right_arm is +Y, up is +Z. Motion stays
    inside the Maker Arm's URDF and motor limits; in drive mode the real arms follow."""

    @toolset_registry.tool_call
    @staticmethod
    def table10_status() -> str:
        """Where the Table 10 arms and the can are, and whether Dream or the real arms are busy.

        Returns:
            JSON with each arm's gripper position (table cm), joints (deg) and jaw opening, the
            can, the Dream runner status, and the real-arm mode (mirror, drive or off).
        """
        out = {"arms": {}}
        import maker_arm_real_mapping as mapping
        for side in SIDES:
            try:
                actors, q, gap, residual = _arm_pose(side)
                g = actors["link_007"].get_actor_location()
                out["arms"][side] = {"gripper_table_cm": _local(g), "joints_deg": [round(math.degrees(v), 1) for v in q],
                                     "jaw_open_percent": round(100 * gap / mapping.JAW_MAX_M), "rigid": residual <= 0.3}
            except Exception as error:
                out["arms"][side] = {"error": repr(error)}
        can = grab._find(grab.PREFIX + "HandoverCan")
        out["can"] = {"table_cm": _local(can.get_actor_location())} if can else None
        out["dream"] = _read_json(_dream_dir() / "status.json")
        # Each real arm's service, live (Saved/RealArmLink/<side>/real_state.json): drive = it follows Unreal
        out["real_arms"] = {s: grab.real_arm_mode(s) or "off (its service isn't running)" for s in SIDES}
        return json.dumps(out)

    @toolset_registry.tool_call
    @staticmethod
    def go_home(side: str = "both") -> str:
        """Moves one or both Table 10 arms carefully back to their idle (home) pose, jaws open.

        Args:
            side: "left", "right" or "both".

        Returns:
            JSON saying whether it started (or why not); follow it with dream_status.
        """
        rt = _runtime()
        if rt is None:
            return json.dumps(_start_runtime())
        side = (side or "both").strip().lower()
        sides = SIDES if side not in SIDES else (side,)
        if _waits(rt):
            return json.dumps(_enqueue(Table10RobotTools.go_home, side))
        busy = _busy(rt, sides)
        if busy:
            return json.dumps({"started": False, "reason": busy})
        if rt.held in sides:
            return json.dumps({"started": False, "reason": f"the {rt.held} arm is holding the can; put it down first"})
        plan = _Plan(rt)
        plan.phase(**{s: (HOME_Q, OPEN) for s in sides})
        _play(plan, f"agent home ({' and '.join(sides)})")
        return json.dumps({"started": True, "arms": list(sides), "seconds": plan.seconds(), "real_arms": _real_note()})

    @toolset_registry.tool_call
    @staticmethod
    def move_gripper(side: str, toward_person_cm: float = 0.0, toward_right_arm_cm: float = 0.0,
                     up_cm: float = 0.0, yaw_deg: float = 0.0, pitch_deg: float = 0.0,
                     roll_deg: float = 0.0) -> str:
        """Moves one Table 10 gripper relative to where it is now; the whole arm follows smoothly.

        Each move is limited to 25 cm and 90 deg per axis. A target the arm can't reach is
        approached as closely as the Maker Arm limits allow, and the reply says so.

        Args:
            side: "left" or "right" arm (left is at world -Y, right at +Y), or "both" to move both
                grippers together by the same amount.
            toward_person_cm: Centimetres across the table, away from the robot bases (negative = back).
            toward_right_arm_cm: Centimetres sideways toward the right arm (negative = toward the left arm).
            up_cm: Centimetres up (negative = down).
            yaw_deg: Turn about the vertical axis, degrees.
            pitch_deg: Tilt, degrees.
            roll_deg: Roll, degrees.

        Returns:
            JSON with the target, whether it is reachable, and the expected remaining error.
        """
        import maker_arm_real_link as rl
        import maker_arm_real_mapping as mapping
        both = (side or "").strip().lower() in ("both", "all", "left and right")
        side = "left" if both else _side(side)
        delta = unreal.Vector(_clamp(toward_person_cm, MAX_MOVE_CM), _clamp(toward_right_arm_cm, MAX_MOVE_CM),
                              _clamp(up_cm, MAX_MOVE_CM))
        turn = unreal.Rotator(_clamp(roll_deg, MAX_TURN_DEG), _clamp(pitch_deg, MAX_TURN_DEG),
                              _clamp(yaw_deg, MAX_TURN_DEG)).quaternion()
        rt = _runtime()
        if rt is None:
            return json.dumps(_start_runtime())
        if rt is not None and both:
            if _waits(rt):
                return json.dumps(_enqueue(Table10RobotTools.move_gripper, "both", toward_person_cm, toward_right_arm_cm, up_cm, yaw_deg, pitch_deg, roll_deg))
            busy = _busy(rt)
            if busy:
                return json.dumps({"side": "both", "moving": False, "reason": busy})
            paths, report = {}, {}
            for s in SIDES:
                p7, r7 = _link7(s, rt.q[s], rt.gap[s])
                axis = rt.cup.get_actor_location() if rt.held == s and rt.cup else p7
                best = None
                for deg in sorted(range(-40, 45, 10), key=abs):
                    p_to, r_to = _spun(p7 + delta, turn * r7, axis + delta, deg) if deg else (p7 + delta, turn * r7)
                    qs, done, total = _straight(s, rt.q[s], rt.gap[s], p7, r7, p_to, r_to)
                    if best is None or done > best[1] + .5:
                        best = (qs, done, total)
                    if total - done < .5:
                        break
                paths[s] = best[0]
                report[s] = {"moved_cm": round(best[1], 1), "asked_cm": round(best[2], 1)}
            if not any(paths.values()):
                return json.dumps({"side": "both", "moving": False, "reason": "neither Maker Arm can move that way "
                                   "from its pose inside its joint limits"})
            plan = _Plan(rt)
            plan.lines(paths, max(r["moved_cm"] for r in report.values()))
            _play(plan, "agent move (both grippers together, straight lines)")
            return json.dumps({"side": "both", "arms": report, "seconds": plan.seconds(), "can_held_by": rt.held,
                               "real_arms": _real_note(), "note": "moving now; an arm that can't go all the way "
                               "stops where its limits allow"})
        if rt is not None:
            # Through the Table 10 runtime, so a can in this gripper moves with it
            if _waits(rt):
                return json.dumps(_enqueue(Table10RobotTools.move_gripper, side, toward_person_cm, toward_right_arm_cm, up_cm, yaw_deg, pitch_deg, roll_deg))
            busy = _busy(rt, (side,))
            if busy:
                return json.dumps({"side": side, "moving": False, "reason": busy})
            p7, r7 = _link7(side, rt.q[side], rt.gap[side])
            p, quat = p7 + delta, turn * r7
            # A straight line, so a can in the gripper goes straight up (or wherever asked) and stays upright;
            # when the exact heading can't be kept, the wrist may turn about the vertical axis (the can turns
            # on itself), whichever gets furthest
            axis = rt.cup.get_actor_location() if rt.held == side and rt.cup else p7
            best = None
            for deg in sorted(range(-40, 45, 10), key=abs):
                p_to, r_to = _spun(p, quat, axis + delta, deg) if deg else (p, quat)
                qs, done, total = _straight(side, rt.q[side], rt.gap[side], p7, r7, p_to, r_to)
                if best is None or done > best[1] + .5:
                    best = (qs, done, total)
                if total - done < .5:
                    break
            qs, done, total = best
            if not qs:
                return json.dumps({"side": side, "moving": False, "reason": "the Maker Arm can't move that way from "
                                   "this pose inside its joint limits"})
            plan = _Plan(rt)
            plan.line(side, qs, done)
            _play(plan, f"agent move ({side} gripper, straight line)")
            return json.dumps({"side": side, "target_table_cm": _local(p), "moved_cm": round(done, 1),
                               "asked_cm": round(total, 1), "seconds": plan.seconds(), "real_arms": _real_note(), "can_held_by": rt.held,
                               "note": "moving now" if total - done < .5 else
                               f"only {done:.0f} of {total:.0f} cm fit inside the Maker Arm limits; it stops there"})
        actors, q0, gap, _residual = _arm_pose(side)
        t = actors["link_007"].get_actor_transform()
        p = t.translation + delta
        quat = turn * t.rotation
        _q, err_mm, err_deg = mapping.ik(q0, gap, rl.MOUNT_YAW[side], rl.TABLE10_BASES[side], [p.x, p.y, p.z],
                                         [quat.x, quat.y, quat.z, quat.w], mapping.drive_limits(), iterations=60)
        handle = grab._handle(side)
        target = (p, quat)
        if handle is not None:
            grab._place(handle, target)
        s = grab._sync[side]
        s["set"], s["target"], s["user_at"] = target, target, time.monotonic()
        reachable = err_mm < 10 and err_deg < 5
        return json.dumps({"side": side, "target_table_cm": _local(p), "reachable": reachable,
                           "expected_error_mm": round(err_mm, 1), "expected_error_deg": round(err_deg, 1),
                           "note": "moving now" if reachable else "out of reach: moving as close as the Maker Arm limits allow"})

    @toolset_registry.tool_call
    @staticmethod
    def set_gripper(side: str, opening_percent: float) -> str:
        """Opens or closes one Table 10 gripper.

        Args:
            side: "left" or "right" arm.
            opening_percent: 0 = closed, 100 = fully open (104.8 mm jaw gap).

        Returns:
            JSON with the new jaw opening.
        """
        import maker_arm_real_link as rl
        import maker_arm_real_mapping as mapping
        side = _side(side)
        gap = mapping.JAW_MAX_M * max(0.0, min(100.0, float(opening_percent))) / 100.0
        rt = _runtime()
        if rt is None:
            return json.dumps(_start_runtime())
        if rt is not None:
            if _waits(rt):
                return json.dumps(_enqueue(Table10RobotTools.set_gripper, side, opening_percent))
            busy = _busy(rt, (side,))
            if busy:
                return json.dumps({"side": side, "moving": False, "reason": busy})
            plan = _Plan(rt)
            plan.phase(seconds=.8, **{side: (None, gap / mapping.JAW_MAX_M)})
            _play(plan, f"agent gripper ({side})")
            note = ("opening it sets the can it holds down on the table below" if rt.held == side and
                    gap > rt.grip_gap + .005 else "")
            return json.dumps({"side": side, "jaw_open_percent": round(100 * gap / mapping.JAW_MAX_M),
                               "jaw_gap_mm": round(gap * 1000, 1), "note": note, "real_arms": _real_note()})
        actors, q, _gap, _residual = _arm_pose(side)
        for link, (p, r) in mapping.fk_ue(q, gap, rl.MOUNT_YAW[side], rl.TABLE10_BASES[side]).items():
            if link in actors and actors[link] is not None:
                actors[link].set_actor_location_and_rotation(unreal.Vector(*p), unreal.Quat(*r).rotator(), False, True)
        return json.dumps({"side": side, "jaw_open_percent": round(100 * gap / mapping.JAW_MAX_M),
                           "jaw_gap_mm": round(gap * 1000, 1)})

    @toolset_registry.tool_call
    @staticmethod
    def dream_task(prompt: str, side: str = "") -> str:
        """Asks the Dream robot policy to do a Table 10 task with the arms and the can.

        Use plain task wording, e.g. "pick up the can" or "put the can down". A prompt with
        put / release / drop / reset / let go sets the can down upright and returns both arms
        to idle; any other prompt runs the pickup with the nearer arm that can reach the can.
        "Pass the can to the other arm" hands it over when the runner can (the reply says if not).

        Args:
            prompt: What the robot should do.
            side: "left" or "right" when the person named an arm: the arm to pick up with, or the
                one to hand the can to.

        Returns:
            JSON saying whether the task was queued (or why not) and its id; follow it with dream_status.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is empty")
        side = (side or "").strip().lower()
        side = side if side in SIDES else ""
        can_do = set((_read_json(_dream_dir() / "runner.json") or {}).get("capabilities") or [])
        held_by = (_read_json(_dream_dir() / "cup.json") or {}).get("held_by")
        lowered = prompt.lower()
        # Before the set-down words: a handover prompt often says the other gripper "releases" it
        if HANDOVER.search(lowered):
            return Table10RobotTools.handover(side)
        # The runner would take "return to idle" for a pickup (it picks up for anything not a put-down)
        if re.search(r"\b(idle|home|rest(ing)? (pose|position)|neutral|park)\b", lowered):
            if rt_held := (_read_json(_dream_dir() / "cup.json") or {}).get("held_by"):
                return json.dumps({"queued": False, "reason": f"the {rt_held} arm is holding the can; put it down first"})
            return Table10RobotTools.go_home(side or "both")
        rt = _runtime()
        if rt is not None and rt.motion is not None:
            return json.dumps({"queued": False, "reason": "the Table 10 arms are still moving; wait for that motion"})
        # The runner sets the can down for these words and starts a pickup for anything else
        setting_down = any(word in lowered for word in ("put", "release", "drop", "reset", "let go", "place", "lower"))
        if setting_down and rt is not None and rt.held in SIDES and not _busy(rt, (rt.held,)):
            # An arm holds it: lower it onto the table carefully instead of opening the jaws in the air
            return json.dumps(_place(rt))
        if not setting_down and held_by:
            return json.dumps({"queued": False, "reason": f"the {held_by} arm is already holding the can; "
                               "put it down first (dream: 'put the can down') before another pickup"})
        if not setting_down and rt is not None and _dream_unavailable():
            result = _pickup(rt, side)
            result["note"] = "Dream is at capacity right now, so this is a planned pickup (no Dream call)"
            return json.dumps(result)
        result = _write_request(prompt[:200], side)
        if side and result.get("queued") and "side" not in can_do:
            result["note"] = (f"the runner doesn't take an arm choice yet and uses the arm nearer the can, "
                              f"which may not be the {side} one")
        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def dream_status() -> str:
        """How the current or last Table 10 Dream task is going.

        Returns:
            JSON with the runner status (queued, starting, running, done or failed, plus detail) and the can.
        """
        return json.dumps({"status": _read_json(_dream_dir() / "status.json"),
                           "planned_motion": _motion_status(_runtime()),
                           "can": _read_json(_dream_dir() / "cup.json")})

    @toolset_registry.tool_call
    @staticmethod
    def pickup(side: str = "") -> str:
        """Picks the can up with one arm as a planned motion (no Dream call) and lifts it 10 cm.

        Use it when Dream is unavailable (dream_status says failed / no capacity) or when asked for
        a pickup without Dream.

        Args:
            side: "left" or "right"; empty for the arm nearer the can.

        Returns:
            JSON saying whether it started (or why not); follow it with dream_status.
        """
        rt = _runtime()
        if rt is None:
            return json.dumps(_start_runtime())
        busy = _busy(rt, ((side or "").strip().lower(),) if (side or "").strip().lower() in SIDES else SIDES)
        if busy:
            return json.dumps({"started": False, "reason": busy})
        return json.dumps(_pickup(rt, (side or "").strip().lower()))

    @toolset_registry.tool_call
    @staticmethod
    def handover(to_side: str = "") -> str:
        """Passes the can from the arm holding it to the other arm (planned motion, no Dream call).

        The holding arm carries the can to the middle of the table, the other arm grasps it from
        its own side, the first arm lets go and returns to its idle pose. About 10 seconds.

        Args:
            to_side: "left" or "right", the arm that should end up holding the can; empty for the other arm.

        Returns:
            JSON saying whether it started (or why not), from which arm to which; follow it with dream_status.
        """
        rt = _runtime()
        if rt is None:
            return json.dumps(_start_runtime())
        busy = _busy(rt)
        if busy:
            return json.dumps({"started": False, "reason": busy})
        to_side = (to_side or "").strip().lower()
        return json.dumps(_handover(rt, to_side))

    @toolset_registry.tool_call
    @staticmethod
    def stop_arm_motion() -> str:
        """Stops any gripper move started with move_gripper (the arms hold where they are).
        A running Dream task is not stopped by this.

        Returns:
            JSON confirming the stop.
        """
        for side in SIDES:
            grab._sync[side]["target"] = None
        out = {"stopped": True}
        status = _read_json(_dream_dir() / "status.json") or {}
        if status.get("state") in ("queued", "starting", "running"):
            # The Dream runner ends a task whose id it finds here, when it can ("cancel" capability)
            request = _read_json(_dream_dir() / "request.json") or {}
            partial = _dream_dir() / "cancel.tmp"
            partial.write_text(json.dumps({"id": request.get("id"), "unix": time.time(), "source": "agent"}))
            partial.replace(_dream_dir() / "cancel.json")
            can_do = set((_read_json(_dream_dir() / "runner.json") or {}).get("capabilities") or [])
            out["dream"] = ("asked the Dream runner to cancel its task" if "cancel" in can_do else
                            "the Dream task keeps running: the runner can't be cancelled yet")
        return json.dumps(out)


def register():
    """Make the toolset visible to the agents (tools() / read / edit). Reload-safe."""
    _reset_drain()
    from toolset_registry.registration import Registration
    previous = getattr(unreal, "_table10_robot_tools_registration", None)
    if previous is not None:
        try:
            previous.unregister()
        except Exception:
            pass
    registration = Registration([Table10RobotTools])
    ok = registration.register()
    unreal._table10_robot_tools_registration = registration
    return ok


register()
