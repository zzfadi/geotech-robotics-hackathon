"""Draws label text with a real font into render targets that the label materials read:
the text at full size plus smaller copies made by shrinking it, which the materials use
as mips so labels stay sharp up close and smooth far away.
"""

import math

import unreal

FONT_PATH = "/Engine/EngineFonts/Roboto.Roboto"
FONT_TYPEFACE = "Bold"
MAX_TEXTURE_SIZE = 2048
# Slate font sizes are points at 96 DPI
POINTS_PER_PIXEL = 0.75
# The text is drawn at full size, then shrunk to 1/2, 1/4, 1/8 and 1/16 size, all in one
# atlas that the label materials use as mips; the gap between copies matches
# TEXT_ATLAS_HLSL. Full-size copies are a multiple of 16 pixels so every copy is whole
ATLAS_LEVELS = 5
ATLAS_GAP = 4
ATLAS_ALIGN = 2 ** (ATLAS_LEVELS - 1)

_state = {"fonts": {}}


def _font(pixel_size: float) -> unreal.Font:
    # Glyphs are rasterized at the font's own size and scaling them while drawing blurs
    # them, so each text size gets its own font
    points = max(1, round(pixel_size * POINTS_PER_PIXEL))
    if points not in _state["fonts"]:
        source = unreal.load_object(None, FONT_PATH)
        font = unreal.new_object(unreal.Font)
        font.set_editor_property("font_cache_type", unreal.FontCacheType.RUNTIME)
        font.set_editor_property("composite_font", source.get_editor_property("composite_font"))
        font.set_editor_property("legacy_font_name", FONT_TYPEFACE)
        font.set_editor_property("legacy_font_size", points)
        _state["fonts"][points] = font
    return _state["fonts"][points]


def _new_target(world, width: int, height: int) -> unreal.TextureRenderTarget2D:
    # Render targets belong to the world they are made in and go away with it. No mips:
    # the engine doesn't rebuild them after a canvas draw, so the atlas provides its own
    target = unreal.RenderingLibrary.create_render_target2d(
        world, width, height, unreal.TextureRenderTargetFormat.RTF_RGBA8, unreal.LinearColor(0.0, 0.0, 0.0, 1.0), False)
    if target is None:
        raise RuntimeError(f"Could not create a {width}x{height} render target")
    return target


def _scratch_target(world) -> unreal.TextureRenderTarget2D:
    """A small canvas for measuring text; the measurement doesn't depend on its size.
    Joining a Multi-User session reloads the level, so it is remade for each world."""
    if _state.get("scratch_world") != world:
        _state["scratch_target"] = _new_target(world, 16, 16)
        _state["scratch_world"] = world
    return _state["scratch_target"]


def _measure_lines(canvas, font: unreal.Font, lines) -> tuple[float, float]:
    """Pixel width of the widest line and height of one line."""
    sizes = [canvas.clipped_text_size(font, line, unreal.Vector2D(1.0, 1.0)) for line in lines]
    return max(size.x for size in sizes), max(size.y for size in sizes)


class TextTexture:
    """Centered lines of text, white on black, drawn into a render target atlas that a
    label material reads as its text mask."""

    def __init__(self, world, text_size: float, pixels_per_unit: float, material):
        self.world = world
        self.text_size = text_size
        self.pixels_per_unit = pixels_per_unit
        self.material = material
        self.target = None

    def measure(self, lines) -> tuple[float, float]:
        """Width of the widest line and height of all lines, in world units."""
        if not lines:
            return 0.0, 0.0
        library = unreal.RenderingLibrary
        canvas, _, context = library.begin_draw_canvas_to_render_target(self.world, _scratch_target(self.world))
        width, line_height = _measure_lines(canvas, _font(self.text_size * self.pixels_per_unit), lines)
        library.end_draw_canvas_to_render_target(self.world, context)
        return width / self.pixels_per_unit, line_height * len(lines) / self.pixels_per_unit

    def draw(self, lines, width: float, height: float) -> None:
        """Draws the lines into a new atlas whose full-size copy covers width x height world units."""
        density = min(self.pixels_per_unit, MAX_TEXTURE_SIZE / max(width, height))
        base = (max(ATLAS_ALIGN, math.ceil(width * density / ATLAS_ALIGN) * ATLAS_ALIGN),
                max(ATLAS_ALIGN, math.ceil(height * density / ATLAS_ALIGN) * ATLAS_ALIGN))
        texture_size = (base[0] * 3 // 2 + ATLAS_GAP + 2, base[1] + ATLAS_GAP * ATLAS_LEVELS)
        # New targets every time: resizing one only takes effect after the next draw, so
        # the canvas would still draw at the old size
        atlas = _new_target(self.world, *texture_size)
        self.target = atlas
        self.material.set_texture_parameter_value("Mask", atlas)
        self.material.set_vector_parameter_value("Atlas", unreal.LinearColor(base[0], base[1], *texture_size))
        if not lines:
            return

        # Full size, drawn by the font, then each smaller copy shrunk from the one before:
        # drawing a texture at exactly half size averages each 2x2 block of its pixels
        levels = [_new_target(self.world, *base)]
        with _Canvas(self.world, levels[0]) as canvas:
            font = _font(self.text_size * density)
            _, line_height = _measure_lines(canvas, font, lines)
            top = (base[1] - line_height * len(lines)) * 0.5
            for index, line in enumerate(lines):
                canvas.draw_text(font, line, unreal.Vector2D(base[0] * 0.5, top + index * line_height),
                                 unreal.Vector2D(1.0, 1.0), unreal.LinearColor(1.0, 1.0, 1.0, 1.0), 0.0,
                                 unreal.LinearColor(0.0, 0.0, 0.0, 0.0), unreal.Vector2D(0.0, 0.0),
                                 True, False, False, unreal.LinearColor(0.0, 0.0, 0.0, 1.0))
        for level in range(1, ATLAS_LEVELS):
            size = (base[0] >> level, base[1] >> level)
            levels.append(_new_target(self.world, *size))
            with _Canvas(self.world, levels[level]) as canvas:
                _copy(canvas, levels[level - 1], (0, 0), size)

        with _Canvas(self.world, atlas) as canvas:
            for level, target in enumerate(levels):
                origin = (0, 0) if level == 0 else (
                    base[0] + ATLAS_GAP, base[1] - (base[1] >> (level - 1)) + ATLAS_GAP * (level - 1))
                _copy(canvas, target, origin, (base[0] >> level, base[1] >> level))


class _Canvas:
    """Draws on a render target for the length of a with block."""

    def __init__(self, world, target: unreal.TextureRenderTarget2D):
        self.world = world
        self.target = target
        self.context = None

    def __enter__(self) -> unreal.Canvas:
        canvas, _, self.context = unreal.RenderingLibrary.begin_draw_canvas_to_render_target(self.world, self.target)
        return canvas

    def __exit__(self, *_) -> None:
        unreal.RenderingLibrary.end_draw_canvas_to_render_target(self.world, self.context)


def _copy(canvas: unreal.Canvas, source: unreal.TextureRenderTarget2D, origin, size) -> None:
    canvas.draw_texture(source, unreal.Vector2D(*origin), unreal.Vector2D(*size), unreal.Vector2D(0.0, 0.0),
                        unreal.Vector2D(1.0, 1.0), unreal.LinearColor(1.0, 1.0, 1.0, 1.0),
                        unreal.BlendMode.BLEND_OPAQUE, 0.0, unreal.Vector2D(0.5, 0.5))
