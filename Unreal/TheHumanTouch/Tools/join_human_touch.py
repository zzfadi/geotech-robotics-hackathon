# /// script
# requires-python = ">=3.11"
# dependencies = ["psutil>=6"]
# ///
"""Launch the single shared human/agent editor; refuse duplicates across projects."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import psutil

PROJECT = Path(__file__).resolve().parents[1]

def editors():
    found = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        name = (proc.info['name'] or '').lower()
        if name in {'unrealeditor', 'unrealeditor.exe', 'unrealeditor-cmd.exe'}:
            found.append(proc.info)
    return found

def arguments(engine, local_address, name):
    windows = platform.system() == 'Windows'
    binary = engine / ('Binaries/Win64/UnrealEditor.exe' if windows else 'Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor')
    return [str(binary), str(PROJECT/'TheHumanTouch.uproject'),
            '-CONCERTSERVER=TheHumanTouch', '-CONCERTSESSION=TheHumanTouch',
            f'-CONCERTDISPLAYNAME={name}', '-CONCERTAUTOCONNECT', '-CONCERTRETRYAUTOCONNECTONERROR',
            f'-UDPMESSAGING_TRANSPORT_UNICAST={local_address}:0',
            '-UDPMESSAGING_TRANSPORT_STATIC=100.84.28.59:9040',
            '-COPRESENCEHUB=http://100.84.28.59:8791', '-COPRESENCEAGENT', '-HUMANTOUCHSHAREDEDITOR',
            '-ini:Engine:[/Script/ConcertClient.ConcertClientConfig]:ClientSettings=(DesktopAvatarActorClass="/CoPresence/BP_AgentGhost.BP_AgentGhost_C",VRAvatarActorClass="/ConcertSyncClient/VRPresence.VRPresence_C")',
            '-ini:EditorPerProjectUserSettings:[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]:bAutoStartServer=True',
            '-ini:EditorPerProjectUserSettings:[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]:ServerPortNumber=8041',
            '-ini:EditorSettings:[/Script/UnrealEd.EditorPerformanceSettings]:bThrottleCPUWhenNotForeground=False']

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-address', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--engine', type=Path, default=Path('C:/Program Files/Epic Games/UE_5.8/Engine') if platform.system()=='Windows' else Path('/Users/Shared/Epic Games/UE_5.8/Engine'))
    args = parser.parse_args()
    running = editors()
    if running:
        raise SystemExit('An Unreal Editor is already running (PIDs: '+', '.join(str(p['pid']) for p in running)+'). Reuse it, or preserve work and close it before relaunching. No additional editor started.')
    import json
    build=json.loads((args.engine/'Build/Build.version').read_text())
    if (build['MajorVersion'],build['MinorVersion'],build['PatchVersion'],build['Changelist'])!=(5,8,3,58210709):
        raise SystemExit('Requires UE 5.8.3 CL 58210709')
    command=arguments(args.engine,args.local_address,args.name)
    logdir=PROJECT/'Saved/CoPresenceLogs';logdir.mkdir(parents=True,exist_ok=True)
    # No credentials are passed in command arguments. The hub code is provisioned separately.
    with (logdir/'shared-editor-launch.log').open('ab') as log:
        kwargs={'creationflags':subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
        proc=subprocess.Popen(command,cwd=PROJECT,stdout=log,stderr=log,**kwargs)
    print(f'Started shared editor PID {proc.pid}; verify session connection in its log.')

if __name__=='__main__':main()
