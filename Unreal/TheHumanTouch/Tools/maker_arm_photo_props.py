"""Approximate foam props from user's Nov 7 photo; dimensions unmeasured.
The block and through-rod are one rigid mesh, avoiding overlapping free bodies.
"""
import math
from pathlib import Path


def prepare(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    lines=['mtllib photo_props.mtl','o BlockWithRod'];vertices=[]
    def vertex(p):
        vertices.append(p);lines.append('v '+' '.join(str(v) for v in p));return len(vertices)
    corners=[(-3,-3,-3),(3,-3,-3),(3,3,-3),(-3,3,-3),(-3,-3,3),(3,-3,3),(3,3,3),(-3,3,3)]
    for p in corners:vertex(p)
    lines+=['g OrangeBlock','usemtl OrangeBlock']
    for face in ((1,4,3,2),(5,6,7,8),(1,2,6,5),(2,3,7,6),(3,4,8,7),(4,1,5,8)):
        lines.append('f '+' '.join(str(v) for v in face))
    # Full 14 cm rod through the center, diameter 2 cm, along Y.
    rings=[]
    for y in (-7,7):rings.append([vertex((math.cos(i*2*math.pi/32),y,math.sin(i*2*math.pi/32))) for i in range(32)])
    lines+=['g YellowRod','usemtl YellowRod']
    for i in range(32):
        j=(i+1)%32;lines.append('f '+' '.join(map(str,(rings[0][i],rings[1][i],rings[1][j],rings[0][j]))))
    lines.append('f '+' '.join(map(str,rings[0])))
    lines.append('f '+' '.join(map(str,reversed(rings[1]))))
    (directory/'BlockWithRod.obj').write_text('\n'.join(lines)+'\n')
    (directory/'photo_props.mtl').write_text('newmtl OrangeBlock\nKd 1 .25 .01\nnewmtl YellowRod\nKd 1 .75 .01\n')
    return directory/'BlockWithRod.obj'


def add():
    import unreal,yam_station
    from maker_arm_station import ASSETS
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Add photo props only while simulation is idle')
    folder=ASSETS+'/Props';path=folder+'/SM_BlockWithRod_Physics'
    mesh=unreal.load_asset(path)
    if mesh is None:
        source=prepare(Path(unreal.Paths.project_saved_dir())/'CoPresenceImports/PhotoProps')
        task=unreal.AssetImportTask();task.filename=str(source);task.destination_path=folder;task.destination_name='SM_BlockWithRod_Physics';task.automated=True;task.save=False;task.replace_existing=False
        options=unreal.FbxImportUI();options.import_mesh=True;options.import_as_skeletal=False;options.import_materials=False;options.import_textures=False;options.automated_import_should_detect_type=False;options.mesh_type_to_import=unreal.FBXImportType.FBXIT_STATIC_MESH
        options.static_mesh_import_data.combine_meshes=True;options.static_mesh_import_data.convert_scene=False;options.static_mesh_import_data.convert_scene_unit=False;options.static_mesh_import_data.auto_generate_collision=False;task.options=options
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task]);mesh=unreal.load_asset(path)
        if mesh is None:raise RuntimeError('Photo prop import failed')
        if not unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).set_convex_decomposition_collisions(mesh,8,16,100000):raise RuntimeError('Compound collision failed')
        unreal.EditorAssetLibrary.set_metadata_tag(mesh,'Fidelity','Approximate photo dimensions; block 6cm, rod diameter 2cm length 14cm; uncalibrated foam/contact')
    subsystem=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    if subsystem.get_convex_collision_count(mesh)==0:raise RuntimeError('Compound has no physics hulls')
    orange=unreal.load_asset(ASSETS+'/M_Station_Orange');yellow=unreal.load_asset(ASSETS+'/M_Station_Yellow')
    if not orange or not yellow:raise RuntimeError('Missing existing foam colors')
    mesh.modify()
    slots=mesh.get_editor_property('static_materials')
    for i,slot in enumerate(slots):mesh.set_material(i,yellow if 'Yellow' in str(slot.get_editor_property('imported_material_slot_name')) else orange)
    existing=yam_station.actors();result=[]
    for label,asset,pos,scale,mat,rotation in [
        ('MakerStation_Approximate_Block_OrangeRodAssembly',mesh,(60,30,78),(1,1,1),None,None),
        ('MakerStation_Approximate_Block_OrangeCylinder',unreal.load_asset('/Engine/BasicShapes/Cylinder'),(67,50,76.5),(.03,.03,.16),orange,unreal.Rotator(90,90,0))]:
        if label in existing:
            actor=existing[label];actor.modify();actor.static_mesh_component.modify();actor.static_mesh_component.set_static_mesh(asset)
            result.append(label);continue
        actor=yam_station.mesh_actor(label,asset,pos,scale,mat)
        if rotation:actor.set_actor_rotation(rotation,True)
        actor.static_mesh_component.set_collision_profile_name('PhysicsActor')
        actor.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
        result.append(label)
    return {'actors':result,'compound_material_slots':[str(s.get_editor_property('imported_material_slot_name')) for s in slots],'dimensions':'block 6cm; through rod 2x14cm; separate cylinder 3x16cm','dynamics':'single rigid compound; runtime enables free Chaos simulation; uncalibrated mass/friction'}
