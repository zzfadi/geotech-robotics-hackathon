"""An agent's face and look: a round cartoon head whose eyes emote with what the agent
is doing, blink and glance around, and which boings when its mood changes and hops when
it finishes something. Plus the colors and size the agent picked for itself. The same
face animates the ghost's 3D head (M_AgentFace) and its icon in the Agents widget
(M_AgentIcon), which share their parameters.
"""

import math
import random
import zlib

import unreal

FACE_MATERIAL_PATH = "/CoPresence/M_AgentFace.M_AgentFace"
# Emotes the face material can draw, by its Emote parameter
EMOTES = {"neutral": 0, "thinking": 1, "interested": 2, "happy": 3, "error": 4, "love": 5, "content": 6}
# What the eyes show for each agent state, unless the agent picked an emote itself
STATE_EMOTES = {"working": "interested", "thinking": "thinking", "done": "happy", "error": "error",
                "saying": "content"}
# Eye colors of the GeoTech agent collection (swag sheet, 23 September 2026). Agents that
# haven't picked a color get one of these, always the same one for the same name
COLLECTION = {"coder": "#69CDEE", "designer": "#8D5FF6", "oracle": "#827DF7",
              "researcher": "#E4FE5B", "manager": "#7CD9B7", "bizdev": "#EB489D"}
BLINK_SECONDS = 0.18
BLINK_INTERVAL = (2.5, 6.0)
GLANCE_INTERVAL = (1.5, 4.0)
GLANCE_SPEED = 6.0
# Bouncing: a spring on how squashed the head is, kicked whenever the face changes
SQUASH_STIFFNESS = 260.0
SQUASH_DAMPING = 8.0
SQUASH_KICK = 2.4
LANDING_KICK = 3.0
HOP_SECONDS = 0.5
# How long the head chatters up and down after it starts talking
TALK_SECONDS = 2.4
HOP_HEIGHT = 9.0
DEFAULT_BODY_COLOR = "#F7F4EE"
BODY_SCALE = 0.44
SIZE_RANGE = (0.6, 1.6)
# An agent private to someone else looks washed out, its colors this far toward a pale gray, so
# everyone can tell it belongs to someone
FADED_COLOR = "#B4B4BC"
FADE_AMOUNT = 0.65


def linear_color(hex_color: str, fallback: str) -> unreal.LinearColor:
    """#RRGGBB as the linear color a material expects."""
    value = (hex_color or fallback).lstrip("#")
    if len(value) != 6:
        value = fallback.lstrip("#")
    channels = []
    for index in (0, 2, 4):
        srgb = int(value[index:index + 2], 16) / 255.0
        channels.append(srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4)
    return unreal.LinearColor(*channels, 1.0)


def faded(color: unreal.LinearColor) -> unreal.LinearColor:
    """The washed-out version of a color, for a private agent."""
    gray = linear_color(FADED_COLOR, FADED_COLOR)
    return unreal.LinearColor(color.r + (gray.r - color.r) * FADE_AMOUNT, color.g + (gray.g - color.g) * FADE_AMOUNT,
                              color.b + (gray.b - color.b) * FADE_AMOUNT, 1.0)


def is_private_to_someone_else(status: dict | None, person: str) -> bool:
    """Whether the hub says the agent is kept to an owner other than this person, which is when it
    shows washed out. Its owner sees it in full."""
    return bool(status) and status.get("public") is False and (status.get("owner") or "") not in ("", person)


def eye_color(name: str, appearance: dict | None) -> str:
    """The agent's eye color: its own pick, or its color from the collection."""
    picked = (appearance or {}).get("eye_color")
    if picked:
        return picked
    colors = list(COLLECTION.values())
    return colors[zlib.crc32(name.encode()) % len(colors)]


def glow_color(name: str, appearance: dict | None) -> str:
    """The glow around the agent's name tag, which also marks it in the Agents widget and
    Agent Chat."""
    return (appearance or {}).get("glow_color") or eye_color(name, appearance)


def agent_size(appearance: dict | None) -> float:
    size = float((appearance or {}).get("size") or 1.0)
    return min(max(size, SIZE_RANGE[0]), SIZE_RANGE[1])


def emote_for(status: dict | None, state: str, seconds_since_update: float) -> str:
    """The emote an agent picked, while it lasts, or the one that fits what it is doing.
    While its voice speaks in a video call, it looks like it is talking."""
    if (status or {}).get("speaking"):
        return STATE_EMOTES["saying"]
    picked = (status or {}).get("emote")
    if picked in EMOTES and seconds_since_update < float((status or {}).get("emote_seconds") or 0.0):
        return picked
    return STATE_EMOTES.get(state, "neutral")


