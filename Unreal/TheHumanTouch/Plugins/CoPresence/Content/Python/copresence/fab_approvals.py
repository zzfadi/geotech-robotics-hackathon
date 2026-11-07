"""Approvals for agents' Fab requests, in people's editors. When an agent asks a person for
a Fab listing, their editor opens it in the Fab tab, reads what the page says about it,
and shows a popup: who asks and why, a picture, the price, size and license, and whether
the publisher allows use with AI. The popup's Add to Project presses the one in the Fab
tab, as if the person had clicked it there, and fab_requests then shares the import with
the session and tells the agent. People can let free, small listings from their own
agents through without asking.

Needs the CoPresence Fab plugin, which reaches into the Fab tab and gives the popup a
window of its own. Without it, people add listings with Open in Fab in Agent Chat instead.
"""

import json
import queue
import re
import tempfile
import threading
import time
import urllib.request
from enum import Enum, auto
from pathlib import Path

import unreal

from copresence import agent_editor, agents_panel, fab_requests, hub_client, ui_widgets
from copresence.ui_widgets import ERROR, MUTED, PERSON, SUCCESS, TEXT, add

DIALOG_TITLE = "Fab Request"
DIALOG_WIDTH = 520.0
# Fab's pictures are 16:9; the popup shows one across its top
PICTURE_HEIGHT = 292.0
PICTURE_BACKGROUND = (0.006, 0.006, 0.006)
FACT_LABEL_WIDTH = 76.0
BUTTON_PADDING = (16, 7, 16, 7)
BUTTON_RADIUS = 4.0
THROBBER_DOT_SIZE = 6.0
SECONDARY_BUTTON = (ui_widgets.SELECTED, ui_widgets.PRESSED, ui_widgets.HOVER, ui_widgets.HOVER)
PRIMARY_BUTTON = (ui_widgets.PRIMARY, ui_widgets.PRIMARY_HOVER, ui_widgets.PRIMARY_PRESSED, ui_widgets.HOVER)
FORMAT_NAMES = {"gltf": "glTF", "glb": "GLB", "fbx": "FBX", "obj": "OBJ", "usd": "USD", "usdz": "USDZ"}
TICK_SECONDS = 0.25
# Waits for the Fab tab: to show the listing, for its page to fill in, and to answer
OPEN_TIMEOUT_SECONDS = 20.0
READ_DELAY_SECONDS = 2.0
READ_INTERVAL_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 25.0
ADD_TIMEOUT_SECONDS = 15.0
CLOSE_AFTER_SECONDS = 4.0
PICTURE_TIMEOUT_SECONDS = 15
# Fab's image server turns away Python's default user agent
PICTURE_USER_AGENT = "CoPresence/0.1"
IMPORTABLE_FORMATS = {"gltf", "glb", "fbx", "obj", "usdz"}
# Buttons in a Fab dialog that confirm adding a listing or accept its license. The page
# runs these patterns too, so they stay plain enough for JavaScript.
CONFIRM_LABEL = re.compile(r"^(add( to project)?|continue|confirm|ok|got it|yes|(i )?(accept|agree)( to)?"
                           r"( (the )?(license|terms|eula))?( (and|&) (continue|add))?)$", re.IGNORECASE)
# Paying, and cookie choices, are never pressed
NEVER_PRESS_LABEL = re.compile(r"buy|cart|checkout|purchase|pay|subscribe|cookie", re.IGNORECASE)

