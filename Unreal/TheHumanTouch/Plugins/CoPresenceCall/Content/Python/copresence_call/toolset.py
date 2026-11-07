import json

import unreal

import toolset_registry
from copresence_call import call_camera, spectator_camera


@unreal.uclass()
class CallCameraTools(unreal.ToolsetDefinition):
    """The camera that shows an agent in video calls: the view people get when they follow
    the agent in the Agents tab, sent out of the agent's editor as an NDI source."""

    @toolset_registry.tool_call
    @staticmethod
    def start_call_camera(source_name: str, width: int, height: int, frames_per_second: int, view_mode: str = "ghost") -> str:
        """Starts filming the agent from behind and above its ghost and sends the picture
        out as an NDI source. Starting it again with the same settings keeps it running.

        Args:
            source_name: Name of the NDI source, e.g. "CoPresence Lukas Claude".
            width: Picture width in pixels, e.g. 1280.
            height: Picture height in pixels, e.g. 720.
            frames_per_second: Pictures sent per second, e.g. 60.
            view_mode: ghost follows the agent; viewport films its editor camera,
                useful for a framed MetaHuman avatar. editor_viewport captures the actual
                rendered editor viewport for simulation recording without a second scene render.

        Returns:
            JSON with the camera's state.
        """
        return json.dumps(call_camera.start(source_name, width, height, frames_per_second, view_mode))

    @toolset_registry.tool_call
    @staticmethod
    def keep_call_camera() -> str:
        """Keeps the call camera running. It stops by itself when nothing has kept it for
        20 seconds, so a call bot that went away doesn't leave it filming.

        Returns:
            JSON with the camera's state.
        """
        return json.dumps(call_camera.keep())

    @toolset_registry.tool_call
    @staticmethod
    def stop_call_camera() -> str:
        """Stops the call camera and takes its NDI source off the network.

        Returns:
            JSON with the camera's state.
        """
        return json.dumps(call_camera.stop())

    @toolset_registry.tool_call
    @staticmethod
    def spectator_status() -> str:
        """How the spectator's camera is doing, in the spectator's editor: whether it films,
        its NDI source, and whom it follows.

        Returns:
            JSON with "running", "source", "target", "framing" and "found".
        """
        return json.dumps(spectator_camera.status())

    @toolset_registry.tool_call
    @staticmethod
    def start_spectator_camera() -> str:
        """Starts the spectator's camera if it isn't running yet. Only the spectator's editor
        (started by Tools/copresence_spectator.py) films for the spectator.

        Returns:
            JSON with the camera's state.
        """
        if not spectator_camera.is_spectator():
            raise ValueError("Only the spectator's editor films for the spectator")
        return json.dumps(spectator_camera.start())

    @toolset_registry.tool_call
    @staticmethod
    def voice_face_status() -> str:
        """Whether this level has the MetaHuman avatar whose mouth the agent's voice moves
        in calls, and how its idle animation is doing.

        Returns:
            JSON with "available" and the idle animation's state.
        """
        from copresence import voice_face
        return json.dumps(voice_face.status())

    @toolset_registry.tool_call
    @staticmethod
    def voice_face_frames(frames_json: str) -> str:
        """Moves the avatar's mouth with the agent's voice: mouth shapes 60 a second, the
        first at a wall-clock time on this machine, then a shut mouth.

        Args:
            frames_json: JSON {"start": time.time() when the first shape should show,
                "shapes": [[jaw, funnel, lips_together, corner_pull], ...], each 0 to 1}. The
                time travels as JSON because tool numbers are 32-bit, too coarse for a clock.

        Returns:
            JSON with "moved" and how many shapes were in time.
        """
        from copresence import voice_face
        frames = json.loads(frames_json)
        return json.dumps(voice_face.frames(float(frames["start"]), frames["shapes"]))

    @toolset_registry.tool_call
    @staticmethod
    def voice_face_stop() -> str:
        """Shuts the avatar's mouth now, when the agent stops talking mid-sentence.

        Returns:
            JSON with "stopped".
        """
        from copresence import voice_face
        return json.dumps(voice_face.stop())

    @toolset_registry.tool_call
    @staticmethod
    def release_avatar() -> str:
        """Lets go of the avatar's animation (shuts its mouth, closes its sequence), so the
        editor can quit cleanly. Run before stopping an agent editor that had a call.

        Returns:
            JSON with "released".
        """
        from copresence import voice_face
        return json.dumps(voice_face.release())
