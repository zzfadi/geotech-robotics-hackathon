"""Shared pieces of the CoPresence editor tabs: the editor's dark theme, helpers that build
UMG widgets from Python, and EditorTab, which opens an Editor Utility Widget and builds
its content whenever a tab of it appears.
"""

import unreal

FONT_PATH = "/Engine/EngineFonts/Roboto.Roboto"

# Editor dark theme, as linear colors
BACKGROUND = (0.010, 0.010, 0.010)
SURFACE = (0.016, 0.016, 0.016)
HOVER = (0.026, 0.026, 0.026)
SELECTED = (0.042, 0.042, 0.046)
PRESSED = (0.060, 0.060, 0.064)
LINE = (0.032, 0.032, 0.032)
FIELD = (0.005, 0.005, 0.005)
TEXT = (0.72, 0.72, 0.72)
MUTED = (0.25, 0.25, 0.25)
PERSON = (0.88, 0.88, 0.88)
ERROR = (0.85, 0.14, 0.10)
SUCCESS = (0.26, 0.54, 0.07)
# The editor's blue for a dialog's main button
PRIMARY = (0.0, 0.162, 0.745)
PRIMARY_HOVER = (0.010, 0.215, 0.870)
PRIMARY_PRESSED = (0.0, 0.110, 0.520)


def color(rgb, alpha: float = 1.0) -> unreal.LinearColor:
    return unreal.LinearColor(rgb[0], rgb[1], rgb[2], alpha)


def slate(rgb) -> unreal.SlateColor:
    return unreal.SlateColor(specified_color=color(rgb))


def font(size: int, bold: bool = False) -> unreal.SlateFontInfo:
    info = unreal.SlateFontInfo()
    info.set_editor_property("font_object", unreal.load_object(None, FONT_PATH))
    info.set_editor_property("typeface_font_name", "Bold" if bold else "Regular")
    info.set_editor_property("size", size)
    return info


def brush(rgb, radius: float = 0.0) -> unreal.SlateBrush:
    result = unreal.SlateBrush()
    result.set_editor_property("tint_color", slate(rgb))
    if radius > 0.0:
        outline = unreal.SlateBrushOutlineSettings()
        outline.set_editor_property("corner_radii", unreal.Vector4(radius, radius, radius, radius))
        outline.set_editor_property("rounding_type", unreal.SlateBrushRoundingType.FIXED_RADIUS)
        result.set_editor_property("draw_as", unreal.SlateBrushDrawType.ROUNDED_BOX)
        result.set_editor_property("outline_settings", outline)
    return result


def margin(left: float, top: float, right: float, bottom: float) -> unreal.Margin:
    return unreal.Margin(left=left, top=top, right=right, bottom=bottom)


def button_style(normal, hovered, pressed, disabled, padding=(0, 0, 0, 0), radius: float = 0.0) -> unreal.ButtonStyle:
    """A button that is a flat colored area, with a color for each state."""
    style = unreal.ButtonStyle()
    for state, rgb in (("normal", normal), ("hovered", hovered), ("pressed", pressed), ("disabled", disabled)):
        style.set_editor_property(state, brush(rgb, radius))
    style.set_editor_property("normal_padding", margin(*padding))
    style.set_editor_property("pressed_padding", margin(*padding))
    return style


def flat_style(selected: bool, padding=(0, 0, 0, 0)) -> unreal.ButtonStyle:
    """A button that is just a flat colored area, lighter when hovered."""
    return button_style(SELECTED if selected else BACKGROUND, PRESSED if selected else HOVER, PRESSED, BACKGROUND,
                        padding)


def add(box, child, fill: bool = False, padding=(0, 0, 0, 0)):
    """Adds a child to a vertical, horizontal or wrap box, or a scroll box."""
    if isinstance(box, unreal.VerticalBox):
        slot = box.add_child_to_vertical_box(child)
    elif isinstance(box, unreal.HorizontalBox):
        slot = box.add_child_to_horizontal_box(child)
        slot.set_vertical_alignment(unreal.VerticalAlignment.V_ALIGN_CENTER)
    elif isinstance(box, unreal.WrapBox):
        slot = box.add_child_to_wrap_box(child)
    else:
        slot = box.add_child(child)
    if fill:
        slot.set_size(unreal.SlateChildSize(1.0, unreal.SlateSizeRule.FILL))
    slot.set_padding(margin(*padding))
    return slot


def style_text_box(box: unreal.EditableTextBox) -> None:
    style = box.get_editor_property("widget_style")
    for state in ("background_image_normal", "background_image_hovered", "background_image_focused",
                  "background_image_read_only"):
        style.set_editor_property(state, brush(FIELD))
    style.set_editor_property("padding", margin(8, 6, 8, 6))
    text_style = style.get_editor_property("text_style")
    text_style.set_editor_property("font", font(10))
    text_style.set_editor_property("color_and_opacity", slate(PERSON))
    style.set_editor_property("text_style", text_style)
    style.set_editor_property("foreground_color", slate(PERSON))
    box.set_editor_property("widget_style", style)


