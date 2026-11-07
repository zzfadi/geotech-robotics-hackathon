"""The MetaHuman avatar's mouth, moved live by the agent's voice in a video call.

The avatar's idle animation (meet_idle) keeps one minute-long sequence open on the avatar
and moves its playhead with the clock, which plays the head's motion and the blinks. Its
mouth controls have no keys of their own. So the call bot (Tools/copresence_call_voice.py)
sends mouth shapes as the voice's audio streams in, each with the wall-clock time it should
show, and they are written as keys into that sequence just ahead of the playhead. The head
and eyes carry on while the agent talks, and nothing waits for a whole answer first.

Shapes come from copresence_voice_core.MouthShapes: (jaw, funnel, lips_together,
corner_pull), each 0 to 1, for the four mouth controls of the MetaHuman face rig. Keys a
moment behind the playhead are removed, so when the minute loops round the mouth is shut.
In a level without the avatar nothing happens, and the call bot stops sending.
"""

import time

import unreal

from copresence import meet_idle as idle
from copresence import meet_speech

FPS = 60
# The idle sequence loops every minute
LOOP_FRAMES = 3600
MOUTH_CHANNELS = meet_speech.NAMES
# Keys this many frames behind the playhead are removed
KEEP_BEHIND_FRAMES = 30
CLEANUP_SECONDS = 0.25

_state = {"sequence": None, "channels": None, "keys": {}, "handle": None, "cleaned": 0.0, "error": "",
          "frames": 0}


def _avatar_here() -> bool:
    try:
        meet_speech.live_avatar()
        return True
    except Exception:
        return False


def _ready() -> bool:
    """Whether the avatar's idle animation runs, starting it when the avatar is in the level."""
    if idle.handle is not None and idle.status.get("state") not in ("error", "stopped", "uninitialized"):
        return True
    if not _avatar_here():
        return False
    if idle.handle is None:
        idle.initialize()
    return idle.handle is not None


def _channels() -> dict:
    sequence = idle.sequence
    # Idle rebuilds its tracks each time it starts (after a scene switch, say) in the same
    # sequence asset, so the cache goes by idle's run, not only by the asset
    run = (sequence, idle.handle)
    if _state["sequence"] != run or not _state["channels"]:
        faces = [binding for binding in sequence.get_bindings() if binding.get_name() == "Face" and binding.get_tracks()]
        if len(faces) != 1:
            raise RuntimeError("The idle sequence has no single Face rig binding")
        section = faces[0].get_tracks()[0].get_sections()[0]
        channels = {str(channel.channel_name): channel for channel in section.get_all_channels()}
        missing = [name for name in MOUTH_CHANNELS if name not in channels]
        if missing:
            raise RuntimeError(f"The face rig has no mouth channels {missing}")
        _state.update(sequence=run, channels={name: channels[name] for name in MOUTH_CHANNELS}, keys={})
    return _state["channels"]


def _frame_at(epoch: float) -> int:
    """The idle sequence's frame at a wall-clock time. Idle counts from a monotonic start;
    the call bot runs on this machine and sends wall-clock times."""
    monotonic = time.monotonic() + (epoch - time.time())
    return int(((monotonic - idle.started) % 60.0) * FPS)


def _value(name: str, shape) -> float:
    jaw, funnel, closed, wide = shape
    if name == "CTRL_C_jaw.Y":
        return jaw
    if "funnel" in name:
        return funnel
    if "Together" in name:
        return closed
    return wide


def _put(frame: int, shape) -> None:
    """Keys one frame of mouth on every mouth channel, replacing what was there."""
    channels = _channels()
    _remove(frame)
    keys = []
    for name, channel in channels.items():
        keys.append((channel, channel.add_key(unreal.FrameNumber(frame), float(_value(name, shape)),
                                              interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)))
    _state["keys"][frame] = keys


def _remove(frame: int) -> None:
    for channel, key in _state["keys"].pop(frame, []):
        try:
            channel.remove_key(key)
        except Exception:
            pass


def _behind(frame: int, playhead: int) -> int:
    """How many frames a frame is behind the playhead, around the loop (0 to LOOP_FRAMES)."""
    return (playhead - frame) % LOOP_FRAMES


def _tick(_delta: float) -> None:
    now = time.monotonic()
    if now - _state["cleaned"] < CLEANUP_SECONDS or not _state["keys"]:
        return
    _state["cleaned"] = now
    try:
        playhead = _frame_at(time.time())
        for frame in [frame for frame in _state["keys"] if KEEP_BEHIND_FRAMES < _behind(frame, playhead) < LOOP_FRAMES // 2]:
            _remove(frame)
    except Exception as error:
        _state["error"] = repr(error)


def _ensure_tick() -> None:
    if _state["handle"] is None:
        _state["handle"] = unreal.register_slate_post_tick_callback(_tick)
        # An editor quitting with the avatar's sequence still open crashes in Multi-User's
        # Sequencer shutdown (UE 5.8), so it is let go on the way out too
        try:
            unreal.register_python_shutdown_callback(release)
        except Exception:
            pass


def release() -> dict:
    """Shuts the mouth and lets go of the avatar's idle sequence, before the editor quits."""
    try:
        if _state["handle"] is not None:
            unreal.unregister_slate_post_tick_callback(_state["handle"])
            _state["handle"] = None
        for frame in list(_state["keys"]):
            _remove(frame)
        if idle.handle is not None:
            idle.cleanup()
        editor = unreal.LevelSequenceEditorBlueprintLibrary
        if editor.get_current_level_sequence() is not None:
            editor.close_level_sequence()
        return {"released": True}
    except Exception as error:
        return {"released": False, "error": repr(error)}


def status() -> dict:
    """Whether there is an avatar to move here, and how the idle animation is doing."""
    available = _avatar_here()
    return {"available": available, "idle": idle.status.get("state"), "frames": _state["frames"],
            "keys": len(_state["keys"]), "error": _state["error"] or idle.status.get("error")}


def frames(start_epoch: float, shapes: list) -> dict:
    """Moves the mouth through `shapes`, 60 a second, the first one at `start_epoch` (wall
    clock), then shuts it. Returns whether there was an avatar to move."""
    if not _ready():
        return {"moved": False, "reason": "no MetaHuman avatar in this level"}
    first = _frame_at(float(start_epoch))
    playhead = _frame_at(time.time())
    written = 0
    for index, shape in enumerate(shapes):
        frame = (first + index) % LOOP_FRAMES
        # Too late for this one: the playhead has passed it
        if 0 < _behind(frame, playhead) < LOOP_FRAMES // 2:
            continue
        _put(frame, [min(1.0, max(0.0, float(value))) for value in shape])
        written += 1
    # A shut mouth just after the last shape, so it never hangs open
    _put((first + len(shapes)) % LOOP_FRAMES, (0.0, 0.0, 0.0, 0.0))
    _state["frames"] += written
    _ensure_tick()
    return {"moved": True, "first_frame": first, "written": written, "late": len(shapes) - written}


def stop() -> dict:
    """Shuts the mouth now: keys ahead of the playhead go, and a shut mouth comes next."""
    if _state["sequence"] != (idle.sequence, idle.handle):
        return {"stopped": False}
    playhead = _frame_at(time.time())
    for frame in [frame for frame in _state["keys"] if _behind(frame, playhead) >= LOOP_FRAMES // 2]:
        _remove(frame)
    _put((playhead + 2) % LOOP_FRAMES, (0.0, 0.0, 0.0, 0.0))
    return {"stopped": True}
