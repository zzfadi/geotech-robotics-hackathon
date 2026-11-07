"""The Agents widget: a tab next to the Details panel. + Agent at the top opens a small form, as
the session dashboard has: the new agent's name (after you to start with), what it thinks with,
and the machine it runs on, from a dropdown of the machines with room, then Add. Below it is
every agent in the session, its face as it is emoting right now, what is in its speech bubble or
what it did last, and the machine it runs on. An agent in a video call is marked Live in Meet.

Each agent's buttons are at the end of its row, after its name. Call rings the agent and opens a
call with it out loud: it hears you until you hang up, or until the call has been quiet for a
while. During a call, a bar above the agents says with whom and how it goes, with Mute mic (the
agent hears quiet, you still hear it) and Hang up. An agent someone brought in with + Agent is
private to them: only they can call it and hear it, and everyone else sees it washed out, marked
"Private to" its owner, while its owner sees it in full. Its owner can make it public, and then
everyone can call it and hears what it says to anyone. Mute on an agent you hear stops you
hearing it, and a call with it goes on. - Agent takes an agent that a station started out of the
session and shuts it down, after asking once more. Under the agents are the people in the
session, as the session dashboard lists them; clicking one at the desk follows them too.

Built at runtime into the Root box of the EUW_AgentStatus widget. The faces are the same
eyes as on the ghosts, drawn by M_AgentIcon and animated by ghost_face.

In VR, the Agents tool in Virtual Scouting's menu (the CoPresenceScouting plugin) shows the very
same rows and buttons, only bigger. Clicking an agent at the desk flies the camera over its
shoulder and follows it around, until you click again or move the camera yourself; tapping one
in VR picks it instead: it comes over and waits by you between tasks. Clicking the left stick
calls the agent you picked, or the only agent around, and clicking it again hangs up. With the
Agents tool in your hand, the trigger draws on the level, and the tab sends the drawing to the
agent you picked, or the one you are in a call with, as the VR game does. When an agent asks you
for something from Fab, the tab in VR shows what it asks for with Decline and Add to Project, as
the popup does at the desk, whose window the headset doesn't show.
"""

import json
import re
import time
from pathlib import Path

import unreal

from copresence import (agent_editor, agents_panel, fab_approvals, follow_camera, ghost_face, ghost_labels, hub_client,
                        local_station, tracking, ui_widgets, voice_link)
from copresence.ui_widgets import ERROR, MUTED, PERSON, TEXT, add

PANEL_ASSET = "/CoPresence/EUW_AgentStatus"
ICON_MATERIAL_PATH = "/CoPresence/M_AgentIcon.M_AgentIcon"
REFRESH_SECONDS = 0.25
ICON_SIZE = 46.0
# Multi-User replaces avatars now and then; an agent missing for longer has left
LOST_SECONDS = 3.0
# A hitch longer than this moves the camera only this much, rather than jumping
MAX_STEP_SECONDS = 0.25
# Moving the camera more than this since the last follow step means the person took over
TAKEOVER_DISTANCE = 4.0
TAKEOVER_DEGREES = 1.0
# Virtual Scouting draws its menu at 0.4 mm a pixel, held about 40 cm away, so rows in VR are bigger,
# though not so big that a few agents and the machines no longer fit without scrolling
VR_SCALE = 1.7
VR_WIDTH = 640.0
# A scroll bar wide enough to grab with the laser in VR
VR_SCROLLBAR = 14.0
# Changing who hears an agent shows at once, and the hub's word on it follows within this long
AUDIENCE_PENDING_SECONDS = 5.0
# The row of an agent private to someone else is washed out, as its ghost in the level is
PRIVATE_OPACITY = 0.55
# Hang up is red, as in video calls
HANG_UP = (0.42, 0.03, 0.02)
HANG_UP_HOVER = (0.58, 0.05, 0.03)
HANG_UP_PRESSED = (0.30, 0.02, 0.01)
# The most agents one call holds, as the voice helper has it
MAX_CALL_AGENTS = 8
# Where people are, as the session dashboard says it
PERSON_PLACES = {"vr": "In VR", "desktop": "In the editor"}
# The height of the form's fields and buttons, so they line up
FIELD_HEIGHT = 28.0
# The hub forgets a conversation after six seconds without hearing of it
CONVERSATION_REPEAT_SECONDS = 2.0
# How long the tab says the drawing was sent, or why it wasn't
DRAWING_NOTE_SECONDS = 6.0
# A new agent shows as starting until it is in the session, for at most this long
STARTING_SECONDS = 300.0
# A start that failed keeps its row this long, saying why, so a failure after a long wait isn't missed
FAILED_START_SECONDS = 30.0
# - Agent asks once more before it removes an agent, and the question stands this long
STOP_CONFIRM_SECONDS = 4.0
# How long the tab says what happened to + Agent or Stop
CONTROL_NOTE_SECONDS = 8.0
# What a new agent can think with: the label in the Model dropdown, the brain, the model the brain
# is told to use, and how hard it reasons to start with, which the Reasoning dropdown then shows.
# Each brain's first one is what a new agent picks unless someone picks another: Opus for Claude
# Code, and on a machine with only Codex, GPT-6 Astra. Codex models start at their own defaults
MODELS = (("Claude Opus 5.5", "claude", "claude-opus-5-5", "high"),
          ("Claude Fable 5.1", "claude", "claude-fable-5-1", "high"),
          ("Claude Sonnet 5.5", "claude", "claude-sonnet-5-5", "high"),
          ("Claude Haiku 5.5", "claude", "claude-haiku-5-5", "high"),
          ("Codex GPT-6 Astra", "codex", "gpt-6-astra", "low"),
          ("Codex GPT-5.6 Sol", "codex", "gpt-5.6-sol", "low"),
          ("Codex GPT-5.6 Terra", "codex", "gpt-5.6-terra", "medium"),
          ("Codex GPT-5.6 Luna", "codex", "gpt-5.6-luna", "medium"))
# How hard a new agent reasons, by the label in the Reasoning dropdown, always sent to its brain so
# what the dropdown says is what it gets
EFFORT_CHOICES = {"Low": "low", "Medium": "medium", "High": "high", "Extra high": "xhigh", "Max": "max"}
# The machine's own words for why a brain can't think there, said in full in the form
BRAIN_PROBLEMS = {"not installed": "isn't installed on", "not signed in": "isn't signed in on",
                  "not answering": "didn't answer on"}
# As the hub and the stations take them: names that are safe in the join scripts and config files
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}")

_state = {"callback": None, "elapsed": 0.0, "last_warning": "", "following": None, "last_pose": None,
          "missing_since": None, "last_step": None, "talking_with": None, "conversation_at": 0.0,
          "sending_drawing": None, "drawing_note": "", "drawing_note_until": 0.0,
          # The agents in the session as of the last refresh, and whom the voice helper was last told about
          "names": [], "voice_sent": None,
          # The agents this person is in a call with, the first called first, the public agents
          # they muted, and whether the VR talk button was down last frame, so a click on it is seen once
          "call": [], "muted_agents": set(), "stick_down": False,
          # Whether this person muted their microphone in the call; the agent hears quiet
          "mic_muted": False,
          # Each agent's owner and whether it is public, as the hub last said: name -> (owner, public)
          "audiences": {},
          # Who-hears changes asked for and not yet confirmed by the hub: name -> (public, when)
          "audience_asked": {},
          # Agents asked for with + Agent that aren't in the session yet: name -> (machine, when)
          "starting": {}, "stop_asked": ("", 0.0), "control_note": "", "control_note_until": 0.0,
          # The machine picked for + Agent, by name; None picks the one with the most room
          "machine": None,
          # Starts that failed, kept a while to say why: name -> (machine, why, until when)
          "start_failed": {},
          # An agent added on this machine while it was off, which goes in once its station is up
          "pending_add": None,
          # The call's live captions as last shown: (agent, what you said, what it said)
          "captions": ("", "", "")}
# The Agents tabs showing in Virtual Scouting's VR menu, each with its rows
_vr_panels = {}
_rig = follow_camera.FollowRig()
# Agents' editors and the spectator talk to nobody, so they have no voice
_COMMAND_LINE = unreal.SystemLibrary.get_command_line().upper()
_DESKTOP_VOICE_OPT_IN = (Path(unreal.Paths.project_saved_dir()) / "CoPresence" / "desktop_voice_enabled").is_file()
_PERSON_EDITOR = ("-COPRESENCEAGENT" not in _COMMAND_LINE or _DESKTOP_VOICE_OPT_IN) and "-COPRESENCESPECTATOR" not in _COMMAND_LINE


def _avatar(name: str):
    """An agent's avatar in the level: its robot ghost, or the plain Multi-User avatar
    of an agent whose editor wasn't set up for the ghost."""
    if name == hub_client.display_name():
        from copresence import local_presence
        if ghost_labels.is_alive(local_presence.actor): return local_presence.actor
    for labels in ghost_labels._ghosts.values():
        if labels.name == name and ghost_labels.is_alive(labels.ghost):
            return labels.ghost
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.ConcertClientPresenceActor):
        label = actor.get_component_by_class(unreal.TextRenderComponent)
        if label is not None and str(label.get_editor_property("text")) == name:
            return actor
    return None


