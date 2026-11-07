"""Agents share the content they make and bring in with everyone in the Multi-User session.

Multi-User carries level edits as they happen, but an asset whose edits it doesn't carry,
such as a material, a texture or a media source, only reaches the others when its package
is saved. Agents' editors refuse ordinary saves (the CoPresence Fab plugin), so an agent
keeps what it makes in its own folder, /Game/_Agents/<agent>, and the MCP server shares the
changed packages there at the end of each task, or sooner with its share tool. Files an
agent imports land in the same folder and are shared as soon as they are in.
"""

import re
import shutil
from pathlib import Path

import unreal

from copresence import tracking

AGENTS_ROOT = "/Game/_Agents"
IMPORTS_FOLDER = "Imports"
MEDIA_FOLDER = "Media"


def slug(text: str) -> str:
    """Letters, digits and underscores only, which any asset or folder name takes."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def agent_folder() -> str:
    """The agent's own content folder, e.g. /Game/_Agents/Sketch_Claude."""
    return f"{AGENTS_ROOT}/{slug(tracking.agent_name()) or 'Agent'}"


def _library():
    """The CoPresence Fab plugin, if this editor has a build of it that can share content."""
    library = getattr(unreal, "CoPresenceFabLibrary", None)
    return library if library is not None and hasattr(library, "share_packages") else None


def max_package_bytes() -> int:
    library = _library()
    return int(library.get_max_shared_package_bytes()) if library else 0


def in_folder(package: str, folder: str) -> bool:
    return package.startswith(folder.rstrip("/") + "/")


def pending(folder: str) -> list[str]:
    """Changed packages in a folder and below that the session doesn't have yet."""
    return sorted(package for package in tracking.dirty_content() if in_folder(package, folder))


def share(packages: list[str]) -> dict:
    """Saves the packages into the session, which sends each one to everyone."""
    library = _library()
    if library is None:
        raise RuntimeError("Sharing needs the CoPresence Fab plugin built for this editor; ask a person to build it")
    outcome = {"shared": [], "unchanged": [], "refused": []}
    for item in library.share_packages(list(packages)):
        package = str(item.package_name)
        if item.result == unreal.CoPresenceShareResult.SHARED:
            outcome["shared"].append({"package": package, "bytes": int(item.size_bytes)})
        elif item.result == unreal.CoPresenceShareResult.UNCHANGED:
            outcome["unchanged"].append(package)
        else:
            outcome["refused"].append({"package": package, "problem": str(item.problem)})
    return outcome


def _packages_in(folder: str) -> set:
    """Packages in a folder and below, unsaved ones included."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    return {str(asset.package_name) for asset in registry.get_assets_by_path(folder, recursive=True)}


def _free_folder(parent: str, name: str) -> str:
    """A folder under parent named after name that holds nothing yet."""
    base = f"{parent}/{slug(name) or 'Import'}"
    folder, number = base, 1
    while unreal.EditorAssetLibrary.does_directory_exist(folder) or _packages_in(folder):
        number += 1
        folder = f"{base}_{number}"
    return folder


def _project_path(folder: str) -> Path:
    """Where a /Game folder is in the project folder itself."""
    content = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()))
    return content / folder.removeprefix("/Game/")


def _remove_empty_folders(folder: str) -> None:
    """Multi-User leaves the folders it wrote held back assets to in the project folder; once
    they are empty, they go, up to the agents' folder."""
    content = _project_path("/Game/")
    agents_root = _project_path(AGENTS_ROOT)
    path = _project_path(folder)
    while path != content and agents_root in (path, *path.parents):
        if path.is_dir():
            if any(path.iterdir()):
                return
            path.rmdir()
        path = path.parent


def _discard(folder: str) -> None:
    """Drops an import that didn't make it into the session: its assets in this editor, and the
    files Multi-User wrote into the project folder while holding them back. The folder is
    new, so nothing else is in it."""
    unreal.EditorAssetLibrary.delete_directory(folder)
    held_back = _project_path(folder)
    agents_root = _project_path(AGENTS_ROOT)
    if held_back.is_dir() and agents_root in held_back.parents:
        shutil.rmtree(held_back, ignore_errors=True)


def import_file(source_path: str, name: str) -> dict:
    """Imports a mesh or picture into a new folder of the agent's own and shares what the
    import made at once. Like Fab imports in people's editors, the new assets are held back
    from the session until they are saved, so it gets one copy of each, not one per step of
    the import. All or nothing: when something in it can't be shared, none of it stays."""
    library = _library()
    if library is None:
        raise RuntimeError("Importing needs the CoPresence Fab plugin built for this editor; ask a person to build it")
    folder = _free_folder(f"{agent_folder()}/{IMPORTS_FOLDER}", name or Path(source_path).stem)
    library.hold_back_new_assets(folder, True)
    try:
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", source_path)
        task.set_editor_property("destination_path", folder)
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", False)
        task.set_editor_property("save", False)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        packages = sorted(_packages_in(folder))
        if not packages:
            raise RuntimeError(f"Unreal couldn't import {Path(source_path).name}")
        outcome = share(packages)
        if outcome["refused"]:
            _discard(folder)
            outcome["discarded"] = True
        else:
            library.delete_held_back_files(folder)
    except Exception:
        _discard(folder)
        raise
    finally:
        library.hold_back_new_assets(folder, False)
        _remove_empty_folders(folder)
    outcome["folder"] = folder
    outcome["assets"] = _describe(folder) if not outcome.get("discarded") else []
    return outcome


def _describe(folder: str) -> list:
    """What an import made, as asset paths with their kind, meshes first."""
    assets = []
    for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
        kind = str(unreal.EditorAssetLibrary.find_asset_data(path).asset_class_path.asset_name)
        assets.append({"path": path.split(".")[0], "kind": kind})
    order = ("StaticMesh", "SkeletalMesh", "Texture2D")
    return sorted(assets, key=lambda asset: (order.index(asset["kind"]) if asset["kind"] in order else len(order),
                                             asset["path"]))


def create_video_source(url: str, name: str) -> dict:
    """A media source in the agent's folder that streams the video at url, shared at once.
    Every editor plays it from the address itself, the way a Media Plate plays it."""
    folder = f"{agent_folder()}/{MEDIA_FOLDER}"
    asset_name = slug(name) or "Video"
    number = 1
    while unreal.EditorAssetLibrary.does_asset_exist(f"{folder}/{asset_name}"):
        number += 1
        asset_name = f"{slug(name) or 'Video'}_{number}"
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    # Media Plate's editor brings the factory; without one, asset tools look for a factory itself
    factory_class = getattr(unreal, "StreamMediaSourceFactoryNew", None)
    source = tools.create_asset(asset_name, folder, unreal.StreamMediaSource, factory_class() if factory_class else None)
    if source is None:
        raise RuntimeError("Unreal couldn't make a stream media source")
    source.set_editor_property("stream_url", url)
    outcome = share([source.get_outermost().get_name()])
    outcome["source"] = source.get_path_name().split(".")[0]
    return outcome
