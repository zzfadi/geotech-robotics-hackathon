"""Agent Chat: a dockable editor tab with the session chat, where people talk to the agents
and give them orders. Address an agent by picking it under To, or by writing @ and its
name in the message ("@Codex add a lamp here"); messages to Everyone reach every agent.
What you have selected in the level is sent along, so agents know what "this" means.

Built at runtime into the Root box of the EUW_AgentChat widget, and kept up to date from
the session hub (see hub_client). Agents' own state lives in the Agents widget
(agents_hud).
"""

import time

import unreal

from copresence import agent_editor, fab_requests, ghost_face, ghost_labels, hub_client, ui_widgets
from copresence.ui_widgets import ERROR, MUTED, PERSON, SURFACE, TEXT, add

# The warm red of annotation mode, so a message with a drawing reads as one
ACCENT = (0.90, 0.19, 0.05)

PANEL_ASSET = "/CoPresence/EUW_AgentChat"
REFRESH_SECONDS = 0.25
EVERYONE = ""
SELECTION_LIMIT = 20

_state = {"callback": None, "elapsed": 0.0, "target": EVERYONE, "notice": ("", MUTED), "last_warning": ""}


def session_participants() -> set:
    """Display names of everyone else in the Multi-User session."""
    infos = unreal.MultiUserClientStatics.get_remote_multi_user_client_infos()
    # Python returns the out array, sometimes paired with the bool result
    if isinstance(infos, tuple):
        infos = infos[-1]
    return {str(info.display_name) for info in infos or []}


def agent_names(agents: dict, seconds_since_poll: float) -> list:
    """Every agent in the session, whoever brought it: those with a ghost in the level,
    those in the session that the hub knows as agents, and those reporting lately."""
    ghosts = {labels.name for labels in ghost_labels._ghosts.values() if labels.name}
    participants = session_participants()
    if agent_editor.is_agent_editor():
        participants.add(hub_client.display_name())
    present = ghosts | (participants & set(agents))
    names = []
    for name in sorted(set(agents) | ghosts):
        status = agents.get(name)
        age = (status or {}).get("age", 0.0) + seconds_since_poll
        if name in present or ghost_labels.effective_state(status, age) != "away":
            names.append(name)
    return names


def agent_color(name: str, appearance: dict | None) -> tuple:
    glow = ghost_face.glow_color(name, appearance)
    linear = ghost_face.linear_color(glow, glow)
    return (linear.r, linear.g, linear.b)


def mentioned_agent(text: str, names: list) -> str | None:
    """The agent a message addresses with @name, preferring the longest matching name."""
    lowered = text.lower()
    for name in sorted(names, key=len, reverse=True):
        if f"@{name.lower()}" in lowered:
            return name
    return None


def target() -> str:
    """Who Agent Chat addresses: an agent's name, or empty for every agent."""
    return _state["target"]


def selection() -> list:
    """What is selected in the level, which messages carry so agents know what "this" means."""
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()
    selection = []
    for actor in actors[:SELECTION_LIMIT]:
        location = actor.get_actor_location()
        selection.append({"label": actor.get_actor_label(), "path": actor.get_path_name(),
                          "location": [round(location.x), round(location.y), round(location.z)]})
    return selection


def _summary(labels: list) -> str:
    return ", ".join(labels[:3]) + (f" +{len(labels) - 3}" if len(labels) > 3 else "")


