#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
station=${1:-}
if [[ -z "$station" ]]; then
    read 'station?Tailscale IPv4 address of the Live Link Hub station: '
fi
# Validate before interpolating the address into the editor Python command.
station=$(python3 -c 'import ipaddress,sys; print(ipaddress.IPv4Address(sys.argv[1]))' "$station")
uv run --script Unreal/TheHumanTouch/Tools/copresence_mcp.py exec-python 8041 \
"import unreal,sys; from pathlib import Path; sys.path.insert(0,str(Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))/'Tools')); import quest2_performer; quest2_performer.start('$station'); result=quest2_performer.status()"
