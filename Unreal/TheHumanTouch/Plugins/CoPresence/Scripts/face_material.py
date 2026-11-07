import unreal

# The agent's head, drawn by its materials rather than modeled: the 3D head on the ghost
# (M_AgentFace) and the flat icon in the Agents widget (M_AgentIcon) share one cartoon
# look, a round toon-shaded body with an outline, rosy cheeks and big eyes. The eyes
# follow the UnrealAssist "Emotions" icons in Figma (error X, love heart, happy
# arrowhead, interested triangle) and the GeoTech agent collection (Oracle's bar over a
# triangle for thinking, Manager's chevron for talking): a colored disc with a black
# glyph, plus a plain pupil for neutral in the same style.
#
# Eye shapes are in the icon's own units, where an eye is 20 across, seen from the front
# with y up. Emote picks the shape (0 neutral, 1 thinking, 2 interested, 3 happy,
# 4 error, 5 love, 6 content), Openness squashes the eyes shut for blinks, and Look moves
# the pupil. Squash stretches the head tall (positive) or flat (negative) and Hop lifts
# it, so ghost_face can make it bounce.
SHAPES_HLSL = """
struct Shapes
{
    float Box(float2 p, float2 halfSize)
    {
        float2 d = abs(p) - halfSize;
        return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
    }
    float Segment(float2 p, float2 a, float2 b)
    {
        float2 pa = p - a;
        float2 ba = b - a;
        return length(pa - ba * saturate(dot(pa, ba) / dot(ba, ba)));
    }
    // Signed distance to a closed polygon of four points, negative inside
    float Quad(float2 p, float2 v0, float2 v1, float2 v2, float2 v3)
    {
        float2 v[4] = { v0, v1, v2, v3 };
        float d = dot(p - v[0], p - v[0]);
        float s = 1.0;
        for (int i = 0, j = 3; i < 4; j = i, i++)
        {
            float2 e = v[j] - v[i];
            float2 w = p - v[i];
            float2 b = w - e * saturate(dot(w, e) / dot(e, e));
            d = min(d, dot(b, b));
            bool3 c = bool3(p.y >= v[i].y, p.y < v[j].y, e.x * w.y > e.y * w.x);
            if (all(c) || all(!c))
            {
                s *= -1.0;
            }
        }
        return s * sqrt(d);
    }
    float Triangle(float2 p, float2 a, float2 b, float2 c)
    {
        float2 e0 = b - a;
        float2 e1 = c - b;
        float2 e2 = a - c;
        float2 v0 = p - a;
        float2 v1 = p - b;
        float2 v2 = p - c;
        float2 pq0 = v0 - e0 * saturate(dot(v0, e0) / dot(e0, e0));
        float2 pq1 = v1 - e1 * saturate(dot(v1, e1) / dot(e1, e1));
        float2 pq2 = v2 - e2 * saturate(dot(v2, e2) / dot(e2, e2));
        float s = sign(e0.x * e2.y - e0.y * e2.x);
        float2 d = min(min(float2(dot(pq0, pq0), s * (v0.x * e0.y - v0.y * e0.x)),
                           float2(dot(pq1, pq1), s * (v1.x * e1.y - v1.y * e1.x))),
                       float2(dot(pq2, pq2), s * (v2.x * e2.y - v2.y * e2.x)));
        return -sqrt(d.x) * sign(d.y);
    }
    float Heart(float2 p)
    {
        p.x = abs(p.x);
        if (p.y + p.x > 1.0)
        {
            return length(p - float2(0.25, 0.75)) - 0.35355;
        }
        float2 q = p - 0.5 * max(p.x + p.y, 0.0);
        return sqrt(min(dot(p - float2(0.0, 1.0), p - float2(0.0, 1.0)), dot(q, q))) * sign(p.x - p.y);
    }
    // The cut-out for one emote, negative inside the shape
    float Glyph(float2 q, int emote, float time, float2 look)
    {
        if (emote == 2)
        {
            return Triangle(q, float2(-1.55, 1.2), float2(6.45, 1.2), float2(2.45, -5.7)) - 1.6;
        }
        if (emote == 3)
        {
            return Quad(q, float2(1.46, 5.22), float2(6.09, -0.7), float2(1.46, 0.93), float2(-3.18, -0.7));
        }
        if (emote == 4)
        {
            float2 r = float2(q.x + q.y, q.x - q.y) * 0.70711;
            return min(Box(r, float2(6.9, 1.06)), Box(float2(r.y, r.x), float2(6.9, 1.06)));
        }
        if (emote == 5)
        {
            float2 scale = float2(8.48, 9.69);
            return Heart((q - float2(2.28, -7.3)) / scale) * min(scale.x, scale.y);
        }
        if (emote == 1)
        {
            // Thinking: Oracle's bar over a small triangle, bobbing gently
            float2 p = q - float2(0.0, sin(time * 2.2) * 0.35);
            float bar = Box(p - float2(0.0, 1.5), float2(5.2, 0.9));
            return min(bar, Triangle(p, float2(-3.4, -4.4), float2(3.4, -4.4), float2(0.0, -0.4)));
        }
        if (emote == 6)
        {
            // Content: Manager's open chevron
            float2 left = q - float2(-2.25, 0.35);
            float2 right = q - float2(2.25, 0.35);
            float armLeft = Box(float2(left.x + left.y, left.y - left.x) * 0.70711, float2(4.0, 1.0));
            float armRight = Box(float2(right.x - right.y, right.y + right.x) * 0.70711, float2(4.0, 1.0));
            return min(armLeft, armRight);
        }
        return length(q - float2(1.5, -0.4) - look * 3.0) - 3.6;
    }
};
Shapes shapes;
"""

