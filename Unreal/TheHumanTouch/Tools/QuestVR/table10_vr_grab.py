"""Table 10 VR grab helper (editor Python, local to this editor).

One grab point per arm. Each Table 10 arm has a ball handle on its wrist-roll motor,
MakerInteraction_T09__ArmHandle_<side>, sitting at the gripper's frame (link_007's origin):
- Selecting ANY part of a Table 10 arm (base, links, jaw fingers, gripper) selects that arm's
  handle, so there is exactly one thing to grab per arm.
- Moving or rotating the handle (Virtual Scouting gizmo) sets link_007 to the handle's pose;
  Tools/maker_arm_real_link.py sees the gripper moved and re-poses the whole arm with its IK
  (in drive mode the real arm follows under the bridge's guards). Rotation pivots at the wrist.
- When nobody is moving the handle, it follows the arm (IK result, Dream, web tests), so it
  always shows where the gripper really is (e.g. snaps back to the reachable pose).
- Selecting any other Table 10 actor (room, table, cameras, light, stickers) deselects it, so
  fixtures can't be dragged by accident. Handover props (the can) stay grabbable; the can's
  decoration parts select the can.

Handle/gripper sync uses set_actor_location_and_rotation without transactions (no Multi-User
history). The handles are hidden in scene captures, so Dream's cameras never see them.

    import sys; sys.path.insert(0, '<project>/Saved/XR')
    import table10_vr_grab as g; g.start(); g.status(); g.stop()
"""
import json
from pathlib import Path
import struct
import sys
import tempfile
import time

import unreal

# maker_arm_real_link / maker_arm_real_mapping live in the project's Tools folder, which isn't on
# sys.path right after an editor restart (the Table 10 agent adds it later).
_TOOLS = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()) + "Tools"
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

PREFIX = "MakerInteraction_T09__"
ARM = PREFIX + "MakerArm_"
HANDLE = PREFIX + "ArmHandle_"
SIDES = ("left", "right")
SELECT_PERIOD_S = 0.0     # selection redirect every frame (no window to drag another part)
SYNC_PERIOD_S = 1 / 30.   # handle <-> gripper sync rate
IDLE_S = 0.4              # handle untouched this long -> it follows the arm again
CHASE_S = 3.0             # keep moving the arm toward a released handle at most this long
IK_ITERATIONS = 10        # same as Tools/maker_arm_real_link.py
MAX_STEP_RAD = 0.0873     # 5 deg per joint per sync tick: the arm glides, never jumps
MOVED_CM = 0.05
MOVED_DOT = 0.99999       # |quat dot| below this counts as rotated (~0.5 deg)
# Table 10 actors people may grab and move in VR (handover props), despite the fixture rule.
GRABBABLE = ("HandoverCup", "HandoverCan", "Cup", "ArmHandle")
# Decoration actors attached to a handover prop: selecting one selects the prop itself.
PROP_PARTS = {PREFIX + "HandoverCan_": PREFIX + "HandoverCan"}

_state = {"handle": None, "sel_accum": 0.0, "sync_accum": 0.0, "accum": 0.0, "last_sync": 0.0, "last_select": 0.0, "error_at": None, "redirects": 0, "deselects": 0,
          "drags": 0, "last": None, "error": None, "ik_error": {s: None for s in SIDES}}
_actors = {}
_sync = {side: {"set": None, "user_at": 0.0, "target": None} for side in SIDES}


def _alive(actor):
    # A wrapper for a deleted actor can make IsValid raise TypeError instead of returning False.
    try:
        return actor is not None and unreal.SystemLibrary.is_valid(actor) and actor.get_world() is not None
    except Exception:
        return False


def _find(label):
    actor = _actors.get(label)
    if not _alive(actor):
        sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actor = next((a for a in sub.get_all_level_actors() if a.get_actor_label() == label), None)
        _actors[label] = actor
    return actor


def _gripper(side):
    return _find(f"{ARM}{side}_link_007")


def _handle(side):
    return _find(f"{HANDLE}{side}")


