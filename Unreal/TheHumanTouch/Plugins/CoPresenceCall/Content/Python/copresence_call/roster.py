"""Who is in the session, as this editor sees it: everyone's name, and whether they are an agent,
a person in VR or a person at a desktop editor. Agents' editors and the spectator tell the
session hub every few seconds, and the session dashboard shows it; the hub itself isn't in the
session, so it can't see who is.

Multi-User shows each person and agent in this editor's level as an avatar, whose class says
how they joined: the agents' robot ghost, a headset with hands, or a desktop editor's camera.
Someone in another level has no avatar here, so how they are in the session isn't known.
"""

import json
import threading
import time
import urllib.request

import unreal

from copresence import hub_client, tracking

GHOST_CLASS_PATH = "/CoPresence/BP_AgentGhost.BP_AgentGhost_C"
CHECK_SECONDS = 2.0
# Sent again at least this often, so the hub knows this editor still sees them
REPORT_SECONDS = 8.0
POST_TIMEOUT_SECONDS = 5.0

_state = {"callback": None, "elapsed": 0.0, "sent": None, "sent_at": 0.0, "posting": False, "last_warning": ""}


def connected() -> bool:
    status = unreal.MultiUserClientStatics.get_multi_user_connection_status_detail()
    return status == unreal.MultiUserConnectionStatus.CONNECTED


def remote_clients() -> dict:
    """Everyone else in the session: display name -> client endpoint id."""
    infos = unreal.MultiUserClientStatics.get_remote_multi_user_client_infos()
    # Python returns the out array, sometimes paired with the bool result
    if isinstance(infos, tuple):
        infos = infos[-1]
    return {str(info.display_name): info.client_endpoint_id for info in infos or [] if str(info.display_name)}


def presence_name(actor: unreal.Actor) -> str:
    """The display name Multi-User wrote on an avatar's name label."""
    try:
        label = actor.get_editor_property("presence_text_component")
    except Exception:
        label = None
    if label is None:
        label = next((component for component in actor.get_components_by_class(unreal.TextRenderComponent)
                      if component.get_name() == "Name"), None)
    return str(label.get_editor_property("text")) if label is not None else ""


def presence_actors() -> dict:
    """The avatar Multi-User shows for each person and agent in this editor's level, by name."""
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    presence_class = getattr(unreal, "ConcertClientPresenceActor", None)
    if world is None or presence_class is None:
        return {}
    actors = {}
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world, presence_class):
        name = presence_name(actor)
        if name:
            actors[name] = actor
    return actors


def kind_of(actor: unreal.Actor) -> str:
    """How someone joined, from the class of the avatar Multi-User shows for them."""
    ghost_class = unreal.load_class(None, GHOST_CLASS_PATH)
    if ghost_class is not None and unreal.MathLibrary.class_is_child_of(actor.get_class(), ghost_class):
        return "agent"
    vr_class = getattr(unreal, "ConcertClientVRPresenceActor", None)
    if vr_class is not None and isinstance(actor, vr_class):
        return "vr"
    return "desktop"


def participants(own_kind: str) -> list[dict]:
    """Everyone in the session, this editor included as own_kind, or nobody while it isn't in it."""
    if not connected():
        return []
    avatars = presence_actors()
    listed = [{"name": name, "kind": kind_of(avatars[name]) if name in avatars else ""}
              for name in remote_clients()]
    listed.append({"name": tracking.agent_name(), "kind": own_kind})
    return sorted(listed, key=lambda participant: participant["name"])


def _post(url: str, payload: dict) -> None:
    request = urllib.request.Request(f"{url}/api/roster", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS).close()
    except Exception:
        # The hub is down or unreachable; the next check sends it again
        _state["sent"] = None
    finally:
        _state["posting"] = False


def _tick(own_kind: str, delta_seconds: float) -> None:
    _state["elapsed"] += delta_seconds
    if _state["elapsed"] < CHECK_SECONDS or _state["posting"]:
        return
    _state["elapsed"] = 0.0
    try:
        listed = participants(own_kind)
        now = time.monotonic()
        if listed == _state["sent"] and now - _state["sent_at"] < REPORT_SECONDS:
            return
        _state["sent"], _state["sent_at"], _state["posting"] = listed, now, True
        # Everything that asks unreal happens here, on the game thread
        payload = {"reporter": tracking.agent_name(), "participants": listed, "code": hub_client.session_code()}
        threading.Thread(target=_post, args=(hub_client.hub_url(), payload), name="CoPresenceRoster", daemon=True).start()
    except Exception as error:
        if str(error) != _state["last_warning"]:
            _state["last_warning"] = str(error)
            unreal.log_warning(f"CoPresence roster: {error}")


def start(own_kind: str) -> None:
    """Keeps telling the hub who is in the session; own_kind is how this editor is in it."""
    if _state["callback"] is None:
        _state["callback"] = unreal.register_slate_post_tick_callback(lambda delta_seconds: _tick(own_kind, delta_seconds))
