"""Deploy debug_server.py and web/index.html to Luna2's running Dream debugger.

Backs up each live file (*.bak-<stamp>), uploads gzip+base64 through the
Luna2 remote helper (pinned SSH host key; login fetched privately from Agents
Vault), verifies SHA-256, then restarts only the TheHumanTouch-DreamDebugger
scheduled task. Refuses while a main-table or fleet command is active.

  uv run --python 3.11 --with paramiko DreamSim/deploy_debugger_luna2.py
"""
import argparse
import base64
import gzip
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = Path('/Users/geo_dennis/.codex/skills/luna2-unreal-meet/scripts/luna2_remote.py')
APP = 'F:/GeoVerse/geotech-robotics-hackathon/Unreal/TheHumanTouch/Saved/DreamDebugger/app'
URL = 'http://100.84.28.59:8786'


def remote(script, helper, expect=None):
    """Run PowerShell on Luna2. The SSH command line is limited to ~8 KB, so keep scripts small."""
    done = subprocess.run(['uv', 'run', '-q', '--python', '3.11', '--with', 'paramiko', str(helper)],
                          input=script, capture_output=True, text=True, timeout=240)
    text = '\n'.join(line for line in (done.stdout + '\n' + done.stderr).splitlines()
                     if line.strip() and not line.startswith(('#<', '<Objs')))
    if done.returncode or (expect and expect not in text):
        raise RuntimeError('Luna2 step failed:\n' + text[-1500:])
    return text


def busy():
    status = json.load(urllib.request.urlopen(URL + '/api/status', timeout=5))
    if (status.get('command') or {}).get('status') in ('queued', 'running'):
        return 'main table command is active'
    fleet = json.load(urllib.request.urlopen(URL + '/api/fleet', timeout=5))
    command = fleet.get('command') or {}
    stale = command.get('status') == 'queued' and not command.get('claimed_by') and not fleet.get('online')
    if command.get('status') in ('queued', 'running') and not stale:
        return 'replica-table run is active'
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--helper', type=Path, default=HELPER)
    args = p.parse_args()
    reason = busy()
    if reason:
        raise SystemExit('Not deploying: ' + reason)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    targets = ('interaction_store.py', 'table10_store.py', 'prompt_routing.py', 'fleet_planning.py', 'debug_server.py', 'web/index.html')
    for target in targets:
        local = HERE/target
        data = local.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        payload = base64.b64encode(gzip.compress(data, 9)).decode()
        staging = f'{APP}/{target}.b64'
        chunks = [payload[i:i+2000] for i in range(0, len(payload), 2000)]
        for n, chunk in enumerate(chunks):
            verb = 'Set-Content' if n == 0 else 'Add-Content'
            remote(f'{verb} -NoNewline -Path "{staging}" -Value \'{chunk}\'; "chunk {n}"', args.helper, expect='chunk %d' % n)
        print(remote(f'''$ErrorActionPreference="Stop"
$t="{APP}/{target}"
$b=[Convert]::FromBase64String((Get-Content -Raw "$t.b64"))
$in=New-Object IO.MemoryStream(,$b); $gz=New-Object IO.Compression.GzipStream($in,[IO.Compression.CompressionMode]::Decompress); $o=New-Object IO.MemoryStream; $gz.CopyTo($o)
[IO.File]::WriteAllBytes("$t.new",$o.ToArray()); Remove-Item "$t.b64"
if ((Get-FileHash "$t.new" -Algorithm SHA256).Hash.ToLower() -ne "{digest}") {{ Remove-Item "$t.new"; throw "hash mismatch" }}
"uploaded {target} staging verified"''', args.helper, expect='uploaded ' + target).splitlines()[-1])
    # Recheck after all uploads: staging must not interrupt commands accepted meanwhile.
    reason = busy()
    if reason:
        raise SystemExit('Not activating staged files: ' + reason)
    target_list = ','.join("'"+target+"'" for target in targets)
    activation = f'''$ErrorActionPreference="Stop"
$targets=@({target_list})
foreach ($name in $targets) {{
 $t="{APP}/$name"
 if (Test-Path $t) {{ Copy-Item $t "$t.bak-{stamp}" -Force }}
 else {{ New-Item "$t.absent-{stamp}" -ItemType File -Force | Out-Null }}
}}
Stop-ScheduledTask -TaskName "TheHumanTouch-DreamDebugger"; Start-Sleep 2
foreach ($name in $targets) {{ $t="{APP}/$name"; Move-Item "$t.new" $t -Force }}
Start-ScheduledTask -TaskName "TheHumanTouch-DreamDebugger"; Start-Sleep 4
"debugger activated {stamp}"'''
    try:
        print(remote(activation, args.helper, expect='debugger activated').splitlines()[-1])
        fleet = json.load(urllib.request.urlopen(URL + '/api/fleet', timeout=10))
        if 'expected_tables' not in fleet:
            raise RuntimeError('New fleet API was not loaded')
        print('fleet API verified; ready sessions:', sum(t.get('ready', False) for t in fleet['tables']))
    except Exception:
        rollback = f'''$ErrorActionPreference="Stop"
Stop-ScheduledTask -TaskName "TheHumanTouch-DreamDebugger"; Start-Sleep 2
foreach ($name in @({target_list})) {{
 $t="{APP}/$name"
 if (Test-Path "$t.bak-{stamp}") {{ Copy-Item "$t.bak-{stamp}" $t -Force }}
 elseif (Test-Path "$t.absent-{stamp}") {{ Remove-Item $t -ErrorAction SilentlyContinue }}
}}
Start-ScheduledTask -TaskName "TheHumanTouch-DreamDebugger"
"debugger rollback {stamp}"'''
        remote(rollback, args.helper, expect='debugger rollback')
        raise



if __name__ == '__main__':
    main()