class FaceAnimator:
    """Blinks, switches emotes with a blink, glances around and bounces, on any material
    with the face parameters (Emote, Openness, Look, Squash, Hop, EyeColor, BodyColor)."""

    def __init__(self, material: unreal.MaterialInstanceDynamic):
        self.material = material
        self.shown = "neutral"
        self.target = "neutral"
        self.blink_start = None
        self.next_blink = 0.0
        self.look = [0.0, 0.0]
        self.look_target = [0.0, 0.0]
        self.next_glance = 0.0
        self.shown_since = 0.0
        self.squash = 0.0
        self.squash_velocity = 0.0
        self.hop_start = None
        # Set while the agent's voice speaks in a video call: the head chatters until it stops
        self.talking = False
        self.appearance = None
        self.applied = {}
        # Heads float up and down out of step with each other
        material.set_scalar_parameter_value("Phase", random.uniform(0.0, 2.0 * math.pi))

    def set_target(self, emote: str) -> None:
        self.target = emote

    def set_talking(self, talking: bool) -> None:
        self.talking = talking

    def set_appearance(self, name: str, appearance: dict | None, washed_out: bool = False) -> bool:
        """Applies the agent's colors, washed out for a private agent; returns whether anything changed."""
        if (name, appearance, washed_out) == self.appearance:
            return False
        self.appearance = (name, appearance, washed_out)
        eyes = eye_color(name, appearance)
        body = linear_color((appearance or {}).get("body_color"), DEFAULT_BODY_COLOR)
        eyes = linear_color(eyes, eyes)
        if washed_out:
            body, eyes = faded(body), faded(eyes)
        self.material.set_vector_parameter_value("BodyColor", body)
        self.material.set_vector_parameter_value("EyeColor", eyes)
        return True

    def update(self, now: float, delta_seconds: float) -> None:
        # Blink now and then, and switch emotes with a blink so the change reads as a reaction
        if self.blink_start is None and (self.target != self.shown or now >= self.next_blink):
            self.blink_start = now
        openness = 1.0
        if self.blink_start is not None:
            progress = (now - self.blink_start) / BLINK_SECONDS
            if progress >= 0.5 and self.shown != self.target:
                self._react(self.target, now)
            if progress >= 1.0:
                self.blink_start = None
                self.next_blink = now + random.uniform(*BLINK_INTERVAL)
            else:
                openness = abs(1.0 - 2.0 * progress)

        if now >= self.next_glance:
            self.look_target = [random.uniform(-0.6, 0.6), random.uniform(-0.3, 0.4)]
            self.next_glance = now + random.uniform(*GLANCE_INTERVAL)
        blend = min(1.0, delta_seconds * GLANCE_SPEED)
        self.look = [current + (target - current) * blend for current, target in zip(self.look, self.look_target)]

        hop = self._bounce(now, delta_seconds)
        self._set_scalar("Openness", round(openness, 3))
        self._set_scalar("Emote", float(EMOTES[self.shown]))
        self._set_scalar("Squash", round(self.squash, 3))
        self._set_scalar("Hop", round(hop, 3))
        look = (round(self.look[0], 3), round(self.look[1], 3))
        if self.applied.get("Look") != look:
            self.applied["Look"] = look
            self.material.set_vector_parameter_value("Look", unreal.LinearColor(look[0], look[1], 0.0, 0.0))

    def _react(self, emote: str, now: float) -> None:
        # A little boing whenever the face changes, and a hop for joy when it turns happy
        self.shown = emote
        self.shown_since = now
        self.squash_velocity += SQUASH_KICK
        if emote == "happy":
            self.hop_start = now

    def _bounce(self, now: float, delta_seconds: float) -> float:
        """Moves the squash spring along and returns how far up the head is, 0 to 1."""
        hop = 0.0
        if self.hop_start is not None:
            progress = (now - self.hop_start) / HOP_SECONDS
            if progress >= 1.0:
                self.hop_start = None
                self.squash_velocity -= LANDING_KICK
            else:
                hop = 4.0 * progress * (1.0 - progress)
        elif self.shown == "content" and (self.talking or now - self.shown_since < TALK_SECONDS):
            hop = 0.15 * abs(math.sin((now - self.shown_since) * 10.0))

        # Small steps keep the spring steady through long frames
        elapsed = min(delta_seconds, 0.1)
        steps = max(1, math.ceil(elapsed / 0.008))
        for _ in range(steps):
            force = SQUASH_STIFFNESS * self.squash + SQUASH_DAMPING * self.squash_velocity
            self.squash_velocity -= force * elapsed / steps
            self.squash += self.squash_velocity * elapsed / steps
        return hop

    def _set_scalar(self, name: str, value: float) -> None:
        if self.applied.get(name) != value:
            self.applied[name] = value
            self.material.set_scalar_parameter_value(name, value)


class GhostFace(FaceAnimator):
    """The face on a ghost's body, which also takes the size the agent picked."""

    def __init__(self, body: unreal.StaticMeshComponent):
        super().__init__(body.create_dynamic_material_instance(0, unreal.load_object(None, FACE_MATERIAL_PATH)))
        self.body = body

    def set_appearance(self, name: str, appearance: dict | None, washed_out: bool = False) -> bool:
        changed = super().set_appearance(name, appearance, washed_out)
        if changed:
            size = agent_size(appearance)
            self.body.set_relative_scale3d(unreal.Vector(BODY_SCALE * size, BODY_SCALE * size, BODY_SCALE * size))
            self.material.set_scalar_parameter_value("HopHeight", HOP_HEIGHT * size)
        return changed
