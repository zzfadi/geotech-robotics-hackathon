"""Annotating: people draw on their editor to point the agents at things, then say what they
mean. The CoPresence Annotate plugin (Plugins/CoPresenceAnnotate) does the drawing: its
shortcut (Ctrl+Shift+Space on a Mac, Ctrl+Alt+Space on Windows) or the pencil on the
toolbar tints the editor, each stroke is worked out as it ends, and a box next to the
drawing takes the note.

This sends each note with its drawing to the session chat, to whoever Agent Chat
addresses or the agent the note names with @, and keeps the box's hint saying who that is.
A message sent any other way while drawing, such as by voice, takes the drawing along
(hub_client.post_message).
"""

import json

import unreal

from copresence import agents_panel, hub_client

REFRESH_SECONDS = 0.2

_state = {"callback": None, "elapsed": 0.0, "hint": None, "last_warning": ""}


def _hint(target: str) -> str:
    return f"Tell {target} what you mean" if target else "Tell the agents what you mean, or @ one of them"


def _tick(delta_seconds: float) -> None:
    _state["elapsed"] += delta_seconds
    if _state["elapsed"] < REFRESH_SECONDS:
        return
    _state["elapsed"] = 0.0

    # Only editors where the plugin is built and switched on can draw
    library = getattr(unreal, "CoPresenceAnnotationLibrary", None)
    if library is None:
        return
    try:
        target = agents_panel.target()
        hint = _hint(target)
        if hint != _state["hint"]:
            library.set_note_hint(unreal.Text(hint))
            _state["hint"] = hint

        notes = library.take_notes()
        if not notes:
            return
        agents, _, seconds_since_poll, _ = hub_client.snapshot()
        names = agents_panel.agent_names(agents, seconds_since_poll)
        for note in notes:
            to = agents_panel.mentioned_agent(note.text, names) or target
            hub_client.post_message(note.text, to, agents_panel.selection(), json.loads(note.annotation_json))
    except Exception as error:
        if str(error) != _state["last_warning"]:
            _state["last_warning"] = str(error)
            unreal.log_warning(f"CoPresence annotation: {error}")


def start() -> None:
    if _state["callback"] is None:
        _state["callback"] = unreal.register_slate_post_tick_callback(_tick)
