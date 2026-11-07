"""Brings each agent avatar to life in every editor that shows ghosts: its name tag, its
cartoon speech bubble, and its face and look (see ghost_face).

The name tag shows the agent's Multi-User display name, which Multi-User writes into the
avatar's hidden stock label. The status bubble shows what the agent is doing, three
bouncing dots while it thinks, and that it is done. What the agent says in the chat
gets speech bubbles of its own, stacked above the status bubble for a few seconds, so a
line isn't lost when the agent gets straight back to work. Agents report all of that to
the session hub on the host (see hub_client).

Both labels are camera-facing sprites with text drawn by label_text. Ghosts are local to
each editor, so all of this is drawn locally and never replicates.

An agent whose editor joined with the plain Multi-User avatar (see PlainAgentAvatar)
still gets the robot head, as long as the hub knows it as an agent.
"""

import math
import textwrap
import time

import unreal

from copresence import ghost_face, hub_client, tracking
from copresence.label_text import TextTexture

GHOST_CLASS_PATH = "/CoPresence/BP_AgentGhost.BP_AgentGhost_C"
SPHERE_MESH_PATH = "/Engine/BasicShapes/Sphere.Sphere"
NAME_TAG_MATERIAL_PATH = "/CoPresence/M_AgentNameTag.M_AgentNameTag"
BUBBLE_MATERIAL_PATH = "/CoPresence/M_AgentBubble.M_AgentBubble"
# Finding ghosts and reading hub results runs a few times a second; animation every frame
REFRESH_SECONDS = 0.2
# A short pause between two steps keeps showing the last step instead of flickering
THINKING_DELAY_SECONDS = 1.2
DONE_SECONDS = 8.0
SAYING_SECONDS = 8.0
ERROR_SECONDS = 6.0
# An agent that stopped reporting (crashed, or its session ended) loses its bubble
STALE_SECONDS = 180.0
BUBBLE_LINE_LENGTH = 30
BUBBLE_MAX_LINES = 2
DONE_TEXT = "OK, I'm done!"
# Lines an agent says in the chat stay up this long, newest lowest
SPEECH_SECONDS = 7.0
SPEECH_SPRITES = ("Speech0", "Speech1", "Speech2")
SPEECH_MAX_LINES = 3

# Sizes are in world units. Text size is the font's em height
NAME_TAG_HEIGHT = 46.0
NAME_TEXT_SIZE = 19.0
NAME_PIXELS_PER_UNIT = 8.0
NAME_OUTLINE = 0.8
NAME_GLOW = 4.5
BUBBLE_TEXT_SIZE = 12.5
BUBBLE_PIXELS_PER_UNIT = 7.0
TEXT_PADDING = (7.0, 4.0)
DOTS_BODY = (46.0, 24.0)
# Room M_AgentBubble keeps around the body for its rim, and below it for the tail
FRAME_PAD = 4.0
FRAME_TAIL = 8.0
TAIL_BASE_WIDTH = 11.0
# Space between the name tag's lettering and the tip of the bubble's tail
NAME_GAP = 3.0
FLOAT_HEIGHT = 1.2
FLOAT_SPEED = 2.4
STACK_GAP = 2.5
# How quickly bubbles slide to their new place when the stack changes
STACK_SPEED = 12.0

_state = {"callback": None, "since_refresh": 0.0, "ghost_class": None, "last_warning": "", "chat_history_seen": False}
# Last step shown per agent, kept on screen while a pause is too short to call thinking
_last_step: dict[str, str] = {}
_ghosts: dict[str, "GhostLabels"] = {}
# When this editor first saw each chat message, which is when its speech bubble starts
_first_seen: dict[int, float] = {}
_work_anchors = {}


def _wrap(text: str, max_lines: int = BUBBLE_MAX_LINES) -> list[str]:
    lines = textwrap.wrap(" ".join(text.split()), BUBBLE_LINE_LENGTH) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:BUBBLE_LINE_LENGTH - 3].rstrip() + "..."
    return lines


