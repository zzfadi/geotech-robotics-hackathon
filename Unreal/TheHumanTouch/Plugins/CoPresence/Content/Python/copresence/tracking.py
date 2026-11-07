"""Task context and change tracking for the agent's edits.

Every agent edit runs as a named editor transaction so it is undoable, attributed to the
agent in the Multi-User session history, and replicated live to the other users.
"""

import re
import traceback

import unreal

# The task the agent is currently working on; edits are tagged and foldered under it
current_task = {"id": None, "title": None, "transactions": 0}


def agent_name() -> str:
    # The name this editor has in the Multi-User session, set by -CONCERTDISPLAYNAME
    try:
        name = str(unreal.MultiUserClientStatics.get_local_multi_user_client_info().display_name)
    except Exception:
        name = ""
    if name:
        return name
    match = re.search(r'-CONCERTDISPLAYNAME=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line(), re.IGNORECASE)
    return (match.group(1) or match.group(2)) if match else "Agent"


def transaction_title(description: str) -> str:
    if current_task["id"]:
        return f"{agent_name()} | {current_task['id']} | {description}"
    return f"{agent_name()} | {description}"


def task_folder() -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", current_task["title"] or "task").strip("-")[:40]
    return f"_Agents/{agent_name()}/{current_task['id']}-{slug}"


def task_tag() -> str:
    return f"{agent_name()}:{current_task['id']}"


# Helpers an editor keeps to itself, like the call camera (Plugins/CoPresenceCall), carry
# this tag; they never reach the session and aren't anyone's work
LOCAL_ONLY_TAG = "CoPresenceLocal"


def is_local_only(actor: unreal.Actor) -> bool:
    if any(str(tag) == LOCAL_ONLY_TAG for tag in actor.tags):
        return True
    # Multi-User avatars are transient actors in the level and move constantly
    presence_class = getattr(unreal, "ConcertClientPresenceActor", None)
    if presence_class is not None:
        return isinstance(actor, presence_class)
    return "Presence" in actor.get_class().get_name()


def actor_state(actor: unreal.Actor) -> dict:
    transform = actor.get_actor_transform()
    location = transform.translation
    rotation = transform.rotation.rotator()
    scale = transform.scale3d
    return {
        "label": actor.get_actor_label(),
        "class": actor.get_class().get_name(),
        "location": [round(location.x, 2), round(location.y, 2), round(location.z, 2)],
        "rotation": [round(rotation.pitch, 2), round(rotation.yaw, 2), round(rotation.roll, 2)],
        "scale": [round(scale.x, 3), round(scale.y, 3), round(scale.z, 3)],
        "folder": str(actor.get_folder_path()),
        "tags": [str(tag) for tag in actor.tags],
    }


def snapshot() -> dict:
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    return {actor.get_path_name(): (actor, actor_state(actor)) for actor in actors if not is_local_only(actor)}


def diff(before: dict, after: dict) -> dict:
    created = [{"path": path, "after": state} for path, (_, state) in after.items() if path not in before]
    deleted = [{"path": path, "before": state} for path, (_, state) in before.items() if path not in after]
    modified = [
        {"path": path, "before": before[path][1], "after": state}
        for path, (_, state) in after.items()
        if path in before and before[path][1] != state
    ]
    return {"created": created, "modified": modified, "deleted": deleted}


def mark_created_actors(after: dict, changes: dict) -> None:
    # Runs inside the edit's transaction so the tag and folder replicate with the edit
    if not current_task["id"]:
        return
    for entry in changes["created"]:
        actor = after[entry["path"]][0]
        actor.modify()
        actor.tags = list(actor.tags) + [unreal.Name(task_tag())]
        actor.set_folder_path(task_folder())
        entry["after"] = actor_state(actor)


def undo_count() -> int:
    return unreal.ToolsetLibrary.get_active_undo_count()


# Packages a level keeps its actors in; they reach everyone with the level's edits
LEVEL_ACTOR_FOLDERS = ("/__ExternalActors__/", "/__ExternalObjects__/")


def dirty_content() -> set[str]:
    """Content packages with unsaved changes in this editor, levels and their actors left out."""
    names = {package.get_name() for package in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()}
    return {name for name in names if not any(folder in name for folder in LEVEL_ACTOR_FOLDERS)}


# How many packages an agent's refused save names
REFUSED_SAVES_LISTED = 3


def take_refused_saves() -> list[str]:
    """Packages this editor refused to save since the last call. The CoPresence Fab plugin
    makes agents' editors refuse to save assets and levels; editors without a build of it
    save as usual."""
    library = getattr(unreal, "CoPresenceFabLibrary", None)
    if library is None or not hasattr(library, "take_refused_saves"):
        return []
    return [str(package) for package in library.take_refused_saves()]


def save_refusal(packages: list[str]) -> str:
    listed = ", ".join(packages[:REFUSED_SAVES_LISTED])
    if len(packages) > REFUSED_SAVES_LISTED:
        listed += f" and {len(packages) - REFUSED_SAVES_LISTED} more"
    return (f"Refused to save {listed}: agents don't save from code. Run the edit again without "
            "saving. Level changes reach everyone without a save; end_task shares the assets in your "
            "content folder, and share_assets shares them sooner, or shares others you changed.")


def run_tracked(description: str, edit) -> dict:
    """Runs edit() inside one named transaction and reports which actors changed.
    A failed edit is undone, so the agent's level never diverges from other users'.
    So is an edit that tried to save, which agents' editors refuse."""
    from copresence import animation
    if animation.is_running():
        # A second transaction would silently merge into the animation's open one
        return {"value": None, "error": "An animation is still running; wait for it to finish.",
                "changes": {"created": [], "modified": [], "deleted": []}, "undo_count": undo_count()}

    # Saves refused before this edit, such as through a read-only tool call, aren't its doing
    take_refused_saves()
    before = snapshot()
    dirty_before = dirty_content()
    undo_count_before = undo_count()
    error_text = None
    value = None
    with unreal.ScopedEditorTransaction(transaction_title(description)):
        try:
            value = edit()
            after = snapshot()
            changes = diff(before, after)
            mark_created_actors(after, changes)
        except Exception:
            error_text = traceback.format_exc()

    refused = take_refused_saves()
    if refused:
        # Last, since the agent sees the end of a long error
        error_text = "\n".join(filter(None, [error_text, save_refusal(refused)]))

    if error_text is not None:
        # Cancelling a transaction keeps its changes applied locally without replicating
        # them, so let it finish and undo it instead; Multi-User replicates both steps
        if undo_count() > undo_count_before:
            unreal.ToolsetLibrary.undo_transaction(False)
        return {"value": value, "error": error_text, "changes": {"created": [], "modified": [], "deleted": []},
                "undo_count": undo_count(), "dirtied": []}

    # Count what actually landed in the undo buffer, since revert undoes exactly that many
    undo_count_after = undo_count()
    current_task["transactions"] += undo_count_after - undo_count_before
    # Assets the edit made or changed; only a save sends most of them to the session
    dirtied = sorted(dirty_content() - dirty_before)
    return {"value": value, "error": error_text, "changes": changes, "undo_count": undo_count_after,
            "dirtied": dirtied}


def find_actor(reference: str) -> unreal.Actor:
    """Resolves an actor by object path or by its unique Outliner label."""
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    if reference.startswith("/"):
        matches = [actor for actor in actors if actor.get_path_name() == reference]
    else:
        matches = [actor for actor in actors if actor.get_actor_label() == reference]
    if len(matches) != 1:
        raise ValueError(f"Expected one actor for '{reference}', found {len(matches)}")
    return matches[0]


def camera_info():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    info = subsystem.get_level_viewport_camera_info()
    # Python returns (location, rotation), sometimes prefixed with the bool result
    if isinstance(info, tuple) and len(info) == 3:
        info = info[1:]
    return info
