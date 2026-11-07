"""Run import_meshes() through a scoped CoPresence task; end_task shares assets."""
def import_meshes():
    import unreal
    from pathlib import Path
    root=Path(unreal.Paths.project_dir())
    folder='/Game/_Agents/Denys_Mac_Codex/YAM'
    tasks=[]
    for source in sorted((root/'Saved/CoPresenceImports/YAM').glob('*.obj')):
        task=unreal.AssetImportTask()
        task.filename=str(source)
        task.destination_path=folder
        task.destination_name='SM_YAM_'+source.stem
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
    return [(t.destination_name,list(t.imported_object_paths)) for t in tasks]
