"""Run once with UnrealEditor -run=pythonscript -script=<this file>."""
import unreal
from pathlib import Path
import json
project = Path(unreal.Paths.project_dir()).resolve()
assert project.name == 'TheHumanTouch', 'Refusing to edit another project'
asset = '/Game/Maps/Empty'
assert not unreal.EditorAssetLibrary.does_asset_exist(asset), 'Map already exists; refusing overwrite'
world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
assert world
assert unreal.EditorLoadingAndSavingUtils.save_map(world, asset)
assert unreal.EditorLoadingAndSavingUtils.load_map(asset)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
report = {'engine': unreal.SystemLibrary.get_engine_version(), 'map': asset, 'actors': [{'name': a.get_name(), 'class': a.get_class().get_name()} for a in actors]}
(project/'Saved').mkdir(exist_ok=True)
(project/'Saved'/'empty-map-validation.json').write_text(json.dumps(report, indent=2))
unreal.log('HUMAN_TOUCH_EMPTY_MAP_VERIFIED ' + json.dumps(report))
