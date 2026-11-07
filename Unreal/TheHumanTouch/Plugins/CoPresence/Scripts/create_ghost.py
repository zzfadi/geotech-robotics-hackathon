import os
import sys

import unreal

# Lets this commandlet script import its sibling modules
sys.path.insert(0, unreal.Paths.convert_relative_path_to_full(
    os.path.join(unreal.Paths.project_plugins_dir(), "CoPresence", "Scripts")))
from face_material import build_face_material, build_icon_material  # noqa: E402
from label_materials import build_bubble_material, build_name_tag_material  # noqa: E402

# Builds the agent's Multi-User avatar: a round cartoon head whose material draws its
# emoting eyes and makes it bounce, plus two camera-facing sprites for its glowing name tag and its cartoon
# speech bubble. ghost_labels drives the eyes, colors, size and labels in every editor.
# The agent editor advertises this class as its DesktopAvatarActorClass, so other users
# see it wherever the agent's viewport camera is. Rebuilds the Blueprint on every run;
# run it with both editors closed.
# Content of the CoPresence plugin
GHOST_FOLDER = "/CoPresence"
GHOST_NAME = "BP_AgentGhost"
SPHERE_MESH = "/Engine/BasicShapes/Sphere.Sphere"

FACE_MATERIAL = f"{GHOST_FOLDER}/M_AgentFace"
ICON_MATERIAL = f"{GHOST_FOLDER}/M_AgentIcon"
NAME_TAG_MATERIAL = f"{GHOST_FOLDER}/M_AgentNameTag"
BUBBLE_MATERIAL = f"{GHOST_FOLDER}/M_AgentBubble"

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
subobjects = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
subobject_library = unreal.SubobjectDataBlueprintFunctionLibrary


def find_handle(handles, variable_name: str):
    for handle in handles:
        data = subobject_library.get_data(handle)
        if str(subobject_library.get_variable_name(data)) == variable_name:
            return handle
    raise RuntimeError(f"Component {variable_name} not found on ghost blueprint")


def template_of(handle, blueprint) -> unreal.Object:
    return subobject_library.get_object_for_blueprint(subobject_library.get_data(handle), blueprint)


def add_component(blueprint, parent_handle, component_class, name: str):
    params = unreal.AddNewSubobjectParams(
        parent_handle=parent_handle, new_class=component_class, blueprint_context=blueprint)
    handle, fail_reason = subobjects.add_new_subobject(params)
    if not subobject_library.is_handle_valid(handle):
        raise RuntimeError(f"Could not add {name}: {fail_reason}")
    subobjects.rename_subobject(handle, unreal.Text(name))
    return template_of(handle, blueprint)


def add_sprite(blueprint, parent_handle, name: str, height: float) -> None:
    # Starts empty; ghost_labels gives it a material instance with the drawn text and
    # sizes it. Sprites always face the camera
    sprite = add_component(blueprint, parent_handle, unreal.MaterialBillboardComponent, name)
    sprite.set_editor_property("relative_location", unreal.Vector(0.0, 0.0, height))
    sprite.set_editor_property("cast_shadow", False)


def build_ghost() -> None:
    # A commandlet starts before the plugin's content is scanned, and existence checks
    # would miss the assets a previous run created
    unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous([GHOST_FOLDER], True)
    blueprint_path = f"{GHOST_FOLDER}/{GHOST_NAME}"
    if unreal.EditorAssetLibrary.does_asset_exist(blueprint_path):
        unreal.EditorAssetLibrary.delete_asset(blueprint_path)
    if unreal.EditorAssetLibrary.does_asset_exist(blueprint_path):
        raise RuntimeError(f"Could not delete {blueprint_path}; remove Plugins/CoPresence/Content/{GHOST_NAME}.uasset and rerun")

    for generated_path in (FACE_MATERIAL, ICON_MATERIAL, BUBBLE_MATERIAL, NAME_TAG_MATERIAL):
        if unreal.EditorAssetLibrary.does_asset_exist(generated_path):
            unreal.EditorAssetLibrary.delete_asset(generated_path)
    face_material = build_face_material(FACE_MATERIAL)
    build_icon_material(ICON_MATERIAL)
    build_name_tag_material(NAME_TAG_MATERIAL)
    build_bubble_material(BUBBLE_MATERIAL)
    sphere = unreal.load_asset(SPHERE_MESH)

    presence_class = unreal.load_class(None, "/Script/ConcertSyncClient.ConcertClientDesktopPresenceActor")
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", presence_class)
    blueprint = asset_tools.create_asset(GHOST_NAME, GHOST_FOLDER, unreal.Blueprint, factory)

    handles = subobjects.k2_gather_subobject_data_for_blueprint(blueprint)

    # Body: the presence mesh keeps whatever mesh the class sets, so it replaces the head
    body = template_of(find_handle(handles, "PresenceMeshComponent"), blueprint)
    body.set_editor_property("static_mesh", sphere)
    body.set_editor_property("override_materials", [face_material])
    body.set_editor_property("relative_scale3d", unreal.Vector(0.44, 0.44, 0.44))
    # Bouncing moves the head a little past the sphere's own bounds
    body.set_editor_property("bounds_scale", 1.5)

    # No monitor mesh: the stock desktop avatar draws a screen in front of the head
    monitor = template_of(find_handle(handles, "DesktopMeshComponent"), blueprint)
    monitor.set_editor_property("static_mesh", None)

    # The stock label is small and pale; NameTag replaces it
    stock_label = template_of(find_handle(handles, "PresenceTextComponent"), blueprint)
    stock_label.set_editor_property("visible", False)

    root_handle = handles[0]
    add_sprite(blueprint, root_handle, "NameTag", 46.0)
    add_sprite(blueprint, root_handle, "Bubble", 80.0)
    # What the agent says in the chat stacks above the bubble (ghost_labels.SPEECH_SPRITES)
    for name in ("Speech0", "Speech1", "Speech2"):
        add_sprite(blueprint, root_handle, name, 80.0)

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint)


build_ghost()
