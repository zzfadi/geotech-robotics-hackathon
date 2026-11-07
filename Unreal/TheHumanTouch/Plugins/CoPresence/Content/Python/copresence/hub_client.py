"""The editor's connection to the session hub on the host: one background thread keeps the
latest agent statuses and chat messages, and chat messages are posted without blocking
the editor. While the Agents tab shows the session, another keeps the hub's dashboard: who is
in the session and which machines can start agents. Nothing here touches unreal off the game
thread; callers read snapshots.
"""

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import unreal

HUB_PORT = 8790
CODE_HEADER = "X-CoPresence-Code"
MESSAGE_LIMIT = 500
RETRY_SECONDS = 5.0
# The dashboard is only asked for while something showed it this recently
DASHBOARD_IDLE_SECONDS = 5.0

_lock = threading.Lock()
_state = {"agents": {}, "messages": [], "received": 0.0, "online": False, "thread": None, "code": None}
_dashboard = {"reply": None, "wanted_at": 0.0, "thread": None, "code": "", "code_at": 0.0}
# Results of posts, handed back to the game thread
_post_results: "queue.Queue[tuple[bool, str]]" = queue.Queue()
_conversation_problems: "queue.Queue[str]" = queue.Queue()
_control_results: "queue.Queue[tuple[dict, str]]" = queue.Queue()


def hub_url() -> str:
    """The hub runs next to the Multi-User server, so editors find it at the server's address."""
    command_line = unreal.SystemLibrary.get_command_line()
    override = re.search(r"-COPRESENCEHUB=(\S+)", command_line)
    if override:
        return override.group(1).rstrip("/")
    server = re.search(r"-UDPMESSAGING_TRANSPORT_STATIC=([^:\s]+)", command_line)
    return f"http://{server.group(1) if server else '127.0.0.1'}:{HUB_PORT}"


def _settings_path() -> Path:
    return Path(unreal.Paths.project_saved_dir()) / "CoPresence" / "settings.json"


def session_code() -> str:
    """The code that lets this editor post to the chat: as typed into Agent Chat, or
    from the code file the hub writes on the host and the join scripts write elsewhere."""
    # The join script writes to the project, while -userdir redirects
    # project_saved_dir() into AgentUser. Prefer the managed connection over
    # stale manually entered settings and re-read it after session transitions.
    managed = Path(__file__).resolve().parents[5] / "Saved" / "CoPresenceLogs" / "hub_code"
    if managed.exists():
        code = managed.read_text().strip()
        if code:
            return code.upper()
    if _state["code"] is None:
        code_file = Path(unreal.Paths.project_saved_dir()) / "CoPresenceLogs" / "hub_code"
        settings = _settings_path()
        if code_file.exists():
            _state["code"] = code_file.read_text().strip()
        elif settings.exists():
            _state["code"] = json.loads(settings.read_text()).get("code", "")
        else:
            _state["code"] = ""
    return _state["code"].upper()


def set_session_code(code: str) -> None:
    _state["code"] = code.strip().upper()
    settings = _settings_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"code": _state["code"]}))


def display_name() -> str:
    info = unreal.MultiUserClientStatics.get_local_multi_user_client_info()
    return str(info.display_name) or "Someone"


def session_name() -> str:
    """The Multi-User session this editor joined, as the join scripts name it, or empty."""
    found = re.search(r"-CONCERTSESSION=(\S+)", unreal.SystemLibrary.get_command_line())
    return found.group(1).strip('"') if found else ""


def _poll(url: str) -> None:
    # Long-polls the hub, which answers as soon as anything changes
    version, last_message = -1, 0
    while True:
        try:
            with urllib.request.urlopen(f"{url}/api/state?after={last_message}&version={version}", timeout=40) as response:
                reply = json.load(response)
            version = reply["version"]
            with _lock:
                if reply["messages"]:
                    last_message = reply["messages"][-1]["id"]
                    _state["messages"] = (_state["messages"] + reply["messages"])[-MESSAGE_LIMIT:]
                _state["agents"] = reply["agents"]
                _state["received"] = time.monotonic()
                _state["online"] = True
        except Exception:
            # Hub not running or unreachable: drop old bubbles and try again shortly.
            # Messages stay, and only newer ones are fetched once it is back
            with _lock:
                _state["agents"] = {}
                _state["online"] = False
            version = -1
            time.sleep(RETRY_SECONDS)


def start() -> None:
    if _state["thread"] is None:
        _state["thread"] = threading.Thread(target=_poll, args=(hub_url(),), name="CoPresenceHub", daemon=True)
        _state["thread"].start()


def snapshot() -> tuple[dict, list, float, bool]:
    """Agent statuses, chat messages, seconds since the hub last answered, and whether it is reachable."""
    with _lock:
        return dict(_state["agents"]), list(_state["messages"]), time.monotonic() - _state["received"], _state["online"]


def take_annotation() -> dict | None:
    """The drawing the person is making on their editor, taken for the message they are about
    to send, or None. Needs the CoPresence Annotate plugin."""
    library = getattr(unreal, "CoPresenceAnnotationLibrary", None)
    if library is None or not library.has_annotation():
        return None
    taken = library.take_annotation()
    return json.loads(taken) if taken else None


