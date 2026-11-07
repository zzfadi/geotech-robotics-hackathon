"""Animated edits: the agent's moves, placements and removals play out smoothly for
everyone in the session instead of jumping.

An animation is one editor transaction kept open across frames. Every frame the actors
move a little and get snapshotted, which Multi-User streams to the other users the same
way it streams a human dragging a gizmo. The transaction closes when the animation ends,
so the whole motion is one undo step and one history entry, and the actors stay locked
for everyone else while they are in motion.
"""

import traceback

import unreal

from copresence import tracking


def _smooth(alpha: float) -> float:
    return alpha * alpha * (3.0 - 2.0 * alpha)


def _pop(alpha: float) -> float:
    # Ease-out with a small overshoot, so things grow slightly past full size and settle
    overshoot = 1.70158
    shifted = alpha - 1.0
    return 1.0 + (overshoot + 1.0) * shifted ** 3 + overshoot * shifted ** 2


def _ease_in(alpha: float) -> float:
    return alpha * alpha * alpha


EASINGS = {"smooth": _smooth, "pop": _pop, "ease_in": _ease_in}

_active = {"callback": None}
_last_result = {"value": None}


def is_running() -> bool:
    return _active["callback"] is not None


def last_result() -> dict | None:
    return _last_result["value"]


def start(description: str, moves: list, duration_seconds: float, easing: str = "smooth", on_finished=None) -> dict:
    """Animates actors to new transforms in one transaction.

    moves holds (actor, location, rotation, scale) tuples; None keeps that part as is.
    on_finished(result) runs after a successful animation and may return a follow-up
    result, e.g. the deletion that ends a shrink-out.
    """
    if is_running():
        raise RuntimeError("Another animation is still running")
    ease = EASINGS[easing]

    tracks = []
    for actor, location, rotation, scale in moves:
        current = actor.get_actor_transform()
        tracks.append({
            "actor": actor,
            "from_location": current.translation,
            "to_location": location if location is not None else current.translation,
            "from_rotation": current.rotation.rotator(),
            "to_rotation": rotation if rotation is not None else current.rotation.rotator(),
            "from_scale": current.scale3d,
            "to_scale": scale if scale is not None else current.scale3d,
        })

    before = tracking.snapshot()
    undo_count_before = tracking.undo_count()
    _last_result["value"] = None
    unreal.SystemLibrary.begin_transaction(
        "CoPresence", unreal.Text(tracking.transaction_title(description)), tracks[0]["actor"])
    try:
        for track in tracks:
            unreal.SystemLibrary.transact_object(track["actor"])
            unreal.SystemLibrary.transact_object(track["actor"].root_component)
    except Exception:
        unreal.SystemLibrary.end_transaction()
        raise

    elapsed = {"seconds": 0.0}

    def apply(alpha: float) -> None:
        eased = ease(alpha)
        for track in tracks:
            actor = track["actor"]
            location = unreal.MathLibrary.v_lerp(track["from_location"], track["to_location"], eased)
            rotation = unreal.MathLibrary.r_lerp(track["from_rotation"], track["to_rotation"], eased, True)
            scale = unreal.MathLibrary.v_lerp(track["from_scale"], track["to_scale"], eased)
            actor.set_actor_location_and_rotation(location, rotation, False, False)
            actor.set_actor_scale3d(scale)
            unreal.SystemLibrary.snapshot_object(actor.root_component)

    def finish(error_text: str | None) -> None:
        unreal.unregister_slate_post_tick_callback(_active["callback"])
        _active["callback"] = None
        unreal.SystemLibrary.end_transaction()

        # Multi-User can reload the map mid-session, which briefly leaves no editor world.
        # The reload replaces these actors, so there is nothing to compare, undo or follow up
        if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world() is None:
            _last_result["value"] = {"done": True, "error": "The level was reloaded before the animation finished",
                                     "changes": {"created": [], "modified": [], "deleted": []},
                                     "undo_count": tracking.undo_count()}
            return

        if error_text is not None:
            # Same rule as run_tracked: never leave unreplicated changes behind
            if tracking.undo_count() > undo_count_before:
                unreal.ToolsetLibrary.undo_transaction(False)
            changes = {"created": [], "modified": [], "deleted": []}
        else:
            changes = tracking.diff(before, tracking.snapshot())
            tracking.current_task["transactions"] += tracking.undo_count() - undo_count_before

        result = {"done": True, "error": error_text, "changes": changes, "undo_count": tracking.undo_count()}
        if error_text is None and on_finished is not None:
            result["followup"] = on_finished(result)
        _last_result["value"] = result

    def step(delta_seconds: float) -> None:
        elapsed["seconds"] += delta_seconds
        alpha = 1.0 if duration_seconds <= 0 else min(elapsed["seconds"] / duration_seconds, 1.0)
        try:
            apply(alpha)
        except Exception:
            finish(traceback.format_exc())
            return
        if alpha >= 1.0:
            finish(None)

    _active["callback"] = unreal.register_slate_post_tick_callback(step)
    return {"duration_seconds": duration_seconds, "actors": [track["actor"].get_actor_label() for track in tracks]}
