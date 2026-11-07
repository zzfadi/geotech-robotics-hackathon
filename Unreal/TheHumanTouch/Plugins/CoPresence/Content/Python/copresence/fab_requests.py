"""Fab requests from agents, handled in people's editors. Agent Chat shows an Open in Fab
button on agent messages that link a Fab listing. It opens the listing in this editor's Fab
tab, where the person adds it with Add to Project as usual. Once the Fab plugin has
imported it, this editor saves the new assets once, so Multi-User shares them with the
session, unless they are too big for it, and tells the agent in chat where they are.

Multi-User also sends its own copy of every asset the moment it is created, twice for
each asset of a Fab import, and the session keeps all of them. While an import is
awaited, the CoPresence Fab plugin holds those copies back, so only the save reaches the
session.

Agents' editors never open the Fab tab: it signs in with the person's saved Epic login.
"""

import re
import sys
import tempfile
import time
from pathlib import Path

import unreal

from copresence import agent_editor, hub_client

FAB_BRIDGE_CLASS = "/Script/Fab.FabBrowserApi"
CHECK_SECONDS = 2.0
# Requests nobody acts on are dropped after this long
PENDING_SECONDS = 2 * 60 * 60
# Bigger downloads stay out of the session and go through git, since everyone who joins
# downloads the whole session
SESSION_LIMIT_BYTES = 40 * 1024 * 1024
# Checks in a row without new assets before an import counts as finished, for when the
# Fab plugin's own record of it hasn't reached its config file yet. Imports pause
# between steps, so this leaves them a while.
SETTLED_CHECKS = 5
FAB_FOLDER = "/Game/Fab"
MESH_CLASSES = ("StaticMesh", "SkeletalMesh", "Blueprint")
LISTED_MESHES = 8
IMPORT_RECORD = re.compile(r'\("([^"]+)",\s*"([^"]+)"\)')
LISTING_LINK = re.compile(r"(?:https?://)?(?:www\.)?fab\.com/(?:plugins/ue5/)?listings/([0-9a-fA-F-]{36})\S*")

_state = {"bridge": None, "callback": None, "elapsed": 0.0, "pending": {}}


def _bridge():
    """The Fab plugin's bridge to its web page, which can open its tab on a page."""
    if _state["bridge"] is None:
        bridge_class = unreal.find_object(None, FAB_BRIDGE_CLASS)
        if bridge_class is None:
            raise RuntimeError("The Fab plugin isn't enabled in this project")
        _state["bridge"] = unreal.new_object(bridge_class)
    return _state["bridge"]


def _config_value(section: str, key: str) -> str:
    """A setting the Fab plugin saved for this project, read from the file, since its
    settings aren't exposed to Python."""
    platform = {"darwin": "MacEditor", "win32": "WindowsEditor"}.get(sys.platform, "LinuxEditor")
    config_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.generated_config_dir())
    path = Path(config_dir) / platform / "EditorPerProjectUserSettings.ini"
    if not path.exists():
        return ""
    in_section = False
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("["):
            in_section = line.strip() == f"[{section}]"
        elif in_section and line.startswith(f"{key}="):
            return line[len(key) + 1:]
    return ""


def imported_listings() -> dict:
    """Content folders the Fab plugin imported listings into, by listing id."""
    record = _config_value("/Script/Fab.FabLocalAssets", "PathsListingID")
    result = {}
    for folder, listing_id in IMPORT_RECORD.findall(record):
        # A pack can record its content AND World Partition bookkeeping folders.
        # Never let the last __ExternalActors__ entry hide the usable content root.
        if _content_folder(folder):
            result[listing_id] = folder
    return result


def _content_folder(folder: str) -> bool:
    return (folder.startswith("/Game/") and
            not folder.split("/")[2].startswith("__External"))


def _download_bytes(listing_id: str) -> int:
    """Size of the listing's download in the Fab plugin's cache."""
    configured = re.search(r'Path="([^"]*)"', _config_value("/Script/Fab.FabSettings", "CacheDirectoryPath"))
    cache = Path(configured.group(1)) if configured and configured.group(1) else Path(tempfile.gettempdir()) / "FabLibrary"
    download = cache / listing_id
    return sum(item.stat().st_size for item in download.rglob("*") if item.is_file()) if download.is_dir() else 0