def effective_state(status: dict | None, seconds_since_update: float) -> str:
    """What an agent is up to now, from its last report and how long ago that was."""
    if not status or seconds_since_update > STALE_SECONDS:
        return "away"
    state = status.get("state") or "idle"
    # After a failed step the agent is working out what to do next
    if state == "error" and seconds_since_update > ERROR_SECONDS:
        return "thinking"
    if state == "saying" and seconds_since_update > SAYING_SECONDS:
        return "idle"
    if state == "done" and seconds_since_update > DONE_SECONDS:
        return "idle"
    return state


def bubble_content(agent: str, status: dict | None, seconds_since_update: float) -> tuple | None:
    """What the status bubble should show: ("text", lines), ("dots", ()), ("done", lines)
    or None. What the agent says goes in speech bubbles instead."""
    state = effective_state(status, seconds_since_update)
    if state == "working":
        _last_step[agent] = status.get("step") or "Working"
        return ("text", tuple(_wrap(_last_step[agent])))
    if state == "thinking":
        if status.get("state") == "thinking" and seconds_since_update < THINKING_DELAY_SECONDS and agent in _last_step:
            return ("text", tuple(_wrap(_last_step[agent])))
        return ("dots", ())
    if state == "done":
        _last_step.pop(agent, None)
        return ("done", (DONE_TEXT,))
    if state == "error":
        return ("text", tuple(_wrap(status.get("step") or "")))
    return None


def status_text(status: dict | None, seconds_since_update: float) -> str:
    """A one-line account of what an agent is doing, for the Agents widget."""
    state = effective_state(status, seconds_since_update)
    step = " ".join(str((status or {}).get("step") or "").split())
    if state == "working":
        return step or "Working"
    if state == "thinking":
        return "Thinking..."
    if state == "done":
        return f"Done: {status['task']}" if status.get("task") else DONE_TEXT
    if state == "saying":
        return f'Said "{step}"'
    if state == "error":
        return step
    return "Away" if state == "away" else "Idle"


def _set_sprite_size(sprite: unreal.MaterialBillboardComponent, material, width: float, height: float) -> None:
    # Sprite sizes are half extents, and the sprite's x runs up the screen
    element = unreal.MaterialSpriteElement()
    element.set_editor_property("material", material)
    element.set_editor_property("base_size_x", height * 0.5)
    element.set_editor_property("base_size_y", width * 0.5)
    sprite.set_elements([element])


# Animations -----------------------------------------------------------------
# Each maps progress (0 to 1) to the bubble's horizontal and vertical scale

def _ease_out_back(progress: float) -> float:
    overshoot = 2.2
    progress -= 1.0
    return 1.0 + (overshoot + 1.0) * progress ** 3 + overshoot * progress ** 2


def _pop(progress: float) -> tuple[float, float]:
    scale = _ease_out_back(progress)
    return scale, scale


def _bump(progress: float) -> tuple[float, float]:
    scale = 1.0 + 0.1 * math.sin(math.pi * progress)
    return scale, scale


def _pop_out(progress: float) -> tuple[float, float]:
    scale = 1.0 - progress * progress
    return scale, scale


def _cheer(progress: float) -> tuple[float, float]:
    # Pops in, then squashes and stretches like jelly as it settles
    scale = _ease_out_back(min(progress * 3.5, 1.0))
    seconds = progress * 1.3
    squash = 0.12 * math.sin(seconds * 16.0) * math.exp(-seconds * 3.5)
    return scale * (1.0 + squash), scale * (1.0 - squash)


ANIMATIONS = {"pop": (0.35, _pop), "bump": (0.25, _bump), "out": (0.18, _pop_out), "cheer": (1.3, _cheer)}