# Reports what the listing page says: title, seller, price, file sizes, formats, license,
# whether the publisher allows use with AI, and a picture
READ_SCRIPT = r"""
(function (listingId) {
  const send = (payload) => window.ue.copresence.report(JSON.stringify(payload));
  const lines = (document.body ? document.body.innerText : '').split('\n').map((line) => line.trim()).filter(Boolean);
  const after = (label) => { const index = lines.lastIndexOf(label); return index >= 0 && index + 1 < lines.length ? lines[index + 1] : ''; };
  const controls = [...document.querySelectorAll('button, [role="button"]')].map((element) => (element.innerText || element.getAttribute('aria-label') || '').trim());
  const title = document.title.replace(/\s*\|\s*Fab\s*$/, '');
  const titleIndex = lines.lastIndexOf(title);
  const addIndex = lines.indexOf('Add to Project');
  const formatsStart = lines.lastIndexOf('Included formats');
  const formatsEnd = formatsStart >= 0 ? lines.indexOf('Details', formatsStart) : -1;
  const sources = [...document.querySelectorAll('img')].map((image) => image.currentSrc || image.src);
  const picture = sources.find((source) => /image_previews\/gallery_images/.test(source)) ||
    sources.find((source) => /image_previews\/listing_thumbnails/.test(source)) || '';
  send({
    kind: 'read', listing: listingId, page: location.href.indexOf(listingId) >= 0, ready: title && title !== 'Fab' && titleIndex >= 0 && controls.length > 0,
    title: title, seller: titleIndex > 0 ? lines[titleIndex - 1] : '', price: addIndex > 0 ? lines[addIndex - 1] : '',
    can_add: controls.indexOf('Add to Project') >= 0, signed_in: controls.indexOf('Sign in') < 0,
    sizes: lines.filter((line, index) => index > 0 && lines[index - 1] === 'File size'),
    formats: formatsStart >= 0 ? lines.slice(formatsStart + 1, formatsEnd > formatsStart ? formatsEnd : formatsStart + 8) : [],
    license: after('License terms'), ai_use: after('Allows usage with AI'), picture: picture
  });
})(%(listing)s);
"""
# Presses Add to Project. Fab may ask first or afterwards, for example to accept the
# listing's license: those dialogs are confirmed, ticking a license box if they have one,
# and the ones it can't answer are reported.
ADD_SCRIPT = r"""
(function (listingId, confirmPattern, neverPattern) {
  const send = (payload) => window.ue.copresence.report(JSON.stringify(payload));
  const confirm = new RegExp(confirmPattern, 'i');
  const never = new RegExp(neverPattern, 'i');
  const label = (element) => (element.innerText || element.getAttribute('aria-label') || '').trim();
  const answered = new WeakSet();
  let reported = '';
  const answerDialog = () => {
    const dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]')]
      .filter((element) => element.getClientRects().length > 0 && !answered.has(element));
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) { return; }
    // A license box usually has to be ticked before the button works
    const boxes = dialog.querySelectorAll('input[type="checkbox"]:not(:checked), [role="checkbox"][aria-checked="false"]');
    for (const box of boxes) {
      const boxText = ((box.closest('label') || box.parentElement || box).innerText || box.getAttribute('aria-label') || '');
      if (/agree|accept|licen[cs]e|terms|eula/i.test(boxText) && !/newsletter|marketing|email|subscri/i.test(boxText)) { box.click(); }
    }
    const buttons = [...dialog.querySelectorAll('button, [role="button"]')];
    const confirmable = buttons.filter((element) => confirm.test(label(element)) && !never.test(label(element)));
    if (!confirmable.length) {
      const text = (dialog.innerText || '').slice(0, 600);
      // Cookie banners are the person's choice and don't block adding
      if (text !== reported && !/cookie/i.test(text)) {
        reported = text;
        send({kind: 'dialog', listing: listingId, text: text, buttons: buttons.map(label).filter(Boolean)});
      }
      return;
    }
    // A button the box just enabled is pressed on the next check
    const choice = confirmable.find((element) => !element.disabled && element.getAttribute('aria-disabled') !== 'true');
    if (!choice) { return; }
    answered.add(dialog);
    choice.click();
    send({kind: 'confirmed', listing: listingId, label: label(choice)});
  };
  answerDialog();
  const button = [...document.querySelectorAll('button, [role="button"]')].find((element) => label(element) === 'Add to Project');
  if (location.href.indexOf(listingId) < 0 || !button || button.disabled) {
    send({kind: 'added', listing: listingId, pressed: false});
    return;
  }
  button.click();
  send({kind: 'added', listing: listingId, pressed: true});
  let checks = 0;
  const timer = setInterval(() => {
    answerDialog();
    checks += 1;
    if (checks >= 24) { clearInterval(timer); }
  }, 500);
})(%(listing)s, %(confirm)s, %(never)s);
"""

