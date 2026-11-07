"""Explicit host lifecycle for the user-requested primary map rename.
Run request() through CoPresence's host Python tool, then inspect status.
The native helper must be built and loaded first. Never bypass its save guard.
"""
import unreal
from copresence import checkpoints, ui_widgets

TARGET = '/Game/Maps/TheHumanTouch'
status = {'state': 'idle'}

def current_map():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_outermost().get_name()

def request():
    before = checkpoints.inspect()
    if before['agent'] != 'Denys Luna2 Claude' or before['session'] != 'TheHumanTouch':
        raise RuntimeError('Only the TheHumanTouch Luna2 host may rename the shared map')
    if before['task'] or before['animation'] or before['maps'] or before['content']:
        raise RuntimeError('Finish and save current edits before renaming')
    if current_map() not in ('/Game/Maps/Empty', TARGET):
        raise RuntimeError('Unexpected current map; refusing rename')
    if status['state'] == 'renaming':
        raise RuntimeError('Rename already running')
    status.update(state='renaming')
    ui_widgets.after(1.0, _rename)
    return dict(status)

def _rename():
    try:
        before = checkpoints.inspect()
        if before['task'] or before['animation'] or before['maps'] or before['content']:
            raise RuntimeError('Editing resumed; rename cancelled')
        if current_map() != TARGET:
            if not unreal.CoPresenceFabLibrary.rename_current_map_for_checkpoint(TARGET):
                raise RuntimeError('Unreal AssetTools refused the map rename')
        if current_map() != TARGET:
            raise RuntimeError('Renamed map is not the active world')
        status.update(state='renamed', map=TARGET)
    except Exception as error:
        status.update(state='error', error=str(error))
