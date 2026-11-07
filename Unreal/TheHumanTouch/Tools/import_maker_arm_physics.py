"""Import dedicated collision meshes and cook convex hulls in scoped CoPresence tasks.
No simulation is enabled; end_task shares assets. Reports stay under Saved.
"""
import json
from pathlib import Path
import unreal
FOLDER='/Game/_Agents/Denys_Mac_Codex/MakerArm/Physics'


def source_data():
    source=Path(unreal.Paths.project_dir())/'Saved/CoPresenceImports/MakerArmPhysics'
    return source,json.loads((source/'physics.json').read_text())


def import_collision_meshes():
    source,spec=source_data()
    tasks=[]
    for name,body in spec['bodies'].items():
        filename=source/(name+'.obj')
        if not filename.is_file(): raise ValueError('Missing '+str(filename))
        path=FOLDER+'/'+body['collision_mesh']
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            raise ValueError('Asset exists; review before reimporting: '+path)
        task=unreal.AssetImportTask()
        task.filename=str(filename)
        task.destination_path=FOLDER
        task.destination_name=body['collision_mesh']
        task.automated=True
        task.save=False
        task.replace_existing=False
        options=unreal.FbxImportUI()
        options.import_mesh=True
        options.import_as_skeletal=False
        options.import_materials=False
        options.import_textures=False
        options.automated_import_should_detect_type=False
        options.mesh_type_to_import=unreal.FBXImportType.FBXIT_STATIC_MESH
        options.static_mesh_import_data.combine_meshes=True
        options.static_mesh_import_data.convert_scene=False
        options.static_mesh_import_data.convert_scene_unit=False
        options.static_mesh_import_data.auto_generate_collision=False
        task.options=options
        tasks.append(task)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
    result={t.destination_name:list(t.imported_object_paths) for t in tasks}
    if any(not paths for paths in result.values()): raise RuntimeError('Import failed: '+str(result))
    return result


def decompose_link(name,hull_count=32,max_hull_verts=32,hull_precision=500000):
    source,spec=source_data()
    if name not in spec['bodies']: raise ValueError('Unknown link')
    mesh=unreal.load_asset(FOLDER+'/'+spec['bodies'][name]['collision_mesh'])
    if not isinstance(mesh,unreal.StaticMesh): raise ValueError('Collision mesh is not imported')
    subsystem=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    mesh.modify()
    if not subsystem.set_convex_decomposition_collisions(mesh,hull_count,max_hull_verts,hull_precision):
        raise RuntimeError('Convex decomposition failed for '+name)
    actual=subsystem.get_convex_collision_count(mesh)
    if actual < 1: raise RuntimeError('No hulls generated for '+name)
    result={'link':name,'asset':mesh.get_path_name(),'hulls':actual,
            'requested_hulls':hull_count,'max_hull_verts':max_hull_verts,
            'precision':hull_precision,'urdf_sha256':spec['urdf_sha256'],
            'status':'generated; contact fit not yet validated'}
    (source/(name+'-collision-report.json')).write_text(json.dumps(result,indent=2)+'\n')
    return result


def export_hulls():
    """Export Unreal's reflected structs; VertexData has no Python accessor in 5.8."""
    source,spec=source_data()
    all_hulls={}
    for name,body in spec['bodies'].items():
        mesh=unreal.load_asset(FOLDER+'/'+body['collision_mesh'])
        aggregate=mesh.get_editor_property('body_setup').get_editor_property('agg_geom')
        all_hulls[name]=[h.export_text() for h in aggregate.get_editor_property('convex_elems')]
    (source/'collision-hulls-text.json').write_text(json.dumps(all_hulls))
    return {name:len(hulls) for name,hulls in all_hulls.items()}


def apply_part_hulls(name, hull_budget=128):
    """Install pre-decomposed CAD-part hulls without joining disconnected solids."""
    import math
    source,spec=source_data()
    body=spec['bodies'][name]
    derived=json.loads((source/(name+'-part-hulls.json')).read_text())
    if derived['urdf_sha256']!=spec['urdf_sha256'] or derived['coordinates']!='UE link-local cm':
        raise ValueError('Collision provenance or coordinate mismatch')
    if not derived['hulls']: raise ValueError('Missing hulls')
    if len(derived['hulls']) > hull_budget:
        raise ValueError('Experimental decomposition exceeds the caller hull budget; benchmark before increasing it')
    buffers=unreal.GeometryScriptSimpleMeshBuffers()
    vertices=[];triangles=[]
    for hull in derived['hulls']:
        if len(hull['vertices'])<4 or not all(math.isfinite(x) for p in hull['vertices'] for x in p):
            raise ValueError('Invalid convex hull')
        offset=len(vertices)
        vertices.extend(unreal.Vector(*p) for p in hull['vertices'])
        for f in hull['faces']:
            if len(f)!=3 or any(i<0 or i>=len(hull['vertices']) for i in f):
                raise ValueError('Invalid hull triangle')
            triangles.append(unreal.IntVector(*(i+offset for i in f)))
    buffers.vertices=vertices;buffers.triangles=triangles
    dynamic=unreal.DynamicMesh()
    unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dynamic,buffers)
    mesh=unreal.load_asset(FOLDER+'/'+body['collision_mesh'])
    if not isinstance(mesh,unreal.StaticMesh): raise ValueError('Missing collision asset')
    options=unreal.GeometryScriptCollisionFromMeshOptions()
    options.method=unreal.GeometryScriptCollisionGenerationMethod.CONVEX_HULLS
    options.max_convex_hulls_per_mesh=1
    options.max_shape_count=0
    options.simplify_hulls=False
    options.auto_detect_boxes=False
    options.auto_detect_spheres=False
    options.auto_detect_capsules=False
    options.remove_fully_contained_shapes=False
    options.min_thickness=0
    options.emit_transaction=True
    mesh.modify()
    unreal.GeometryScript_Collision.set_static_mesh_collision_from_mesh(dynamic,mesh,options)
    count=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).get_convex_collision_count(mesh)
    if count!=len(derived['hulls']):
        raise RuntimeError('Hull count changed during import: '+str(count))
    result={'link':name,'hulls':count,'method':'independent CAD parts, CoACD 1.0.14',
            'threshold_m':derived['threshold_m'],'status':'requires exported-hull coverage and contact checks'}
    (source/(name+'-collision-report.json')).write_text(json.dumps(result,indent=2)+'\n')
    return result