_state = {"callback": None, "elapsed": 0.0, "last_message": None, "queue": [], "active": None, "channel": None,
          "popup": None,
          # The popup's status line, which the Agents tab in VR shows too: (text, tone)
          "status": ("", None)}
# Downloaded listing pictures, handed from the download thread to the game thread
_pictures: "queue.Queue[tuple[str, Path]]" = queue.Queue()


def is_available() -> bool:
    return hasattr(unreal, "CoPresenceFabLibrary") and not agent_editor.is_agent_editor()


# Settings ---------------------------------------------------------------------

def _settings_path() -> Path:
    return Path(unreal.Paths.project_saved_dir()) / "CoPresence" / "fab.json"


def _settings() -> dict:
    # Read every time: the person's own agent can change it when they ask it to
    path = _settings_path()
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def _set_auto_approve(enabled: bool) -> None:
    settings = _settings()
    settings["auto_approve_own_agents"] = enabled
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings))


def _is_own_agent(agent: str) -> bool:
    # Personal agents are named after the person who brought them, e.g. "Lukas Claude"
    person = hub_client.display_name()
    return agent.startswith(person + " ") or agent in {f"Codex {person}", f"Claude {person}"}


# What the page says ------------------------------------------------------------

def _megabytes(size: str) -> float | None:
    match = re.match(r"([\d.,]+)\s*(KB|MB|GB)", size.strip(), re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "")) * {"kb": 1 / 1024, "mb": 1.0, "gb": 1024.0}[match.group(2).lower()]


def _is_pack(info: dict) -> bool:
    formats = {item.lower() for item in info.get("formats", [])}
    return "unreal engine" in formats and not formats & IMPORTABLE_FORMATS


def _largest_size(info: dict) -> tuple[str, float] | None:
    """The listing's biggest download, as Fab writes it and in MB."""
    sizes = [(text, _megabytes(text)) for text in info.get("sizes", [])]
    known = [size for size in sizes if size[1] is not None]
    return max(known, key=lambda size: size[1]) if known else None


def _auto_approves(request: dict) -> bool:
    """People can let free listings their own agents ask for through without asking, as
    long as the publisher allows use with AI."""
    info = request["info"]
    return (_settings().get("auto_approve_own_agents", False) and _is_own_agent(request["agent"])
            and info.get("can_add") and info.get("price", "").lower() == "free"
            and info.get("ai_use", "").lower() != "no")


def _facts(info: dict) -> list[tuple[str, str, tuple]]:
    """The rows the popup shows about a listing: label, value and the value's color."""
    facts = []
    price = info.get("price", "")
    if price:
        facts.append(("Price", price, SUCCESS if price.lower() == "free" else TEXT))
    largest = _largest_size(info)
    if largest:
        facts.append(("Size", largest[0] if len(info["sizes"]) == 1 else f"Up to {largest[0]}", TEXT))
    formats = info.get("formats", [])
    if formats:
        named = ", ".join(FORMAT_NAMES.get(item.lower(), item) for item in formats)
        facts.append(("Formats", "Unreal Engine pack" if _is_pack(info) else named, TEXT))
    if info.get("license"):
        facts.append(("License", info["license"], TEXT))
    ai_use = info.get("ai_use", "").lower()
    if ai_use:
        facts.append(("AI use", "Not allowed" if ai_use == "no" else "Allowed", ERROR if ai_use == "no" else SUCCESS))
    return facts


def _reason(text: str) -> str:
    """What an agent's request says about why, without the ask and the link."""
    reason = fab_requests.without_listing_link(text)
    return re.sub(r'^Could you add ".*?" from Fab\?\s*', "", reason)


# The popup ---------------------------------------------------------------------

class _Tone(Enum):
    """How the popup's status line reads: a note, work under way, done, or a problem."""
    NOTE = auto()
    BUSY = auto()
    DONE = auto()
    PROBLEM = auto()


