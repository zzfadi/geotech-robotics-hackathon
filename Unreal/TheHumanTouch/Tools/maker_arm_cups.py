"""Hollow drinking-cup fixture for HoldMyBeer; dimensions/dynamics are assumptions."""
import math
from pathlib import Path

ASSETS='/Game/_Agents/Denys_Mac_Codex/MakerArm/Cups'
LABEL='MakerStation_Prop_Cup'


def prepare(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    n=64;vertices=[];faces=[]
    # Outer bottom/rim; inner bottom/rim. Centred origin, centimetres.
    for radius,z in ((3.,-6.),(4.,6.),(2.8,-5.7),(3.8,6.)):
        vertices.extend((radius*math.cos(i*2*math.pi/n),radius*math.sin(i*2*math.pi/n),z) for i in range(n))
    for i in range(n):
        j=(i+1)%n
        faces.extend(((i,j,n+j,n+i),(2*n+i,3*n+i,3*n+j,2*n+j),(n+i,n+j,3*n+j,3*n+i)))
    faces.extend((tuple(reversed(range(n))),tuple(range(2*n,3*n))))
    out=['o DrinkingCup']+['v %.8f %.8f %.8f'%v for v in vertices]+['f '+' '.join(str(i+1) for i in f) for f in faces]
    path=directory/'DrinkingCup.obj';path.write_text('\n'.join(out)+'\n');return path


def import_asset():
    import unreal,yam_station
    folder=ASSETS;path=folder+'/SM_DrinkingCup'
    mesh=unreal.load_asset(path)
    if mesh is None:
        source=prepare(Path(unreal.Paths.project_saved_dir())/'CoPresenceImports/DrinkingCup')
        task=unreal.AssetImportTask();task.filename=str(source);task.destination_path=folder;task.destination_name='SM_DrinkingCup';task.automated=True;task.save=False;task.replace_existing=False
        opts=unreal.FbxImportUI();opts.import_mesh=True;opts.import_as_skeletal=False;opts.import_materials=False;opts.import_textures=False;opts.automated_import_should_detect_type=False;opts.mesh_type_to_import=unreal.FBXImportType.FBXIT_STATIC_MESH
        opts.static_mesh_import_data.combine_meshes=True;opts.static_mesh_import_data.convert_scene=False;opts.static_mesh_import_data.convert_scene_unit=False;opts.static_mesh_import_data.auto_generate_collision=False;task.options=opts
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task]);mesh=unreal.load_asset(path)
        if mesh is None:raise RuntimeError('Cup import failed')
        if not unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).set_convex_decomposition_collisions(mesh,32,16,1000000):raise RuntimeError('Cup compound collision generation failed')
    subsystem=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    mesh.modify();settings=subsystem.get_lod_build_settings(mesh,0)
    settings.recompute_normals=True;settings.recompute_tangents=True
    subsystem.set_lod_build_settings(mesh,0,settings)
    old=yam_station.ASSETS
    try:
        yam_station.ASSETS=ASSETS;mat=yam_station.material('Cup_Ivory',(.82,.76,.63))
    finally:yam_station.ASSETS=old
    mesh.modify();mesh.set_material(0,mat)
    unreal.EditorAssetLibrary.set_metadata_tag(mesh,'Fidelity','Assumed cup:12cm high,8cm rim diameter,6cm base diameter,0.2cm wall,0.3cm base. Approximate compound collision; no liquid or calibrated hardware dynamics.')
    return mesh


def cup_layout(index, seed=20261107):
    """Shared deterministic count/position contract with the web planner."""
    import random
    if type(index) is not int or not 0 <= index <= 8:raise ValueError('Expected table index0..8')
    rng=random.Random(f'{seed}:{index}');out=[]
    for ordinal,y in enumerate((-32,32,-16,16,0)[:4+index%2]):
        out.append({'label':LABEL+('' if ordinal==0 else '_%02d'%(ordinal+1)),
                    'location_cm':[rng.uniform(48,52 if ordinal==4 else 62),y+rng.uniform(-1.5,1.5),81],
                    'rotation_deg':[0,0,0], 'yaw_deg':0})
    return out