# Cartoon shading for the round body, lit from the viewer's upper left whatever the level
# looks like: flat color, a cool shadow on the lower right and a small shine, each with a
# crisp edge. Normals have x right, y up and z toward the viewer
TOON_HLSL = """
struct Toon
{
    // 1 where value is above threshold, with an edge one pixel wide
    float Step(float value, float threshold)
    {
        return saturate(0.5 + (value - threshold) / max(fwidth(value), 0.0001));
    }
    float3 Shade(float3 normal, float3 body)
    {
        float3 light = normalize(float3(-0.4, 0.7, 0.6));
        float3 color = lerp(body * float3(0.5, 0.52, 0.7), body, Step(dot(normal, light), 0.1));
        float3 shine = normalize(light + float3(0.0, 0.0, 1.0));
        return lerp(color, lerp(body, float3(1.0, 1.0, 1.0), 0.7), Step(dot(normal, shine), 0.975));
    }
    // Rosy cheeks below the eyes, at `face` in eye units
    float Cheeks(float2 face)
    {
        float cover = 0.0;
        for (int side = -1; side <= 1; side += 2)
        {
            float2 cheek = (face - float2(14.5 * side, -12.0)) / float2(4.6, 2.4);
            float edge = (length(cheek) - 1.0) * 2.4;
            cover = max(cover, saturate(0.5 - edge / max(fwidth(edge), 0.0001)));
        }
        return cover;
    }
};
Toon toon;
"""

# Both eyes at `face` (eye units, centered between the eyes): x is the colored disc's
# coverage, y the black glyph's, z the dark ring around the disc, w the catchlight
EYE_PAIR_HLSL = """
int emote = (int)round(Emote);
float openness = clamp(Openness, 0.04, 1.0);
float4 eye = 0.0;
for (int side = -1; side <= 1; side += 2)
{
    float2 q = face - float2(11.0 * side, 0.0);
    float disc;
    float glyph = 1.0e4;
    float sparkle = 1.0e4;
    if (openness < 0.18)
    {
        // Closed: a short rounded line
        disc = shapes.Segment(q, float2(-7.5, 0.0), float2(7.5, 0.0)) - 1.3;
    }
    else
    {
        float2 squashed = float2(q.x, q.y / openness);
        disc = (length(squashed) - 10.0) * openness;
        glyph = shapes.Glyph(squashed, emote, Time, Look.xy) * openness;
        // On the pupil when there is one, else on the disc's upper left
        float3 shine = emote == 0 ? float3(float2(0.1, 1.0) + Look.xy * 3.0, 1.2) : float3(-4.8, 4.8, 2.0);
        sparkle = (length(squashed - shine.xy) - shine.z) * openness;
    }
    float discWidth = max(fwidth(disc), 0.0001);
    float discCover = saturate(0.5 - disc / discWidth);
    float ringCover = saturate(0.5 - (disc - 1.3) / discWidth);
    float glyphCover = saturate(0.5 - glyph / max(fwidth(glyph), 0.0001)) * discCover;
    float sparkleCover = saturate(0.5 - sparkle / max(fwidth(sparkle), 0.0001)) * discCover * (emote == 0 ? 1.0 : 1.0 - glyphCover);
    eye = max(eye, float4(discCover, glyphCover, ringCover, sparkleCover));
}
"""

