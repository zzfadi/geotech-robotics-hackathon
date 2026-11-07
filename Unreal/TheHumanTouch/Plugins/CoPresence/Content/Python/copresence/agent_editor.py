import unreal

# Frames to wait after startup so the level viewport exists before changing it
STARTUP_DELAY_TICKS = 120


def is_agent_editor() -> bool:
    return "-COPRESENCEAGENT" in unreal.SystemLibrary.get_command_line().upper()


def is_commandlet() -> bool:
    return "-RUN=" in unreal.SystemLibrary.get_command_line().upper()


def configure_agent_editor() -> None:
    """The agent's viewport only needs to render when the agent captures it, so it
    stops redrawing every frame. Presence still follows the camera, and captures render
    on demand. This is a per-viewport setting, so unlike a console variable it is not
    synced to the other users in the Multi-User session."""
    state = {"ticks": 0, "callback": None}

    def apply(delta_seconds: float) -> None:
        state["ticks"] += 1
        if state["ticks"] < STARTUP_DELAY_TICKS:
            return
        unreal.unregister_slate_post_tick_callback(state["callback"])
        from copresence import roomrunner_sim
        live = roomrunner_sim._instance is not None
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(live)

    state["callback"] = unreal.register_slate_post_tick_callback(apply)
