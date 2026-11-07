"""Host-owned persistence boundary; no scene edits, undo reset, or history deletion."""
import time
import unreal
from copresence import animation, tracking, ui_widgets

status = {"state": "idle"}


def inspect():
    info = unreal.MultiUserClientStatics.get_multi_user_session_info()
    return {
        "agent": tracking.agent_name(),
        "session": str(info.session_name),
        "task": tracking.current_task.get("id"),
        "animation": animation.is_running(),
        "maps": [p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()],
        "content": [p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()],
        "pending": [str(p) for p in unreal.MultiUserClientStatics.gather_session_changes(True)],
        "operation": dict(status),
    }


def request():
    before = inspect()
    if before["agent"] != "Denys Luna2 Claude" or before["session"] != "TheHumanTouch":
        raise RuntimeError("Only the TheHumanTouch Luna2 host may checkpoint")
    if before["task"] or before["animation"] or before["content"]:
        raise RuntimeError("Finish active edits and share dirty content before checkpointing")
    if status["state"] == "saving":
        raise RuntimeError("Checkpoint already running")
    status.clear()
    status.update(state="saving", started=time.time())
    # Leave the scoped tool transaction before invoking the native persistence path.
    ui_widgets.after(1.0, _save)
    return dict(status)


def _save():
    try:
        before = inspect()
        if before["task"] or before["animation"] or before["content"]:
            raise RuntimeError("Editing resumed; checkpoint deferred")
        unreal.CoPresenceFabLibrary.persist_session_changes(True)
        # A normal map save is acknowledged by the Concert server asynchronously.
        # Keep all guards while waiting for that package event to reach this client.
        ui_widgets.after(1.0, _verify_save)
    except Exception as error:
        status.update(state="error", error=str(error))


def _verify_save():
    try:
        after = inspect()
        if after["task"] or after["animation"] or after["maps"] or after["content"]:
            raise RuntimeError("Editing resumed or dirty packages remain; no Git checkpoint allowed")
        if after["pending"]:
            if time.time() - status["started"] >= 15:
                raise RuntimeError("Persistence incomplete after server acknowledgement wait; no Git checkpoint allowed")
            # Re-persist the acknowledged revision without publishing another map save.
            # The real server event can replace the initial local dummy revision.
            attempts = status.get("persist_retries", 0)
            if attempts < 3:
                unreal.CoPresenceFabLibrary.persist_session_changes(False)
                status["persist_retries"] = attempts + 1
            ui_widgets.after(1.0, _verify_save)
            return
        status.update(state="saved", finished=time.time())
    except Exception as error:
        status.update(state="error", error=str(error))