def _folder_bytes(folder: str) -> int:
    """Size on disk of a /Game folder, for packs the Fab plugin installs as files."""
    content_dir = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()))
    path = content_dir / folder.removeprefix("/Game/")
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0


def _registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _packages_in(folder: str) -> set:
    """Packages in a folder and below, unsaved ones included."""
    return {str(asset.package_name) for asset in _registry().get_assets_by_path(folder, recursive=True)}


def _top_folders() -> set:
    return set(_registry().get_sub_paths("/Game", False))


def _settled_import(request: dict) -> str | None:
    """The folder of content that appeared since the request and has stopped growing, for
    when the Fab plugin's own record of the import hasn't reached its config file yet."""
    new = _packages_in(FAB_FOLDER) - request["fab_before"]
    folder = _common_folder(new)
    if not new:
        # Unreal Engine packs install into a new top-level folder instead
        new_folders = {path for path in _top_folders() - request["folders_before"]
                       if _content_folder(str(path))}
        new = set().union(*(_packages_in(path) for path in new_folders))
        folder = next(iter(new_folders)) if len(new_folders) == 1 else _common_folder(new)
    if new and len(new) == request["new"]:
        request["settled"] += 1
    else:
        request.update(new=len(new), settled=0)
    return folder if request["settled"] >= SETTLED_CHECKS else None


def _common_folder(asset_paths: set) -> str:
    folders = [path.rsplit("/", 1)[0].split("/") for path in asset_paths]
    common = []
    for parts in zip(*folders):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return "/".join(common)


def _meshes(folder: str) -> list:
    """What can be placed from the folder, named relative to it."""
    meshes = []
    for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
        asset_class = str(unreal.EditorAssetLibrary.find_asset_data(path).asset_class_path.asset_name)
        if asset_class in MESH_CLASSES:
            name = path.split(".")[0].removeprefix(folder.rstrip("/") + "/")
            meshes.append(name if asset_class == "StaticMesh" else f"{name} ({asset_class})")
    return meshes


def listing_in(text: str) -> str | None:
    """The listing id of a Fab listing link in a chat message."""
    match = LISTING_LINK.search(text)
    return match.group(1).lower() if match else None


def without_listing_link(text: str) -> str:
    return re.sub(r"\s+", " ", LISTING_LINK.sub("", text)).strip()


def title_in(text: str) -> str:
    """The listing title an agent's request quotes."""
    match = re.search(r'"([^"]+)"', text)
    return match.group(1) if match else ""


def _new_request(agent: str, title: str) -> dict:
    return {"agent": agent, "title": title, "opened": time.time(), "fab_before": _packages_in(FAB_FOLDER),
            "folders_before": _top_folders(), "new": 0, "settled": 0}


def finish_if_imported(listing_id: str, agent: str, title: str) -> str | None:
    """Tells the agent where the listing is if the project has it already. Returns a line
    for the person, or None when the project doesn't have it."""
    folder = imported_listings().get(listing_id)
    # Counts imports that are still unsaved, which the directory checks leave out
    if folder and unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
        return f"Already in {folder}. " + _finish(listing_id, _new_request(agent, title), folder)
    return None


def _hold_back_library():
    """The CoPresence Fab plugin, if this editor has a build of it that can hold imports
    back. Without it, Multi-User sends new assets as usual."""
    library = getattr(unreal, "CoPresenceFabLibrary", None)
    return library if library is not None and hasattr(library, "hold_back_new_assets") else None


def _hold_back_new_assets(hold_back: bool) -> None:
    """Keeps new assets in the Fab folder out of the session until they are saved."""
    library = _hold_back_library()
    if library:
        library.hold_back_new_assets(FAB_FOLDER, hold_back)


def track_import(listing_id: str, agent: str, title: str) -> None:
    """Watches for the listing's import, then shares it and tells the agent."""
    _state["pending"][listing_id] = _new_request(agent, title)
    _hold_back_new_assets(True)
    if _state["callback"] is None:
        _state["callback"] = unreal.register_slate_post_tick_callback(_tick)