class _ApprovalPopup:
    """The popup for one request, in a window of its own: the listing's picture across the
    top, who asks and why, what the listing is, and Decline or Add to Project."""

    def __init__(self, request: dict):
        self.request = request
        self.ui = ui_widgets.Widgets(None)
        self.picture_requested = False
        self.picture_failed = False
        agents = hub_client.snapshot()[0]
        self.agent_rgb = agents_panel.agent_color(request["agent"], (agents.get(request["agent"]) or {}).get("appearance"))

        page = self.ui.make(unreal.VerticalBox)
        self.root = self.ui.sized(page, DIALOG_WIDTH, None)
        self.hero = self.ui.sized(self._picture_frame(), None, PICTURE_HEIGHT)
        add(page, self.hero)

        body = self.ui.make(unreal.VerticalBox)
        add(page, self.ui.border(ui_widgets.SURFACE, body, padding=(20, 16, 20, 18)))
        asker = self.ui.make(unreal.HorizontalBox)
        add(asker, self.ui.text("\u25CF", size=9, rgb=self.agent_rgb), padding=(0, 0, 6, 0))
        add(asker, self.ui.text(f"{request['agent']} asks to add", size=9, rgb=MUTED))
        add(body, asker)
        self.details = self.ui.make(unreal.VerticalBox)
        add(body, self.details, padding=(0, 4, 0, 0))
        if request["reason"]:
            add(body, self._quote(request["reason"]), padding=(0, 14, 0, 0))
        self.facts = self.ui.make(unreal.VerticalBox)
        add(body, self.facts, padding=(0, 14, 0, 0))
        add(body, self._status_row(), padding=(0, 10, 0, 0))

        add(page, self.ui.line())
        add(page, self.ui.border(ui_widgets.BACKGROUND, self._footer(), padding=(20, 12, 20, 12)))
        self.show_info()

    def _picture_frame(self) -> unreal.Border:
        self.picture = self.ui.make(unreal.Image)
        # An image without a texture draws white, so it waits hidden for the picture
        self.picture.set_visibility(unreal.SlateVisibility.HIDDEN)
        scale = self.ui.make(unreal.ScaleBox)
        scale.set_stretch(unreal.Stretch.SCALE_TO_FILL)
        scale.set_content(self.picture)
        frame = self.ui.border(PICTURE_BACKGROUND, scale)
        frame.set_clipping(unreal.WidgetClipping.CLIP_TO_BOUNDS)
        return frame

    def _quote(self, text: str) -> unreal.HorizontalBox:
        """The agent's reason, marked with its color."""
        row = self.ui.make(unreal.HorizontalBox)
        add(row, self.ui.sized(self.ui.border(self.agent_rgb), 2, None)).set_vertical_alignment(
            unreal.VerticalAlignment.V_ALIGN_FILL)
        add(row, self.ui.text(text, rgb=TEXT, wrap=True), fill=True, padding=(10, 1, 0, 1))
        return row

    def _status_row(self) -> unreal.HorizontalBox:
        row = self.ui.make(unreal.HorizontalBox)
        self.throbber = self.ui.make(unreal.Throbber)
        self.throbber.set_number_of_pieces(3)
        # Its default pieces don't draw in editor windows, so they're small dots
        dot = ui_widgets.brush(TEXT, radius=THROBBER_DOT_SIZE / 2)
        dot_size = unreal.DeprecateSlateVector2D()
        # This size type has no fields in Python, only text
        dot_size.import_text(f"(X={THROBBER_DOT_SIZE},Y={THROBBER_DOT_SIZE})")
        dot.set_editor_property("image_size", dot_size)
        self.throbber.set_editor_property("image", dot)
        self.throbber.set_visibility(unreal.SlateVisibility.COLLAPSED)
        add(row, self.throbber, padding=(0, 0, 8, 0))
        self.status = self.ui.text("Reading the listing in your Fab tab...", size=9, rgb=MUTED, wrap=True)
        add(row, self.status, fill=True)
        return row

    def _footer(self) -> unreal.HorizontalBox:
        row = self.ui.make(unreal.HorizontalBox)
        if _is_own_agent(self.request["agent"]):
            # Kept on the popup for as long as it is open: Unreal forgets what ticking the box does
            # once Python no longer holds the box
            self.auto_box = self.ui.make(unreal.CheckBox)
            self.auto_box.set_is_checked(_settings().get("auto_approve_own_agents", False))
            self.auto_box.on_check_state_changed.add_callable(_set_auto_approve)
            self.auto_box.set_content(self.ui.text("Auto-add free listings from my agents", size=9, rgb=TEXT, wrap=True))
            # The editor's own look, with room between the box and its label
            check_style = unreal.CoPresenceFabLibrary.get_editor_check_box_style()
            check_style.set_editor_property("padding", ui_widgets.margin(6, 0, 0, 0))
            self.auto_box.set_editor_property("widget_style", check_style)
            self.auto_box.set_tool_tip_text(unreal.Text(
                "Free listings your own agents ask for are added without asking, accepting the license Fab "
                "asks for. Paid listings and listings whose publisher forbids use with AI always ask."))
            add(row, self.auto_box, fill=True, padding=(0, 0, 12, 0))
        else:
            add(row, self.ui.make(unreal.Spacer), fill=True)
        self.decline_button = self._button("Decline", SECONDARY_BUTTON, False, lambda: _decide(False))
        add(row, self.decline_button, padding=(0, 0, 8, 0))
        self.approve_button = self._button("Add to Project", PRIMARY_BUTTON, True, lambda: _decide(True))
        add(row, self.approve_button)
        return row

    def _button(self, label: str, colors: tuple, bold: bool, on_click) -> unreal.Button:
        style = ui_widgets.button_style(*colors, padding=BUTTON_PADDING, radius=BUTTON_RADIUS)
        return self.ui.button(self.ui.text(label, bold=bold, rgb=PERSON), on_click, style=style)

    def show_info(self) -> None:
        info = self.request["info"]
        self.details.clear_children()
        title = info.get("title") or self.request["title"] or "Fab listing"
        add(self.details, self.ui.text(title, size=15, bold=True, rgb=PERSON, wrap=True))
        if info.get("seller"):
            add(self.details, self.ui.text(f"by {info['seller']}", rgb=MUTED), padding=(0, 2, 0, 0))

        self.facts.clear_children()
        for label, value, rgb in _facts(info):
            row = self.ui.make(unreal.HorizontalBox)
            add(row, self.ui.sized(self.ui.text(label, rgb=MUTED), FACT_LABEL_WIDTH, None)).set_vertical_alignment(
                unreal.VerticalAlignment.V_ALIGN_TOP)
            add(row, self.ui.text(value, rgb=rgb, wrap=True), fill=True).set_vertical_alignment(
                unreal.VerticalAlignment.V_ALIGN_TOP)
            add(self.facts, row, padding=(0, 0, 0, 5))

        has_picture = bool(info.get("picture")) and not self.picture_failed
        self.hero.set_visibility(unreal.SlateVisibility.VISIBLE if has_picture else unreal.SlateVisibility.COLLAPSED)
        if has_picture and not self.picture_requested:
            self.picture_requested = True
            _download_picture(self.request["listing"], info["picture"])
        self.approve_button.set_is_enabled(bool(info.get("can_add")))

    def show_picture(self, path: Path | None) -> None:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        texture = unreal.RenderingLibrary.import_file_as_texture2d(world, str(path)) if path else None
        if texture is None:
            self.picture_failed = True
            self.hero.set_visibility(unreal.SlateVisibility.COLLAPSED)
            return
        self.picture.set_brush_from_texture(texture, True)
        self.picture.set_visibility(unreal.SlateVisibility.HIT_TEST_INVISIBLE)

    def set_status(self, text: str, tone: _Tone) -> None:
        rgb = {_Tone.DONE: SUCCESS, _Tone.PROBLEM: ERROR}.get(tone, MUTED)
        self.status.set_text(unreal.Text(text))
        self.status.set_color_and_opacity(ui_widgets.slate(rgb))
        self.throbber.set_visibility(unreal.SlateVisibility.HIT_TEST_INVISIBLE if tone == _Tone.BUSY
                                     else unreal.SlateVisibility.COLLAPSED)

    def lock(self) -> None:
        self.approve_button.set_is_enabled(False)
        self.decline_button.set_is_enabled(False)


