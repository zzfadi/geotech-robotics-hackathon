"""Move one existing station into its grid cell inside a scoped CoPresence task.

Call migrate_table once per transaction while simulations are idle. Source CAD,
robot rotations, scales, and table-local coordinates must remain unchanged.
The fleet layout's offset() must use maker_arm_grid.offset before future runs.
"""
import unreal
import maker_arm_grid as grid
import maker_arm_fleet as fleet


def _xyz(v):
    return (v.x, v.y, v.z)


def number_sticker(index, local=False):
    """A flush tabletop number; stable internal T00..T10 become visible 1..11."""
    fleet._check_idle()
    d = grid.offset(index)
    actors = fleet._actors()
    subsystem = fleet._subsystem()
    prefix = 'MakerFleet_T%02d__' % index
    for suffix in ('Label_Front', 'Label_Back'):
        old = actors.get(prefix + suffix)
        if old is not None:
            subsystem.destroy_actor(old)
    tags = [unreal.Name(fleet.FLEET_TAG), unreal.Name('MakerTableNumber')]
    if local:
        tags.append(unreal.Name(fleet._local_tag()))
    if index:
        tags.append(unreal.Name(fleet.table_tag(index)))
    for suffix, kind, height in (('NumberSticker', unreal.StaticMeshActor, 75.06),
                                 ('NumberText', unreal.TextRenderActor, 75.13)):
        name = prefix + suffix
        a = actors.get(name)
        if a is None:
            a = subsystem.spawn_actor_from_class(kind, unreal.Vector(70+d[0], -70+d[1], height))
            a.set_actor_label(name)
            a.set_folder_path('MakerFleet/T%02d' % index)
            a.set_editor_property('tags', tags)
        a.modify()
        a.set_actor_location(unreal.Vector(70+d[0], -70+d[1], height), False, True)
        if suffix == 'NumberSticker':
            c = a.static_mesh_component
            c.set_static_mesh(unreal.load_asset('/Engine/BasicShapes/Cube.Cube'))
            c.set_material(0, unreal.load_asset('/Engine/BasicShapes/BasicShapeMaterial'))
            c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            c.set_cast_shadow(False)
            a.set_actor_scale3d(unreal.Vector(.20, .20, .001))
        else:
            a.set_editor_property('sprite_scale', 0.0)
            a.set_actor_rotation(unreal.Rotator(pitch=90, yaw=0, roll=0), False)
            c = a.text_render
            c.set_text(str(index+1))
            c.set_font(unreal.load_asset('/Engine/EngineFonts/RobotoDistanceField'))
            c.set_world_size(13)
            c.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
            c.set_vertical_alignment(unreal.VerticalTextAligment.EVRTA_TEXT_CENTER)
            c.set_text_render_color(unreal.Color(0, 0, 0, 255))
            c.set_cast_shadow(False)
            for icon in a.get_components_by_class(unreal.BillboardComponent):
                icon.set_visibility(False)
    return {'table_id': index, 'sticker': str(index+1)}


def brighten_global():
    """Shared ambient fill plus daylight, with existing table lights preserved."""
    fleet._check_idle()
    actors = fleet._actors()
    ambient = actors.get('MakerLighting_Ambient')
    if ambient is None:
        ambient = fleet._subsystem().spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 600))
        ambient.set_actor_label('MakerLighting_Ambient')
    ambient.modify()
    component = ambient.light_component
    component.modify()
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_editor_property('source_type', unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP)
    component.set_cubemap(unreal.load_asset('/Engine/MapTemplates/Sky/DaylightAmbientCubemap'))
    component.set_intensity(2.5)
    component.set_editor_property('lower_hemisphere_is_black', False)
    sun = actors.get('MakerLighting_GlobalDaylight')
    if sun is None:
        sun = fleet._subsystem().spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1000))
        sun.set_actor_label('MakerLighting_GlobalDaylight')
        sun.set_folder_path('MakerStation/Environment')
    sun.modify()
    sun.set_actor_rotation(unreal.Rotator(pitch=-60, yaw=-35, roll=0), False)
    light = sun.light_component
    light.modify()
    light.set_mobility(unreal.ComponentMobility.MOVABLE)
    light.set_intensity(1800.0)
    light.set_editor_property('light_source_angle', 3.0)
    light.set_editor_property('atmosphere_sun_light', True)
    light.set_cast_shadows(False)
    if not any(isinstance(a, unreal.SkyAtmosphere) for a in actors.values()):
        sky = fleet._subsystem().spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
        sky.set_actor_label('MakerLighting_DaylightSky')
        sky.set_folder_path('MakerStation/Environment')
    return {'ambient_intensity': component.intensity, 'daylight_lux': light.intensity}