class _StatusPanel:
    """The Agents tab's widgets: + Agent and its form, then one row per agent with an animated face
    and its buttons. VR shows the same rows and buttons, only bigger;
    tapping an agent there picks it rather than following it."""

    def __init__(self, root: unreal.VerticalBox, vr: bool = False):
        self.vr = vr
        self.scale = VR_SCALE if vr else 1.0
        self.ui = ui_widgets.Widgets(root.get_outer())
        self.rows = {}
        self.row_names = None
        root.clear_children()
        page = self.ui.make(unreal.VerticalBox)
        body = self.ui.border(ui_widgets.BACKGROUND, page)
        # The VR menu takes the size of its tabs, so this one needs a width to wrap its text at
        add(root, self.ui.sized(body, VR_WIDTH, None) if vr else body, fill=True)
        self._build_session_bar(page)
        self._build_call_bar(page)
        self.fab_card = None
        if vr:
            self._build_fab_card(page)
        # Only shown when voice can't work on this machine
        self.voice_text = self.ui.text("", size=self._size(9), rgb=ERROR, wrap=True)
        self.voice_bar = self.ui.border(ui_widgets.SURFACE, self.voice_text, padding=self._space(12, 6, 12, 6))
        self.voice_bar.set_visibility(unreal.SlateVisibility.COLLAPSED)
        self.voice_shown = None
        add(page, self.voice_bar)
        self.list = self.ui.make(unreal.VerticalBox)
        content = self.ui.make(unreal.VerticalBox)
        add(content, self.list)
        self._build_people(content)
        scroll = self.ui.make(unreal.ScrollBox)
        if vr:
            scroll.set_scrollbar_thickness(unreal.Vector2D(VR_SCROLLBAR * self.scale, VR_SCROLLBAR * self.scale))
            scroll.set_always_show_scrollbar(True)
        add(scroll, content)
        add(page, scroll, fill=True, padding=self._space(0, 4, 0, 4))
        self.empty = self.ui.text("No agents in the session yet.", size=self._size(10), rgb=MUTED)
        add(self.list, self.empty, padding=self._space(12, 8, 12, 8))
        # The call's live captions where nothing else shows them: in VR without the captions below
        # the view, and at a desk without the caption plugin
        self.caption_bar = None
        self.captions_shown = None
        captions_shown_elsewhere = getattr(unreal, "CoPresenceVRCaptions" if vr else "CoPresenceCaptionLibrary", None)
        if captions_shown_elsewhere is None:
            self._build_caption_bar(page)
        if vr:
            self._build_drawing_bar(page)

    def _build_session_bar(self, page: unreal.VerticalBox) -> None:
        """The Agents heading with + Agent, which opens the form for a new agent, as the session
        dashboard has it: its name, what it thinks with and the machine it runs on."""
        bar = self.ui.make(unreal.VerticalBox)
        head = self.ui.make(unreal.HorizontalBox)
        add(head, self.ui.text("Agents", size=self._size(10), bold=True, rgb=PERSON))
        self.count_text = self.ui.text("", size=self._size(9), rgb=MUTED)
        add(head, self.count_text, padding=self._space(6, 1, 0, 0))
        add(head, self.ui.make(unreal.Spacer), fill=True)
        add_style = ui_widgets.button_style(ui_widgets.PRIMARY, ui_widgets.PRIMARY_HOVER, ui_widgets.PRIMARY_PRESSED,
                                            ui_widgets.SURFACE, self._space(10, 4, 10, 4), radius=4 * self.scale)
        self.add_button = self.ui.button(self.ui.text("+ Agent", size=self._size(9), bold=True, rgb=PERSON),
                                         self._toggle_form, style=add_style)
        self.add_button.set_tool_tip_text(unreal.Text("Start a new agent"))
        add(head, self.add_button)
        add(bar, head, padding=self._space(12, 8, 12, 6))
        self._build_form(bar)
        self.session_note = self.ui.text("", size=self._size(9), rgb=TEXT, wrap=True)
        add(bar, self.session_note, padding=self._space(12, 0, 12, 6))
        self.session_shown = None
        add(page, bar)

    def _build_form(self, bar: unreal.VerticalBox) -> None:
        """+ Agent's form, on a band of its own across the tab: the name, the machine it runs on and
        the model it thinks with, each with a small label, then Cancel and Add. Only models the
        picked machine can run are offered."""
        self.form_open = False
        # The name the form last suggested, which picking a Codex or Claude model replaces
        self.suggested_name = ""
        height = FIELD_HEIGHT * self.scale
        fields = self.ui.make(unreal.VerticalBox)
        add(fields, self._caption("Name"))
        self.name_box = self.ui.make(unreal.EditableTextBox)
        ui_widgets.style_text_box(self.name_box)
        self._scale_text_box(self.name_box)
        self.name_box.set_hint_text(unreal.Text("Agent name, like Brian Claude"))
        add(fields, self.ui.sized(self.name_box, None, height), padding=self._space(0, 3, 0, 10))
        self.model_box = self.ui.make(unreal.ComboBoxString)
        add(fields, self._caption("Model"))
        self._style_combo(self.model_box)
        add(fields, self.ui.sized(self.model_box, None, height), padding=self._space(0, 3, 0, 10))
        choices = self.ui.make(unreal.HorizontalBox)
        self.machine_box = self.ui.make(unreal.ComboBoxString)
        self.effort_box = self.ui.make(unreal.ComboBoxString)
        for label, combo, gap in (("Machine", self.machine_box, 10), ("Reasoning", self.effort_box, 0)):
            column = self.ui.make(unreal.VerticalBox)
            add(column, self._caption(label))
            self._style_combo(combo)
            add(column, self.ui.sized(combo, None, height), padding=self._space(0, 3, 0, 0))
            add(choices, column, fill=True, padding=self._space(0, 0, gap, 0))
        self.machine_box.on_selection_changed.add_callable(self._machine_changed)
        self.model_box.on_selection_changed.add_callable(self._model_changed)
        add(fields, choices)
        self._build_machine_section(fields)
        actions = self.ui.make(unreal.HorizontalBox)
        # Why Add couldn't go ahead, right where the person looks
        self.form_error = self.ui.text("", size=self._size(9), rgb=ERROR, wrap=True)
        add(actions, self.form_error, fill=True, padding=self._space(0, 0, 8, 0))
        cancel = self.ui.button(self.ui.text("Cancel", size=self._size(9), rgb=TEXT), self._toggle_form,
                                style=self._button_style(False))
        add(actions, self.ui.sized(cancel, None, height), padding=self._space(0, 0, 6, 0))
        add_style = ui_widgets.button_style(ui_widgets.PRIMARY, ui_widgets.PRIMARY_HOVER, ui_widgets.PRIMARY_PRESSED,
                                            ui_widgets.SURFACE, self._space(16, 4, 16, 4), radius=4 * self.scale)
        confirm = self.ui.button(self.ui.text("Add", size=self._size(9), bold=True, rgb=PERSON), self._add_from_form,
                                 style=add_style)
        add(actions, self.ui.sized(confirm, None, height))
        add(fields, actions, padding=self._space(0, 12, 0, 0))
        self.form = self.ui.border(ui_widgets.SURFACE, fields, padding=self._space(12, 10, 12, 12))
        self.form.set_visibility(unreal.SlateVisibility.COLLAPSED)
        self.machines_shown = None
        self.models_shown = None
        self.efforts_shown = None
        # What each label in the Model dropdown stands for: (brain, model, why it can't run here)
        self.model_options = {}
        # Which station each label in the Machine dropdown stands for
        self.machine_options = {}
        add(bar, self.form, padding=self._space(0, 0, 0, 6))

    def _caption(self, label: str) -> unreal.TextBlock:
        return self.ui.text(label, size=self._size(8), rgb=MUTED)

    def _scale_text_box(self, box: unreal.EditableTextBox) -> None:
        style = box.get_editor_property("widget_style")
        text_style = style.get_editor_property("text_style")
        text_style.set_editor_property("font", ui_widgets.font(self._size(10)))
        style.set_editor_property("text_style", text_style)
        style.set_editor_property("padding", ui_widgets.margin(*self._space(8, 5, 8, 5)))
        box.set_editor_property("widget_style", style)

    def _style_combo(self, combo: unreal.ComboBoxString) -> None:
        """The machine dropdown in the same dark field as the name, with the tab's font."""
        try:
            combo.set_editor_property("font", ui_widgets.font(self._size(9)))
            combo.set_editor_property("foreground_color", ui_widgets.slate(PERSON))
            combo.set_editor_property("content_padding", ui_widgets.margin(*self._space(8, 2, 8, 2)))
            style = combo.get_editor_property("widget_style")
            button_box = style.get_editor_property("combo_button_style")
            button = button_box.get_editor_property("button_style")
            radius = 4 * self.scale
            for state, rgb in (("normal", ui_widgets.FIELD), ("hovered", ui_widgets.HOVER), ("pressed", ui_widgets.PRESSED)):
                button.set_editor_property(state, ui_widgets.brush(rgb, radius))
            button_box.set_editor_property("button_style", button)
            style.set_editor_property("combo_button_style", button_box)
            combo.set_editor_property("widget_style", style)
        except Exception as error:
            # An engine that names these otherwise keeps its own look for the dropdown
            unreal.log_warning(f"CoPresence Agents widget: the machine dropdown keeps its own look ({error})")

    def _toggle_form(self) -> None:
        """Opens the form with a name ready, after the person, or closes it."""
        self.form_open = not self.form_open
        _show(self.form, self.form_open)
        self.form_error.set_text(unreal.Text(""))
        if self.form_open:
            self.machines_shown = self.models_shown = None
            self.suggested_name = _new_agent_name(hub_client.dashboard() or {}, self._picked_model()[0])
            self.name_box.set_text(unreal.Text(self.suggested_name))

    def _picked_model(self) -> tuple[str, str, str]:
        """The brain, model and any reason it can't run, as the Model dropdown has picked."""
        return self.model_options.get(str(self.model_box.get_selected_option()), (MODELS[0][1], MODELS[0][2], ""))

    def _picked_effort(self) -> str:
        brain, model, _ = self._picked_model()
        return EFFORT_CHOICES.get(str(self.effort_box.get_selected_option()), _starting_effort(brain, model))

    def _machine_changed(self, item, selection_type) -> None:
        # Another machine may run other brains
        self.models_shown = None

    def _model_changed(self, item, selection_type) -> None:
        current = " ".join(str(self.name_box.get_text()).split())
        # A name the person typed stays; the one the form suggested follows Claude or Codex
        if current and current != self.suggested_name:
            return
        self.suggested_name = _new_agent_name(hub_client.dashboard() or {}, self._picked_model()[0])
        self.name_box.set_text(unreal.Text(self.suggested_name))

    def _picked_machine_name(self) -> str:
        return self.machine_options.get(str(self.machine_box.get_selected_option()), "")

    def _add_from_form(self) -> None:
        brain, model, problem = self._picked_model()
        machine = self._picked_machine_name()
        name, effort = str(self.name_box.get_text()), self._picked_effort()
        board = hub_client.dashboard()
        own = machine == local_station.station_name()
        if own and local_station.state(board) == "stopping":
            refusal = f"This {local_station.machine_word()} is just stopping its last agent; try again in a moment"
        elif own and local_station.state(board) in ("off", "starting"):
            # Its station starts in the background, and the agent follows once it is up
            refusal = _brain_refusal(brain, problem, machine) or _add_after_turning_on(name, brain, model, effort, board)
        else:
            refusal = _brain_refusal(brain, problem, machine) or _start_named_agent(name, brain, model, effort, machine)
        if refusal:
            self.form_error.set_text(unreal.Text(refusal))
            unreal.log_warning(f"CoPresence Agents widget: couldn't add an agent: {refusal}")
            return
        self._toggle_form()

    def _update_form(self, board: dict | None) -> None:
        """The machines with room in the first dropdown, this machine among them, keeping the one
        picked; in the second the models that machine can run; and this machine's own section
        while it is the one picked."""
        if not self.form_open:
            return
        entries = _machine_entries(board)
        labels = [label for label, _ in entries]
        if labels != self.machines_shown:
            self.machines_shown = labels
            self.models_shown = None
            selected = self._picked_machine_name()
            self.machine_options = dict(entries)
            picked = _picked_machine(board)
            wanted = selected or (picked["name"] if picked else "")
            keep = next((label for label, name in entries if name == wanted), "")
            _fill_combo(self.machine_box, labels, keep)
        machine = self._picked_machine_name()
        station = next((entry for entry in _agent_machines(board) if entry["name"] == machine), None)
        if station is None and machine == local_station.station_name():
            station = _this_machine_station(board)
        options = _model_options(station)
        labels = [option[0] for option in options]
        if labels != self.models_shown:
            self.models_shown = labels
            # Picked by what it stands for, since a label changes when its brain gets set up; to
            # start with, the first one that can run there
            picked = self._picked_model()[:2] if self.model_options else None
            self.model_options = {label: (brain, model, problem) for label, brain, model, problem in options}
            keep = next((label for label, brain, model, _ in options if (brain, model) == picked), "") or \
                next((label for label, _, _, problem in options if not problem), "")
            _fill_combo(self.model_box, labels, keep)
        # Another model starts at its own level; the person can pick another after
        model_picked = self._picked_model()[:2]
        if model_picked != self.efforts_shown:
            self.efforts_shown = model_picked
            starting = _starting_effort(*model_picked)
            _fill_combo(self.effort_box, _effort_labels(model_picked[0]),
                        next((label for label, effort in EFFORT_CHOICES.items() if effort == starting), ""))
        self._update_machine_section(board, machine == local_station.station_name())

    def _build_machine_section(self, fields: unreal.VerticalBox) -> None:
        """Under Machine, while this machine is picked: signing Claude Code or Codex in when they
        aren't ready here, and whether others in the session can add agents to it."""
        self.machine_section = self.ui.make(unreal.VerticalBox)
        # One line for each of Claude Code and Codex that isn't ready here, with a way to set it up
        self.brain_lines = self.ui.make(unreal.VerticalBox)
        add(self.machine_section, self.brain_lines)
        share_line = self.ui.make(unreal.HorizontalBox)
        self.share_text = self.ui.text("", size=self._size(9), rgb=MUTED, wrap=True)
        add(share_line, self.share_text, fill=True, padding=self._space(0, 5, 8, 0))
        self.share_control = self._small_button("Let everyone in the session add agents to this machine, or only you",
                                                self._toggle_share)
        add(share_line, self.share_control["button"])
        add(self.machine_section, share_line, padding=self._space(0, 4, 0, 0))
        self.machine_section.set_visibility(unreal.SlateVisibility.COLLAPSED)
        add(fields, self.machine_section, padding=self._space(0, 8, 0, 0))
        self.machine_shown = None

    def _toggle_share(self) -> None:
        board = hub_client.dashboard()
        local_station.set_shared(board, not local_station.is_shared(board))
        self.machine_shown = None

    def _update_machine_section(self, board: dict | None, picked: bool) -> None:
        problems = (local_station.brain_problems(board) or {}) if picked else {}
        shared = picked and local_station.is_shared(board)
        trouble = local_station.problem() if picked else ""
        shown = (picked, tuple(sorted(problems.items())), shared, trouble)
        if shown == self.machine_shown:
            return
        self.machine_shown = shown
        _show(self.machine_section, picked)
        if not picked:
            return
        word = local_station.machine_word()
        sharing = f"Anyone in the session can add agents to this {word}" if shared \
            else f"Only you can add agents to this {word}"
        self.share_text.set_text(unreal.Text(trouble or sharing))
        self.share_text.set_color_and_opacity(ui_widgets.slate(ERROR if trouble else MUTED))
        _set_label(self.share_control, "Stop sharing" if shared else "Share", self._button_style(False), TEXT)
        self.brain_lines.clear_children()
        for brain, why in sorted(problems.items()):
            if why:
                self._add_brain_line(brain, why)

    def _add_brain_line(self, brain: str, why: str) -> None:
        line = self.ui.make(unreal.HorizontalBox)
        tool = local_station.BRAIN_NAMES.get(brain, brain)
        # "isn't signed in on" as the form says it, here about this machine
        words = BRAIN_PROBLEMS.get(why, "isn't set up on").removesuffix(" on")
        add(line, self.ui.text(f"{tool} {words} here", size=self._size(9), rgb=TEXT, wrap=True),
            fill=True, padding=self._space(0, 4, 8, 0))
        # Claude Code installs with its own installer; Codex comes from npm, which people set up themselves
        install = why == "not installed"
        if not install or brain == "claude":
            setup = self._small_button(f"Open the one-time {tool} sign-in on this machine",
                                       self._for_agent(lambda chosen: local_station.sign_in(chosen, install), brain))
            _set_label(setup, "Install and sign in" if install else "Sign in", self._button_style(False), TEXT)
            add(line, setup["button"])
        add(self.brain_lines, line, padding=self._space(0, 2, 0, 0))

    def _build_caption_bar(self, page: unreal.VerticalBox) -> None:
        """What you say in a call, as the agent's voice hears it, and what the agent says back."""
        bar = self.ui.make(unreal.VerticalBox)
        self.caption_lines = []
        for _ in range(2):
            line = self.ui.make(unreal.HorizontalBox)
            label = self.ui.text("", size=self._size(9), bold=True, rgb=TEXT)
            words = self.ui.text("", size=self._size(10), rgb=PERSON, wrap=True)
            slot = add(line, label, padding=self._space(0, 1, 8, 0))
            slot.set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_TOP)
            slot = add(line, words, fill=True)
            slot.set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_TOP)
            add(bar, line, padding=self._space(0, 2, 0, 2))
            self.caption_lines.append((line, label, words))
        self.caption_bar = self.ui.border(ui_widgets.SURFACE, bar, padding=self._space(12, 6, 12, 6))
        self.caption_bar.set_visibility(unreal.SlateVisibility.COLLAPSED)
        add(page, self.caption_bar)

    def _update_caption_bar(self, agents: dict) -> None:
        captions = _state["captions"]
        if self.caption_bar is None or captions == self.captions_shown:
            return
        self.captions_shown = captions
        agent, heard, said = captions
        _show(self.caption_bar, bool(heard or said), hit_test=False)
        appearance = (agents.get(agent) or {}).get("appearance")
        agent_rgb = agents_panel.agent_color(agent, appearance) if agent else PERSON
        for (line, label, words), name, text, rgb in zip(self.caption_lines, ("You", agent), (heard, said),
                                                          (TEXT, agent_rgb)):
            _show(line, bool(text), hit_test=False)
            label.set_text(unreal.Text(name))
            label.set_color_and_opacity(ui_widgets.slate(rgb))
            words.set_text(unreal.Text(text))

    def _build_call_bar(self, page: unreal.VerticalBox) -> None:
        """While you are in a call: with whom and how it goes, Mute mic, and Hang up."""
        line = self.ui.make(unreal.HorizontalBox)
        labels = self.ui.make(unreal.VerticalBox)
        self.call_name = self.ui.text("", size=self._size(10), bold=True, rgb=PERSON)
        self.call_name.set_text_overflow_policy(unreal.TextOverflowPolicy.ELLIPSIS)
        self.call_name.set_clipping(unreal.WidgetClipping.CLIP_TO_BOUNDS)
        add(labels, self.call_name)
        self.call_status = self.ui.text("", size=self._size(9), rgb=MUTED, wrap=True)
        add(labels, self.call_status, padding=self._space(0, 1, 0, 0))
        add(line, labels, fill=True, padding=self._space(0, 0, 8, 0))
        self.mic_control = self._small_button("Mute your microphone: the agent hears quiet, and you still hear it",
                                              _toggle_mic)
        add(line, self.mic_control["button"], padding=self._space(0, 0, 4, 0))
        hang_up_style = ui_widgets.button_style(HANG_UP, HANG_UP_HOVER, HANG_UP_PRESSED, ui_widgets.SURFACE,
                                                self._space(8, 4, 8, 4), radius=4 * self.scale)
        hang_up = self.ui.button(self.ui.text("Hang up", size=self._size(9), bold=True, rgb=PERSON), _hang_up,
                                 style=hang_up_style)
        add(line, hang_up)
        # A shade lighter than the rest of the tab, so the call stands out and its buttons show on it
        self.call_bar = self.ui.border(ui_widgets.SELECTED, line, padding=self._space(12, 8, 12, 8))
        self.call_bar.set_visibility(unreal.SlateVisibility.COLLAPSED)
        self.call_shown = None
        add(page, self.call_bar, padding=self._space(0, 0, 0, 6))

    def _update_call_bar(self, agents: dict) -> None:
        call = tuple(_state["call"])
        words, problem = _group_call_words(call) if call else ("", False)
        shown = (call, words, problem, _state["mic_muted"])
        if shown == self.call_shown:
            return
        self.call_shown = shown
        _show(self.call_bar, bool(call))
        if not call:
            return
        self.call_name.set_text(unreal.Text(", ".join(call)))
        # One agent's name in its color; several in the tab's own
        appearance = (agents.get(call[0]) or {}).get("appearance")
        rgb = agents_panel.agent_color(call[0], appearance) if len(call) == 1 else PERSON
        self.call_name.set_color_and_opacity(ui_widgets.slate(rgb))
        self.call_status.set_text(unreal.Text(words))
        self.call_status.set_color_and_opacity(ui_widgets.slate(ERROR if problem else MUTED))
        mic_muted = _state["mic_muted"]
        _set_label(self.mic_control, "Unmute mic" if mic_muted else "Mute mic", self._button_style(mic_muted),
                   PERSON if mic_muted else TEXT)

    def _build_people(self, content: unreal.VerticalBox) -> None:
        """Everyone else in the session, as the session dashboard lists them: where they are and
        whom they talk with. At a desk, clicking one follows them with the camera, as clicking an
        agent does."""
        head = self.ui.make(unreal.HorizontalBox)
        add(head, self.ui.text("People", size=self._size(10), bold=True, rgb=PERSON))
        self.people_count = self.ui.text("", size=self._size(9), rgb=MUTED)
        add(head, self.people_count, padding=self._space(6, 1, 0, 0))
        add(content, head, padding=self._space(12, 14, 12, 6))
        self.people_list = self.ui.make(unreal.VerticalBox)
        add(content, self.people_list)
        self.no_people = self.ui.text("Nobody else is in the session.", size=self._size(10), rgb=MUTED)
        self.people_rows = {}
        self.people_names = None

    def _build_people_rows(self, names: list) -> None:
        self.people_names = names
        self.people_list.clear_children()
        self.people_rows = {}
        if not names:
            add(self.people_list, self.no_people, padding=self._space(12, 4, 12, 8))
            return
        badge_size = ICON_SIZE * self.scale
        for name in names:
            line = self.ui.make(unreal.HorizontalBox)
            # A circle with their initial, where an agent has its face
            badge = self.ui.border(ui_widgets.SELECTED, self.ui.text(name[:1].upper(), size=self._size(10), bold=True,
                                                                     rgb=TEXT))
            badge.set_brush(ui_widgets.brush(ui_widgets.SELECTED, badge_size / 2))
            badge.set_horizontal_alignment(unreal.HorizontalAlignment.H_ALIGN_CENTER)
            badge.set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_CENTER)
            add(line, self.ui.sized(badge, badge_size, badge_size), padding=self._space(10, 6, 10, 6))
            labels = self.ui.make(unreal.VerticalBox)
            name_text = self.ui.text(name, size=self._size(10), bold=True, rgb=PERSON)
            name_text.set_text_overflow_policy(unreal.TextOverflowPolicy.ELLIPSIS)
            name_text.set_clipping(unreal.WidgetClipping.CLIP_TO_BOUNDS)
            add(labels, name_text)
            status_text = self.ui.text("", size=self._size(9), rgb=MUTED, wrap=True)
            add(labels, status_text, padding=self._space(0, 2, 0, 0))
            add(line, labels, fill=True, padding=self._space(0, 6, 8, 6))
            line.set_visibility(unreal.SlateVisibility.HIT_TEST_INVISIBLE)
            if self.vr:
                # Following moves the camera, which in VR would move the person wearing the headset
                row = self.ui.border(ui_widgets.BACKGROUND, line)
            else:
                row = self.ui.button(line, self._for_agent(_toggle_follow, name))
                row.set_tool_tip_text(unreal.Text(f"Follow {name} with the camera; move the camera to stop"))
            add(self.people_list, row)
            self.people_rows[name] = {"row": row, "status_text": status_text, "status": None, "picked": False}

    def _update_people(self, board: dict | None) -> None:
        if board is None:
            return
        me = hub_client.display_name()
        people = [person for person in board.get("people") or [] if person.get("name") and person["name"] != me]
        names = [person["name"] for person in people]
        if names != self.people_names:
            self._build_people_rows(names)
            self.people_count.set_text(unreal.Text(str(len(names)) if names else ""))
        for person in people:
            name = person["name"]
            row = self.people_rows[name]
            talking = person.get("talking_with")
            text = f"Talking with {talking}" if talking else PERSON_PLACES.get(person.get("kind"), "In the session")
            following = not self.vr and _state["following"] == name
            if following:
                text = f"{text}   (following; move the camera to stop)"
            self._set_status(row, text, MUTED)
            if row["picked"] != following:
                row["picked"] = following
                row["row"].set_style(ui_widgets.flat_style(following))

    def _build_drawing_bar(self, page: unreal.VerticalBox) -> None:
        """Sends what the Agents tool drew to the agent you picked, or takes it back."""
        bar = self.ui.make(unreal.VerticalBox)
        self.drawing_text = self.ui.text("", size=self._size(9), rgb=TEXT, wrap=True)
        add(bar, self.drawing_text, padding=self._space(12, 8, 12, 4))
        self.drawing_buttons = self.ui.make(unreal.HorizontalBox)
        self.send_text = self.ui.text("", size=self._size(10), bold=True, rgb=PERSON)
        send_style = ui_widgets.button_style(ui_widgets.PRIMARY, ui_widgets.PRIMARY_HOVER, ui_widgets.PRIMARY_PRESSED,
                                             ui_widgets.SURFACE, self._space(14, 8, 14, 8), radius=6 * self.scale)
        self.send_button = self.ui.button(self.send_text, _send_drawing, style=send_style)
        add(self.drawing_buttons, self.send_button, fill=True, padding=self._space(12, 4, 4, 10))
        for label, action in (("Undo", _undo_stroke), ("Clear", _clear_drawing)):
            button = self.ui.button(self.ui.text(label, size=self._size(10), rgb=TEXT), action, padding=self._space(14, 8, 14, 8))
            add(self.drawing_buttons, button, padding=self._space(4, 4, 4, 10))
        add(bar, self.drawing_buttons)
        self.drawing_bar = self.ui.border(ui_widgets.SURFACE, bar)
        self.drawing_bar.set_visibility(unreal.SlateVisibility.COLLAPSED)
        self.drawing_shown = None
        add(page, self.drawing_bar)

    def _update_drawing_bar(self) -> None:
        tool = getattr(unreal, "CoPresenceAgentsToolActor", None)
        has_drawing = bool(tool and tool.has_drawing())
        agent = _drawing_target()
        sending = _state["sending_drawing"]
        note = _state["drawing_note"] if time.monotonic() < _state["drawing_note_until"] else ""
        hint = False
        if sending:
            note = f"Sending your drawing to {sending}..."
        elif has_drawing and not agent:
            note = "Tap an agent to send it your drawing"
        elif not has_drawing and not note and tool is not None:
            # The Agents tool is in your hand while its tab shows, so the trigger draws
            note, hint = "Pull the trigger to draw on the level for an agent", True
        shown = (has_drawing, agent, note)
        if shown == self.drawing_shown:
            return
        self.drawing_shown = shown
        if not has_drawing and not note:
            self.drawing_bar.set_visibility(unreal.SlateVisibility.COLLAPSED)
            return
        self.drawing_bar.set_visibility(unreal.SlateVisibility.VISIBLE)
        self.drawing_text.set_color_and_opacity(ui_widgets.slate(MUTED if hint else TEXT))
        self.drawing_text.set_text(unreal.Text(note))
        self.drawing_text.set_visibility(unreal.SlateVisibility.VISIBLE if note else unreal.SlateVisibility.COLLAPSED)
        self.drawing_buttons.set_visibility(unreal.SlateVisibility.VISIBLE if has_drawing else unreal.SlateVisibility.COLLAPSED)
        self.send_text.set_text(unreal.Text(f"Send to {agent}" if agent else "Send"))
        self.send_button.set_is_enabled(bool(agent) and not sending)

    def _build_fab_card(self, page: unreal.VerticalBox) -> None:
        """What an agent asks you to add from Fab, with Decline and Add to Project, in VR, where the
        popup's window doesn't show. Two lines, so the agents still show under it."""
        if not fab_approvals.is_available():
            return
        card = self.ui.make(unreal.VerticalBox)
        asker = self.ui.make(unreal.HorizontalBox)
        self.fab_dot = self.ui.text("\u25CF", size=self._size(9), rgb=TEXT)
        add(asker, self.fab_dot, padding=self._space(0, 1, 6, 0)).set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_TOP)
        self.fab_title = self.ui.text("", size=self._size(10), rgb=PERSON, wrap=True)
        add(asker, self.fab_title, fill=True)
        add(card, asker)
        line = self.ui.make(unreal.HorizontalBox)
        self.fab_facts = self.ui.text("", size=self._size(9), rgb=TEXT, wrap=True)
        add(line, self.fab_facts, fill=True, padding=self._space(0, 0, 8, 0)).set_vertical_alignment(
            unreal.VerticalAlignment.V_ALIGN_CENTER)
        self.fab_buttons = self.ui.make(unreal.HorizontalBox)
        decline = self.ui.button(self.ui.text("Decline", size=self._size(9), rgb=TEXT), lambda: fab_approvals.decide(False),
                                 padding=self._space(10, 6, 10, 6))
        add(self.fab_buttons, decline, padding=self._space(0, 0, 4, 0))
        add_style = ui_widgets.button_style(ui_widgets.PRIMARY, ui_widgets.PRIMARY_HOVER, ui_widgets.PRIMARY_PRESSED,
                                            ui_widgets.SURFACE, self._space(10, 6, 10, 6), radius=4 * self.scale)
        self.fab_add = self.ui.button(self.ui.text("Add to Project", size=self._size(9), bold=True, rgb=PERSON),
                                      lambda: fab_approvals.decide(True), style=add_style)
        add(self.fab_buttons, self.fab_add)
        add(line, self.fab_buttons).set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_CENTER)
        add(card, line, padding=self._space(0, 6, 0, 0))
        self.fab_status = self.ui.text("", size=self._size(9), rgb=MUTED, wrap=True)
        add(card, self.fab_status, padding=self._space(0, 6, 0, 0))
        # Lighter than the tab, like the call bar, since it waits for an answer
        self.fab_card = self.ui.border(ui_widgets.SELECTED, card, padding=self._space(12, 8, 12, 8))
        self.fab_card.set_visibility(unreal.SlateVisibility.COLLAPSED)
        self.fab_shown = None
        add(page, self.fab_card, padding=self._space(0, 0, 0, 6))

    def _update_fab_card(self, agents: dict) -> None:
        if self.fab_card is None:
            return
        request = fab_approvals.shown_request()
        if request == self.fab_shown:
            return
        self.fab_shown = request
        _show(self.fab_card, request is not None)
        if request is None:
            return
        agent = request["agent"]
        rgb = agents_panel.agent_color(agent, (agents.get(agent) or {}).get("appearance"))
        self.fab_dot.set_color_and_opacity(ui_widgets.slate(rgb))
        self.fab_title.set_text(unreal.Text(f"{agent} asks for {request['title']}"))
        # What decides it at a glance: the price, the size and what it comes as, and AI use only when it isn't allowed
        facts = request["facts"]
        shown_facts = [facts[label] for label in ("Price", "Size", "Formats") if facts.get(label)]
        if facts.get("AI use") == "Not allowed":
            shown_facts.append("AI use not allowed")
        self.fab_facts.set_text(unreal.Text("   ".join(shown_facts)))
        status_rgb = ERROR if request["problem"] else ui_widgets.SUCCESS if request["done"] else MUTED
        self.fab_status.set_text(unreal.Text(request["status"]))
        self.fab_status.set_color_and_opacity(ui_widgets.slate(status_rgb))
        _show(self.fab_status, request["news"] and bool(request["status"]), hit_test=False)
        _show(self.fab_buttons, request["deciding"])
        self.fab_add.set_is_enabled(request["can_add"])

    def _update_session_bar(self, board: dict | None) -> None:
        can_start, reason = _can_start(board)
        count = len(_state["names"])
        shown = (str(count) if count else "", can_start, _control_note() or reason)
        self._update_form(board)
        if shown == self.session_shown:
            return
        self.session_shown = shown
        count_text, can_start, note = shown
        self.count_text.set_text(unreal.Text(count_text))
        self.session_note.set_text(unreal.Text(note))
        _show(self.session_note, bool(note), hit_test=False)
        self.add_button.set_is_enabled(can_start or self.form_open)

    def _size(self, points: int) -> int:
        return round(points * self.scale)

    def _space(self, *pixels) -> tuple:
        return tuple(value * self.scale for value in pixels)

    def _picked(self) -> str | None:
        """The agent the rows act on: the one picked in VR, or followed at the desk."""
        return _state["talking_with"] if self.vr else _state["following"]

    def _row_note(self, name: str) -> str:
        notes = []
        if self.vr and _state["talking_with"] == name:
            notes.append("picked; tap to let go")
        if not self.vr and _state["following"] == name:
            notes.append("following; move the camera to stop")
        # How a call with it goes shows in the call bar
        return "; ".join(notes)

    def _update_voice_bar(self) -> None:
        problem = voice_link.helper_problem()
        shown = problem if problem and not voice_link.is_running() else None
        if shown == self.voice_shown:
            return
        self.voice_shown = shown
        if shown is None:
            self.voice_bar.set_visibility(unreal.SlateVisibility.COLLAPSED)
            return
        self.voice_bar.set_visibility(unreal.SlateVisibility.VISIBLE)
        self.voice_text.set_text(unreal.Text(f"No voice: {problem}"))

    def _update_buttons(self, row: dict, name: str) -> None:
        """Call, Mute and who hears the agent, as they stand for this person right now."""
        me = hub_client.display_name()
        owner, public = _audience(name)
        calling = name in _state["call"]
        muted = name in _state["muted_agents"]
        shown = (owner, public, calling, muted)
        if row["buttons_shown"] == shown:
            return
        row["buttons_shown"] = shown
        mine = owner == me or not owner
        # In the call it shows so, and pressing it then hangs up on this agent alone
        _set_label(row["call"], "In call" if calling else "Call", self._button_style(calling), PERSON if calling else TEXT)
        row["call"]["button"].set_is_enabled(calling or _may_call(name))
        # Mute is for an agent you hear: a public one, or the one you are in a call with
        _show(row["mute"]["button"], public or calling)
        _set_label(row["mute"], "Unmute" if muted else "Mute", self._button_style(muted), PERSON if muted else TEXT)
        # Only the owner shares the agent or keeps it private, and so does anyone for an agent nobody owns
        _show(row["audience"]["button"], mine)
        _set_label(row["audience"], "Make private" if public else "Make public", self._button_style(False), TEXT)
        private_text = "" if public else ("Private to you" if owner == me else f"Private to {owner}")
        row["private"].set_text(unreal.Text(private_text))
        _show(row["private"], bool(private_text), hit_test=False)
        # Someone else's private agent is washed out, as its ghost in the level is, but never its buttons
        for widget in row["faded"]:
            widget.set_render_opacity(PRIVATE_OPACITY if _washed_out(name) else 1.0)

    def _button_style(self, active: bool):
        # Agents' buttons sit in a row that lights up too while the pointer is on it, so a hovered
        # button lights up more than that
        if active:
            return ui_widgets.button_style(ui_widgets.PRIMARY, ui_widgets.PRIMARY_HOVER, ui_widgets.PRIMARY_PRESSED,
                                           ui_widgets.SURFACE, self._space(8, 4, 8, 4), radius=4 * self.scale)
        return ui_widgets.button_style(ui_widgets.SURFACE, ui_widgets.PRESSED, ui_widgets.PRIMARY,
                                       ui_widgets.SURFACE, self._space(8, 4, 8, 4), radius=4 * self.scale)

    def update(self, agents: dict, seconds_since_poll: float, board: dict | None) -> None:
        self._update_session_bar(board)
        self._update_call_bar(agents)
        self._update_voice_bar()
        self._update_caption_bar(agents)
        self._update_people(board)
        if self.vr:
            self._update_fab_card(agents)
            self._update_drawing_bar()
        names = _state["names"]
        starting = [name for name in _state["starting"] if name not in names]
        starting += [name for name in _state["start_failed"] if name not in names and name not in starting]
        participants = agents_panel.session_participants()
        if agent_editor.is_agent_editor(): participants.add(hub_client.display_name())
        if (names, starting) != self.row_names:
            self._build_rows(names, starting)
        # Agents a station runs can be stopped from here, ones still on their way in too
        stations = {agent["name"]: agent.get("station") or "" for agent in (board or {}).get("agents", [])}
        listed = {agent["name"] for station in (board or {}).get("stations", []) for agent in station.get("agents") or []}
        for name in starting:
            failed = _state["start_failed"].get(name)
            if failed:
                self._set_status(self.rows[name], f"Couldn't start on {failed[0]}: {failed[1]}", ERROR)
            else:
                machine, asked_at = _state["starting"][name]
                waiting = (_state["pending_add"] or {}).get("name") == name
                progress = f"Starting on this {local_station.machine_word()}..." if waiting \
                    else _start_progress(board, name, machine, asked_at)
                self._set_status(self.rows[name], progress, TEXT)
            self._update_stop(self.rows[name], name, name in listed)
        for name in names:
            status = agents.get(name)
            age = (status or {}).get("age", 0.0) + seconds_since_poll
            state = ghost_labels.effective_state(status, age)
            row = self.rows[name]
            appearance = (status or {}).get("appearance")
            if row["face"].set_appearance(name, appearance, _washed_out(name)):
                row["name"].set_color_and_opacity(ui_widgets.slate(agents_panel.agent_color(name, appearance)))
            row["face"].set_target(ghost_face.emote_for(status, state, age))
            row["face"].set_talking(bool((status or {}).get("speaking")))
            text = ghost_labels.status_text(status, age)
            if (status or {}).get("speaking"):
                text = "Talking in the call"
            # Quiet for a while, but still in the session
            if text == "Away" and name in participants:
                text = "Idle"
            note = self._row_note(name)
            if note:
                text = f"{text}   ({note})"
            self._set_status(row, text, TEXT if state in ("working", "saying", "error") else MUTED)
            picked = self._picked() == name
            if row["picked"] != picked:
                row["picked"] = picked
                row["button"].set_style(ui_widgets.flat_style(picked))
            self._update_buttons(row, name)
            in_call = (status or {}).get("call") == "in_call"
            if row["in_call"] != in_call:
                row["in_call"] = in_call
                visibility = unreal.SlateVisibility.HIT_TEST_INVISIBLE if in_call else unreal.SlateVisibility.COLLAPSED
                for widget in row["live"]:
                    widget.set_visibility(visibility)
            self._update_stop(row, name, bool(stations.get(name)))
            machine = stations.get(name, "")
            if row["machine_shown"] != machine:
                row["machine_shown"] = machine
                row["machine"].set_text(unreal.Text(f"on {machine}" if machine else ""))
                _show(row["machine"], bool(machine), hit_test=False)

    def _set_status(self, row: dict, text: str, rgb) -> None:
        if row["status"] != text:
            row["status"] = text
            row["status_text"].set_text(unreal.Text(text))
            row["status_text"].set_color_and_opacity(ui_widgets.slate(rgb))

    def _update_stop(self, row: dict, name: str, stoppable: bool) -> None:
        asked, asked_at = _state["stop_asked"]
        shown = (stoppable, asked == name and time.monotonic() - asked_at < STOP_CONFIRM_SECONDS)
        if row["stop_shown"] == shown:
            return
        row["stop_shown"] = shown
        row["stop"].set_visibility(unreal.SlateVisibility.VISIBLE if stoppable else unreal.SlateVisibility.COLLAPSED)
        row["stop_text"].set_text(unreal.Text("Remove?" if shown[1] else "- Agent"))
        row["stop_text"].set_color_and_opacity(ui_widgets.slate(ERROR if shown[1] else TEXT))

    def animate(self, now: float, delta_seconds: float) -> None:
        for row in self.rows.values():
            row["face"].update(now, delta_seconds)

    def _build_rows(self, names: list, starting: list) -> None:
        self.row_names = (names, starting)
        self.list.clear_children()
        self.rows = {}
        if not names and not starting:
            add(self.list, self.empty, padding=self._space(12, 8, 12, 8))
            return
        parent = unreal.load_object(None, ICON_MATERIAL_PATH)
        icon_size = ICON_SIZE * self.scale
        for name in names + starting:
            live = name in names
            line = self.ui.make(unreal.HorizontalBox)
            icon = self.ui.make(unreal.Image)
            icon.set_brush_from_material(parent)
            face = ghost_face.FaceAnimator(icon.get_dynamic_material())
            face_box = self.ui.sized(icon, icon_size, icon_size)
            slot = add(line, face_box, padding=self._space(10, 8, 10, 8))
            slot.set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_TOP)
            block = self.ui.make(unreal.VerticalBox)
            # The name, then the agent's buttons at the end of the same line, the same at the desk and
            # in VR. A name too long for the room left ends in an ellipsis rather than running under them
            title = self.ui.make(unreal.HorizontalBox)
            name_text = self.ui.text(name, size=self._size(10), bold=True, rgb=PERSON)
            name_text.set_text_overflow_policy(unreal.TextOverflowPolicy.ELLIPSIS)
            name_text.set_clipping(unreal.WidgetClipping.CLIP_TO_BOUNDS)
            add(title, name_text, fill=True)
            # Shown while the agent is in a video call, where people outside the session see the level
            live_dot = self.ui.text("\u25CF", size=self._size(8), rgb=ERROR)
            live_label = self.ui.text("Live in Meet", size=self._size(9), rgb=TEXT)
            for widget in (live_dot, live_label):
                widget.set_visibility(unreal.SlateVisibility.COLLAPSED)
            add(title, live_dot, padding=self._space(6, 0, 4, 0))
            add(title, live_label)
            controls = {}
            if live:
                for key, action, tip in (
                        ("call", _toggle_call, "Call this agent, or add it to your call, where you talk to one "
                                               "agent at a time by saying its name"),
                        ("mute", _toggle_mute, "Stop hearing this agent; a call with it goes on, with its words "
                                               "in the captions"),
                        ("audience", _toggle_audience,
                         "Private: only you can call it and hear it. Public: everyone can")):
                    controls[key] = self._small_button(tip, self._for_agent(action, name))
                    add(title, controls[key]["button"], padding=self._space(4, 0, 0, 0))
            # Takes the agent out of the session and shuts it down, after asking once more
            stop_text = self.ui.text("- Agent", size=self._size(9), rgb=TEXT)
            stop = self.ui.button(stop_text, self._stopper(name), style=self._button_style(False))
            stop.set_tool_tip_text(unreal.Text("Remove this agent from the session and shut it down"))
            stop.set_visibility(unreal.SlateVisibility.COLLAPSED)
            add(title, stop, padding=self._space(4, 0, 0, 0))
            add(block, title)
            # Who it is private to and the machine that runs it, wrapping when the row is narrow
            details = self.ui.make(unreal.WrapBox)
            private_text = self.ui.text("", size=self._size(9), bold=True, rgb=TEXT)
            machine_text = self.ui.text("", size=self._size(9), rgb=MUTED)
            for text in (private_text, machine_text):
                text.set_visibility(unreal.SlateVisibility.COLLAPSED)
                add(details, text, padding=self._space(0, 0, 8, 0))
            add(block, details)
            status_text = self.ui.text("", size=self._size(9), rgb=MUTED, wrap=True)
            add(block, status_text, padding=self._space(0, 1, 0, 0))
            add(line, block, fill=True, padding=self._space(0, 6, 8, 8))
            # The whole row is one button that picks the agent; only the agent's own buttons inside it
            # take clicks of their own
            for widget in (face_box, name_text, details, status_text):
                widget.set_visibility(unreal.SlateVisibility.HIT_TEST_INVISIBLE)
            line.set_visibility(unreal.SlateVisibility.SELF_HIT_TEST_INVISIBLE)
            button = self.ui.button(line, self._picker(name) if live else (lambda: None))
            add(self.list, button)
            self.rows[name] = {"button": button, "face": face, "name": name_text,
                               "faded": (face_box, name_text, live_dot, live_label, details, status_text),
                               "status_text": status_text, "status": None, "picked": False,
                               "live": (live_dot, live_label), "in_call": None, "private": private_text,
                               "call": controls.get("call"), "mute": controls.get("mute"),
                               "audience": controls.get("audience"), "buttons_shown": None if live else (),
                               "stop": stop, "stop_text": stop_text, "stop_shown": None,
                               "machine": machine_text, "machine_shown": None}

    def _small_button(self, tip: str, on_click) -> dict:
        """One of an agent's buttons, with its label; the label is set as the button's state changes."""
        text = self.ui.text("", size=self._size(9), rgb=TEXT)
        button = self.ui.button(text, on_click, style=self._button_style(False))
        button.set_tool_tip_text(unreal.Text(tip))
        return {"button": button, "text": text, "shown": None}

    def _for_agent(self, action, name: str):
        # Unreal's click event takes nothing, so the agent is bound here rather than as an argument
        return lambda: action(name)

    def _picker(self, name: str):
        if self.vr:
            return lambda: _toggle_pick(name)
        return lambda: _toggle_follow(name)

    def _stopper(self, name: str):
        return lambda: _ask_stop(name)