def _side(label):
    for prefix in (ARM, HANDLE):
        if label.startswith(prefix):
            side = label[len(prefix):].split("_", 1)[0]
            return side if side in SIDES else None
    return None


def _pose(actor):
    t = actor.get_actor_transform()
    return t.translation, t.rotation


def _differs(a, b):
    (pa, qa), (pb, qb) = a, b
    if (pa - pb).length() > MOVED_CM:
        return True
    dot = qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w
    return abs(dot) < MOVED_DOT


def _place(actor, pose):
    loc, quat = pose
    actor.set_actor_location_and_rotation(loc, quat.rotator(), False, True)


def _arm_actors(side):
    import maker_arm_real_link as rl
    return {link: _find(f"{rl.ARM_PREFIX}{side}_{link}") for link in rl.VISUAL_LINKS}


def _drive_arm(side, target):
    """Pose the WHOLE arm toward the handle pose in one go: damped-least-squares IK within the
    Maker Arm limits (URDF joint limits intersected with the real motors' SDK limits, minus a
    1 deg margin: maker_arm_real_mapping.drive_limits), joint steps capped per tick, then every
    link placed from forward kinematics with the base fixed at its mount. The arm is therefore
    always one rigid URDF chain; the link module sees a consistent FK pose and adopts it."""
    import maker_arm_real_link as rl
    import maker_arm_real_mapping as mapping
    actors = _arm_actors(side)
    if any(a is None for a in actors.values()):
        return None
    yaw, base = rl.MOUNT_YAW[side], rl.TABLE10_BASES[side]
    poses = {}
    for link, actor in actors.items():
        t = actor.get_actor_transform()
        poses[link] = ([t.translation.x, t.translation.y, t.translation.z],
                       [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w])
    q0, gap, _residual = mapping.extract(poses, yaw, base, 0.0)
    limits = mapping.drive_limits()
    (tp, tq) = target
    q, err_mm, err_deg = mapping.ik(q0, gap, yaw, base, [tp.x, tp.y, tp.z], [tq.x, tq.y, tq.z, tq.w],
                                   limits, iterations=IK_ITERATIONS)
    q = [a + max(-MAX_STEP_RAD, min(MAX_STEP_RAD, b - a)) for a, b in zip(q0, q)]
    q = mapping.clamp(q, limits)
    for link, (p, r) in mapping.fk_ue(q, gap, yaw, base).items():
        if link in actors:
            actors[link].set_actor_location_and_rotation(unreal.Vector(*p), unreal.Quat(*r).rotator(), False, True)
    _state["ik_error"][side] = (round(err_mm, 1), round(err_deg, 1))
    return q


_link_mode = {"mode": None, "read_at": 0.0}


def real_arm_mode(side):
    """This arm's real-arm service mode from Saved/RealArmLink/<side>/real_state.json while the service
    is writing it ('drive' = Unreal drives the real arm; 'web', 'mirror' or 'hold' = the real arm leads),
    else None."""
    link = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())) / "RealArmLink"
    try:
        d = json.loads((link / side / "real_state.json").read_text())
        if time.time() - float(d.get("unix", 0)) < 2.0:
            return d.get("mode")
    except Exception:
        pass
    return None


def _real_follows_measured(side, now):
    """True while Tools/maker_arm_real_link.py poses this arm from the measured real joints: then the
    real arm leads and the handle must not drive the virtual one (the link would overwrite it every
    tick). Keyed off the arm's own service mode (per the Table 10 session, 2026-11-08: the per-arm
    'source' label can lag); the link's overall mode only when that arm's service isn't running."""
    if now - _link_mode["read_at"] > 0.25:
        _link_mode["read_at"] = now
        _link_mode["sides"] = {s: real_arm_mode(s) for s in SIDES}
        try:
            d = json.loads((Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()))
                            / "RealArmLink" / "ue_state.json").read_text())
            _link_mode["mode"] = d.get("mode")
        except Exception:
            pass
    side_mode = (_link_mode.get("sides") or {}).get(side)
    if side_mode is not None:
        return side_mode != "drive"
    return (_link_mode.get("mode") or "") in ("mirror", "web")