def replace_all(seed=20261107):
    import unreal,maker_arm_grid
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:raise RuntimeError('Replace cups only while simulation idle')
    mesh=import_asset();sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors=list(sub.get_all_level_actors());removed=[];created=[]
    for a in actors:
        short=a.get_actor_label().split('__')[-1]
        if short.startswith(('MakerStation_Approximate_Block_','MakerStation_Approximate_Rod_')) or short in ('MakerStation_UnverifiedControlProxy','Dreamlabs_YAM_ControlComputerProxy'):
            removed.append(a.get_actor_label());a.modify();sub.destroy_actor(a)
    existing={a.get_actor_label():a for a in sub.get_all_level_actors()}
    layouts={}
    for i in range(9):
        prefix='' if i==0 else 'MakerFleet_T%02d__'%i;d=maker_arm_grid.offset(i);layouts[i]=cup_layout(i,seed)
        for spec in layouts[i]:
            name=prefix+spec['label'];p=spec['location_cm'];location=unreal.Vector(p[0]+d[0],p[1]+d[1],p[2])
            a=existing.get(name)
            if a is None:a=sub.spawn_actor_from_class(unreal.StaticMeshActor,location);a.set_actor_label(name);created.append(name)
            a.modify();a.set_folder_path('MakerStation/Cups' if i==0 else 'MakerFleet/T%02d/Cups'%i)
            a.set_editor_property('tags',[unreal.Name('HoldMyBeerCup')]+([unreal.Name('MakerFleet'),unreal.Name('MakerFleetT%02d'%i)] if i else []))
            a.set_actor_location_and_rotation(location,unreal.Rotator(),False,True);a.set_actor_scale3d(unreal.Vector(1,1,1))
            c=a.static_mesh_component;c.modify();c.set_mobility(unreal.ComponentMobility.MOVABLE);c.set_static_mesh(mesh);c.set_collision_profile_name('PhysicsActor');c.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS);c.set_simulate_physics(False)
    return {'removed_obsolete_actors':removed,'created_cups':created,'cup_count':sum(map(len,layouts.values())), 'seed':seed,'layouts':layouts,'convex_hulls':unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).get_convex_collision_count(mesh),'physics':'Free dynamics enabled only in runtime; uncalibrated .05kg/.8 friction assumptions; no liquid'}


def mark_blue_target():
    """Give Cup_03 a unique visual cue on each Dream table; no physical edits."""
    import unreal,yam_station
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Change target material only while simulation idle')
    actors={a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
    labels=[('' if i==0 else 'MakerFleet_T%02d__'%i)+LABEL+'_03' for i in range(9)]
    missing=[name for name in labels if name not in actors]
    if missing:raise RuntimeError('Missing target cups: '+repr(missing))
    old=yam_station.ASSETS
    try:
        yam_station.ASSETS=ASSETS
        material=yam_station.material('Cup_Blue',(.02,.22,.8))
    finally:yam_station.ASSETS=old
    for name in labels:
        actor=actors[name];component=actor.static_mesh_component
        actor.modify();component.modify();component.set_material(0,material)
    return {'target_label':LABEL+'_03','material':material.get_path_name(),'cups':labels,
            'scope':'Visual cue only; geometry, placement, collisions and dynamics unchanged'}


def center_existing(seed=20261107):
    """Reposition only the existing40 cups; no asset/arm/physics changes."""
    import unreal,maker_arm_grid
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Move cups only while simulation idle')
    actors={a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
    changes=[]
    for index in range(9):
        offset=maker_arm_grid.offset(index);prefix='' if index==0 else 'MakerFleet_T%02d__'%index
        for spec in cup_layout(index,seed):
            name=prefix+spec['label']
            if name not in actors:raise RuntimeError('Missing existing cup '+name)
            changes.append((actors[name],spec,offset))
    for actor,spec,offset in changes:
        actor.modify();actor.root_component.modify();p=spec['location_cm']
        actor.set_actor_location_and_rotation(unreal.Vector(*(p[i]+offset[i] for i in range(3))),unreal.Rotator(),False,True)
    return {'seed':seed,'cups_moved':len(changes),'bounds_cm':{'x':[48,62],'y':[-33.5,33.5],'center_slot_x':[48,52]},'table10_untouched':True}