# The session and starting agents --------------------------------------------------

def _coming_to(station: dict) -> list:
    """Agents asked for on this machine that it doesn't list yet."""
    running = {agent["name"] for agent in station.get("agents") or []}
    return [name for name, (machine, _) in _state["starting"].items() if machine == station["name"] and name not in running]


def _room(station: dict) -> float:
    """How many more agents a machine can start, counting ones on their way: none without
    anything to think with, and any number when it says 0."""
    can = station.get("can") or {}
    if not can.get("brains"):
        return 0
    limit = can.get("max_agents", 1)
    if limit == 0:
        return float("inf")
    return int(limit or 1) - len(station.get("agents") or []) - len(_coming_to(station))


def _free_station(board: dict) -> dict | None:
    """The machine with the most room for another agent, or None."""
    best, best_room = None, 0
    for station in _agent_machines(board):
        room = _room(station)
        if room > best_room:
            best, best_room = station, room
    return best


def _agent_machines(board: dict | None) -> list:
    """The machines that can run agents for this person, for the Machine dropdown: shared ones, and
    their own. Someone else's machine they haven't shared thinks with that person's account."""
    me = hub_client.display_name()
    return [station for station in (board or {}).get("stations", [])
            if ((station.get("can") or {}).get("brains") or (station.get("can") or {}).get("unavailable"))
            and (station.get("public", True) or station.get("owner") == me)]