def _sync_arm(side, now):
    handle, gripper = _handle(side), _gripper(side)
    if handle is None or gripper is None:
        return
    if _real_follows_measured(side, now):     # real arm leads: the handle just shows where it is
        _sync[side]["target"] = None
        if _differs(_pose(handle), _pose(gripper)):
            _place(handle, _pose(gripper))
            _sync[side]["set"] = _pose(gripper)
        return
    h, g, s = _pose(handle), _pose(gripper), _sync[side]
    if s["set"] is None:                       # first sight: put the handle on the wrist
        _place(handle, g)
        s["set"] = g
        return
    if _differs(h, s["set"]):                  # someone moved the handle
        s["set"], s["user_at"], s["target"] = h, now, h
        _state["drags"] += 1
    if s.get("target") is not None:            # chase the target with the whole arm (capped steps)
        _drive_arm(side, s["target"])
        if not _differs(_pose(gripper), s["target"]) or now - s["user_at"] > CHASE_S:
            s["target"] = None
        return
    if now - s["user_at"] > IDLE_S and _differs(h, g):   # idle: the handle follows the arm
        _place(handle, g)
        s["set"] = g


def _redirect():
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    selected = sub.get_selected_level_actors()
    if not selected:
        return
    keep, changed = [], False
    for actor in selected:
        label = actor.get_actor_label()
        if not label.startswith(PREFIX):
            keep.append(actor)
            continue
        part_of = next((prop for part, prop in PROP_PARTS.items() if label.startswith(part)), None)
        if part_of:  # a decoration piece: select the prop it belongs to
            prop = actor.get_attach_parent_actor()
            if prop is not None and prop.get_actor_label() == part_of:
                keep.append(prop)
                changed = True
                _state["redirects"] += 1
                _state["last"] = f"{label[len(PREFIX):]} -> {part_of[len(PREFIX):]}"
                continue
        if any(word in label for word in GRABBABLE):  # the can, or an arm handle
            keep.append(actor)
            continue
        changed = True
        side = _side(label) if label.startswith(ARM) else None
        target = (_handle(side) or _gripper(side)) if side else None
        if target is not None:
            keep.append(target)
            _state["redirects"] += 1
            _state["last"] = f"{label[len(PREFIX):]} -> {target.get_actor_label()[len(PREFIX):]}"
        else:
            _state["deselects"] += 1
            _state["last"] = f"deselected {label[len(PREFIX):]}"
    if changed:
        unique = list({a.get_path_name(): a for a in keep}.values())
        sub.set_selected_level_actors(unique)


_GEN = object()   # this load's token; callbacks left registered by earlier reloads do nothing


def _sync_side(*_args, **_kw):          # stubs: earlier reloads' callbacks still call these names
    return None


def _redirect_selection(*_args, **_kw):
    return None


def _tick_current(delta):
    now = time.monotonic()
    try:
        if now - _state["last_sync"] >= SYNC_PERIOD_S:     # wall-clock gate: never faster than 30 Hz
            _state["last_sync"] = now
            for side in SIDES:
                _sync_arm(side, now)
        if now - _state["last_select"] >= SELECT_PERIOD_S:
            _state["last_select"] = now
            _redirect()
    except Exception as error:  # keep ticking; report through status()
        _state["error"], _state["error_at"] = repr(error), time.time()


def _registry():
    # Survives importlib.reload (the unreal module isn't reloaded), so start() can always remove
    # the previous load's callback instead of stacking another one.
    if not hasattr(unreal, "_table10_vr_grab_handles"):
        unreal._table10_vr_grab_handles = []
    return unreal._table10_vr_grab_handles


_MODEL = "/Game/_Agents/Denys_Mac_Codex/MakerArm/Cans/FreeWave/SM_FreeWaveCan/StaticMeshes/"
# The can is one actor: its proxy MakerInteraction_T09__HandoverCan shows the whole Free Wave can as one
# mesh (pivot at the can's centre), so it moves as one piece in every editor
_MESHES = {PREFIX + "HandoverCan": _MODEL + "SM_FreeWaveCan",
           HANDLE + "left": "/Engine/BasicShapes/Sphere", HANDLE + "right": "/Engine/BasicShapes/Sphere"}
