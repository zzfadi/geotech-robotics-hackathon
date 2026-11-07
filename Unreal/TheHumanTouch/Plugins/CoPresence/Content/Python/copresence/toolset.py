import contextlib
import io
import json
import os
import sys

import unreal

import toolset_registry
from copresence import animation, sharing, tracking

_flight = {"callback": None}

# New props start this small and grow to full size, removed ones shrink to it first
TINY_SCALE = 0.02
# A line of sight passes through at most this many invisible things, such as volumes and
# avatars, before it counts as clear
SEE_THROUGH_LIMIT = 8


def _vector(values) -> unreal.Vector | None:
    if values is None:
        return None
    if isinstance(values, (int, float)):
        return unreal.Vector(values, values, values)
    return unreal.Vector(*values)


def _rotator(values) -> unreal.Rotator | None:
    if values is None:
        return None
    pitch, yaw, roll = values
    return unreal.Rotator(roll=roll, pitch=pitch, yaw=yaw)


def _first_blocker(world: unreal.World, start: unreal.Vector, end: unreal.Vector, see_through: list) -> list | None:
    """The distance from start to the first thing that hides end from it, and that thing's
    label, or None when nothing does. Lines pass through volumes, which captures don't
    show, and through avatars and other actors an editor keeps to itself, which come and go."""
    passed = list(see_through)
    for _ in range(SEE_THROUGH_LIMIT):
        hit = unreal.SystemLibrary.line_trace_single(world, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True,
                                                     passed, unreal.DrawDebugTrace.NONE, False)
        if hit is None:
            return None
        fields = hit.to_tuple()
        blocking, distance, actor = fields[0], fields[3], fields[9]
        if not blocking:
            return None
        if actor is None or not (isinstance(actor, unreal.Volume) or tracking.is_local_only(actor)):
            return [round(distance, 1), actor.get_actor_label() if actor else ""]
        passed.append(actor)
    return None