# Paints the shaded body, cheeks, eyes and outline
COMPOSE_HLSL = """
float3 color = toon.Shade(normal, bodyColor);
color = lerp(color, float3(1.0, 0.33, 0.42), cheeks * BlushAmount);
color = lerp(color, LineColor.rgb, eye.z);
color = lerp(color, eyeColor, eye.x);
color = lerp(color, GlyphColor.rgb, eye.y);
color = lerp(color, float3(1.0, 1.0, 1.0), eye.w);
color = lerp(color, LineColor.rgb, outline);
"""

FACE_HLSL = SHAPES_HLSL + TOON_HLSL + """
// Front of the body is local +X; the viewer's left is local +Y. Positions go to world
// units through the mesh's scale, then to eye units
float2 world = float2(-LocalPos.y * length(AxisY), LocalPos.z * length(AxisZ));
float2 face = (world - float2(0.0, Placement.y)) * (20.0 / Placement.x);
float frontFacing = saturate(LocalPos.x / 15.0);
""" + EYE_PAIR_HLSL + """
eye *= frontFacing;
float cheeks = toon.Cheeks(face) * frontFacing;
// View space has z pointing away from the viewer
float3 normal = normalize(NormalView) * float3(1.0, 1.0, -1.0);
// The outline is a share of the head's size up close, and at least a couple of pixels
// far away, where facing drops to 0 about twice as fast per pixel as it is from the edge
float facing = dot(normalize(NormalWorld), CameraVector);
float outline = max(1.0 - toon.Step(facing, 0.3), saturate(2.1 - facing / (2.0 * max(fwidth(facing), 0.0001))));
// Unlit colors still go through the tonemapper, which pales them, so start them richer
float3 bodyColor = BodyColor.rgb * 1.15;
float3 eyeColor = max(lerp(dot(EyeColor.rgb, float3(0.2126, 0.7152, 0.0722)).xxx, EyeColor.rgb, 1.25), 0.0) * 1.1;
""" + COMPOSE_HLSL + """
return color;
"""

# Squash and stretch for the 3D head, as an offset in the body's own space: taller and
# thinner when Squash is positive, keeping its volume
MOTION_HLSL = """
float stretch = max(1.0 + Squash, 0.2);
return LocalPos * float3(rsqrt(stretch), rsqrt(stretch), stretch) - LocalPos;
"""

# Floating up and down gently, plus hops
LIFT_HLSL = """
return float3(0.0, 0.0, sin(Time * 2.1 + Phase) * 1.4 + Hop * HopHeight);
"""

# The whole head, flat, for the Agents widget: the same body and eyes, lit from the same
# side, squashing and hopping like the 3D head
ICON_HLSL = SHAPES_HLSL + TOON_HLSL + """
float2 head = float2(UV.x - 0.5, 0.53 - UV.y) - float2(0.0, Hop * 0.06);
float stretch = max(1.0 + Squash, 0.2);
head = float2(head.x * sqrt(stretch), head.y / stretch);
float radius = 0.41;
float2 unit = head / radius;
float3 normal = float3(unit, sqrt(saturate(1.0 - dot(unit, unit))));
float2 face = (head - float2(0.0, -0.02)) / (0.27 / 20.0);
""" + EYE_PAIR_HLSL + """
float cheeks = toon.Cheeks(face);
float headDistance = length(head) - radius;
float pixel = max(fwidth(headDistance), 0.0001);
float headCover = saturate(0.5 - headDistance / pixel);
float outline = saturate(1.8 + headDistance / pixel);
float3 bodyColor = BodyColor.rgb;
float3 eyeColor = EyeColor.rgb;
""" + COMPOSE_HLSL + """
return float4(color, headCover);
"""

