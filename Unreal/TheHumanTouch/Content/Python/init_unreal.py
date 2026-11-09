"""Apply station presentation when the editor or a PIE/SIE world opens."""
from pathlib import Path
import sys

import unreal

if not any(arg.lower().startswith("-run=") for arg in unreal.SystemLibrary.get_command_line().split()):
    sys.path.insert(0,str(Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))/'Tools'))
    import station_presentation
    station_presentation.install()