class Bubble:
    """One cartoon speech bubble sprite: draws its text, pops in, bumps when its text
    changes, pops out, and slides to wherever its ghost's stack of bubbles puts it. Only
    the lowest bubble of a stack has a tail."""

    def __init__(self, world, ghost: unreal.Actor, sprite):
        self.sprite = sprite
        self.material = unreal.MaterialLibrary.create_dynamic_material_instance(ghost, _state["bubble_material"])
        self.text = TextTexture(world, BUBBLE_TEXT_SIZE, BUBBLE_PIXELS_PER_UNIT, self.material)
        self.content = None
        # The chat message a speech bubble shows
        self.message_id = None
        self.body = DOTS_BODY
        self.tail = True
        self.tail_tip = 0.0
        self.animation = None
        self.animation_start = 0.0
        self.applied = None
        # Height of the bubble's center above the name tag, easing toward its place
        self.lift = None
        sprite.set_visibility(False, False)

    def show(self, content: tuple | None, now: float) -> None:
        if content is None:
            if self.content is not None and self.animation != "out":
                self._animate("out", now)
            return
        if content == self.content and self.animation != "out":
            return
        appearing = self.content is None or self.animation == "out"
        self.content = content
        self._draw()
        if content[0] == "done":
            self._animate("cheer", now)
        else:
            self._animate("pop" if appearing else "bump", now)

    def set_tail(self, tail: bool) -> None:
        if tail != self.tail:
            self.tail = tail
            if self.content is not None:
                self._draw()

    def scale(self, now: float) -> tuple[float, float] | None:
        """The bubble's scale this frame, or None once it has popped out."""
        if self.content is None:
            return None
        if self.animation is None:
            return 1.0, 1.0
        duration, curve = ANIMATIONS[self.animation]
        progress = (now - self.animation_start) / duration
        if progress < 1.0:
            return curve(progress)
        if self.animation == "out":
            self.content = None
            self.message_id = None
            self.animation = None
            self.lift = None
            self.sprite.set_visibility(False, False)
            return None
        self.animation = None
        return 1.0, 1.0

    def place(self, offset: unreal.Vector, ghost_rotation: unreal.Rotator, scale_x: float, scale_y: float) -> None:
        self.sprite.set_relative_location(offset.unrotate(ghost_rotation), False, False)
        tail_room = FRAME_TAIL if self.tail else 0.0
        width = (self.body[0] + 2.0 * FRAME_PAD) * scale_x
        height = (self.body[1] + 2.0 * FRAME_PAD + 2.0 * tail_room) * scale_y
        applied = (round(width, 2), round(height, 2))
        if applied != self.applied:
            self.applied = applied
            _set_sprite_size(self.sprite, self.material, width, height)

    def _animate(self, animation: str, now: float) -> None:
        self.animation = animation
        self.animation_start = now
        self.applied = None

    def _draw(self) -> None:
        kind, lines = self.content
        self.material.set_scalar_parameter_value("Dots", 1.0 if kind == "dots" else 0.0)
        if kind == "dots":
            self.body = DOTS_BODY
        else:
            text_width, text_height = self.text.measure(lines)
            self.body = (text_width + 2.0 * TEXT_PADDING[0], text_height + 2.0 * TEXT_PADDING[1])
        # Without a tail the sprite drops the room kept for it, and the tail sits inside the body
        tail_room = FRAME_TAIL if self.tail else 0.0
        self.material.set_vector_parameter_value("Frame", unreal.LinearColor(FRAME_PAD, tail_room, 0.0, 0.0))
        # The text texture covers the whole sprite, so the text lands in the body's middle
        self.text.draw(lines, self.body[0] + 2.0 * FRAME_PAD, self.body[1] + 2.0 * FRAME_PAD + 2.0 * tail_room)
        if self.tail:
            # The tail starts near the body's lower left and hooks further left
            base_left = -min(self.body[0] * 0.21, 20.0)
            base_width = min(TAIL_BASE_WIDTH, self.body[0] * 0.28)
            self.tail_tip = base_left - FRAME_TAIL * 0.45
        else:
            # A small tail hidden inside the body; a zero-width one breaks the shader's math
            base_left, base_width, self.tail_tip = -1.0, 2.0, 0.0
        self.material.set_vector_parameter_value("BodySize", unreal.LinearColor(self.body[0], self.body[1], 0.0, 0.0))
        self.material.set_vector_parameter_value("Tail", unreal.LinearColor(base_left, base_width, self.tail_tip, 0.0))
        self.applied = None
        self.sprite.set_visibility(True, False)