_HANDLE_MATERIAL = "/Game/_Agents/Denys_Mac_Codex/MakerArm/VR/MI_ArmHandle"
# Denys's can as one mesh (SourceAssets/FreeWaveCan/free_wave_can_whole.glb, made by combine_can_glb.py).
# If an editor ever comes up without the imported mesh (agents can't save), repair() imports it again
_CAN_SOURCES = (Path(__file__).resolve().parents[2] / "SourceAssets" / "FreeWaveCan" / "free_wave_can_whole.glb",
                Path(__file__).resolve().parents[5] / "Assets" / "FreeWaveCan" / "free_wave_can_whole.glb")
_CAN_SOURCE = next((p for p in _CAN_SOURCES if p.exists()), _CAN_SOURCES[0])


def _ensure_can_model():
    if unreal.EditorAssetLibrary.does_asset_exist(_MODEL + "SM_FreeWaveCan") or not _CAN_SOURCE.exists():
        return False
    # Imported under the name the mesh has in the project (a one-mesh glb takes its file's name)
    copy = Path(tempfile.mkdtemp()) / "SM_FreeWaveCan.glb"
    copy.write_bytes(_CAN_SOURCE.read_bytes())
    task = unreal.AssetImportTask()
    task.filename, task.destination_path = str(copy), _MODEL.split("/SM_FreeWaveCan/")[0]
    task.automated, task.replace_existing, task.save = True, True, False
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    mesh = unreal.load_asset(_MODEL + "SM_FreeWaveCan")
    if mesh is not None:
        # The VR laser is a Visibility trace: the can needs collision to be grabbed
        mesh.get_editor_property("body_setup").set_editor_property(
            "collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
    return True


def repair():
    """After an editor restart these actors can come back with no mesh (an invisible can): Multi-User
    replays them before the agent-folder meshes load, or, with no session, the meshes were never
    saved. Import the can model again if needed, then put the meshes (and handle material) back."""
    try:
        _state["can_model_imported"] = _ensure_can_model()
    except Exception as error:
        _state["can_model_imported"] = f"failed: {error!r}"
    fixed = []
    for label, path in _MESHES.items():
        actor = _find(label)
        if actor is None:
            continue
        comp = actor.static_mesh_component
        missing_mesh = comp.static_mesh is None
        missing_mat = label.startswith(HANDLE) and (not comp.get_materials() or comp.get_materials()[0] is None)
        if missing_mesh or missing_mat:
            with unreal.ScopedEditorTransaction("Repair Table 10 VR props after restart"):
                comp.modify()
                if missing_mesh:
                    comp.set_static_mesh(unreal.load_asset(path))
                if missing_mat:
                    comp.set_material(0, unreal.load_asset(_HANDLE_MATERIAL))
            fixed.append(label[len(PREFIX):])
    _state["repaired"] = fixed
    return fixed


def start():
    stop()
    try:
        repair()
    except Exception as error:
        _state["error"] = f"repair: {error!r}"
    _actors.clear()
    for side in SIDES:
        _sync[side].update(set=None, user_at=0.0, target=None)
    _state.update(last_sync=0.0, last_select=0.0, error_at=None)
    handle = unreal.register_slate_post_tick_callback(_tick_current)
    _registry().append(handle)
    _state["handle"] = handle
    return status()


def stop():
    for handle in list(_registry()):
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
        _registry().remove(handle)
    _state["handle"] = None
    return status()


def status():
    return {"running": _state["handle"] is not None, "redirects": _state["redirects"],
            "deselects": _state["deselects"], "handle_drags": _state["drags"],
            "last": _state["last"], "error": _state["error"], "error_at": _state["error_at"],
            "repaired_on_start": _state.get("repaired"), "ik_error_mm_deg": _state["ik_error"],
            "grippers_found": {s: _gripper(s) is not None for s in SIDES},
            "handles_found": {s: _handle(s) is not None for s in SIDES}}