@unreal.uclass()
class CoPresenceTools(unreal.ToolsetDefinition):
    """Edits made by an agent that is a Multi-User participant. Every editing tool here
    runs inside one named editor transaction so the change is undoable, attributed to
    the agent, and replicates live to the other users in the session."""

    @toolset_registry.tool_call
    @staticmethod
    def begin_task(task_id: str, title: str) -> str:
        """Starts a task. Actors created until end_task are tagged <agent>:<task_id> and
        put in the Outliner folder _Agents/<agent>/<task_id>-<title>.

        Args:
            task_id: Short unique id for the task, e.g. t-0003.
            title: Short human-readable title.

        Returns:
            JSON with the agent name, tag and folder used for this task, the agent's content
            folder, and this editor's process id.
        """
        tracking.current_task.update({"id": task_id, "title": title, "transactions": 0})
        return json.dumps({"agent": tracking.agent_name(), "tag": tracking.task_tag(), "folder": tracking.task_folder(),
                           "content_folder": sharing.agent_folder(), "editor_pid": os.getpid()})

    @toolset_registry.tool_call
    @staticmethod
    def end_task() -> str:
        """Ends the current task.

        Returns:
            JSON with the task id and how many transactions it recorded.
        """
        task = tracking.current_task
        finished = {"id": task["id"], "transactions": task["transactions"]}
        task.update({"id": None, "title": None, "transactions": 0})
        return json.dumps(finished)

    @toolset_registry.tool_call
    @staticmethod
    def transact(toolset_name: str, tool_name: str, arguments_json: str, description: str) -> str:
        """Calls another registered tool inside one named transaction.

        Use this instead of call_tool for any tool that modifies the level
        (spawning, transforms, tags, labels, properties, components), otherwise the
        change stays local and other users never see it.

        Args:
            toolset_name: Full toolset name, e.g. editor_toolset.toolsets.actor.ActorTools.
            tool_name: Tool name without the toolset prefix, e.g. set_actor_transform.
            arguments_json: The tool arguments as a JSON object string.
            description: Short human-readable summary shown in the session history.

        Returns:
            JSON with the tool's return value or error, and the actors created, modified
            and deleted.
        """
        def edit():
            call_result = unreal.ToolsetRegistry.execute_tool(toolset_name, tool_name, arguments_json)
            # Tools that finish on a later frame would escape the transaction
            if not call_result.is_complete:
                raise RuntimeError("tool did not complete synchronously; its edits may not replicate")
            if call_result.error:
                raise RuntimeError(str(call_result.error))
            return call_result.value

        return json.dumps(tracking.run_tracked(description, edit))

    @toolset_registry.tool_call
    @staticmethod
    def run_python(code: str, description: str) -> str:
        """Runs editor Python inside one named transaction.

        The code has `unreal` in scope. Assign to a variable named `result` to
        return a value. Call obj.modify() before changing an object directly so the
        change is recorded; editor property setters do this automatically.

        Args:
            code: Python source to execute.
            description: Short human-readable summary shown in the session history.

        Returns:
            JSON with `result`, captured stdout, an error traceback if it failed, and
            the actors created, modified and deleted.
        """
        scope = {"unreal": unreal}
        captured_stdout = io.StringIO()

        def edit():
            with contextlib.redirect_stdout(captured_stdout):
                exec(code, scope)
            return repr(scope["result"]) if "result" in scope else None

        outcome = tracking.run_tracked(description, edit)
        scenes = sys.modules.get("copresence.meet_scenes")
        if scenes is not None:
            scenes.transaction_finished(outcome)
        outcome["stdout"] = captured_stdout.getvalue()[-4000:]
        return json.dumps(outcome)

    @toolset_registry.tool_call
    @staticmethod
    def animate_actors(moves_json: str, duration_seconds: float, easing: str, description: str) -> str:
        """Moves, rotates and/or scales actors smoothly over time as one transaction.
        Other users watch them glide, as if the agent dragged them. Returns at once;
        call animation_result after duration_seconds for the outcome.

        Args:
            moves_json: JSON list of {"actor": label or path, "location": [x, y, z],
                "rotation": [pitch, yaw, roll], "scale": number or [x, y, z]}; omitted
                parts stay as they are.
            duration_seconds: How long the motion takes.
            easing: smooth, pop (overshoots slightly), or ease_in.
            description: Short summary shown in the session history.

        Returns:
            JSON with the animated actors and duration, or an error.
        """
        try:
            moves = [(tracking.find_actor(move["actor"]), _vector(move.get("location")),
                      _rotator(move.get("rotation")), _vector(move.get("scale")))
                     for move in json.loads(moves_json)]
            return json.dumps(animation.start(description, moves, duration_seconds, easing))
        except Exception as error:
            return json.dumps({"error": str(error)})

    @toolset_registry.tool_call
    @staticmethod
    def place_animated(toolset_name: str, tool_name: str, arguments_json: str, duration_seconds: float,
                       description: str) -> str:
        """Spawns an actor with a spawn tool, then grows it in from nothing with a small
        pop. The spawn and the grow are two transactions.

        Args:
            toolset_name: Toolset of the spawn tool, e.g. editor_toolset.toolsets.scene.SceneTools.
            tool_name: Spawn tool returning the new actor, e.g. add_to_scene_from_asset.
            arguments_json: The spawn tool's arguments as a JSON object string.
            duration_seconds: How long the grow takes.
            description: Short summary shown in the session history.

        Returns:
            JSON with the spawn outcome and, if it succeeded, the started grow animation.
        """
        spawned = {}

        def spawn():
            call_result = unreal.ToolsetRegistry.execute_tool(toolset_name, tool_name, arguments_json)
            if not call_result.is_complete:
                raise RuntimeError("tool did not complete synchronously; its edits may not replicate")
            if call_result.error:
                raise RuntimeError(str(call_result.error))
            actor = tracking.find_actor(json.loads(call_result.value)["returnValue"]["refPath"])
            # Spawn and snap at full size, then shrink it so the grow ends where it belongs
            spawned["actor"] = actor
            spawned["scale"] = actor.get_actor_scale3d()
            actor.set_actor_scale3d(spawned["scale"] * TINY_SCALE)
            return call_result.value

        outcome = tracking.run_tracked(description, spawn)
        if outcome["error"] is None:
            outcome["animation"] = animation.start(
                f"{description} (grow in)", [(spawned["actor"], None, None, spawned["scale"])], duration_seconds, "pop")
        return json.dumps(outcome)

    @toolset_registry.tool_call
    @staticmethod
    def remove_animated(actors_json: str, duration_seconds: float, description: str) -> str:
        """Shrinks actors out of sight, then deletes them. The shrink and the delete are
        two transactions. Returns at once; call animation_result for the outcome.

        Args:
            actors_json: JSON list of actor labels or paths.
            duration_seconds: How long the shrink takes.
            description: Short summary shown in the session history.

        Returns:
            JSON with the started animation, or an error.
        """
        try:
            actors = [tracking.find_actor(reference) for reference in json.loads(actors_json)]
        except Exception as error:
            return json.dumps({"error": str(error)})

        def delete(_result: dict) -> dict:
            subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            return tracking.run_tracked(f"{description} (delete)", lambda: subsystem.destroy_actors(actors))

        moves = [(actor, None, None, actor.get_actor_scale3d() * TINY_SCALE) for actor in actors]
        return json.dumps(animation.start(f"{description} (shrink out)", moves, duration_seconds, "ease_in", delete))

    @toolset_registry.tool_call
    @staticmethod
    def animation_result() -> str:
        """Outcome of the last animation: whether it is done, the actors it changed, any
        follow-up such as the deletion after a shrink-out, and the undo count.

        Returns:
            JSON with done set false while the animation is still running.
        """
        if animation.is_running():
            return json.dumps({"done": False})
        return json.dumps(animation.last_result() or {"done": True, "error": "No animation has run"})

    @toolset_registry.tool_call
    @staticmethod
    def locate_actors(actors_json: str) -> str:
        """World locations of actors, for deciding where to look before working on them.

        Args:
            actors_json: JSON list of actor labels or paths.

        Returns:
            JSON mapping each reference to [x, y, z], or to an error string.
        """
        located = {}
        for reference in json.loads(actors_json):
            try:
                location = tracking.find_actor(reference).get_actor_location()
                located[reference] = [round(location.x, 1), round(location.y, 1), round(location.z, 1)]
            except Exception as error:
                located[reference] = str(error)
        return json.dumps(located)

    @toolset_registry.tool_call
    @staticmethod
    def actor_bounds(actors_json: str) -> str:
        """World bounding boxes of actors, for framing them with the camera.

        Args:
            actors_json: JSON list of actor labels or paths.

        Returns:
            JSON mapping each reference to {"center": [x, y, z], "extent": [x, y, z]},
            where extent is half the box's size, or to an error string.
        """
        bounds = {}
        for reference in json.loads(actors_json):
            try:
                center, extent = tracking.find_actor(reference).get_actor_bounds(False)
                bounds[reference] = {"center": [round(center.x, 1), round(center.y, 1), round(center.z, 1)],
                                     "extent": [round(extent.x, 1), round(extent.y, 1), round(extent.z, 1)]}
            except Exception as error:
                bounds[reference] = str(error)
        return json.dumps(bounds)

    @toolset_registry.tool_call
    @staticmethod
    def trace_lines(lines_json: str, see_through_json: str) -> str:
        """Checks lines of sight in the level without changing anything, such as whether
        a camera at one point would see another.

        Args:
            lines_json: JSON list of lines, each [[x, y, z], [x, y, z]]: start and end in cm.
            see_through_json: JSON list of actor paths the lines pass through.

        Returns:
            JSON list with, for each line, null when nothing is in the way, or the
            distance from its start to the first thing in the way and that thing's label.
        """
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        if world is None:
            raise RuntimeError("No level is open")
        paths = set(json.loads(see_through_json))
        see_through = []
        if paths:
            actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
            see_through = [actor for actor in actors if actor.get_path_name() in paths]
        return json.dumps([_first_blocker(world, unreal.Vector(*start), unreal.Vector(*end), see_through)
                           for start, end in json.loads(lines_json)])

    @toolset_registry.tool_call
    @staticmethod
    def verify_edit(undo_count_after: int) -> str:
        """Checks whether Multi-User rejected the agent's last edit. The server rejects
        transactions on objects another user has locked (for example while dragging
        them), and this editor then undoes the edit on its own. Call shortly after an
        edit with the undo_count that edit returned.

        Args:
            undo_count_after: The undo_count returned by the edit.

        Returns:
            JSON with whether the edit was rejected and how many transactions were undone.
        """
        undone = max(undo_count_after - tracking.undo_count(), 0)
        task = tracking.current_task
        if undone and task["id"]:
            task["transactions"] = max(task["transactions"] - undone, 0)
        return json.dumps({"rejected": undone > 0, "undone": undone})

    @toolset_registry.tool_call
    @staticmethod
    def fly_to(x: float, y: float, z: float, pitch: float, yaw: float, duration_seconds: float) -> str:
        """Glides the agent's viewport camera to a pose. Other users see the agent's
        avatar travel there, since Multi-User presence follows this camera.

        Args:
            x: Target location X in centimeters.
            y: Target location Y in centimeters.
            z: Target location Z in centimeters.
            pitch: Target pitch in degrees, negative looks down.
            yaw: Target yaw in degrees.
            duration_seconds: Flight time; 0 jumps immediately.

        Returns:
            JSON with the start and target poses.
        """
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        start_location, start_rotation = tracking.camera_info()
        target_location = unreal.Vector(x, y, z)
        target_rotation = unreal.Rotator(roll=0.0, pitch=pitch, yaw=yaw)

        if _flight["callback"] is not None:
            unreal.unregister_slate_post_tick_callback(_flight["callback"])
            _flight["callback"] = None

        if duration_seconds <= 0:
            subsystem.set_level_viewport_camera_info(target_location, target_rotation)
        else:
            elapsed = {"seconds": 0.0}

            def step(delta_seconds: float) -> None:
                elapsed["seconds"] += delta_seconds
                alpha = min(elapsed["seconds"] / duration_seconds, 1.0)
                eased = alpha * alpha * (3.0 - 2.0 * alpha)
                location = unreal.MathLibrary.v_lerp(start_location, target_location, eased)
                rotation = unreal.MathLibrary.r_lerp(start_rotation, target_rotation, eased, True)
                subsystem.set_level_viewport_camera_info(location, rotation)
                if alpha >= 1.0:
                    unreal.unregister_slate_post_tick_callback(_flight["callback"])
                    _flight["callback"] = None

            _flight["callback"] = unreal.register_slate_post_tick_callback(step)

        return json.dumps({
            "from": [start_location.x, start_location.y, start_location.z, start_rotation.pitch, start_rotation.yaw],
            "to": [x, y, z, pitch, yaw],
        })

    @toolset_registry.tool_call
    @staticmethod
    def session_info() -> str:
        """Describes the Multi-User session: who is connected and where each user's
        camera is, plus the agent's own camera and current task.

        Returns:
            JSON with the session, the local agent, remote users and the current task.
        """
        statics = unreal.MultiUserClientStatics
        solo = '-COPRESENCESOLO' in unreal.SystemLibrary.get_command_line().upper()
        session = None if solo else statics.get_multi_user_session_info()
        remote_infos = [] if solo else statics.get_remote_multi_user_client_infos()
        # Python returns the out array, sometimes paired with the bool result
        if isinstance(remote_infos, tuple):
            remote_infos = remote_infos[-1]

        users = []
        for info in remote_infos or []:
            transform = statics.get_multi_user_presence_transform(info.client_endpoint_id)
            location = transform.translation
            rotation = transform.rotation.rotator()
            users.append({
                "name": info.display_name,
                "camera": [round(location.x), round(location.y), round(location.z), round(rotation.pitch), round(rotation.yaw)],
            })

        camera_location, camera_rotation = tracking.camera_info()
        return json.dumps({
            "session": None if solo else str(session.session_name),
            "mode": "solo" if solo else "multiuser",
            "agent": tracking.agent_name(),
            "agent_camera": [round(camera_location.x), round(camera_location.y), round(camera_location.z),
                             round(camera_rotation.pitch), round(camera_rotation.yaw)],
            "users": users,
            "task": tracking.current_task,
            "content_folder": sharing.agent_folder(),
            "editor_pid": os.getpid(),
        })

    @toolset_registry.tool_call
    @staticmethod
    def agent_content(packages_json: str) -> str:
        """The agent's own content folder and the changed packages in it that the session
        doesn't have yet. Changes nothing.

        Args:
            packages_json: JSON list of other package names to check, such as assets the
                agent changed outside its folder.

        Returns:
            JSON with the folder, its pending packages, which of the given packages still
            have unsaved changes, and the biggest package that can be shared, in bytes.
        """
        folder = sharing.agent_folder()
        dirty = tracking.dirty_content()
        return json.dumps({
            "folder": folder,
            "pending": sharing.pending(folder),
            "still_changed": sorted(set(json.loads(packages_json)) & dirty),
            "max_package_bytes": sharing.max_package_bytes(),
        })

    @toolset_registry.tool_call
    @staticmethod
    def share_content(packages_json: str) -> str:
        """Shares the agent's changed content with everyone in the Multi-User session by saving
        it into the session: the changed packages in its own folder, and the given others.
        Never saves levels, their actors, or packages someone else is editing.

        Args:
            packages_json: JSON list of other package names to share as well, such as an
                existing material the agent changed.

        Returns:
            JSON with the folder, and the packages shared (with their size in bytes),
            unchanged and refused (with why).
        """
        folder = sharing.agent_folder()
        packages = sharing.pending(folder) + [package for package in json.loads(packages_json)
                                              if not sharing.in_folder(package, folder)]
        outcome = sharing.share(packages)
        outcome["folder"] = folder
        return json.dumps(outcome)

    @toolset_registry.tool_call
    @staticmethod
    def import_file(source_path: str, name: str) -> str:
        """Imports a mesh (FBX, OBJ, glTF) or picture (PNG, JPEG, ...) into a new folder in the
        agent's content folder, and shares what the import made with the session at once.

        Args:
            source_path: Full path of the file on this machine.
            name: Name for the new folder, e.g. "Sofa"; empty uses the file's name.

        Returns:
            JSON with the folder, the assets the import made, and what was shared or refused.
            When anything in it couldn't be shared, discarded is true and nothing stays.
        """
        return json.dumps(sharing.import_file(source_path, name))

    @toolset_registry.tool_call
    @staticmethod
    def create_video_source(url: str, name: str) -> str:
        """Makes a media source for a video at a web address in the agent's content folder and
        shares it. Every editor streams the video from the address itself.

        Args:
            url: The video's http or https address.
            name: Name for the media source, e.g. "LaunchTrailer".

        Returns:
            JSON with the media source's path and what was shared or refused.
        """
        return json.dumps(sharing.create_video_source(url, name))

    @toolset_registry.tool_call
    @staticmethod
    def undo_transactions(count: int) -> str:
        """Undoes the agent's own most recent transactions. Multi-User broadcasts each
        undo, so other users see the change revert. Only affects this agent's history.

        Args:
            count: How many transactions to undo.

        Returns:
            JSON with how many undos succeeded.
        """
        undone = 0
        for _ in range(max(count, 0)):
            if not unreal.ToolsetLibrary.undo_transaction(False):
                break
            undone += 1
        return json.dumps({"undone": undone})