def _machine_line(station: dict) -> str:
    """What a machine runs, what is on its way there, and whether it has room for more."""
    running = [agent["name"] for agent in station.get("agents") or []]
    coming = _coming_to(station)
    parts = [f"Runs {', '.join(running)}"] if running else []
    if coming:
        parts.append(f"starting {', '.join(coming)}")
    room = _room(station)
    if room == float("inf"):
        parts.append("room for more" if parts else "Can start agents")
    elif room > 0:
        parts.append(f"room for {room} more" if parts else f"Can start {room} agent{'' if room == 1 else 's'}")
    else:
        parts.append("full")
    line = "; ".join(parts)
    return line[:1].upper() + line[1:]


def _picked_machine(board: dict | None) -> dict | None:
    """The machine a new agent starts on: the one picked, while it still runs a station, or else
    the one with the most room, or any that runs agents."""
    machines = _agent_machines(board)
    picked = next((station for station in machines if station["name"] == _state["machine"]), None)
    return picked or _free_station(board or {}) or (machines[0] if machines else None)


def _can_start(board: dict | None) -> tuple[bool, str]:
    """Whether any machine has room for another agent, and why not."""
    if board is None:
        return False, ""
    if _free_station(board) is None:
        return False, "No machine can start an agent right now"
    return True, ""