def _download_picture(listing_id: str, url: str) -> None:
    """Fetches the listing's picture off the game thread; the tick shows it once it's here,
    or drops the picture from the popup if it can't be fetched."""
    target = Path(tempfile.gettempdir()) / "copresence-fab" / f"{listing_id}{Path(url).suffix or '.jpg'}"

    def download() -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(url, headers={"User-Agent": PICTURE_USER_AGENT})
            with urllib.request.urlopen(request, timeout=PICTURE_TIMEOUT_SECONDS) as response:
                target.write_bytes(response.read())
            _pictures.put((listing_id, target))
        except Exception:
            # The popup works without a picture
            _pictures.put((listing_id, None))

    threading.Thread(target=download, name="CoPresenceFabPicture", daemon=True).start()


def _show_downloaded_pictures() -> None:
    while not _pictures.empty():
        listing_id, path = _pictures.get_nowait()
        popup = _popup_ui()
        if popup and popup.request["listing"] == listing_id:
            popup.show_picture(path)


def _show_popup(request: dict) -> None:
    popup = _ApprovalPopup(request)
    unreal.CoPresenceFabLibrary.open_dialog(popup.root, unreal.Text(DIALOG_TITLE))
    _state["popup"] = {"ui": popup, "close_at": None}
    _state["status"] = (popup.status.get_text(), _Tone.NOTE)