def post_message(text: str, to: str, selection: list, annotation: dict | None = None, source: str = "") -> None:
    """Sends a chat message on a background thread; the outcome arrives through take_post_results.
    A drawing the person is making goes with it, however the message was written, so a
    message spoken while drawing carries the drawing too. A drawing made in VR says so in
    its source, so the agent looks at it from beside the person rather than from their head."""
    if annotation is None:
        annotation = take_annotation()
    payload = {"author": display_name(), "role": "human", "text": text, "to": to,
               "selection": selection, "code": session_code()}
    if source:
        payload["source"] = source
    if annotation is not None:
        # The pictures travel apart from what the drawing marks, which the hub lists in the chat
        payload["annotation_images"] = annotation.pop("images", {})
        payload["annotation"] = annotation
    # Everything that asks unreal happens here, on the game thread
    url = f"{hub_url()}/api/messages"

    def send() -> None:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            # A drawing's pictures can take a moment to upload
            urllib.request.urlopen(request, timeout=20 if annotation else 5).close()
            _post_results.put((True, ""))
        except urllib.error.HTTPError as error:
            _post_results.put((False, "Wrong session code" if error.code == 403 else f"Hub refused the message ({error.code})"))
        except Exception:
            _post_results.put((False, "Can't reach the session hub"))

    threading.Thread(target=send, name="CoPresenceChatPost", daemon=True).start()


def take_post_results() -> list[tuple[bool, str]]:
    results = []
    while not _post_results.empty():
        results.append(_post_results.get_nowait())
    return results


def post_conversation(agent: str, person: str) -> None:
    """Tells the hub a person is talking with an agent, which brings the agent over to wait by
    them between tasks; an empty person ends the conversation. The hub forgets a conversation
    nobody repeats within a few seconds, so callers say it again now and then."""
    payload = {"agent": agent, "person": person, "code": session_code()}
    url = f"{hub_url()}/api/conversation"

    def send() -> None:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except urllib.error.HTTPError as error:
            _conversation_problems.put(f"The session hub refused the conversation ({error.code}), so {agent} won't come over")
        except Exception:
            _conversation_problems.put(f"Can't reach the session hub, so {agent} won't come over")

    threading.Thread(target=send, name="CoPresenceConversation", daemon=True).start()


def take_conversation_problem() -> str | None:
    """The latest reason a conversation didn't reach the hub, or None."""
    problem = None
    while not _conversation_problems.empty():
        problem = _conversation_problems.get_nowait()
    return problem


# The session dashboard ---------------------------------------------------------------

def dashboard() -> dict | None:
    """The hub's dashboard: the people in the session, the agents with the machine that runs
    each, the machines that can start agents, and what was asked of them lately. None until
    the hub has answered. Calling this keeps it fresh in the background for a while."""
    now = time.monotonic()
    _dashboard["wanted_at"] = now
    if now - _dashboard["code_at"] >= RETRY_SECONDS:
        _dashboard["code"], _dashboard["code_at"] = session_code(), now
    if _dashboard["thread"] is None:
        _dashboard["thread"] = threading.Thread(target=_poll_dashboard, args=(hub_url(),), name="CoPresenceDashboard",
                                                daemon=True)
        _dashboard["thread"].start()
    return _dashboard["reply"]


def _poll_dashboard(url: str) -> None:
    # Long-polls like _poll, but rests while nothing shows the dashboard
    version = -1
    while True:
        if time.monotonic() - _dashboard["wanted_at"] > DASHBOARD_IDLE_SECONDS:
            version = -1
            time.sleep(0.5)
            continue
        request = urllib.request.Request(f"{url}/api/dashboard?version={version}",
                                         headers={CODE_HEADER: _dashboard["code"]})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                reply = json.load(response)
            version = reply["version"]
            _dashboard["reply"] = reply
        except Exception:
            _dashboard["reply"] = None
            version = -1
            time.sleep(RETRY_SECONDS)


def control(action: str, **fields) -> None:
    """Asks the hub to start or stop an agent through the station on a machine, as the
    dashboard does, on a background thread. take_control_results hands back what was asked
    with the hub's error, which is empty when the hub took it on."""
    asked = {"action": action, **fields}
    # Who asks: an agent someone starts is theirs, and only its owner shares it or keeps it private
    payload = {**asked, "person": display_name(), "code": session_code()}
    url = f"{hub_url()}/api/control"

    def send() -> None:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(request, timeout=10).close()
            _control_results.put((asked, ""))
        except urllib.error.HTTPError as error:
            try:
                reason = str(json.loads(error.read()).get("error") or "")
            except ValueError:
                reason = ""
            _control_results.put((asked, reason or f"The session hub refused it ({error.code})"))
        except Exception:
            _control_results.put((asked, "Can't reach the session hub"))

    threading.Thread(target=send, name="CoPresenceControl", daemon=True).start()


def take_control_results() -> list[tuple[dict, str]]:
    results = []
    while not _control_results.empty():
        results.append(_control_results.get_nowait())
    return results