def _taken_names(board: dict) -> set:
    """Names already in the session or on their way in."""
    return ({entry["name"] for entry in board.get("agents", []) + board.get("people", [])}
            | set(_state["names"]) | set(_state["starting"]))


def _new_agent_name(board: dict, brain: str) -> str:
    """The name the form suggests, after the person who brings it: Lukas Claude, then Lukas Claude 2."""
    person = re.sub(r"[^A-Za-z0-9 ._-]", "", hub_client.display_name()).strip() or "Team"
    base = f"{person} {brain.title()}"[:56]
    taken = _taken_names(board)
    name, number = base, 2
    while name in taken:
        name, number = f"{base} {number}", number + 1
    return name


def _machine_entries(board: dict | None) -> list[tuple[str, str]]:
    """The Machine dropdown, as (label, station): the machines with room this person may use, and
    this machine, which can always be picked, since Add turns it on when it doesn't run agents yet."""
    own = local_station.station_name() if _PERSON_EDITOR else ""
    mine = f"This {local_station.machine_word()}"
    entries = [(mine if station["name"] == own else station["name"], station["name"])
               for station in _agent_machines(board) if _room(station) > 0 or station["name"] == own]
    if own and all(name != own for _, name in entries):
        entries.append((mine, own))
    return entries


def _this_machine_station(board: dict | None) -> dict:
    """This machine as the Model dropdown sees it before its station runs: the quick check's word on
    Claude Code and Codex, or both while that isn't in yet."""
    problems = local_station.brain_problems(board)
    if problems is None:
        return {"can": {"brains": list(local_station.BRAIN_NAMES)}}
    return {"can": {"brains": [brain for brain, why in problems.items() if not why],
                    "unavailable": {brain: why for brain, why in problems.items() if why}}}


