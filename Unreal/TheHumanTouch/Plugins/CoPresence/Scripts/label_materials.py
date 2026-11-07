import unreal

# Materials for the labels above an agent's ghost. Both are camera-facing sprites drawn
# on top of the scene. Their text comes from a render target that ghost_labels draws
# with a real font (white on black, read from the red channel), so it stays sharp at any
# size and the outline and glow can reach all the way around every letter.

# The text mask is an atlas: the text at full size on the left, then shrunk to half,
# quarter, eighth and sixteenth size stacked to its right. Render targets don't rebuild
# their mips after drawing, so these are hand-made mips: each pixel reads the two copies
# closest to its size on screen and blends them, which keeps distant labels smooth
# instead of shimmering. Atlas.xy is the size of the full-size copy in pixels and
# Atlas.zw the size of the texture. The 4 pixel gap between copies matches ATLAS_GAP in
# ghost_labels.
TEXT_ATLAS_HLSL = """
struct TextAtlas
{
    float2 baseSize;
    float2 textureSize;
    float lowLevel;
    float levelBlend;

    float SampleLevel(Texture2D mask, SamplerState maskSampler, float level, float2 uv)
    {
        float scale = exp2(-level);
        float2 size = baseSize * scale;
        float2 origin = level < 0.5 ? float2(0.0, 0.0)
            : float2(baseSize.x + 4.0, baseSize.y * (1.0 - 2.0 * scale) + 4.0 * (level - 1.0));
        float2 margin = 0.5 / size;
        float2 pixel = origin + clamp(uv, margin, 1.0 - margin) * size;
        return Texture2DSampleLevel(mask, maskSampler, pixel / textureSize, 0).r;
    }

    float Sample(Texture2D mask, SamplerState maskSampler, float2 uv)
    {
        return lerp(SampleLevel(mask, maskSampler, lowLevel, uv),
                    SampleLevel(mask, maskSampler, lowLevel + 1.0, uv), levelBlend);
    }
};
TextAtlas atlas;
atlas.baseSize = Atlas.xy;
atlas.textureSize = Atlas.zw;
// Full-size texels per screen pixel picks the copies, like a mip chain
float2 footprint = max(abs(ddx(UV)), abs(ddy(UV))) * atlas.baseSize;
float lod = clamp(log2(max(max(footprint.x, footprint.y), 0.0001)), 0.0, 3.999);
atlas.lowLevel = floor(lod);
atlas.levelBlend = lod - atlas.lowLevel;
"""

# Name tag: warm white text, a dark outline and a soft orange glow, all built from the
# text mask by sampling around each pixel. Shape holds the sprite's width and height,
# the outline width and the glow radius, all in world units. The glow reads a copy two
# sizes smaller, which is already soft, so it fades smoothly instead of stepping.
NAME_TAG_HLSL = TEXT_ATLAS_HLSL + """
float2 outlineRadius = Shape.zz / Shape.xy;
float2 glowRadius = Shape.ww / Shape.xy;
float glowLevel = min(atlas.lowLevel + 2.0, 4.0);
float fill = atlas.Sample(Mask, MaskSampler, UV);
float outline = fill;
float glow = atlas.SampleLevel(Mask, MaskSampler, glowLevel, UV);
for (int i = 0; i < 16; i++)
{
    float angle = i * 0.39269908;
    float2 direction = float2(cos(angle), sin(angle));
    outline = max(outline, atlas.Sample(Mask, MaskSampler, UV + direction * outlineRadius));
    outline = max(outline, atlas.Sample(Mask, MaskSampler, UV + direction * outlineRadius * 0.5));
    glow += atlas.SampleLevel(Mask, MaskSampler, glowLevel, UV + direction * glowRadius * 0.45);
    glow += atlas.SampleLevel(Mask, MaskSampler, glowLevel, UV + direction * glowRadius * 0.9);
}
glow = saturate(glow / 33.0 * GlowGain);

float3 color = lerp(GlowColor.rgb, OutlineColor.rgb, outline);
color = lerp(color, TextColor.rgb, fill);
return float4(color, max(glow * GlowOpacity, outline));
"""

