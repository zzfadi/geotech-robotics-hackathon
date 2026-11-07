"""Restore the saved pool float color recipe after a build map loads.

Actor-local dynamic materials are transient; the map stores the geometry while
this recipe recreates its finishes without creating new content packages.
"""
import unreal


def restore():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world.get_path_name().split('.')[0] != '/Game/Maps/Tomorrowland':
        return 0
    restored = 0
    base = unreal.load_asset('/Engine/BasicShapes/BasicShapeMaterial')
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        name = actor.get_actor_label()
        if not name.startswith('Pool_Doughnut_'):
            continue
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        if component is None:
            continue
        component.set_material(0, base)
        material = component.create_dynamic_material_instance(0)
        color = (.75, .1, .3, 1) if '_1_' in name else (.9, .5, .04, 1)
        material.set_vector_parameter_value('Color', unreal.LinearColor(*color))
        restored += 1
    return restored