def shown_request() -> dict | None:
    """The request the popup asks about, for the Agents tab in VR, where the popup's window doesn't
    show: who asks, the listing and its facts, the popup's status line, and whether it waits for an
    answer; None while there is no popup."""
    popup = _popup_ui()
    if popup is None:
        return None
    request = popup.request
    info = request["info"]
    text, tone = _state["status"]
    return {"listing": request["listing"], "agent": request["agent"],
            "title": info.get("title") or request["title"] or "Fab listing",
            "facts": {label: value for label, value, _rgb in _facts(info)}, "status": str(text),
            # A note only says what Add to Project does; the rest say how it goes
            "news": tone in (_Tone.BUSY, _Tone.DONE, _Tone.PROBLEM),
            "problem": tone == _Tone.PROBLEM, "done": tone == _Tone.DONE,
            "deciding": request["stage"] == "deciding", "can_add": bool(info.get("can_add"))}


def decide(approved: bool) -> None:
    """Adds the listing the popup asks about, or declines it, as its buttons do."""
    _decide(approved)


def _popup_ui() -> _ApprovalPopup | None:
    return _state["popup"]["ui"] if _state["popup"] else None


def _close_popup() -> None:
    if _state["popup"]:
        unreal.CoPresenceFabLibrary.close_dialog(_state["popup"]["ui"].root)
        _state["popup"] = None


def _popup_closed_by_person() -> bool:
    popup = _state["popup"]
    return bool(popup) and not unreal.CoPresenceFabLibrary.is_dialog_open(popup["ui"].root)


# The Fab tab -------------------------------------------------------------------

def _run(script: str, **values) -> bool:
    encoded = {name: json.dumps(value) for name, value in values.items()}
    return unreal.CoPresenceFabLibrary.run_script_in_fab_tab(script % encoded)


def _on_report(payload: str) -> None:
    """What a script in the Fab tab's page reports, through the CoPresence Fab plugin."""
    try:
        _handle_report(json.loads(payload))
    except Exception as error:
        unreal.log_warning(f"CoPresence Fab approvals: {error}")
        _status(f"Something went wrong: {error}", _Tone.PROBLEM)