def enable_global_illumination():
    """Daylight with live screen-space GI and room fill at overview distances."""
    result = brighten_global()
    actors = fleet._actors()
    changed = 0
    for actor in actors.values():
        name = actor.get_actor_label()
        if not name.startswith(('MakerLighting_', 'MakerEnvironment_', 'MakerFleet_')):
            continue
        if isinstance(actor, unreal.Light):
            component = actor.light_component
            actor.modify()
            component.modify()
            component.set_mobility(unreal.ComponentMobility.MOVABLE)
            # Keep inexpensive point fills local when the global rig is reapplied.
            is_point_fill = name.split('__', 1)[-1] == 'MakerLighting_LocalPointFill'
            component.set_editor_property('max_draw_distance', 1800.0 if is_point_fill else 0.0)
            component.set_editor_property('max_distance_fade_range', 300.0 if is_point_fill else 0.0)
            changed += 1
        if isinstance(actor, unreal.PostProcessVolume):
            actor.modify()
            settings = actor.settings
            settings.set_editor_property('override_dynamic_global_illumination_method', False)
            actor.set_editor_property('settings', settings)
    unreal.SystemLibrary.execute_console_command(None, 'r.DynamicGlobalIlluminationMethod 2')
    result.update({'movable_lights': changed,
                   'gi_method': unreal.SystemLibrary.get_console_variable_int_value('r.DynamicGlobalIlluminationMethod'),
                   'mesh_distance_fields': unreal.SystemLibrary.get_console_variable_int_value('r.GenerateMeshDistanceFields')})
    return result


def simplify_daylight():
    """Sun/sky plus two practical ceiling fixtures per room, stable exposure."""
    result = enable_global_illumination()
    removed = []
    extra = ('MakerLighting_SoftKey', 'MakerLighting_SoftFill', 'MakerLighting_BackFill',
             'MakerEnvironment_WindowFill', 'MakerEnvironment_RoomBounce')
    for name, actor in fleet._actors().items():
        short = name.split('__', 1)[-1]
        if short in extra and isinstance(actor, unreal.Light):
            removed.append(name)
            fleet._subsystem().destroy_actor(actor)
        elif short in ('MakerEnvironment_CeilingLeft', 'MakerEnvironment_CeilingRight'):
            actor.modify()
            actor.light_component.modify()
            actor.light_component.set_intensity(1800.0)
        elif isinstance(actor, unreal.PostProcessVolume) and name == 'MakerLighting_Exposure':
            actor.modify()
            settings = actor.settings
            settings.set_editor_property('override_auto_exposure_bias', True)
            settings.set_editor_property('auto_exposure_bias', -.5)
            actor.set_editor_property('settings', settings)
    result.update({'removed_fill_lights': len(removed), 'exposure_bias_ev': -.5})
    return result


def set_exposure(bias=-.5):
    """Set the shared fixed-exposure compensation without touching light power."""
    fleet._check_idle()
    actor = fleet._actors().get('MakerLighting_Exposure')
    if not isinstance(actor, unreal.PostProcessVolume):
        raise RuntimeError('Missing shared exposure volume')
    actor.modify()
    settings = actor.settings
    settings.set_editor_property('override_auto_exposure_bias', True)
    settings.set_editor_property('auto_exposure_bias', float(bias))
    actor.set_editor_property('settings', settings)
    return {'exposure_bias_ev': float(bias)}


def add_room_fill(index):
    """One inexpensive, range-limited point fill per room; global sun remains key."""
    fleet._check_idle()
    d = grid.offset(index)
    prefix = '' if index == 0 else fleet.label(index, '')
    name = prefix+'MakerLighting_LocalPointFill'
    actors = fleet._actors()
    light = actors.get(name)
    if light is None:
        light = fleet._subsystem().spawn_actor_from_class(unreal.PointLight, unreal.Vector(45+d[0], d[1], 230))
        light.set_actor_label(name)
        light.set_folder_path('MakerFleet/T%02d' % index if index else 'MakerStation/Environment')
        if index:
            light.set_editor_property('tags', [unreal.Name(fleet.FLEET_TAG), unreal.Name(fleet.table_tag(index))])
    light.modify()
    c = light.light_component
    c.modify()
    c.set_mobility(unreal.ComponentMobility.MOVABLE)
    c.set_editor_property('intensity_units', unreal.LightUnits.LUMENS)
    c.set_intensity(700.0)
    c.set_cast_shadows(False)
    c.set_editor_property('attenuation_radius', 500.0)
    c.set_editor_property('source_radius', 30.0)
    c.set_editor_property('max_draw_distance', 1800.0)
    c.set_editor_property('max_distance_fade_range', 300.0)
    c.set_editor_property('use_temperature', True)
    c.set_temperature(5000.0)
    return {'table': index, 'point_fill_lumens': c.intensity, 'casts_shadows': False,
            'range_cm': 500, 'draw_distance_cm': 1800}