# Cartoon speech bubble: a white rounded body with a tail hooking down to the left, a
# black outline, an orange rim offset down and left like a sticker, and the text in
# dark ink. Three dots bounce inside it while the agent is thinking.
#
# Everything is measured in world units. BodySize is the white body; Frame holds the
# padding around it for the rim (x) and the room below it for the tail (y). The sprite
# is BodySize + (2 * pad, 2 * pad + 2 * tail): the tail room is mirrored above the body so
# the body, and the text in the mask, stay centered on the sprite. Tail holds the left
# end and width of the tail's base and the x of its tip, relative to the body's center.
BUBBLE_HLSL = TEXT_ATLAS_HLSL + """
float2 body = BodySize.xy;
float pad = Frame.x;
float tail = Frame.y;
float2 quad = body + float2(2.0 * pad, 2.0 * pad + 2.0 * tail);
float2 p = (UV - 0.5) * quad;
p.y = -p.y;

float2 halfBody = body * 0.5;
float radius = min(body.y * 0.45, 11.0);
float outlineWidth = 1.4;
float2 rimOffset = float2(-1.5, -2.0);
float3 white = float3(1.35, 1.32, 1.27);
float3 black = float3(0.015, 0.01, 0.008);
float3 orange = float3(1.0, 0.33, 0.04);
float3 ink = float3(0.03, 0.02, 0.015);

// Tail: a wedge from the lower left of the body down to its tip
float2 tailA = float2(Tail.x, -halfBody.y + 3.0);
float2 tailB = float2(Tail.x + Tail.y, -halfBody.y + 3.0);
float2 tailC = float2(Tail.z, -halfBody.y - tail + 1.0);

// Signed distance to the outline, negative inside: once for the body, once for the rim
float distances[2];
for (int layer = 0; layer < 2; layer++)
{
    float2 q = layer == 0 ? p : p - rimOffset;

    float2 corner = abs(q) - halfBody + radius;
    float box = length(max(corner, 0.0)) + min(max(corner.x, corner.y), 0.0) - radius;

    float2 e0 = tailB - tailA;
    float2 e1 = tailC - tailB;
    float2 e2 = tailA - tailC;
    float2 v0 = q - tailA;
    float2 v1 = q - tailB;
    float2 v2 = q - tailC;
    float2 pq0 = v0 - e0 * saturate(dot(v0, e0) / dot(e0, e0));
    float2 pq1 = v1 - e1 * saturate(dot(v1, e1) / dot(e1, e1));
    float2 pq2 = v2 - e2 * saturate(dot(v2, e2) / dot(e2, e2));
    float side = sign(e0.x * e2.y - e0.y * e2.x);
    float2 nearest = min(min(float2(dot(pq0, pq0), side * (v0.x * e0.y - v0.y * e0.x)),
                             float2(dot(pq1, pq1), side * (v1.x * e1.y - v1.y * e1.x))),
                         float2(dot(pq2, pq2), side * (v2.x * e2.y - v2.y * e2.x)));
    float wedge = -sqrt(nearest.x) * sign(nearest.y);

    // Smooth union so the tail grows out of the body without a seam
    float blend = 3.0;
    float h = max(blend - abs(box - wedge), 0.0) / blend;
    distances[layer] = min(box, wedge) - h * h * blend * 0.25;
}

float bodyDistance = distances[0];
float rimDistance = distances[1];
float aa = max(fwidth(bodyDistance), 0.0001);

float rimCover = saturate(0.5 - rimDistance / aa);
float rimFill = saturate(0.5 - (rimDistance + outlineWidth) / aa);
float bodyCover = saturate(0.5 - bodyDistance / aa);
float bodyFill = saturate(0.5 - (bodyDistance + outlineWidth) / aa);

float3 color = lerp(black, orange, rimFill);
color = lerp(color, lerp(black, white, bodyFill), bodyCover);
float alpha = max(rimCover, bodyCover);

// Dark text blended over white in linear color looks thinner than it is, and more so
// as it shrinks with distance, so the edges are weighted toward ink
float inkCover = saturate(pow(atlas.Sample(Mask, MaskSampler, UV), 0.6) * (1.0 + 0.15 * lod));
color = lerp(color, ink, inkCover * bodyFill);

if (Dots > 0.5)
{
    for (int i = 0; i < 3; i++)
    {
        float bounce = max(sin(Time * 7.0 - i * 0.9), 0.0) * 3.2;
        float2 center = float2((i - 1) * 10.5, -1.6 + bounce);
        float2 offset = p - center;
        float dotRadius = 3.5;
        float dotCover = saturate(0.5 - (length(offset) - dotRadius) / aa) * bodyFill;
        float shine = saturate(dot(offset / dotRadius, float2(-0.55, 0.65)));
        float3 dotColor = lerp(float3(0.14, 0.08, 0.05), float3(0.6, 0.47, 0.4), shine * shine);
        color = lerp(color, dotColor, dotCover);
    }
}

return float4(color, alpha);
"""

# Black until ghost_labels assigns the drawn text
EMPTY_MASK_TEXTURE = "/Engine/EngineResources/Black.Black"

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
material_library = unreal.MaterialEditingLibrary