class _ChatPanel:
    """The chat tab's widgets, built once per opened tab and updated from hub snapshots."""

    def __init__(self, root: unreal.VerticalBox):
        self.ui = ui_widgets.Widgets(root.get_outer())
        self.chips = {}
        self.chip_names = None
        self.names = []
        self.last_message = 0
        root.clear_children()

        page = self.ui.make(unreal.VerticalBox)
        add(root, self.ui.border(ui_widgets.BACKGROUND, page), fill=True)

        targets = self.ui.make(unreal.HorizontalBox)
        add(page, targets, padding=(12, 8, 12, 8))
        add(targets, self.ui.text("TO", size=8, rgb=MUTED, bold=True), padding=(0, 0, 10, 0))
        self.chip_box = self.ui.make(unreal.WrapBox)
        add(targets, self.chip_box, fill=True)
        add(page, self.ui.line())

        self.chat = self.ui.make(unreal.ScrollBox)
        add(page, self.chat, fill=True, padding=(0, 4, 0, 4))
        add(self.chat, self.ui.text("Messages to the agents show up here. Write @ and a name to ask one of them.",
                                    rgb=MUTED, wrap=True), padding=(12, 4, 12, 4))

        add(page, self.ui.line())
        composer = self.ui.make(unreal.VerticalBox)
        add(page, self.ui.border(SURFACE, composer, padding=(10, 8, 10, 8)))

        self.message_row = self.ui.make(unreal.HorizontalBox)
        add(composer, self.message_row)
        self.message_box = self.ui.make(unreal.EditableTextBox)
        ui_widgets.style_text_box(self.message_box)
        self.message_box.on_text_committed.add_callable(self._on_message_committed)
        add(self.message_row, self.message_box, fill=True)
        send = self.ui.button(self.ui.text("Send", bold=True, rgb=PERSON), self._send, selected=True, padding=(12, 6, 12, 6))
        add(self.message_row, send, padding=(6, 0, 0, 0))

        self.code_row = self.ui.make(unreal.HorizontalBox)
        add(composer, self.code_row)
        self.code_box = self.ui.make(unreal.EditableTextBox)
        ui_widgets.style_text_box(self.code_box)
        self.code_box.set_hint_text(unreal.Text("Session code from the host"))
        self.code_box.on_text_committed.add_callable(lambda text, method: self._save_code())
        add(self.code_row, self.code_box, fill=True)
        join = self.ui.button(self.ui.text("Join chat", bold=True, rgb=PERSON), self._save_code, selected=True,
                              padding=(12, 6, 12, 6))
        add(self.code_row, join, padding=(6, 0, 0, 0))

        self.notice = self.ui.text("", size=9, rgb=MUTED, wrap=True)
        add(composer, self.notice, padding=(2, 6, 2, 0))
        self._show_code_prompt(not hub_client.session_code())

    # Who to address

    def update_targets(self, agents: dict, seconds_since_poll: float) -> None:
        self.names = agent_names(agents, seconds_since_poll)
        chip_names = [EVERYONE] + self.names
        if chip_names != self.chip_names:
            self.chip_names = chip_names
            self.chip_box.clear_children()
            self.chips = {}
            for name in chip_names:
                appearance = (agents.get(name) or {}).get("appearance")
                label = self.ui.make(unreal.HorizontalBox)
                if name:
                    add(label, self.ui.text("\u25CF", size=8, rgb=agent_color(name, appearance)), padding=(0, 0, 5, 0))
                add(label, self.ui.text(name or "Everyone", bold=name == _state["target"], rgb=PERSON))
                chip = self.ui.button(label, self._chooser(name), selected=name == _state["target"], padding=(9, 4, 9, 4))
                add(self.chip_box, chip, padding=(0, 0, 6, 0))
                self.chips[name] = chip
            if _state["target"] not in self.chips:
                self._choose(EVERYONE)
        self._update_hint()

    def _chooser(self, name: str):
        def choose_and_focus():
            self._choose(name)
            if name:
                from copresence import agents_hud
                agents_hud._toggle_follow(name)
        return choose_and_focus

    def _choose(self, name: str) -> None:
        _state["target"] = name
        for chip_name, chip in self.chips.items():
            chip.set_style(ui_widgets.flat_style(chip_name == name, (9, 4, 9, 4)))
        self._update_hint()

    def _update_hint(self) -> None:
        target = _state["target"]
        self.message_box.set_hint_text(unreal.Text(f"Message {target}..." if target else "Message every agent..."))

    # Chat

    def update_chat(self, messages: list, agents: dict) -> None:
        new = [message for message in messages if message["id"] > self.last_message]
        if not new:
            return
        at_end = self.chat.get_scroll_offset() >= self.chat.get_scroll_offset_of_end() - 4.0
        if self.last_message == 0:
            self.chat.clear_children()
        me = hub_client.display_name()
        for message in new:
            self.last_message = message["id"]
            add(self.chat, self._message_widget(message, agents), padding=(12, 5, 12, 5))
        if at_end or any(message["author"] == me for message in new):
            self.chat.scroll_to_end()

    def _message_widget(self, message: dict, agents: dict):
        block = self.ui.make(unreal.VerticalBox)
        header = self.ui.make(unreal.HorizontalBox)
        is_agent = message["role"] == "agent"
        appearance = (agents.get(message["author"]) or {}).get("appearance") if is_agent else None
        author_color = agent_color(message["author"], appearance) if is_agent else PERSON
        add(header, self.ui.text(message["author"], bold=True, rgb=author_color))
        if message["to"]:
            add(header, self.ui.text(f"  to {message['to']}", rgb=MUTED))
        add(header, self.ui.make(unreal.Spacer), fill=True)
        add(header, self.ui.text(time.strftime("%H:%M", time.localtime(message["time"])), size=8, rgb=MUTED))
        add(block, header)
        # An agent asking for a Fab listing: people add it from their own Fab tab, and the
        # button stands in for the link
        listing_id = fab_requests.listing_in(message["text"]) if is_agent and not agent_editor.is_agent_editor() else None
        text = fab_requests.without_listing_link(message["text"]) if listing_id else message["text"]
        add(block, self.ui.text(text, rgb=TEXT, wrap=True), padding=(0, 2, 0, 0))
        labels = [str(item.get("label")) for item in message.get("selection") or []]
        if labels:
            add(block, self.ui.text(f"with {_summary(labels)}", size=9, rgb=MUTED, wrap=True), padding=(0, 2, 0, 0))
        annotation = message.get("annotation")
        if annotation:
            marks = [str(mark.get("label")) for mark in annotation.get("marks") or [] if mark.get("label")]
            drawing = f"drew on {_summary(marks)}" if marks else "with a drawing"
            add(block, self.ui.text(drawing, size=9, rgb=ACCENT, wrap=True), padding=(0, 2, 0, 0))
        if listing_id:
            row = self.ui.make(unreal.HorizontalBox)
            add(row, self.ui.button(self.ui.text("Open in Fab", bold=True, rgb=PERSON),
                                    self._fab_opener(listing_id, message), selected=True, padding=(10, 4, 10, 4)))
            add(block, row, padding=(0, 6, 0, 0))
        return block

    def _fab_opener(self, listing_id: str, message: dict):
        def open_in_fab() -> None:
            try:
                notice = fab_requests.open_listing(listing_id, message["author"], fab_requests.title_in(message["text"]))
                _state["notice"] = (notice, MUTED)
            except Exception as error:
                _state["notice"] = (f"Couldn't open Fab: {error}", ERROR)
        return open_in_fab

    # Composer

    def _on_message_committed(self, text, commit_method) -> None:
        if commit_method == unreal.TextCommit.ON_ENTER:
            self._send()

    def _send(self) -> None:
        text = str(self.message_box.get_text()).strip()
        if not text:
            return
        if not hub_client.session_code():
            self._show_code_prompt(True)
            return
        # Writing @name addresses that agent, whatever is picked under To
        to = mentioned_agent(text, self.names) or _state["target"]
        hub_client.post_message(text, to, selection())
        self.message_box.set_text(unreal.Text(""))
        _state["notice"] = ("Sending...", MUTED)

    def _save_code(self) -> None:
        code = str(self.code_box.get_text()).strip()
        if len(code) < 4:
            _state["notice"] = ("Ask the host for the 6-letter session code.", ERROR)
            return
        hub_client.set_session_code(code)
        self.code_box.set_text(unreal.Text(""))
        _state["notice"] = ("", MUTED)
        self._show_code_prompt(False)

    def _show_code_prompt(self, show: bool) -> None:
        visible, hidden = unreal.SlateVisibility.VISIBLE, unreal.SlateVisibility.COLLAPSED
        self.code_row.set_visibility(visible if show else hidden)
        self.message_row.set_visibility(hidden if show else visible)

    def update_notice(self, online: bool) -> None:
        for succeeded, problem in hub_client.take_post_results():
            _state["notice"] = ("", MUTED) if succeeded else (problem, ERROR)
            if problem == "Wrong session code":
                self._show_code_prompt(True)
        text, rgb = _state["notice"]
        if not text:
            labels = [item["label"] for item in selection()]
            if not online:
                text, rgb = "Can't reach the session hub. Is the host running it?", ERROR
            elif labels:
                text = "Sent with your selection: " + _summary(labels)
            else:
                text = "Select actors to point the agents at them."
        if str(self.notice.get_text()) != text:
            self.notice.set_text(unreal.Text(text))
            self.notice.set_color_and_opacity(ui_widgets.slate(rgb))


tab = ui_widgets.EditorTab(PANEL_ASSET, _ChatPanel)


def open_panel() -> None:
    """Opens Agent Chat, or brings it to the front."""
    tab.open()


def _tick(delta_seconds: float) -> None:
    _state["elapsed"] += delta_seconds
    if _state["elapsed"] < REFRESH_SECONDS:
        return
    _state["elapsed"] = 0.0
    try:
        panel = tab.current()
        if panel is None:
            return
        agents, messages, seconds_since_poll, online = hub_client.snapshot()
        panel.update_targets(agents, seconds_since_poll)
        panel.update_chat(messages, agents)
        panel.update_notice(online)
    except Exception as error:
        tab.reset()
        if str(error) != _state["last_warning"]:
            _state["last_warning"] = str(error)
            unreal.log_warning(f"CoPresence Agent Chat: {error}")


def start() -> None:
    if _state["callback"] is not None:
        return
    hub_client.start()
    ui_widgets.register_menu_entry(
        "CoPresenceAgentChat", "Agent Chat", "Talk to the agents in the session and give them orders",
        "from copresence import agents_panel; agents_panel.open_panel()", "Icons.Comment")
    _state["callback"] = unreal.register_slate_post_tick_callback(_tick)
