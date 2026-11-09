"""Table 10 <-> real Maker Arms, Unreal side. Editor Python; local transforms only.

The SDK bridge (Tools/real_arm_bridge.py, run with the maker-arm-sdk venv) and this module talk
through two files in Saved/RealArmLink:
  real_state.json  bridge -> Unreal: mode ('mirror' | 'web' | 'drive' | 'hold') and measured joints per side
  ue_state.json    Unreal -> bridge: Table 10 joints per side (URDF radians, jaw gap metres)

Modes (decided by the bridge, read here every tick):
- mirror: the real arms are torque-off; Table 10's arms are posed from the measured joints, so
  moving a real arm by hand moves the virtual one 1:1 (joint level).
- web: an arm is torque-on and driven by the real-arm service itself (web tests, or holding with no
  Unreal); Table 10 mirrors it like in mirror mode. Once Table 10 matches it, the service hands the
  arm to drive.
- drive, or no fresh bridge state: drag a Table 10 gripper (MakerInteraction_T09__MakerArm_<side>_link_007) with the
  gizmo; damped-least-squares IK poses the whole arm within joint limits valid for both the
  model and the real motors, and ue_state.json publishes the joints for the bridge to follow.
  An arm posed by another tool through forward kinematics (set_pose, playback) is picked up by
  exact joint recovery instead.

Test motions (web buttons via DreamSim/table10_agent.py -> command.json): 'move_up' and 'idle'
(maker_arm_real_tests) run here only while both arms are torque-off (mirror): a virtual
preview. With an arm on, the agent sends the tests to the real-arm service instead (web mode).
In mirror mode they run as a virtual preview: Table 10 moves, the real arms stay still, then Table 10 eases back to the
measured real pose and mirroring resumes.

Transforms are set without transactions or Modify(): no Multi-User history and nothing saved.
Runs in the active Simulate/PIE world when there is one, otherwise in the editor world.

    import maker_arm_real_link as link; link.start(); link.status(); link.stop()
"""

import json
import math
import os
import time
import traceback
from pathlib import Path

import unreal

import maker_arm_real_mapping as mapping
import maker_arm_real_tests as tests
from maker_arm_grid import offset
from maker_arm_station import BASES, MOUNT_YAW, PREFIX, VISUAL_LINKS

# Table 10 is the separate human-interaction station (grid index 9), not the original T00
# station that fleet Table 1 uses: its arms are MakerInteraction_T09__MakerArm_<side>_<link>,
# translated by the grid offset (no rotation or scale).
TABLE10_INDEX = 9
ARM_PREFIX = "MakerInteraction_T%02d__%s" % (TABLE10_INDEX, PREFIX)
TABLE10_BASES = {side: tuple(BASES[side][i] + offset(TABLE10_INDEX)[i] for i in range(3)) for side in BASES}

SIDES = ("left", "right")
TICK_HZ = 30.0
REAL_FRESH_S = 0.5
MOVED_CM = 0.05
MOVED_DOT = 0.99999  # |quat dot| below this counts as rotated (~0.5 deg)
CONSISTENT_CM = 0.3  # recovered joints reproduce every link within this -> posed by FK
IK_ITERATIONS = 10
# Test motions advance a virtual clock by at most this per tick, so an editor hitch pauses the
# motion instead of jumping it (the bridge rejects any target > 25 deg from its last and holds).
MAX_TEST_STEP_S = 0.1
LAG_ABORT_DEG = 12.0   # real arm this far behind the virtual target ...
LAG_ABORT_S = 1.0      # ... for this long: the bridge is holding; abort and say how to resync
PREVIEW_HOLD_S = 1.5   # virtual preview: pause at the end before easing back to the real pose
RETURN_SPEED_RAD_S = 0.6

_link = None


def default_directory():
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())) / "RealArmLink"


def _write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def _moved(a, b):
    if math.dist(a[0], b[0]) > MOVED_CM:
        return True
    return abs(sum(x * y for x, y in zip(a[1], b[1]))) < MOVED_DOT