def _add_after_turning_on(name: str, brain: str, model: str, effort: str, board: dict | None) -> str:
    """Add for this machine while it doesn't run agents: turns it on, shows the agent on its way in
    at once, and sends it once the station is up. Returns why it can't, or empty."""
    name = " ".join(name.split())
    if not AGENT_NAME.fullmatch(name):
        return "Use letters, digits, spaces, dots, dashes or underscores for the name"
    if name in _taken_names(board or {}):
        return f"{name} is already in the session"
    if local_station.state(board) == "off":
        local_station.turn_on(board)
        if local_station.problem():
            return local_station.problem()
    machine = local_station.station_name()
    _state["machine"] = machine
    _state["start_failed"].pop(name, None)
    _state["pending_add"] = {"name": name, "brain": brain, "model": model, "effort": effort}
    _state["starting"][name] = (machine, time.monotonic())
    return ""


def _add_pending(board: dict | None) -> None:
    """The agent asked for while this machine was off goes in once its station is up; or its row
    says why not, when the station didn't start or can't run the model picked."""
    pending = _state["pending_add"]
    if not pending:
        return
    name = pending["name"]
    station = local_station.own_station(board)
    if station is None:
        # Turning on gave up, or failed and said why
        if local_station.state(board) == "off":
            _state["pending_add"] = None
            _fail_start(name, local_station.problem() or f"this {local_station.machine_word()} didn't start running agents")
        return
    _state["pending_add"] = None
    can = station.get("can") or {}
    brain = pending["brain"]
    if brain not in (can.get("brains") or []):
        # Said in the agent's row, after the form has closed, so it says where signing in is
        how = BRAIN_PROBLEMS.get((can.get("unavailable") or {}).get(brain, ""), "isn't set up on")
        word = local_station.machine_word()
        tool = local_station.BRAIN_NAMES.get(brain, brain)
        _fail_start(name, f"{tool} {how} this {word}; sign in from + Agent with This {word} picked")
        return
    # Counted from now, so the hub's command for it is the one its row follows
    _state["starting"][name] = (station["name"], time.monotonic())
    hub_client.control("agent.start", station=station["name"], name=name, brain=brain,
                       **({"model": pending["model"]} if pending["model"] else {}),
                       **({"effort": pending["effort"]} if pending["effort"] else {}))


def _model_options(station: dict | None) -> list[tuple[str, str, str, str]]:
    """Every model, for the machine picked: (label, brain, model, why it can't run there). One
    whose brain isn't set up there stays listed, marked, so people see it exists."""
    if station is None:
        return []
    can = station.get("can") or {}
    ready = set(can.get("brains") or [])
    unavailable = can.get("unavailable") or {}
    options = []
    for label, brain, model, _ in MODELS:
        problem = "" if brain in ready else str(unavailable.get(brain) or "not set up")
        options.append((f"{label} ({problem})" if problem else label, brain, model, problem))
    return options


def _effort_labels(brain: str) -> list[str]:
    """The Reasoning choices, the same for Claude Code and Codex, which both go up to Max."""
    return list(EFFORT_CHOICES)


def _starting_effort(brain: str, model: str) -> str:
    """How hard a model reasons until someone picks another level."""
    return next((effort for _, entry_brain, entry_model, effort in MODELS
                 if (entry_brain, entry_model) == (brain, model)), "high")


def _brain_refusal(brain: str, problem: str, machine: str) -> str:
    """Why the model picked can't run on that machine, or empty."""
    if not problem:
        return ""
    how = BRAIN_PROBLEMS.get(problem, "isn't set up on")
    tool = "Claude Code" if brain == "claude" else "Codex"
    if _PERSON_EDITOR and machine == local_station.station_name():
        return f"{tool} {how} this {local_station.machine_word()}; use Sign in above, or pick another model"
    fix = {"claude": "claude auth login", "codex": "codex login"}[brain]
    return f"{tool} {how} {machine}; set it up there ({fix}), or pick another model"


