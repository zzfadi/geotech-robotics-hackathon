"""Draw the external simulation tick in Unreal; only PIE copies of meshes move."""

import itertools
import json
import socket
import time
from pathlib import Path

import unreal

_sock = None
_handle = None
_latest = None
_received = 0
_pie_world = None
_meshes = {}


def export_layout(path, space="table_v1"):
    actors = {
        a.get_actor_label(): a
        for a in unreal.get_editor_subsystem(
            unreal.EditorActorSubsystem
        ).get_all_level_actors()
    }
    table = actors["Dreamlabs_YAM_Table"]  # Table actor retained after arm replacement.
    origin, extent = table.get_actor_bounds(False)
    bases = {}
    for side in ("left", "right"):
        t = actors[f"MakerArm_{side}_base_link"].get_actor_transform()
        p, q = t.translation, t.rotation
        bases[side] = [[p.x, p.y, p.z], [q.x, q.y, q.z, q.w]]
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {"space": space, "bases": bases, "table_height_cm": origin.z + extent.z},
            indent=2,
        )
    )
    return str(dest)


def _draw(world, state, move_meshes):
    global _pie_world, _meshes
    if move_meshes and _pie_world != world:
        _pie_world = world
        _meshes = {
            a.get_actor_label(): a
            for a in unreal.GameplayStatics.get_all_actors_of_class(
                world, unreal.StaticMeshActor
            )
            if a.get_actor_label().startswith("MakerArm_")
        }
        for mesh in _meshes.values():
            mesh.static_mesh_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    # All robot geometry, human proxies and contacts below share this one simulation tick.
    for side in ("left", "right"):
        chain = ["base_link", *(f"link_{i:03}" for i in range(2, 8)), "grasp_center"]
        for parent, child in itertools.pairwise(chain):
            a = state["arms"][f"{side}_{parent}"]
            b = state["arms"][f"{side}_{child}"]
            unreal.SystemLibrary.draw_debug_line(
                world,
                unreal.Vector(*a[:3]),
                unreal.Vector(*b[:3]),
                unreal.LinearColor(0.6, 0.9, 0.1, 1),
                0.0,
                1.6,
            )
    if move_meshes:
        for name, pose in state["arms"].items():
            actor = _meshes.get("MakerArm_" + name)
            if actor:
                actor.set_actor_location_and_rotation(
                    unreal.Vector(*pose[:3]),
                    unreal.Quat(*pose[3:]).rotator(),
                    False,
                    True,
                )
    for who, _, a, b in state["colliders"]:
        color = (
            unreal.LinearColor(1, 0.4, 0.05, 1)
            if who == "Human_Quest_A"
            else unreal.LinearColor(0, 0.9, 1, 1)
        )
        unreal.SystemLibrary.draw_debug_line(
            world, unreal.Vector(*a), unreal.Vector(*b), color, 0.0, 3.0
        )
        unreal.SystemLibrary.draw_debug_sphere(
            world, unreal.Vector(*b), 4.5, 8, color, 0.0, 0.8
        )
    for _, _, _, point in state["contacts"]:
        unreal.SystemLibrary.draw_debug_sphere(
            world,
            unreal.Vector(*point),
            1.4,
            8,
            unreal.LinearColor(1, 0.05, 0.1, 1),
            0.0,
            1.0,
        )
    token = state["token"]
    unreal.SystemLibrary.draw_debug_box(
        world,
        unreal.Vector(*token[:3]),
        unreal.Vector(2.2, 2.2, 2.2),
        unreal.LinearColor(0.8, 0.4, 1, 1),
        unreal.Quat(*token[3:]).rotator(),
        0.0,
        1.0,
    )


def _poll(_delta):
    global _latest, _received, _pie_world, _meshes
    try:
        for _ in range(120):
            try:
                raw, _ = _sock.recvfrom(65535)
            except BlockingIOError:
                break
            frame = json.loads(raw)
            if frame.get("version") == 1 and isinstance(frame.get("tick"), int):
                age = time.monotonic_ns() - int(frame["target_ns"])
                if age > 300_000_000 or age < 0:
                    continue
                if _latest and int(frame["target_ns"]) <= int(_latest["target_ns"]):
                    continue
                _latest = frame
                _received = time.monotonic()
        if not _latest or time.monotonic() - _received > 0.20:
            return
        pie = unreal.EditorLevelLibrary.get_pie_worlds(False)
        if pie:
            for world in pie:
                _draw(world, _latest, True)
        else:
            _pie_world = None
            _meshes = {}
            world = unreal.get_editor_subsystem(
                unreal.UnrealEditorSubsystem
            ).get_editor_world()
            if world:
                _draw(world, _latest, False)
    except (
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        KeyError,
        AttributeError,
    ) as exc:
        unreal.log_warning("Contact preview stopped: " + str(exc))
        stop()


def start(port=17863):
    global _sock, _handle
    if _sock is not None:
        return status()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.setblocking(False)
        _handle = unreal.register_slate_post_tick_callback(_poll)
    except Exception:
        sock.close()
        raise
    _sock = sock
    return status()


def status():
    if not _latest:
        return {"listening": _sock is not None, "tick": None}
    return {
        "listening": _sock is not None,
        "tick": _latest["tick"],
        "target_ns": _latest["target_ns"],
        "inputs": _latest["inputs"],
        "contacts": _latest["contacts"],
        "fresh": time.monotonic() - _received <= 0.20,
    }


def stop():
    global _handle, _sock, _latest, _pie_world, _meshes
    if _handle is not None:
        unreal.unregister_slate_post_tick_callback(_handle)
    if _sock is not None:
        _sock.close()
    _handle = _sock = _latest = _pie_world = None
    _meshes = {}