class GhostLabels:
    """One ghost's name tag and bubbles: keeps them upright above it, with the status
    bubble just above the name tag and what the agent said stacked above that, newest
    first."""

    def __init__(self, world, ghost: unreal.Actor, stock_label, body, name_sprite, bubble_sprite, speech_sprites: list):
        self.ghost = ghost
        self.face = ghost_face.GhostFace(body)
        self.size = 1.0
        self.glow = None
        self.stock_label = stock_label
        self.name_sprite = name_sprite
        self.name = None
        self.name_size = (0.0, 0.0)
        self.name_material = unreal.MaterialLibrary.create_dynamic_material_instance(ghost, _state["name_material"])
        self.name_text = TextTexture(world, NAME_TEXT_SIZE, NAME_PIXELS_PER_UNIT, self.name_material)
        self.status = Bubble(world, ghost, bubble_sprite)
        self.speech = [Bubble(world, ghost, sprite) for sprite in speech_sprites]
        self.name_sprite.set_visibility(False, False)

    def set_name(self, name: str) -> None:
        if name == self.name:
            return
        self.name = name
        # Room around the lettering for the outline and the glow
        margin = NAME_OUTLINE + NAME_GLOW + 1.0
        text_width, text_height = self.name_text.measure([name])
        self.name_size = (text_width + 2.0 * margin, text_height + 2.0 * margin)
        self.name_text.draw([name], *self.name_size)
        self.name_material.set_vector_parameter_value(
            "Shape", unreal.LinearColor(self.name_size[0], self.name_size[1], NAME_OUTLINE, NAME_GLOW))
        _set_sprite_size(self.name_sprite, self.name_material, *self.name_size)
        self.name_sprite.set_visibility(True, False)

    def set_look(self, name: str, appearance: dict | None, washed_out: bool = False) -> None:
        """The agent's chosen colors and size, for its face, body and name tag; washed out while
        the agent is private to someone else."""
        self.face.set_appearance(name, appearance, washed_out)
        self.size = ghost_face.agent_size(appearance)
        glow = (ghost_face.glow_color(name, appearance), washed_out)
        if glow != self.glow:
            self.glow = glow
            color = ghost_face.linear_color(glow[0], glow[0])
            # The glow is drawn bright so it blooms like a light, and a private agent's barely glows
            brightness = 1.0 if washed_out else 3.0
            if washed_out:
                color = ghost_face.faded(color)
            self.name_material.set_vector_parameter_value(
                "GlowColor", unreal.LinearColor(color.r * brightness, color.g * brightness, color.b * brightness, 1.0))

    def show(self, content: tuple | None, now: float) -> None:
        self.status.show(content, now)

    def say(self, said: list, now: float) -> None:
        """Gives the lines the agent said lately, (message id, lines) oldest first, speech
        bubbles; lines whose time is up pop out."""
        if not self.speech:
            return
        current = {message_id for message_id, _ in said}
        for bubble in self.speech:
            if bubble.message_id is not None and bubble.message_id not in current:
                bubble.show(None, now)
        for message_id, lines in said:
            if any(bubble.message_id == message_id for bubble in self.speech):
                continue
            # More lines than bubbles: the oldest one makes room
            bubble = next((bubble for bubble in self.speech if bubble.message_id is None), None) or \
                min(self.speech, key=lambda candidate: candidate.message_id)
            bubble.message_id = message_id
            bubble.show(("text", tuple(lines)), now)

    def update(self, now: float, delta_seconds: float, camera_right: unreal.Vector, camera_up: unreal.Vector) -> None:
        self.face.update(now, delta_seconds)
        # Components follow the ghost's camera rotation, so world offsets are turned into
        # its local space. The name tag hangs straight above even when the camera tilts
        ghost_rotation = self.ghost.get_actor_rotation()
        name_offset = unreal.Vector(0.0, 0.0, NAME_TAG_HEIGHT * self.size)
        self.name_sprite.set_relative_location(name_offset.unrotate(ghost_rotation), False, False)

        stack = [self.status] + sorted((bubble for bubble in self.speech if bubble.message_id is not None),
                                       key=lambda bubble: -bubble.message_id)
        shown = [(bubble, scale) for bubble in stack if (scale := bubble.scale(now)) is not None]
        floating = FLOAT_HEIGHT * math.sin(now * FLOAT_SPEED)
        # Heights above the name tag's center, along the viewer's screen
        bottom = self.name_size[1] * 0.5 - NAME_GLOW + NAME_GAP
        column = 0.0
        for index, (bubble, (scale_x, scale_y)) in enumerate(shown):
            bubble.set_tail(index == 0)
            half_body = bubble.body[1] * 0.5
            if bubble.tail:
                # Grows out of its tail, whose tip sits just above the name tag's lettering
                # and over the middle of it; the bubbles above line up with this one
                target = bottom + (half_body + FRAME_TAIL - 1.0) * scale_y
                column = -bubble.tail_tip * scale_x
            else:
                target = bottom + (half_body + FRAME_PAD) * scale_y
            if bubble.lift is None:
                bubble.lift = target
            else:
                bubble.lift += (target - bubble.lift) * min(1.0, delta_seconds * STACK_SPEED)
            offset = name_offset + camera_up * (bubble.lift + floating) + camera_right * column
            bubble.place(offset, ghost_rotation, scale_x, scale_y)
            bottom = bubble.lift + (half_body + FRAME_PAD) * scale_y + STACK_GAP