def migrate_table(index):
    grid.offset(index)  # validate before any edits
    fleet._check_idle()
    all_actors = fleet._actors()
    prefix = '' if index == 0 else fleet.label(index, '')
    floor_name = prefix + 'MakerEnvironment_Floor'
    floor = all_actors.get(floor_name)
    if floor is None:
        raise RuntimeError('Missing floor: ' + floor_name)
    if index:
        actors = {a.get_actor_label(): a for a in fleet.fleet_actors(index)}
    else:
        actors = fleet.sources()
        for name, actor in all_actors.items():
            if name.startswith('MakerEnvironment_') or name.startswith('MakerFleet_T00__Label_'):
                actors[name] = actor
    before = {name: (_xyz(a.get_actor_location()), _xyz(a.get_actor_scale3d()))
              for name, a in actors.items() if 'MakerArm_' in name or 'MakerStation_' in name}
    delta = grid.translation(index, _xyz(floor.get_actor_location()))
    moved = 0
    if max(abs(v) for v in delta) > .001:
        # Attached cameras travel with their parent; move only room roots.
        for actor in actors.values():
            parent = actor.get_attach_parent_actor()
            if parent is not None and parent.get_actor_label() in actors:
                continue
            actor.modify()
            p = _xyz(actor.get_actor_location())
            actor.set_actor_location(unreal.Vector(*(p[i]+delta[i] for i in range(3))), False, True)
            moved += 1
    subsystem = fleet._subsystem()
    roof = actors.get(prefix + 'MakerEnvironment_Roof')
    if roof is not None:
        subsystem.destroy_actor(roof)
    cube = unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
    plaster = unreal.load_asset('/Game/_Agents/Denys_Mac_Codex/Environment/M_HallPlaster')
    surfaces = 0
    for name, spec in grid.shell(index).items():
        label = prefix + 'MakerEnvironment_' + name
        actor = actors.get(label)
        if actor is None:
            actor = subsystem.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*spec['location']))
            actor.set_actor_label(label)
            actor.set_folder_path('MakerFleet/T%02d' % index if index else 'MakerStation/Environment')
            if index:
                actor.set_editor_property('tags', [unreal.Name(fleet.FLEET_TAG), unreal.Name(fleet.table_tag(index))])
            actor.static_mesh_component.set_static_mesh(cube)
            if plaster:
                actor.static_mesh_component.set_material(0, plaster)
            actors[label] = actor
        if not isinstance(actor, unreal.StaticMeshActor):
            raise RuntimeError('Room shell is not a static mesh: ' + label)
        if (max(abs(a-b) for a,b in zip(_xyz(actor.get_actor_location()), spec['location'])) > .001
                or max(abs(a-b) for a,b in zip(_xyz(actor.get_actor_scale3d()), spec['scale'])) > .001):
            actor.modify()
            actor.set_actor_location(unreal.Vector(*spec['location']), False, True)
            actor.set_actor_scale3d(unreal.Vector(*spec['scale']))
            surfaces += 1
    for name, (p, scale) in before.items():
        a = actors[name]
        if max(abs(v-(p[i]+delta[i])) for i,v in enumerate(_xyz(a.get_actor_location()))) > .01:
            raise RuntimeError('Table-local pose changed during grid migration: ' + name)
        if max(abs(x-y) for x,y in zip(scale, _xyz(a.get_actor_scale3d()))) > .00001:
            raise RuntimeError('Protected mesh scale changed: ' + name)
    return {'table': index, 'offset_cm': grid.offset(index), 'translated_roots': moved,
            'shell_updates': surfaces, 'protected_actors_verified': len(before)}


def soften_room_lighting():
    """Even inexpensive room light: no direct-light shadow maps, mild screen AO."""
    result = brighten_global()
    count = 0
    for actor in fleet._actors().values():
        name = actor.get_actor_label()
        if not name.startswith(('MakerLighting_', 'MakerEnvironment_', 'MakerFleet_')):
            continue
        if isinstance(actor, (unreal.RectLight, unreal.PointLight)):
            actor.modify()
            actor.light_component.modify()
            actor.light_component.set_cast_shadows(False)
            count += 1
    volume = fleet._actors()['MakerLighting_Exposure']
    volume.modify()
    settings = volume.settings
    settings.set_editor_property('override_ambient_occlusion_intensity', True)
    settings.set_editor_property('ambient_occlusion_intensity', .5)
    settings.set_editor_property('override_ambient_occlusion_radius', True)
    settings.set_editor_property('ambient_occlusion_radius', 50.)
    volume.set_editor_property('settings', settings)
    result.update({'unshadowed_local_lights': count, 'ambient_occlusion_intensity': .5})
    return result