class _Builder:
    """Lays out one sprite material: parameters on the left feeding a Custom node whose
    float4 result becomes the emissive color and opacity."""

    def __init__(self, path: str):
        folder, name = path.rsplit("/", 1)
        self.material = asset_tools.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
        self.material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        self.material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        self.material.set_editor_property("disable_depth_test", True)
        self.material.set_editor_property("two_sided", True)
        # Drawn after temporal anti-aliasing: the sprites write no depth or motion, so it
        # would jitter them and smear the text while the camera moves
        self.material.set_editor_property("translucency_pass", unreal.MaterialTranslucencyPass.MTP_AFTER_MOTION_BLUR)
        self.inputs: dict[str, unreal.MaterialExpression] = {}
        self.row = 0

    def _node(self, expression_class, x: int):
        node = material_library.create_material_expression(self.material, expression_class, x, self.row * 120)
        self.row += 1
        return node

    def uv(self) -> None:
        self.inputs["UV"] = self._node(unreal.MaterialExpressionTextureCoordinate, -900)

    def time(self) -> None:
        self.inputs["Time"] = self._node(unreal.MaterialExpressionTime, -900)

    def mask(self) -> None:
        node = self._node(unreal.MaterialExpressionTextureObjectParameter, -900)
        node.set_editor_property("parameter_name", "Mask")
        node.set_editor_property("texture", unreal.load_asset(EMPTY_MASK_TEXTURE))
        self.inputs["Mask"] = node

    def vector(self, name: str, value: unreal.LinearColor) -> None:
        node = self._node(unreal.MaterialExpressionVectorParameter, -900)
        node.set_editor_property("parameter_name", name)
        node.set_editor_property("default_value", value)
        self.inputs[name] = node

    def scalar(self, name: str, value: float) -> None:
        node = self._node(unreal.MaterialExpressionScalarParameter, -900)
        node.set_editor_property("parameter_name", name)
        node.set_editor_property("default_value", value)
        self.inputs[name] = node

    def finish(self, code: str, description: str) -> unreal.Material:
        custom = material_library.create_material_expression(self.material, unreal.MaterialExpressionCustom, -500, 0)
        custom.set_editor_property("code", code)
        custom.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT4)
        custom.set_editor_property("description", description)
        custom_inputs = []
        for input_name in self.inputs:
            custom_input = unreal.CustomInput()
            custom_input.set_editor_property("input_name", input_name)
            custom_inputs.append(custom_input)
        custom.set_editor_property("inputs", custom_inputs)
        for input_name, source in self.inputs.items():
            # Vector parameters reach the custom node as RGB unless the RGBA output is used
            output = "RGBA" if isinstance(source, unreal.MaterialExpressionVectorParameter) else ""
            if not material_library.connect_material_expressions(source, output, custom, input_name):
                raise RuntimeError(f"Could not connect {input_name} in {self.material.get_name()}")

        for channels, material_property, y in (("rgb", unreal.MaterialProperty.MP_EMISSIVE_COLOR, 0),
                                               ("a", unreal.MaterialProperty.MP_OPACITY, 150)):
            mask = material_library.create_material_expression(self.material, unreal.MaterialExpressionComponentMask, -250, y)
            for channel in "rgba":
                mask.set_editor_property(channel, channel in channels)
            material_library.connect_material_expressions(custom, "", mask, "")
            output = mask
            if material_property == unreal.MaterialProperty.MP_EMISSIVE_COLOR:
                # These are UI labels: bright daylight exposure must not turn
                # their white background and lettering into black silhouettes.
                inverse = material_library.create_material_expression(
                    self.material, unreal.MaterialExpressionEyeAdaptationInverse, 0, y)
                weight = material_library.create_material_expression(
                    self.material, unreal.MaterialExpressionConstant, -250, -150)
                weight.set_editor_property("r", 1.0)
                material_library.connect_material_expressions(mask, "", inverse, "LightValueInput")
                material_library.connect_material_expressions(weight, "", inverse, "AlphaInput")
                output = inverse
            material_library.connect_material_property(output, "", material_property)

        material_library.recompile_material(self.material)
        unreal.EditorAssetLibrary.save_loaded_asset(self.material)
        return self.material


def build_name_tag_material(path: str) -> unreal.Material:
    builder = _Builder(path)
    builder.uv()
    builder.mask()
    builder.vector("Atlas", unreal.LinearColor(1024.0, 256.0, 1540.0, 272.0))
    builder.vector("Shape", unreal.LinearColor(140.0, 35.0, 0.8, 4.5))
    builder.vector("TextColor", unreal.LinearColor(1.3, 1.15, 1.0, 1.0))
    builder.vector("OutlineColor", unreal.LinearColor(0.02, 0.01, 0.0, 1.0))
    builder.vector("GlowColor", unreal.LinearColor(3.2, 0.75, 0.12, 1.0))
    builder.scalar("GlowOpacity", 0.9)
    builder.scalar("GlowGain", 2.2)
    return builder.finish(NAME_TAG_HLSL, "Name tag")


def build_bubble_material(path: str) -> unreal.Material:
    builder = _Builder(path)
    builder.uv()
    builder.mask()
    builder.vector("Atlas", unreal.LinearColor(1024.0, 256.0, 1540.0, 272.0))
    builder.vector("BodySize", unreal.LinearColor(60.0, 26.0, 0.0, 0.0))
    builder.vector("Frame", unreal.LinearColor(4.0, 8.0, 0.0, 0.0))
    builder.vector("Tail", unreal.LinearColor(-12.0, 10.0, -16.0, 0.0))
    builder.time()
    builder.scalar("Dots", 0.0)
    return builder.finish(BUBBLE_HLSL, "Speech bubble")