def _fill_combo(combo, options: list[str], keep: str) -> None:
    """Puts the options in a dropdown, keeping the one picked while it is still there."""
    combo.clear_options()
    for option in options:
        combo.add_option(option)
    if options:
        combo.set_selected_option(keep if keep in options else options[0])


def _start_named_agent(name: str, brain: str, model: str, effort: str, machine: str) -> str:
    """Add from the form: a new agent with the name given, on the machine and with the model and
    reasoning picked. It shows as on its way in at once. Returns why it can't, or empty."""
    board = hub_client.dashboard() or {}
    name = " ".join(name.split())
    if not AGENT_NAME.fullmatch(name):
        return "Use letters, digits, spaces, dots, dashes or underscores for the name"
    station = next((station for station in _agent_machines(board) if station["name"] == machine), None)
    if station is None or _room(station) <= 0:
        return f"{machine or 'That machine'} can't start another agent right now"
    if brain not in station["can"]["brains"]:
        return f"{machine} can't run {brain.title()}"
    if name in _taken_names(board):
        return f"{name} is already in the session"
    _state["machine"] = machine
    _state["start_failed"].pop(name, None)
    # Shown as on its way in at once; the hub's answer and the station's report follow
    _state["starting"][name] = (machine, time.monotonic())
    hub_client.control("agent.start", station=machine, name=name, brain=brain,
                       **({"model": model} if model else {}), **({"effort": effort} if effort else {}))
    return ""


def _ask_stop(name: str) -> None:
    asked, asked_at = _state["stop_asked"]
    if asked != name or time.monotonic() - asked_at >= STOP_CONFIRM_SECONDS:
        _state["stop_asked"] = (name, time.monotonic())
        return
    _state["stop_asked"] = ("", 0.0)
    # One still starting stops showing as starting
    _state["starting"].pop(name, None)
    hub_client.control("agent.stop", name=name)
    _note_control(f"Removing {name}...")


def _note_control(text: str) -> None:
    _state["control_note"] = text
    _state["control_note_until"] = time.monotonic() + CONTROL_NOTE_SECONDS


def _control_note() -> str:
    return _state["control_note"] if time.monotonic() < _state["control_note_until"] else ""


def _start_command(board: dict | None, name: str, asked_at: float) -> dict | None:
    """The hub's command to start this agent, the latest one; older tries under the same name don't count."""
    asked_seconds_ago = time.monotonic() - asked_at
    found = None
    for command in (board or {}).get("commands", []):
        if command.get("action") == "agent.start" and (command.get("args") or {}).get("name") == name \
                and float(command.get("age") or 0.0) <= asked_seconds_ago + 1.0:
            found = command
    return found


def _failed_start(board: dict | None, name: str, asked_at: float) -> str | None:
    """Why the machine couldn't start this agent, once its station said so."""
    command = _start_command(board, name, asked_at)
    if command is None or command.get("state") != "failed":
        return None
    return command.get("message") or "the machine couldn't start it"


def _start_progress(board: dict | None, name: str, machine: str, asked_at: float) -> str:
    """How far a new agent has got on its way into the session, from the command its machine got."""
    state = (_start_command(board, name, asked_at) or {}).get("state")
    if state == "queued":
        return f"Waiting for {machine} to pick it up..."
    if state == "sent":
        return f"Starting on {machine}..."
    if state == "done":
        return "Joining the session..."
    return f"Adding on {machine}..."


def _fail_start(name: str, message: str) -> None:
    """A start that didn't work keeps its row a while, saying why, then goes."""
    machine = _state["starting"].pop(name, ("", 0.0))[0]
    _state["start_failed"][name] = (machine or "its machine", message, time.monotonic() + FAILED_START_SECONDS)


def _update_starting(board: dict | None) -> None:
    """Hears back from the hub, sends an agent that waited for this machine to turn on, and forgets
    agents on their way once they're in the session or starting them failed."""
    _add_pending(board)
    for asked, error in hub_client.take_control_results():
        if not error:
            continue
        name = asked.get("name", "")
        if asked["action"] == "agent.start":
            _fail_start(name, error)
        elif asked["action"] == "agent.audience":
            _state["audience_asked"].pop(name, None)
            _note_control(f"Couldn't change who hears {name}: {error}")
        else:
            _note_control(f"Couldn't remove {name}: {error}")
    now = time.monotonic()
    for name, (_, since) in list(_state["starting"].items()):
        failed = _failed_start(board, name, since)
        if failed:
            _fail_start(name, failed)
        elif name in _state["names"]:
            del _state["starting"][name]
        elif now - since > STARTING_SECONDS:
            # Its editor may have crashed while starting; the row says so rather than just going away
            _fail_start(name, f"it didn't join within {round(STARTING_SECONDS / 60)} minutes")
    for name, (_, _, until) in list(_state["start_failed"].items()):
        if now >= until or name in _state["names"]:
            del _state["start_failed"][name]


# Following an agent with the camera -----------------------------------------------

def _toggle_follow(name: str) -> None:
    if agent_editor.is_agent_editor() and name == hub_client.display_name() and _avatar(name) is None:
        # In agent-only mode our own camera IS the presence, so frame our work.
        _state["following"] = None
        actors = [a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
                  if any(str(tag).startswith(name + ':') for tag in a.tags)]
        if actors:
            boxes = [a.get_actor_bounds(False) for a in actors]
            low = [min(getattr(c, axis)-getattr(e, axis) for c,e in boxes) for axis in ('x','y','z')]
            high = [max(getattr(c, axis)+getattr(e, axis) for c,e in boxes) for axis in ('x','y','z')]
            center = unreal.Vector(*[(a+b)/2 for a,b in zip(low, high)])
            distance = max(600.0, max(b-a for a,b in zip(low, high))*1.2)
            position = center + unreal.Vector(-distance, -distance, distance*.7)
            unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
                position, unreal.MathLibrary.find_look_at_rotation(position, center))
        return
    _state["following"] = None if _state["following"] == name else name
    _state["last_pose"] = None
    _state["missing_since"] = None
    _state["last_step"] = None
    _rig.reset()
    # Frame immediately on click, including when the editor is not ticking in realtime.
    if _state["following"]:
        ghost = _avatar(name)
        if ghost is not None:
            center = ghost.get_actor_location()
            position = center + unreal.Vector(-450, -450, 220)
            rotation = unreal.MathLibrary.find_look_at_rotation(position, center)
            unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(position, rotation)
            _state["last_pose"] = (position, rotation)


def _follow() -> None:
    name = _state["following"]
    if not name:
        return
    # Slate also ticks inside long editor tasks, several times a frame with the frame's
    # time each, so the camera steps by the clock instead
    now = time.monotonic()
    delta_seconds = min(now - (_state["last_step"] or now), MAX_STEP_SECONDS)
    _state["last_step"] = now
    ghost = _avatar(name)
    if ghost is None:
        if _state["missing_since"] is None:
            _state["missing_since"] = now
        elif now - _state["missing_since"] > LOST_SECONDS:
            _state["following"] = None
        return
    _state["missing_since"] = None
    location, rotation = tracking.camera_info()
    last = _state["last_pose"]
    if last is not None:
        moved = (location - last[0]).length() > TAKEOVER_DISTANCE
        turned = abs(unreal.MathLibrary.normalized_delta_rotator(rotation, last[1]).yaw) > TAKEOVER_DEGREES
        if moved or turned:
            # The person moved the camera themselves
            _state["following"] = None
            return

    new_location, new_rotation = _rig.step(ghost, location, rotation, delta_seconds)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(new_location, new_rotation)
    _state["last_pose"] = (new_location, new_rotation)


# Talking with an agent ------------------------------------------------------------

def _vr_talk():
    """The VR talk button from the CoPresenceScouting plugin, or None without it."""
    return getattr(unreal, "CoPresenceVRTalk", None)


def _in_vr() -> bool:
    talk = _vr_talk()
    return bool(talk.is_in_vr_mode()) if talk is not None else bool(_vr_panels)


def _set_label(control: dict, text: str, style, rgb) -> None:
    shown = (text, rgb)
    if control["shown"] == shown:
        return
    control["shown"] = shown
    control["text"].set_text(unreal.Text(text))
    control["text"].set_color_and_opacity(ui_widgets.slate(rgb))
    control["button"].set_style(style)


def _show(widget, visible: bool, hit_test: bool = True) -> None:
    shown = unreal.SlateVisibility.VISIBLE if hit_test else unreal.SlateVisibility.HIT_TEST_INVISIBLE
    widget.set_visibility(shown if visible else unreal.SlateVisibility.COLLAPSED)


def _audience(name: str) -> tuple[str, bool]:
    """The agent's owner and whether it is public, showing a change asked for here at once."""
    owner, public = _state["audiences"].get(name, ("", True))
    asked = _state["audience_asked"].get(name)
    if asked is not None and time.monotonic() - asked[1] < AUDIENCE_PENDING_SECONDS:
        return owner or hub_client.display_name(), asked[0]
    return owner, public


def _washed_out(name: str) -> bool:
    """Whether the agent is private to someone else, which is when it shows washed out here and in
    the level. Your own private agents and public ones show in full."""
    owner, public = _audience(name)
    return not public and owner not in ("", hub_client.display_name())


def _update_audiences(agents: dict, board: dict | None) -> None:
    """Keeps each agent's owner and whether it is public, from the hub, and lets go of changes asked
    for here once the hub has them."""
    entries = {name: status for name, status in agents.items()}
    entries.update({entry["name"]: entry for entry in (board or {}).get("agents", [])})
    _state["audiences"] = {name: (entry.get("owner") or "", entry.get("public") is not False)
                           for name, entry in entries.items()}
    now = time.monotonic()
    for name, (public, asked_at) in list(_state["audience_asked"].items()):
        if _state["audiences"].get(name, ("", True))[1] == public or now - asked_at >= AUDIENCE_PENDING_SECONDS:
            del _state["audience_asked"][name]


def _may_call(name: str) -> bool:
    """Whether this person may call the agent: it is public, theirs, or nobody's."""
    owner, public = _audience(name)
    return public or owner in ("", hub_client.display_name())


def _toggle_call(name: str) -> None:
    """Call rings the agent, or adds it to the call going on, where you talk to one agent at a time
    by saying its name; in the call, pressing it again hangs up on that agent alone. Calling an
    agent you muted unmutes it, since calling it means you want to hear it."""
    call = _state["call"]
    if name in call:
        call.remove(name)
        if not call:
            _state["mic_muted"] = False
        return
    if not _may_call(name) or len(call) >= MAX_CALL_AGENTS:
        return
    _state["muted_agents"].discard(name)
    call.append(name)


