"""Media Plates showing agents' videos play in every editor in the session.

A Media Plate plays its video in the editor that sets it up, but Multi-User only carries the
plate and the media source it shows, not whether it plays, so the other editors would show
it still. This opens, in each editor, the plates that show a media source from an agent's
content folder (copresence.sharing) and are set to play by themselves, once for each source
they get. A plate someone stops in their own editor stays stopped until it gets another
source, and plates of the project's own are left as they are. VR plays them by itself, since a
game starts its plates when they appear.
"""

import unreal

from copresence import sharing

CHECK_SECONDS = 2.0

_state = {"callback": None, "elapsed": 0.0, "opened": {}, "last_warning": ""}


def _plates() -> list:
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.MediaPlate)) if world else []


def _check() -> None:
    opened = _state["opened"]
    present = set()
    for plate in _plates():
        path = plate.get_path_name()
        present.add(path)
        component = plate.media_plate_component
        source = component.get_selected_media_source()
        if source is None or not component.get_editor_property("auto_play"):
            continue
        source_path = source.get_path_name()
        if not sharing.in_folder(source_path, sharing.AGENTS_ROOT) or opened.get(path) == source_path:
            continue
        opened[path] = source_path
        player = component.get_media_player()
        if player is None or player.get_url() != source.get_url():
            component.open()
    for path in set(opened) - present:
        del opened[path]


def _tick(delta_seconds: float) -> None:
    _state["elapsed"] += delta_seconds
    if _state["elapsed"] < CHECK_SECONDS:
        return
    _state["elapsed"] = 0.0
    try:
        _check()
    except Exception as error:
        if str(error) != _state["last_warning"]:
            _state["last_warning"] = str(error)
            unreal.log_warning(f"CoPresence media plates: {error}")


def start() -> None:
    if _state["callback"] is None and hasattr(unreal, "MediaPlate"):
        _state["callback"] = unreal.register_slate_post_tick_callback(_tick)