class Widgets:
    """Makes UMG widgets inside one widget tree."""

    def __init__(self, tree):
        self.tree = tree
        # Every button made here, kept for as long as its tab. Unreal forgets what a button does
        # once Python no longer holds the button, so one kept nowhere stops working at the
        # editor's next garbage collection, about a minute later
        self.buttons = []

    def make(self, widget_class):
        return unreal.new_object(widget_class, outer=self.tree)

    def text(self, value: str, size: int = 10, rgb=TEXT, bold: bool = False, wrap: bool = False) -> unreal.TextBlock:
        block = self.make(unreal.TextBlock)
        block.set_text(unreal.Text(value))
        block.set_font(font(size, bold))
        block.set_color_and_opacity(slate(rgb))
        block.set_auto_wrap_text(wrap)
        return block

    def border(self, rgb, child=None, padding=(0, 0, 0, 0)) -> unreal.Border:
        result = self.make(unreal.Border)
        result.set_brush_color(color(rgb))
        result.set_padding(margin(*padding))
        if child is not None:
            result.set_content(child)
        return result

    def line(self) -> unreal.SizeBox:
        size_box = self.make(unreal.SizeBox)
        size_box.set_height_override(1.0)
        size_box.set_content(self.border(LINE))
        return size_box

    def sized(self, child, width: float | None, height: float | None) -> unreal.SizeBox:
        """Fixes the child's width, height or both; None leaves that one to the child."""
        size_box = self.make(unreal.SizeBox)
        if width is not None:
            size_box.set_width_override(width)
        if height is not None:
            size_box.set_height_override(height)
        size_box.set_content(child)
        return size_box

    def button(self, child, on_click, selected: bool = False, padding=(0, 0, 0, 0),
               style: unreal.ButtonStyle | None = None) -> unreal.Button:
        result = self.make(unreal.Button)
        result.set_style(style or flat_style(selected, padding))
        slot = result.set_content(child)
        slot.set_horizontal_alignment(unreal.HorizontalAlignment.H_ALIGN_FILL)
        slot.set_padding(margin(0, 0, 0, 0))
        # Click events pass no arguments, so callers capture what they need
        result.on_clicked.add_callable(on_click)
        self.buttons.append(result)
        return result


class EditorTab:
    """An Editor Utility Widget whose only content is a vertical box named Root, filled in
    by `build(root)` whenever a tab of it appears: opened, reopened, or restored by the
    editor with its layout."""

    def __init__(self, asset_path: str, build):
        self.asset_path = asset_path
        self.build = build
        self.widget = None
        self.ui = None

    def _subsystem(self):
        return unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)

    def open(self) -> None:
        """Opens the tab, or brings it to the front of its tab stack."""
        self._subsystem().spawn_and_register_tab(unreal.load_asset(self.asset_path))
        self.current()

    def is_open(self) -> bool:
        return self._subsystem().find_utility_widget_from_blueprint(unreal.load_asset(self.asset_path)) is not None

    def current(self):
        """The tab's built UI, or None while no tab of it is open."""
        widget = self._subsystem().find_utility_widget_from_blueprint(unreal.load_asset(self.asset_path))
        if widget is None:
            self.widget = self.ui = None
        elif widget != self.widget:
            self.widget = widget
            self.ui = self.build(widget.get_editor_property("Root"))
        return self.ui

    def reset(self) -> None:
        self.widget = self.ui = None


def after(seconds: float, callback) -> None:
    """Runs callback on the game thread once, a while from now."""
    state = {"left": seconds, "handle": None}

    def tick(delta_seconds: float) -> None:
        state["left"] -= delta_seconds
        if state["left"] > 0.0:
            return
        unreal.unregister_slate_post_tick_callback(state["handle"])
        callback()

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


def open_docked(tabs: list) -> None:
    """Opens the tabs that aren't open, in order, and brings the first to the front. The
    launch scripts keep a spot for them next to the Details panel in the editor layout
    (Tools/dock_agent_tabs.py), so they open there rather than in windows of their own."""
    for tab in tabs:
        if not tab.is_open():
            tab.open()
    tabs[0].open()


def register_menu_entry(name: str, label: str, tooltip: str, command: str, icon: str) -> None:
    """Adds an entry to the Window menu and a button to the level editor toolbar."""
    menus = unreal.ToolMenus.get()
    places = [("LevelEditor.MainMenu.Window", unreal.MultiBlockType.MENU_ENTRY),
              ("LevelEditor.LevelEditorToolBar.User", unreal.MultiBlockType.TOOL_BAR_BUTTON)]
    for menu_name, entry_type in places:
        entry = unreal.ToolMenuEntryExtensions.init_menu_entry(
            "CoPresence", name, unreal.Text(label), unreal.Text(tooltip),
            unreal.ToolMenuStringCommandType.PYTHON, "", command)
        entry.type = entry_type
        entry.set_icon("EditorStyle", icon)
        menus.extend_menu(menu_name).add_menu_entry("CoPresence", entry)
    menus.refresh_all_widgets()