def _hang_up() -> None:
    """Ends the whole call."""
    _state["call"].clear()
    _state["mic_muted"] = False


def _group_call_words(call: tuple) -> tuple[str, bool]:
    """How a call goes: one agent's call as its status says, and with several, who speaks, a
    problem with any of them, or how to talk to one."""
    if len(call) == 1:
        return _call_words(voice_link.status_for(call[0]))
    statuses = {name: voice_link.status_for(name) for name in call}
    for name, status in statuses.items():
        words, problem = _call_words(status)
        if problem:
            return (words if words.startswith("your microphone") else f"{name} {words}"), True
    speaking = [name for name, status in statuses.items() if status == "in a call, speaking"]
    if speaking:
        return f"{speaking[0]} is speaking", False
    if all(status in ("", "ringing") for status in statuses.values()):
        return "ringing", False
    return "say an agent's name to talk to it", False


def _call_words(status: str) -> tuple[str, bool]:
    """How a call goes, in a few words for the call bar, and whether that is a problem."""
    if status.startswith("in a call, but "):
        return status[len("in a call, but "):], True
    if status.startswith("in a call, "):
        return status[len("in a call, "):], False
    return status or "ringing", status not in ("", "ringing", "working on what you said")


def _toggle_mic() -> None:
    """Mute mic in a call: the agents hear quiet while you still hear them."""
    if _state["call"]:
        _state["mic_muted"] = not _state["mic_muted"]


def _toggle_mute(name: str) -> None:
    """Mute stops you hearing the agent, in your call with it and when it talks to others; a call
    with it goes on, and its words still show in the captions."""
    muted = _state["muted_agents"]
    if name in muted:
        muted.discard(name)
    else:
        muted.add(name)


def _toggle_audience(name: str) -> None:
    """Makes the agent public, or keeps it to its owner; the row shows the change at once."""
    public = not _audience(name)[1]
    _state["audience_asked"][name] = (public, time.monotonic())
    hub_client.control("agent.audience", name=name, public=public)


def _call_targets() -> list[str]:
    """The agents this person is in a call with, the first called first. In VR, clicking the left
    stick calls the agent picked in the Agents tool, or the only one around, and clicking it again
    hangs up."""
    talk = _vr_talk() if _in_vr() else None
    down = bool(talk is not None and talk.is_talk_held())
    if down and not _state["stick_down"]:
        names = _state["names"]
        picked = _state["talking_with"] or (names[0] if len(names) == 1 else None)
        if _state["call"]:
            _hang_up()
        elif picked:
            _toggle_call(picked)
    _state["stick_down"] = down
    call = _state["call"]
    if not call:
        return []
    # The call ended on its own, or an agent in it left the session or became private to someone else
    ended = voice_link.call_ended(call[0])
    if ended:
        _note_control(f"Call with {', '.join(call)} ended: {ended}")
        _hang_up()
        return []
    call[:] = [name for name in call if name in _state["names"] and _may_call(name)]
    if not call:
        _state["mic_muted"] = False
    return list(call)


def _toggle_pick(name: str) -> None:
    """Tapping an agent in VR picks it: it comes over and waits by you, and gets your drawings."""
    previous = _state["talking_with"]
    _state["talking_with"] = None if previous == name else name
    if previous:
        hub_client.post_conversation(previous, "")
    # Said to the hub right away, then repeated
    _state["conversation_at"] = 0.0
    _keep_talking()


def _keep_talking() -> None:
    """Repeats the conversation to the hub, which forgets one nobody repeats, and ends it once
    VR Mode has closed."""
    name = _state["talking_with"]
    if not name:
        return
    if not _in_vr():
        _state["talking_with"] = None
        hub_client.post_conversation(name, "")
        return
    now = time.monotonic()
    if now - _state["conversation_at"] >= CONVERSATION_REPEAT_SECONDS:
        _state["conversation_at"] = now
        hub_client.post_conversation(name, hub_client.display_name())


def _drawing_tool():
    """The Agents tool's drawing in VR, from the CoPresenceScouting plugin, or None without it."""
    return getattr(unreal, "CoPresenceAgentsToolActor", None)


def _drawing_target() -> str | None:
    """Whom a drawing goes to: the agent picked in VR, or else the only agent in the call."""
    call = _state["call"]
    return _state["talking_with"] or (call[0] if len(call) == 1 else None)


def _send_drawing() -> None:
    tool = _drawing_tool()
    agent = _drawing_target()
    if tool is None or not agent or _state["sending_drawing"]:
        return
    # The pictures for the agent take a few frames; _post_taken_drawing sends them on
    if tool.take_drawing():
        _state["sending_drawing"] = agent


def _undo_stroke() -> None:
    tool = _drawing_tool()
    if tool is not None:
        tool.undo_stroke()


def _clear_drawing() -> None:
    tool = _drawing_tool()
    if tool is not None:
        tool.clear_drawing()


def _note_drawing(text: str) -> None:
    _state["drawing_note"] = text
    _state["drawing_note_until"] = time.monotonic() + DRAWING_NOTE_SECONDS


def _post_taken_drawing() -> None:
    """Posts the drawing to the agent once its pictures are ready, as the VR game sends one."""
    agent = _state["sending_drawing"]
    tool = _drawing_tool()
    if not agent or tool is None:
        return
    if not _vr_panels:
        # VR Mode ended and took the drawing with it
        _state["sending_drawing"] = None
        return
    taken = tool.get_taken_drawing()
    if not taken:
        return
    _state["sending_drawing"] = None
    drawing = json.loads(taken)
    if "annotation" not in drawing:
        _note_drawing("Couldn't take pictures of the drawing; send it again")
        return
    hub_client.post_message(drawing["text"], agent, drawing["selection"], drawing["annotation"], source="vr")
    tool.clear_drawing()
    _note_drawing(f"{agent} has your drawing")


def _shown_vr_panels() -> list:
    panel_class = getattr(unreal, "CoPresenceAgentsPanel", None)
    return list(panel_class.get_shown_panels()) if panel_class else []


def _update_vr_panels(shown: list, agents: dict, seconds_since_poll: float, board: dict | None) -> None:
    """Fills the Agents tabs Virtual Scouting's VR menu shows, and lets go of closed ones."""
    for panel in [panel for panel in _vr_panels if panel not in shown]:
        del _vr_panels[panel]
    if shown:
        # Moving the camera after an agent would move the person around in VR
        _state["following"] = None
    for panel in shown:
        if panel not in _vr_panels:
            _vr_panels[panel] = _StatusPanel(panel, vr=True)
        _vr_panels[panel].update(agents, seconds_since_poll, board)


# Opening and updating -------------------------------------------------------------

tab = ui_widgets.EditorTab(PANEL_ASSET, _StatusPanel)


def open_panel() -> None:
    """Opens the Agents widget, or brings it to the front."""
    tab.open()


def _update_captions(agents: dict) -> None:
    """The call's live captions, what you say and what the agent says back: along the bottom of the
    level viewport where the caption plugin (CoPresenceAnnotate) is built, a little below where you
    look in VR Mode (CoPresenceScouting), and in the Agents tab where neither shows them."""
    agent, heard, said = voice_link.captions(_state["call"])
    shown = (agent, heard, said)
    if shown == _state["captions"]:
        return
    _state["captions"] = shown
    vr_captions = getattr(unreal, "CoPresenceVRCaptions", None)
    if vr_captions is not None:
        vr_captions.set_captions(heard, agent, said)
    library = getattr(unreal, "CoPresenceCaptionLibrary", None)
    if library is None:
        return
    appearance = (agents.get(agent) or {}).get("appearance")
    rgb = agents_panel.agent_color(agent, appearance) if agent else ui_widgets.PERSON
    library.set_captions(heard, agent, said, ui_widgets.color(rgb))


def _refresh() -> None:
    agents, _, seconds_since_poll, _ = hub_client.snapshot()
    _state["names"] = agents_panel.agent_names(agents, seconds_since_poll)
    panel = tab.current()
    shown = _shown_vr_panels()
    # The hub's dashboard is only asked for while the session shows somewhere
    watching = panel is not None or shown or _state["pending_add"] or (_PERSON_EDITOR and local_station.may_be_running())
    board = hub_client.dashboard() if watching else None
    _update_starting(board)
    _update_audiences(agents, board)
    if _PERSON_EDITOR:
        _update_captions(agents)
    if panel is not None:
        panel.update(agents, seconds_since_poll, board)
    _update_vr_panels(shown, agents, seconds_since_poll, board)
    if _PERSON_EDITOR:
        _keep_talking()
        _post_taken_drawing()
        voice_link.ensure_helper()
        if board is not None:
            own = local_station.station_name()
            busy = bool(_state["pending_add"]) or any(machine == own for machine, _ in _state["starting"].values())
            local_station.tick(board, busy)
    problem = hub_client.take_conversation_problem()
    if problem and problem != _state["last_warning"]:
        _state["last_warning"] = problem
        unreal.log_warning(f"CoPresence Agents widget: {problem}")


def _tick(delta_seconds: float) -> None:
    try:
        _follow()
        # Rows refresh a few times a second; the faces animate every frame
        _state["elapsed"] += delta_seconds
        refreshed = _state["elapsed"] >= REFRESH_SECONDS
        if refreshed:
            _state["elapsed"] = 0.0
            _refresh()
        # The VR talk button is read every frame, so a click on it is never missed
        if _PERSON_EDITOR:
            call = (tuple(_call_targets()), tuple(sorted(_state["muted_agents"])), _state["mic_muted"])
            if refreshed or call != _state["voice_sent"]:
                _state["voice_sent"] = call
                voice_link.update(list(call[0]), list(call[1]), call[2])
        now = time.monotonic()
        for ui in (tab.ui, *_vr_panels.values()):
            if ui is not None:
                ui.animate(now, delta_seconds)
    except Exception as error:
        tab.reset()
        _vr_panels.clear()
        if str(error) != _state["last_warning"]:
            _state["last_warning"] = str(error)
            unreal.log_warning(f"CoPresence Agents widget: {error}")


def start() -> None:
    if _state["callback"] is not None:
        return
    hub_client.start()
    ui_widgets.register_menu_entry(
        "CoPresenceAgents", "Agents", "The session, every agent in it, how it feels and what it is doing. Click one to "
        "follow it, Call to talk with it out loud", "from copresence import agents_hud; agents_hud.open_panel()",
        "Icons.Visible")
    _state["callback"] = unreal.register_slate_post_tick_callback(_tick)
