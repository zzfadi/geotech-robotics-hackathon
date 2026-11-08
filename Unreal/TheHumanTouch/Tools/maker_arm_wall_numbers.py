"""Paint 1–11 onto room walls with translucent texture decal materials.

Run import_materials() then apply(index) in scoped tasks while the fleet is idle.
Numbers are artistic navigation aids; no robot/table/camera transforms change.
"""
from pathlib import Path
import unreal
import maker_arm_grid as grid
import maker_arm_fleet as fleet

ASSETS = '/Game/_Agents/Denys_Mac_Codex/Environment/WallNumbers'


def import_materials():
    fleet._check_idle()
    root = Path(unreal.Paths.project_dir()) / 'SourceAssets/Environment/WallNumbers'
    tasks = []
    for n in range(1, 12):
        if unreal.load_asset(ASSETS + '/T_WallNumber_%02d' % n):
            continue
        task = unreal.AssetImportTask()
        task.filename = str(root / ('wall_number_%02d.png' % n))
        task.destination_path = ASSETS
        task.destination_name = 'T_WallNumber_%02d' % n
        task.automated = True
        task.save = False
        tasks.append(task)
    if tasks:
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
    for n in range(1, 12):
        name = 'M_WallNumber_%02d' % n
        mat = unreal.load_asset(ASSETS + '/' + name)
        texture = unreal.load_asset(ASSETS + '/T_WallNumber_%02d' % n)
        if texture is None:
            raise RuntimeError('Missing imported wall number %d' % n)
        if mat is None:
            mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, ASSETS, unreal.Material, unreal.MaterialFactoryNew())
        mat.modify()
        unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
        mat.set_editor_property('material_domain', unreal.MaterialDomain.MD_DEFERRED_DECAL)
        mat.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
        sample = unreal.MaterialEditingLibrary.create_material_expression(mat, unreal.MaterialExpressionTextureSample)
        sample.set_editor_property('texture', texture)
        unreal.MaterialEditingLibrary.connect_material_property(sample, 'RGB', unreal.MaterialProperty.MP_BASE_COLOR)
        unreal.MaterialEditingLibrary.connect_material_property(sample, 'A', unreal.MaterialProperty.MP_OPACITY)
        unreal.MaterialEditingLibrary.recompile_material(mat)
    return {'number_textures': 11, 'decal_materials': 11}


def apply(index):
    fleet._check_idle()
    if not 0 <= index <= 10:
        raise ValueError('Wall number index must be 0..10')
    dx, dy, _ = grid.offset(index)
    material = unreal.load_asset(ASSETS + '/M_WallNumber_%02d' % (index+1))
    if material is None:
        raise RuntimeError('Import number materials first')
    actors = fleet._actors()
    # Square projection volumes sit on the inside wall faces and floor.
    # Roll compensates the decal texture UV orientation so numerals read upright.
    for side, pos, rot, size in (
            ('Rear', (-350+dx, dy, 440), (0, 0, 90), (8, 120, 120)),
            ('Back', (440+dx, dy, 440), (0, 180, 90), (8, 120, 120)),
            ('Floor', (220+dx, 200+dy, 2), (-90, 0, 90), (8, 100, 100))):
        name = 'MakerFleet_T%02d__WallNumber_%s' % (index, side)
        actor = actors.get(name)
        if actor is None:
            actor = fleet._subsystem().spawn_actor_from_class(unreal.DecalActor, unreal.Vector(*pos))
            actor.set_actor_label(name)
            actor.set_folder_path('MakerFleet/T%02d/WallNumbers' % index)
            actor.set_editor_property('tags', [unreal.Name(fleet.FLEET_TAG)] + ([unreal.Name(fleet.table_tag(index))] if index else []))
        if not isinstance(actor, unreal.DecalActor):
            raise ValueError('Wall-number name belongs to another actor')
        actor.modify()
        actor.set_editor_property('sprite_scale', 0.0)
        actor.set_actor_location_and_rotation(unreal.Vector(*pos), unreal.Rotator(pitch=rot[0], yaw=rot[1], roll=rot[2]), False, True)
        component = actor.get_component_by_class(unreal.DecalComponent)
        component.modify()
        component.set_editor_property('decal_size', unreal.Vector(*size))
        component.set_decal_material(material)
        component.set_editor_property('fade_screen_size', .001)
        for icon in actor.get_components_by_class(unreal.BillboardComponent) + actor.get_components_by_class(unreal.ArrowComponent):
            icon.modify()
            icon.set_visibility(False)
    return {'table': index, 'painted_number': index+1, 'wall_decals': 2, 'floor_decals': 1}