class PlainAgentAvatar:
    """An agent whose editor joined with the plain Multi-User avatar, which only an
    editor's launch settings can change. Its head becomes the robot's, with the emoting
    face, and its name label takes the agent's color. Python can't add components to an
    avatar, so it has no name tag sprite or bubble; its eyes show what it is doing."""

    def __init__(self, avatar: unreal.Actor, label, head, monitor):
        self.ghost = avatar
        self.stock_label = label
        self.name = None
        self.size = None
        self.color = None
        head.set_static_mesh(_state["sphere"])
        # The plain head is turned sideways; the face is drawn on the robot head's front
        head.set_relative_rotation(unreal.Rotator(0.0, 0.0, 0.0), False, False)
        self.face = ghost_face.GhostFace(head)
        monitor.set_visibility(False, False)

    def set_name(self, name: str) -> None:
        self.name = name

    def set_look(self, name: str, appearance: dict | None, washed_out: bool = False) -> None:
        self.face.set_appearance(name, appearance, washed_out)
        size = ghost_face.agent_size(appearance)
        if size != self.size:
            self.size = size
            # Clear of the robot head, which is bigger than the plain one
            self.stock_label.set_relative_location(unreal.Vector(0.0, 0.0, 26.0 * size + 4.0), False, False)
        color = ghost_face.FADED_COLOR.lstrip("#") if washed_out else ghost_face.glow_color(name, appearance).lstrip("#")
        if color != self.color and len(color) == 6:
            self.color = color
            self.stock_label.set_text_render_color(
                unreal.Color(r=int(color[0:2], 16), g=int(color[2:4], 16), b=int(color[4:6], 16), a=255))

    def show(self, content: tuple | None, now: float) -> None:
        pass

    def say(self, said: list, now: float) -> None:
        pass

    def update(self, now: float, delta_seconds: float, camera_right: unreal.Vector, camera_up: unreal.Vector) -> None:
        self.face.update(now, delta_seconds)


def _component(actor: unreal.Actor, component_class, name: str):
    return next((component for component in actor.get_components_by_class(component_class)
                 if component.get_name() == name), None)


