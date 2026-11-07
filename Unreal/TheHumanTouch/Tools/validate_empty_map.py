"""Run with UE 5.8.3 -run=pythonscript -script=<absolute path>."""
import json
from pathlib import Path
import unreal

project = Path(unreal.Paths.project_dir()).resolve()
assert project.name == 'TheHumanTouch', 'Wrong project'
version = unreal.SystemLibrary.get_engine_version()
assert version.startswith('5.8.3-58210709'), version
world = unreal.EditorLoadingAndSavingUtils.load_map('/Game/Maps/Empty')
assert world, 'Map failed to load'
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
assert not actors, 'Expected zero actors in the baseline map'
report = {'engine': version, 'map': world.get_path_name(), 'actor_count': len(actors)}
output = project / 'Saved' / 'empty-map-validation.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2))
unreal.log('HUMAN_TOUCH_VALIDATED ' + json.dumps(report))