def is_tracking(listing_id: str) -> bool:
    return listing_id in _state["pending"]


def open_fab_tab(listing_id: str) -> None:
    base_url = _bridge().call_method("GetUrl")
    _bridge().call_method("OpenInNewTab", (f"{base_url}/listings/{listing_id}",))


def open_listing(listing_id: str, agent: str, title: str = "") -> str:
    """Opens an agent's pick in this editor's Fab tab, or answers at once if the project
    already has it. Returns a line for the person."""
    if agent_editor.is_agent_editor():
        raise RuntimeError("Agents' editors don't open Fab")
    already = finish_if_imported(listing_id, agent, title)
    if already:
        return already
    track_import(listing_id, agent, title)
    open_fab_tab(listing_id)
    return f"Opened in your Fab tab. Click Add to Project and {agent} gets it when the import finishes."


def _finish(listing_id: str, request: dict, folder: str) -> str:
    """Shares a finished import with the session if it is small enough and tells the agent.
    Returns a line for the person."""
    name = f'"{request["title"]}"' if request["title"] else "the listing"
    agent = request["agent"]
    # Unreal Engine packs arrive as files on disk, which Multi-User doesn't send
    is_pack = not folder.startswith(FAB_FOLDER + "/")
    size_mb = (_folder_bytes(folder) if is_pack else _download_bytes(listing_id)) / (1024 * 1024)
    if is_pack:
        text = (f"Installed {name} from Fab as an Unreal Engine pack in {folder} ({size_mb:.0f} MB). "
                f"Verify availability before placing: editors using the same project directory can scan "
                f"{folder} and load the assets already on disk. For editors with separate project copies, "
                f"wait for Git distribution and confirmation that the assets load there.")
        notice = f"Pack installed. {agent} will check shared-project availability; separate copies need Git distribution."
    elif size_mb * 1024 * 1024 > SESSION_LIMIT_BYTES:
        text = (f"Added {name} from Fab here, but at {size_mb:.0f} MB it's too big to share in the session. "
                f"Don't place it until it's in git.")
        notice = f"At {size_mb:.0f} MB it's too big for the session, so it stays unsaved here. {agent} knows to wait."
    else:
        if not unreal.EditorAssetLibrary.save_directory(folder, only_if_is_dirty=True, recursive=True):
            raise RuntimeError(f"Fab import could not be saved: {folder}; preserving import files for retry")
        library = _hold_back_library()
        if library:
            # Multi-User wrote the assets it held back into the project folder; now that
            # they are saved, the session's sandbox has them
            library.delete_held_back_files(folder)
        meshes = _meshes(folder)
        listed = ", ".join(meshes[:LISTED_MESHES]) or "materials and textures only"
        if len(meshes) > LISTED_MESHES:
            listed += f" and {len(meshes) - LISTED_MESHES} more"
        text = f"Added {name} from Fab and shared it with the session. In {folder}: {listed}."
        notice = f"Saved and shared with the session. {agent} can place it now."
    hub_client.post_message(text, agent, [])
    return notice


def _check() -> None:
    imported = imported_listings()
    pending = _state["pending"]
    for listing_id, request in list(pending.items()):
        folder = imported.get(listing_id)
        # New content can only be told apart while a single request waits
        if folder is None and len(pending) == 1:
            folder = _settled_import(request)
        if folder:
            # Keep the handoff pending if saving/sharing failed. Never discard
            # its recovery state before _finish has actually succeeded.
            if time.time() < request.get("retry_after", 0):
                continue
            try:
                _finish(listing_id, request, folder)
            except Exception:
                request["retry_after"] = time.time() + 30
                raise
            del pending[listing_id]
        elif time.time() - request["opened"] > PENDING_SECONDS:
            del pending[listing_id]


def _tick(delta_seconds: float) -> None:
    _state["elapsed"] += delta_seconds
    if _state["elapsed"] < CHECK_SECONDS:
        return
    _state["elapsed"] = 0.0
    try:
        _check()
    except Exception as error:
        unreal.log_warning(f"CoPresence Fab requests: {error}")
    if not _state["pending"]:
        _hold_back_new_assets(False)
        unreal.unregister_slate_post_tick_callback(_state["callback"])
        _state["callback"] = None