def _load_shared() -> None:
    _state["ghost_class"] = unreal.load_class(None, GHOST_CLASS_PATH)
    _state["sphere"] = unreal.load_object(None, SPHERE_MESH_PATH)
    _state["name_material"] = unreal.load_object(None, NAME_TAG_MATERIAL_PATH)
    _state["bubble_material"] = unreal.load_object(None, BUBBLE_MATERIAL_PATH)
    # Never repair material graphs during editor startup. That dirties plugin
    # packages before Concert joins, preventing the join and later scene switches.
    # Exposure compensation belongs in the authored assets; regenerate older
    # distributions using Scripts/label_materials.py during asset preparation.


def is_alive(actor) -> bool:
    """Whether an actor is still in the level. Python keeps wrappers of actors a level
    reload destroyed, and Unreal can't even check those."""
    try:
        return unreal.SystemLibrary.is_valid(actor)
    except Exception:
        return False


def _recent_speech(messages: list, online: bool, now: float) -> dict:
    """What each agent said in the chat during the last few seconds, as (message id,
    lines) oldest first. The seconds count from when this editor first saw a message, so
    other machines' clocks don't matter."""
    if not _state["chat_history_seen"]:
        if not online:
            return {}
        # Whatever was said before this editor connected is history, not speech
        for message in messages:
            _first_seen[message["id"]] = -math.inf
        _state["chat_history_seen"] = True
    said = {}
    for message in messages:
        if message["role"] != "agent":
            continue
        if now - _first_seen.setdefault(message["id"], now) < SPEECH_SECONDS:
            said.setdefault(message["author"], []).append((message["id"], _wrap(message["text"], SPEECH_MAX_LINES)))
    return said


def _refresh(now: float) -> None:
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world is None:
        return
    if _state["ghost_class"] is None:
        _load_shared()

    agents, messages, seconds_since_poll, online = hub_client.snapshot()
    said = _recent_speech(messages, online, now)
    # Scene task tags replicate to all clients: use them to place the eyes beside
    # the work, independently of the camera used to inspect the agent.
    _work_anchors.clear()
    groups = {}
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        for tag in actor.tags:
            owner, separator, task = str(tag).rpartition(':t-')
            if not separator or not task.isdigit(): continue
            center, extent = actor.get_actor_bounds(False)
            if max(extent.x, extent.y, extent.z) > 5000: continue
            number = int(task)
            previous = groups.get(owner)
            if previous is None or number > previous[0]: groups[owner] = (number, [])
            if number == groups[owner][0]: groups[owner][1].append((center, extent))
    for name, (_, boxes) in groups.items():
        low = [min(getattr(c,a)-getattr(e,a) for c,e in boxes) for a in ('x','y','z')]
        high = [max(getattr(c,a)+getattr(e,a) for c,e in boxes) for a in ('x','y','z')]
        _work_anchors[name] = unreal.Vector((low[0]+high[0])/2-200, (low[1]+high[1])/2-200, max(high[2]+100,260))

    from copresence import local_presence
    local_presence.ensure(world, _state["ghost_class"])
    present = []
    for ghost in unreal.GameplayStatics.get_all_actors_of_class(world, _state["ghost_class"]):
        key = ghost.get_path_name()
        if key not in _ghosts:
            stock_label = _component(ghost, unreal.TextRenderComponent, "Name")
            body = _component(ghost, unreal.StaticMeshComponent, "Presence")
            sprites = {component.get_name(): component
                       for component in ghost.get_components_by_class(unreal.MaterialBillboardComponent)}
            if stock_label is None or body is None or "NameTag" not in sprites or "Bubble" not in sprites:
                continue
            speech = [sprites[name] for name in SPEECH_SPRITES if name in sprites]
            _ghosts[key] = GhostLabels(world, ghost, stock_label, body, sprites["NameTag"], sprites["Bubble"], speech)
        present.append(key)

    # Agents whose editors joined with the plain Multi-User avatar get the robot head too;
    # people keep theirs
    for avatar in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.ConcertClientDesktopPresenceActor):
        key = avatar.get_path_name()
        if key in present:
            continue
        if key not in _ghosts:
            label = _component(avatar, unreal.TextRenderComponent, "Name")
            head = _component(avatar, unreal.StaticMeshComponent, "Presence")
            monitor = _component(avatar, unreal.StaticMeshComponent, "Desktop")
            if label is None or head is None or monitor is None or str(label.get_editor_property("text")) not in agents:
                continue
            _ghosts[key] = PlainAgentAvatar(avatar, label, head, monitor)
        present.append(key)

    for key in present:
        labels = _ghosts[key]
        name = str(labels.stock_label.get_editor_property("text"))
        if name:
            labels.set_name(name)
        status = agents.get(name)
        age = (status or {}).get("age", 0.0) + seconds_since_poll
        labels.show(bubble_content(name, status, age), now)
        labels.say(said.get(name, []), now)
        if name:
            labels.set_look(name, (status or {}).get("appearance"),
                            ghost_face.is_private_to_someone_else(status, hub_client.display_name()))
        labels.face.set_target(ghost_face.emote_for(status, effective_state(status, age), age))
        labels.face.set_talking(bool((status or {}).get("speaking")))

    for key in set(_ghosts) - set(present):
        del _ghosts[key]