class Link:
    def __init__(self, directory=None):
        self.dir = Path(directory) if directory else default_directory()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.real_path = self.dir / "real_state.json"
        self.ue_path = self.dir / "ue_state.json"
        self.command_path = self.dir / "command.json"
        self.command_mtime = None
        self.handled = None
        self.motion = None
        self.test = None
        self.world_kind = "editor"
        self.limits = mapping.drive_limits()
        self.world = None
        self.world_name = None
        self.actors = {}
        self.q = {s: None for s in SIDES}
        self.gap = {s: 0.0 for s in SIDES}
        self.expected = {}
        self.source = {s: None for s in SIDES}
        self.ik_error = {s: (0.0, 0.0) for s in SIDES}
        self.mode = "starting"
        self.error = None
        self.last_error_log = 0.0
        self.seq = 0
        self.accum = 0.0
        self.real = None
        self.real_mtime = None
        self._last = {}
        self.ticks = 0
        self.callback = unreal.register_slate_post_tick_callback(self._tick)

    def stop(self):
        if self.callback is not None:
            unreal.unregister_slate_post_tick_callback(self.callback)
            self.callback = None

    # ---- world and actors ---------------------------------------------------------------
    def _active_world(self):
        ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        game = ues.get_game_world()
        # get_editor_world() logs an error every call during Play/Simulate: only ask when idle.
        self.world_kind = "simulate" if game is not None else "editor"
        return game if game is not None else ues.get_editor_world()

    def _resolve(self, world):
        found = {}
        for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor):
            label = actor.get_actor_label()
            for side in SIDES:
                prefix = ARM_PREFIX + side + "_"
                if label.startswith(prefix) and label[len(prefix):] in VISUAL_LINKS:
                    key = (side, label[len(prefix):])
                    if key in found:
                        raise ValueError("duplicate Table 10 link " + label)
                    found[key] = actor
        missing = [s + "_" + l for s in SIDES for l in VISUAL_LINKS if (s, l) not in found]
        if missing:
            raise ValueError("missing Table 10 links: " + ", ".join(missing[:4]))
        self.actors = found
        self.world = world
        self.world_name = world.get_path_name()
        self.q = {s: None for s in SIDES}
        self.expected = {}

    def _poses(self, side):
        out = {}
        for link in VISUAL_LINKS:
            t = self.actors[(side, link)].get_actor_transform()
            p, r = t.translation, t.rotation
            out[link] = ([p.x, p.y, p.z], [r.x, r.y, r.z, r.w])
        return out

    def _apply(self, side, q, gap):
        poses = mapping.fk_ue(q, gap, MOUNT_YAW[side], TABLE10_BASES[side])
        for link in VISUAL_LINKS:
            p, r = poses[link]
            self.actors[(side, link)].set_actor_location_and_rotation(
                unreal.Vector(*p), unreal.Quat(*r).rotator(), False, True)
        self.q[side] = list(q)
        self.gap[side] = gap
        self.expected[side] = self._poses(side)

    # ---- bridge state -------------------------------------------------------------------
    def _read_one(self, path):
        try:
            value = json.loads(path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return self._last.get(path) if path.exists() else None  # mid-write: keep the previous sample
        self._last[path] = value
        return value

    def _read_real(self):
        """Merge the per-arm services' states (left/, right/) into one view with a mode per side.
        Falls back to a single real_state.json (one process for both arms)."""
        now = time.time()
        merged = {"arms": {}, "modes": {}, "unix": 0.0}
        sources = [self.dir / side / "real_state.json" for side in SIDES]
        if not any(p.exists() for p in sources):
            sources = [self.real_path]
        for path in sources:
            value = self._read_one(path)
            if not value or now - value.get("unix", 0) > REAL_FRESH_S:
                continue
            for side, arm in (value.get("arms") or {}).items():
                merged["arms"][side] = arm
                merged["modes"][side] = value.get("mode")
            merged["unix"] = max(merged["unix"], value.get("unix", 0))
        if not merged["modes"]:
            self.real = None
            return None
        modes = set(merged["modes"].values())
        # 'web': torque on, driven by the real-arm service (web tests, or holding without Unreal); Table 10 mirrors it.
        merged["mode"] = ("drive" if "drive" in modes else "hold" if "hold" in modes else
                          "web" if "web" in modes else "mirror")
        self.real = merged
        return merged

    # ---- test motions -------------------------------------------------------------------
    def _read_command(self):
        try:
            mtime = os.stat(self.command_path).st_mtime_ns
        except FileNotFoundError:
            return None
        if mtime == self.command_mtime:
            return None
        self.command_mtime = mtime
        try:
            command = json.loads(self.command_path.read_text())
        except (ValueError, OSError):
            self.command_mtime = None  # mid-write; read again next tick
            return None
        if command.get("id") == self.handled:
            return None
        self.handled = command.get("id")
        return command

    def _report(self, command, state, detail="", **extra):
        self.test = {"id": command.get("id"), "action": command.get("action"), "state": state,
                     "detail": detail, "unix": time.time(), **extra}

    def _start_test(self, command, real):
        action = command.get("action")
        if action == "stop":
            if self.motion is not None:
                self.motion = None
                self._report(command, "stopped", "held at the current pose")
            else:
                self._report(command, "stopped", "nothing was moving")
            return
        if time.time() - float(command.get("unix", 0)) > 15:
            return self._report(command, "rejected", "command too old")
        if real and real.get("mode") == "hold":
            return self._report(command, "rejected", "an arm is holding after a fault: press Release now (or use the E-stop) first")
        if real and "web" in (real.get("modes") or {}).values():
            return self._report(command, "rejected", "the real-arm service runs tests while the arms are on")
        preview = bool(real and real.get("mode") == "mirror")
        if any(self.q[s] is None for s in SIDES):
            return self._report(command, "rejected", "Table 10 arm pose not known yet")
        try:
            motion = tests.plan(action, self.q, self.gap, MOUNT_YAW, TABLE10_BASES, self.limits)
        except ValueError as error:
            return self._report(command, "rejected", str(error))
        motion["t"] = 0.0
        motion["last"] = time.monotonic()
        motion["lag_since"] = None
        motion["command"] = command
        motion["real"] = bool(real) and not preview
        motion["preview"] = preview
        motion["phase"] = "move"
        self.motion = motion
        self._report(command, "running", self._who(motion), duration_s=round(motion["duration_s"], 2), progress=0.0)

    def _who(self, motion):
        if motion["preview"]:
            return ("virtual preview: the real arms are off (torque off) and stay still; "
                    "press Turn arms ON to make them follow")
        return "real arms follow" if motion["real"] else "virtual only (no bridge)"

    def _step_test(self, real):
        motion = self.motion
        command = motion["command"]
        mode = (real or {}).get("mode")
        if motion["preview"] and mode == "drive":
            self.motion = None  # bridge left mirror mid-preview: stop; drive engages only on a match
            return self._report(command, "aborted", "bridge left mirror mode during the virtual preview")
        if not motion["preview"] and mode == "mirror":
            self.motion = None
            return self._report(command, "aborted", "bridge switched to mirror (torque off)")
        if motion["phase"] != "move":
            return self._preview_tail(motion, real)
        now = time.monotonic()
        motion["t"] += min(now - motion["last"], MAX_TEST_STEP_S)
        motion["last"] = now
        t = motion["t"]
        lag = self._lag_deg(real) if motion["real"] else None
        if lag is not None and lag > LAG_ABORT_DEG:
            motion["lag_since"] = motion["lag_since"] or now
            if now - motion["lag_since"] > LAG_ABORT_S:
                self.motion = None
                return self._report(command, "aborted", "real arm stopped following (%.0f deg behind; the bridge is "
                                                        "holding). Type m then d in the bridge terminal to resync" % lag,
                                    lag_deg=round(lag, 1))
        else:
            motion["lag_since"] = None
        for side, q in tests.sample(motion, t).items():
            self._apply(side, q, self.gap[side])
            self.source[side] = "test"
        done = t >= motion["duration_s"]
        self._report(command, "done" if done and not motion["preview"] else "running", self._who(motion),
                     duration_s=round(motion["duration_s"], 2), progress=round(min(1.0, t / motion["duration_s"]), 3),
                     lag_deg=None if lag is None else round(lag, 1))
        if done and motion["preview"]:
            motion["phase"], motion["hold_until"] = "hold", time.monotonic() + PREVIEW_HOLD_S
        elif done:
            self.motion = None

    def _preview_tail(self, motion, real):
        """After a virtual preview: hold the end pose, then ease back to the measured real pose."""
        command = motion["command"]
        now = time.monotonic()
        if motion["phase"] == "hold":
            if now < motion["hold_until"]:
                return
            measured = {s: ((real or {}).get("arms") or {}).get(s, {}).get("q") for s in SIDES}
            paths = {s: [list(self.q[s]), mapping.clamp(measured[s], mapping.URDF_LIMITS)]
                     for s in SIDES if measured[s] and self.q[s] is not None}
            travel = max([max(abs(a - b) for a, b in zip(*p)) for p in paths.values()] or [0.0])
            motion.update(phase="return", back={"paths": paths, "duration_s": max(1.0, math.pi / 2 * travel / RETURN_SPEED_RAD_S)},
                          t=0.0, last=now)
            return self._report(command, "running", "virtual preview finished; easing back to the real arm pose", progress=1.0)
        motion["t"] += min(now - motion["last"], MAX_TEST_STEP_S)
        motion["last"] = now
        for side, q in tests.sample(motion["back"], motion["t"]).items():
            self._apply(side, q, self.gap[side])
            self.source[side] = "test"
        if motion["t"] >= motion["back"]["duration_s"]:
            self.motion = None  # mirroring resumes from the real arms
            self._report(command, "done", "virtual preview done (real arms did not move); back in mirror", progress=1.0)

    def _lag_deg(self, real):
        """Worst joint gap between the measured real arms (drive) and the last published target."""
        if not real or real.get("mode") != "drive":
            return None
        worst = None
        for side in SIDES:
            if (real.get("modes") or {}).get(side, real.get("mode")) != "drive":
                continue
            measured = (real.get("arms") or {}).get(side, {}).get("q")
            if measured and self.q[side] is not None:
                gap = max(abs(math.degrees(a - b)) for a, b in zip(measured, self.q[side]))
                worst = gap if worst is None else max(worst, gap)
        return worst

    # ---- per side -----------------------------------------------------------------------
    def _mirror(self, side, arm):
        q = mapping.clamp(arm["q"], mapping.URDF_LIMITS)
        gap = min(mapping.JAW_MAX_M, max(0.0, arm.get("gap_m", self.gap[side])))
        if (self.q[side] is None or max(abs(a - b) for a, b in zip(q, self.q[side])) > 1e-4
                or abs(gap - self.gap[side]) > 1e-4 or side not in self.expected):
            self._apply(side, q, gap)
        self.source[side] = "real"

    def _follow_handle(self, side):
        yaw, base = MOUNT_YAW[side], TABLE10_BASES[side]
        if self.source.get(side) in ("real", "test"):
            # This arm is now driven from Unreal: drop the stale "real" label left by mirroring, or the VR wrist-handle
            # helper (which leaves arms labelled "real" alone) never drives it (2026-11-08).
            self.source[side] = "recovered"
        poses = self._poses(side)
        expected = self.expected.get(side)
        if expected is not None and not any(_moved(poses[l], expected[l]) for l in VISUAL_LINKS):
            return
        q, gap, residual = mapping.extract(poses, yaw, base, self.gap[side])
        if residual <= CONSISTENT_CM:
            # Posed through FK by someone else (or first sight): adopt it as is.
            self.q[side], self.gap[side], self.expected[side] = q, gap, poses
            self.source[side] = "recovered"
            return
        # A link was dragged: solve for the gripper's new pose and re-pose the whole arm.
        start = self.q[side] if self.q[side] is not None else q
        target_p, target_r = poses["link_007"]
        q, err_mm, err_deg = mapping.ik(start, self.gap[side], yaw, base, target_p, target_r,
                                        self.limits, iterations=IK_ITERATIONS)
        self._apply(side, q, self.gap[side])
        self.ik_error[side] = (err_mm, err_deg)
        self.source[side] = "handle"

    # ---- tick ---------------------------------------------------------------------------
    def _tick(self, delta):
        self.accum += delta
        if self.accum < 1.0 / TICK_HZ:
            return
        self.accum = 0.0
        try:
            world = self._active_world()
            if world is None:
                return
            if (self.world is None or not unreal.SystemLibrary.is_valid(self.world)
                    or world.get_path_name() != self.world_name
                    or any(not unreal.SystemLibrary.is_valid(a) for a in self.actors.values())):
                self._resolve(world)
            real = self._read_real()
            command = self._read_command()
            if command:
                self._start_test(command, real)
            if self.motion is not None:
                self.mode = "test"
                self._step_test(real)
            elif real:
                # Per side: an arm in mirror/hold poses Table 10; an arm in drive follows Table 10.
                self.mode = real.get("mode")
                for side in SIDES:
                    arm = real["arms"].get(side)
                    if real["modes"].get(side) in ("mirror", "hold", "web") and arm and arm.get("q"):
                        self._mirror(side, arm)
                    else:
                        self._follow_handle(side)
            else:
                self.mode = "handle (no bridge)"
                for side in SIDES:
                    self._follow_handle(side)
            self.seq += 1
            self.ticks += 1
            _write(self.ue_path, {
                "seq": self.seq, "unix": time.time(), "mode": self.mode,
                "world": self.world_kind, "test": self.test,
                "arms": {s: {"q": self.q[s], "gap_m": self.gap[s], "source": self.source[s],
                             "ik_error_mm": round(self.ik_error[s][0], 2),
                             "ik_error_deg": round(self.ik_error[s][1], 2)}
                         for s in SIDES if self.q[s] is not None}})
            self.error = None
        except Exception as e:
            self.error = repr(e)
            now = time.monotonic()
            if now - self.last_error_log > 5:
                self.last_error_log = now
                unreal.log_warning("maker_arm_real_link: " + traceback.format_exc())
            self.world = None

    def status(self):
        return {"running": self.callback is not None, "mode": self.mode, "error": self.error,
                "world": self.world_name, "ticks": self.ticks, "dir": str(self.dir),
                "q_deg": {s: [round(math.degrees(v), 2) for v in self.q[s]] if self.q[s] else None
                          for s in SIDES},
                "gap_mm": {s: round(1000 * self.gap[s], 1) for s in SIDES},
                "source": self.source, "ik_error": self.ik_error, "test": self.test}


def _stop_strays():
    """Stop every running Link from this module, including ones orphaned by an importlib.reload
    (a reload resets the module handle, and the old instance's tick callback would keep running)."""
    import gc
    for o in gc.get_objects():
        try:
            if type(o).__name__ == 'Link' and type(o).__module__ == __name__ and getattr(o, 'callback', None) is not None:
                o.stop()
        except Exception:
            pass


def start(directory=None):
    global _link
    stop()
    _stop_strays()
    _link = Link(directory)
    return _link.status()


def stop():
    global _link
    if _link is not None:
        _link.stop()
        _link = None


def status():
    return _link.status() if _link is not None else {"running": False}