def _handle_report(report: dict) -> None:
    request = _state["active"]
    if request is None or report.get("listing") != request["listing"]:
        return
    kind = report.get("kind")
    if kind == "read" and report.get("page") and report.get("ready"):
        if request["stage"] == "reading":
            _read(request, report)
        elif request["stage"] == "deciding" and _popup_ui():
            # The person may have signed in or added it to their library meanwhile
            request["info"] = report
            _popup_ui().show_info()
            _explain(report)
    elif kind == "added" and request["stage"] == "adding":
        if report.get("pressed"):
            _set_stage(request, "importing")
            _status("Add requested. Waiting for Fab to finish importing; check the Fab tab for any prompts.", _Tone.BUSY)
        else:
            _status("Couldn't press Add to Project. Click it in your Fab tab to add it.", _Tone.PROBLEM)
            _set_stage(request, "importing")
    elif kind == "confirmed" and request["stage"] in ("adding", "importing"):
        _status(f'Answered Fab with "{report.get("label", "")}". Adding it...', _Tone.BUSY)
    elif kind == "dialog" and request["stage"] in ("adding", "importing"):
        # The page confirms what it can; this one needs the person
        unreal.log_warning(f"CoPresence Fab approvals: Fab asked {report.get('text', '')!r} "
                           f"with buttons {report.get('buttons', [])}")
        _status("Fab is asking something in your Fab tab; answer it there.", _Tone.PROBLEM)


def _read(request: dict, info: dict) -> None:
    request["info"] = info
    _set_stage(request, "deciding")
    _show_popup(request)
    if _auto_approves(request):
        # Still shown, so people see what came in and can turn this off
        _approve(request)
        _status("Adding it without asking: it's free and your agent asked. Untick the box below to stop that.",
                _Tone.BUSY)
    else:
        _explain(info)


def _explain(info: dict) -> None:
    if not info.get("signed_in"):
        _status("Sign in to Fab in your Fab tab, then add it.", _Tone.PROBLEM)
    elif not info.get("can_add"):
        _status("This listing can't be added straight away: it isn't free or isn't yours yet. See it in your Fab tab.",
                _Tone.PROBLEM)
    elif _is_pack(info):
        _status("An Unreal Engine pack stays on your machine and reaches the others through git.", _Tone.NOTE)
    else:
        _status("Adds it to your Fab library under its license, then imports it into the project.", _Tone.NOTE)


def _status(text: str, tone: _Tone) -> None:
    _state["status"] = (text, tone)
    popup = _popup_ui()
    if popup:
        popup.set_status(text, tone)


def _set_stage(request: dict, stage: str) -> None:
    request.update(stage=stage, stage_time=time.time())


# Deciding ----------------------------------------------------------------------

def _approve(request: dict) -> None:
    # Starts watching before pressing, so everything the import adds counts as new
    fab_requests.track_import(request["listing"], request["agent"], request["info"].get("title") or request["title"])
    _set_stage(request, "adding")
    popup = _popup_ui()
    if popup:
        popup.lock()
    _status("Adding it through your Fab tab...", _Tone.BUSY)
    _run(ADD_SCRIPT, listing=request["listing"], confirm=CONFIRM_LABEL.pattern, never=NEVER_PRESS_LABEL.pattern)


def _decide(approved: bool) -> None:
    request = _state["active"]
    if request is None or request["stage"] != "deciding":
        return
    if approved:
        _approve(request)
        return
    _decline(request)


def _decline(request: dict) -> None:
    title = request["info"].get("title") or request["title"] or "the listing"
    hub_client.post_message(f'I declined "{title}" from Fab.', request["agent"], [])
    _close_popup()
    _state["active"] = None


# Watching and moving along -----------------------------------------------------

def _watch_chat() -> None:
    """Queues new Fab requests from agents to this person, or from their agents to everyone."""
    _agents, messages, _seconds_since_poll, online = hub_client.snapshot()
    if _state["last_message"] is None:
        # Only requests from now on, once the hub has sent its history; earlier ones have
        # the Open in Fab button
        if online:
            _state["last_message"] = max((message["id"] for message in messages), default=0)
        return
    me = hub_client.display_name()
    for message in messages:
        if message["id"] <= _state["last_message"]:
            continue
        _state["last_message"] = message["id"]
        listing_id = fab_requests.listing_in(message["text"])
        if message["role"] != "agent" or listing_id is None:
            continue
        if message["to"] != me and not (not message["to"] and _is_own_agent(message["author"])):
            continue
        known = [request["listing"] for request in _state["queue"]] + ([_state["active"]["listing"]] if _state["active"] else [])
        if listing_id not in known and not fab_requests.is_tracking(listing_id):
            _state["queue"].append({"listing": listing_id, "agent": message["author"],
                                    "title": fab_requests.title_in(message["text"]), "reason": _reason(message["text"]),
                                    "info": {}, "stage": "queued", "stage_time": time.time(), "last_read": 0.0})