def _sync_presence(dt=1/60):
    """Keep custom editor ghosts at Concert's authoritative remote camera pose.

    Some editor-only Blueprint avatars do not tick after a level switch. These
    avatars are transient, local presentation objects; never edit scene actors.
    """
    statics = unreal.MultiUserClientStatics
    infos = statics.get_remote_multi_user_client_infos()
    if isinstance(infos, tuple): infos = infos[-1]
    endpoints = {str(info.display_name): info.client_endpoint_id for info in infos or []}
    for labels in _ghosts.values():
        endpoint = endpoints.get(labels.name)
        if endpoint is None or not is_alive(labels.ghost): continue
        if not isinstance(labels.ghost, unreal.ConcertClientPresenceActor): continue
        # Concert's native tick already interpolates the remote camera pose.
        # A second transform writer here fights it, especially when a work
        # anchor differs from the camera. Only repair avatars without a tick.
        if labels.ghost.is_actor_tick_enabled(): continue
        pose = statics.get_multi_user_presence_transform(endpoint)
        target = _work_anchors.get(labels.name, pose.translation)
        here = labels.ghost.get_actor_location()
        labels.ghost.set_actor_location(here + (target-here)*min(dt*4,1), False, False)
        labels.ghost.set_actor_rotation(pose.rotation.rotator(), False)


def _tick(delta_seconds: float) -> None:
    now = time.monotonic()
    try:
        _state["since_refresh"] += delta_seconds
        if _state["since_refresh"] >= REFRESH_SECONDS:
            _state["since_refresh"] = 0.0
            _refresh(now)
        # Multi-User replaces avatars when someone joins; drop any that went away since
        # the last refresh
        for key in [key for key, labels in _ghosts.items() if not is_alive(labels.ghost)]:
            del _ghosts[key]
        if not _ghosts:
            return
        _sync_presence(delta_seconds)
        from copresence import local_presence
        local_presence.update(delta_seconds)
        camera_location, camera_rotation = tracking.camera_info()
        camera_right = camera_rotation.get_right_vector()
        camera_up = camera_rotation.get_up_vector()
        for labels in _ghosts.values():
            body = labels.face.body
            body.set_world_rotation(unreal.MathLibrary.find_look_at_rotation(body.get_world_location(), camera_location), False, False)
            labels.update(now, delta_seconds, camera_right, camera_up)
    except Exception as error:
        # Usually a ghost removed between refreshes; the next refresh rebuilds the rest
        _ghosts.clear()
        if str(error) != _state["last_warning"]:
            _state["last_warning"] = str(error)
            unreal.log_warning(f"CoPresence ghost labels: {error}")


def start() -> None:
    if _state["callback"] is not None:
        return
    hub_client.start()
    _state["callback"] = unreal.register_slate_post_tick_callback(_tick)
