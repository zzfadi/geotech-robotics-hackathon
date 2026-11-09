"""Tells the CoPresence agent tools whether this editor is in VR Mode (editor Python; local, untracked).

In VR Mode, EditorAppToolset.CaptureViewport fails `Bitmap.Num() == ViewRect.Area()` in
UnrealClient.cpp (UE 5.8.3) and the editor crashes (2026-11-08 15:19, an agent's `look` during
a VR session). Tools/copresence_mcp.py asks this toolset before every viewport image and sends a
placeholder instead while VR Mode is on. The check is CoPresenceScouting's UCoPresenceVRTalk::IsInVRMode,
which only tests a weak pointer to VR Mode's avatar. Never use
VPScoutingSubsystem.get_active_editor_vr_controllers() for this: it crashed the editor in VR Mode.

Saved/XR/join_vr.py registers it once the editor's MCP answers:
    import sys; sys.path.insert(0, '<project>/Saved/XR'); import editor_vr_state
"""
import json

import unreal
import toolset_registry


def vr_mode_active() -> bool:
    talk = getattr(unreal, "CoPresenceVRTalk", None)
    return bool(talk is not None and talk.is_in_vr_mode())


@unreal.uclass()
class EditorVRState(unreal.ToolsetDefinition):
    """Whether this editor is in VR Mode, where viewport captures crash it."""

    @toolset_registry.tool_call
    @staticmethod
    def vr_mode_active() -> str:
        """Whether someone is in VR Mode in this editor right now.

        Returns:
            JSON {"vr_mode": true or false}.
        """
        return json.dumps({"vr_mode": vr_mode_active()})


def register():
    """Make the toolset visible to the agent tools. Reload-safe."""
    from toolset_registry.registration import Registration
    previous = getattr(unreal, "_editor_vr_state_registration", None)
    if previous is not None:
        try:
            previous.unregister()
        except Exception:
            pass
    registration = Registration([EditorVRState])
    ok = registration.register()
    unreal._editor_vr_state_registration = registration
    return ok


register()