def _start(request: dict) -> None:
    _state["active"] = request
    if fab_requests.finish_if_imported(request["listing"], request["agent"], request["title"]):
        _state["active"] = None
        return
    fab_requests.open_fab_tab(request["listing"])
    _set_stage(request, "opening")


def _advance(request: dict) -> None:
    waited = time.time() - request["stage_time"]
    stage = request["stage"]
    if stage == "opening":
        if request["listing"] in unreal.CoPresenceFabLibrary.get_fab_tab_url():
            channel = unreal.CoPresenceFabLibrary.connect_fab_tab()
            if channel is not None and channel != _state["channel"]:
                channel.on_report.add_callable(_on_report)
                _state["channel"] = channel
            _set_stage(request, "reading")
        elif waited > OPEN_TIMEOUT_SECONDS:
            _give_up(request, "The Fab tab didn't open the listing.")
    elif stage == "reading":
        if waited > READ_TIMEOUT_SECONDS:
            _give_up(request, "Couldn't read the listing in your Fab tab.")
        elif waited > READ_DELAY_SECONDS and time.time() - request["last_read"] > READ_INTERVAL_SECONDS:
            request["last_read"] = time.time()
            _run(READ_SCRIPT, listing=request["listing"])
    elif stage == "deciding":
        if _popup_closed_by_person():
            _state["popup"] = None
            _decline(request)
        elif not request["info"].get("can_add") and time.time() - request["last_read"] > READ_INTERVAL_SECONDS:
            request["last_read"] = time.time()
            _run(READ_SCRIPT, listing=request["listing"])
    elif stage == "adding":
        if waited > ADD_TIMEOUT_SECONDS:
            _status("Fab didn't answer. If it didn't start downloading, click Add to Project in your Fab tab.",
                    _Tone.PROBLEM)
            _set_stage(request, "importing")
    elif stage == "importing":
        if not fab_requests.is_tracking(request["listing"]):
            _finished(request)


def _give_up(request: dict, problem: str) -> None:
    """Hands a request back to the person: the popup says what went wrong, and they can add
    it from the Fab tab themselves while the import is still watched for."""
    fab_requests.track_import(request["listing"], request["agent"], request["title"])
    _set_stage(request, "importing")
    if not _state["popup"]:
        _show_popup(request)
    _status(f"{problem} Add it from your Fab tab if you want it.", _Tone.PROBLEM)


def _finished(request: dict) -> None:
    popup = _state["popup"]
    if popup is None:
        _state["active"] = None
        return
    if popup["close_at"] is None:
        _status(f"Done. {request['agent']} has been told where it is.", _Tone.DONE)
        popup["close_at"] = time.time() + CLOSE_AFTER_SECONDS
    elif time.time() >= popup["close_at"]:
        _close_popup()
        _state["active"] = None


def _tick(delta_seconds: float) -> None:
    _state["elapsed"] += delta_seconds
    if _state["elapsed"] < TICK_SECONDS:
        return
    _state["elapsed"] = 0.0
    try:
        _watch_chat()
        _show_downloaded_pictures()
        if _state["active"] is None and _state["queue"]:
            _start(_state["queue"].pop(0))
        if _state["active"] is not None:
            _advance(_state["active"])
    except Exception as error:
        unreal.log_warning(f"CoPresence Fab approvals: {error}")
        _status(f"Something went wrong: {error}", _Tone.PROBLEM)
        _state["active"] = None


def start() -> None:
    if _state["callback"] is None and is_available():
        hub_client.start()
        _state["callback"] = unreal.register_slate_post_tick_callback(_tick)