# Default look, as linear colors
WARM_WHITE = unreal.LinearColor(0.930, 0.906, 0.855, 1.0)
INK = unreal.LinearColor(0.010, 0.010, 0.014, 1.0)

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
material_library = unreal.MaterialEditingLibrary


def _node(material, expression_class, x: int, y: int):
    return material_library.create_material_expression(material, expression_class, x, y)


def _parameter(material, expression_class, name: str, value, y: int):
    node = _node(material, expression_class, -1200, y)
    node.set_editor_property("parameter_name", name)
    node.set_editor_property("default_value", value)
    return node


def _axis_length_source(material, vector, y: int):
    constant = _node(material, unreal.MaterialExpressionConstant3Vector, -1400, y)
    constant.set_editor_property("constant", unreal.LinearColor(*vector, 0.0))
    transform = _node(material, unreal.MaterialExpressionTransform, -1200, y)
    transform.set_editor_property("transform_source_type", unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_LOCAL)
    transform.set_editor_property("transform_type", unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
    material_library.connect_material_expressions(constant, "", transform, "")
    return transform


def _connect(source, output: str, target, input_name: str) -> None:
    if not material_library.connect_material_expressions(source, output, target, input_name):
        raise RuntimeError(f"Could not connect {input_name}")


def _channel(material, source, channel: str, y: int):
    # Masks start with more than one channel picked, so set all four
    mask = _node(material, unreal.MaterialExpressionComponentMask, -600, y)
    for name in "rgba":
        mask.set_editor_property(name, name == channel)
    _connect(source, "", mask, "")
    return mask


def _custom(material, code: str, output_type, description: str, inputs: dict):
    custom = _node(material, unreal.MaterialExpressionCustom, -800, 0)
    custom.set_editor_property("code", code)
    custom.set_editor_property("output_type", output_type)
    custom.set_editor_property("description", description)
    custom_inputs = []
    for input_name in inputs:
        custom_input = unreal.CustomInput()
        custom_input.set_editor_property("input_name", input_name)
        custom_inputs.append(custom_input)
    custom.set_editor_property("inputs", custom_inputs)
    for input_name, source in inputs.items():
        # Vector parameters reach the custom node as RGB unless the RGBA output is used
        output = "RGBA" if isinstance(source, unreal.MaterialExpressionVectorParameter) else ""
        _connect(source, output, custom, input_name)
    return custom


def _emote_parameters(material) -> dict:
    return {
        "Emote": _parameter(material, unreal.MaterialExpressionScalarParameter, "Emote", 0.0, -100),
        "Openness": _parameter(material, unreal.MaterialExpressionScalarParameter, "Openness", 1.0, 0),
        "Look": _parameter(material, unreal.MaterialExpressionVectorParameter, "Look", unreal.LinearColor(0.0, 0.0, 0.0, 0.0), 100),
        "Time": _node(material, unreal.MaterialExpressionTime, -1200, 300),
    }


def _look_parameters(material) -> dict:
    vector = unreal.MaterialExpressionVectorParameter
    return {
        "BodyColor": _parameter(material, vector, "BodyColor", WARM_WHITE, 400),
        "EyeColor": _parameter(material, vector, "EyeColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0), 500),
        "GlyphColor": _parameter(material, vector, "GlyphColor", unreal.LinearColor(0.004, 0.004, 0.004, 1.0), 600),
        "LineColor": _parameter(material, vector, "LineColor", INK, 700),
        "BlushAmount": _parameter(material, unreal.MaterialExpressionScalarParameter, "Blush", 0.6, 800),
    }


def _bounce_parameters(material) -> dict:
    scalar = unreal.MaterialExpressionScalarParameter
    return {
        "Squash": _parameter(material, scalar, "Squash", 0.0, 900),
        "Hop": _parameter(material, scalar, "Hop", 0.0, 1000),
    }


def _new_material(path: str) -> unreal.Material:
    folder, name = path.rsplit("/", 1)
    material = asset_tools.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    # Flat cartoon colors that look the same in any lighting
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    return material


def _transform_vector(material, source, from_space, to_space, y: int):
    transform = _node(material, unreal.MaterialExpressionTransform, -1400, y)
    transform.set_editor_property("transform_source_type", from_space)
    transform.set_editor_property("transform_type", to_space)
    _connect(source, "", transform, "")
    return transform


def build_icon_material(path: str) -> unreal.Material:
    material = _new_material(path)
    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_UI)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)

    inputs = {
        "UV": _node(material, unreal.MaterialExpressionTextureCoordinate, -1200, -300),
        **_emote_parameters(material),
        **_look_parameters(material),
        **_bounce_parameters(material),
    }
    custom = _custom(material, ICON_HLSL, unreal.CustomMaterialOutputType.CMOT_FLOAT4, "Agent icon", inputs)
    color = _node(material, unreal.MaterialExpressionComponentMask, -500, 0)
    for channel in "rgba":
        color.set_editor_property(channel, channel in "rgb")
    _connect(custom, "", color, "")
    material_library.connect_material_property(color, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    material_library.connect_material_property(_channel(material, custom, "a", 150), "", unreal.MaterialProperty.MP_OPACITY)

    material_library.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def build_face_material(path: str) -> unreal.Material:
    material = _new_material(path)
    vector_source = unreal.MaterialVectorCoordTransformSource
    vector_target = unreal.MaterialVectorCoordTransform

    # Where the surface is before the bounce moves it, so the face moves along with it
    world_position = _node(material, unreal.MaterialExpressionWorldPosition, -1600, -400)
    world_position.set_editor_property("world_position_shader_offset",
                                       unreal.WorldPositionIncludedOffsets.WPT_EXCLUDE_ALL_SHADER_OFFSETS)
    local_position = _node(material, unreal.MaterialExpressionTransformPosition, -1400, -400)
    local_position.set_editor_property("transform_source_type", unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_WORLD)
    local_position.set_editor_property("transform_type", unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_LOCAL)
    _connect(world_position, "", local_position, "")
    normal = _node(material, unreal.MaterialExpressionVertexNormalWS, -1600, -600)

    emote = _emote_parameters(material)
    bounce = _bounce_parameters(material)
    inputs = {
        "LocalPos": local_position,
        "AxisY": _axis_length_source(material, (0.0, 1.0, 0.0), -300),
        "AxisZ": _axis_length_source(material, (0.0, 0.0, 1.0), -200),
        "NormalWorld": normal,
        "NormalView": _transform_vector(material, normal, vector_source.TRANSFORMSOURCE_WORLD, vector_target.TRANSFORM_VIEW, -700),
        "CameraVector": _node(material, unreal.MaterialExpressionCameraVectorWS, -1600, -800),
        **emote,
        # x: width of one eye in world units, y: height of the eyes above the body's center
        "Placement": _parameter(material, unreal.MaterialExpressionVectorParameter, "Placement", unreal.LinearColor(13.5, 0.0, 0.0, 0.0), 200),
        **_look_parameters(material),
    }
    head = _custom(material, FACE_HLSL, unreal.CustomMaterialOutputType.CMOT_FLOAT3, "Head", inputs)
    material_library.connect_material_property(head, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    motion = _custom(material, MOTION_HLSL, unreal.CustomMaterialOutputType.CMOT_FLOAT3, "Squash and stretch",
                     {"LocalPos": local_position, "Squash": bounce["Squash"]})
    lift = _custom(material, LIFT_HLSL, unreal.CustomMaterialOutputType.CMOT_FLOAT3, "Float and hop", {
        "Time": emote["Time"],
        "Phase": _parameter(material, unreal.MaterialExpressionScalarParameter, "Phase", 0.0, 1100),
        "Hop": bounce["Hop"],
        "HopHeight": _parameter(material, unreal.MaterialExpressionScalarParameter, "HopHeight", 9.0, 1200),
    })
    offset = _node(material, unreal.MaterialExpressionAdd, -400, 1000)
    _connect(_transform_vector(material, motion, vector_source.TRANSFORMSOURCE_LOCAL, vector_target.TRANSFORM_WORLD, 1000),
             "", offset, "A")
    _connect(lift, "", offset, "B")
    material_library.connect_material_property(offset, "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)

    material_library.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material
