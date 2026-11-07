# Join TheHumanTouch from Windows

Prerequisites: Unreal Engine 5.8.3 (CL 58210709), Git, uv, Visual Studio C++ build tools, and authorized GeoTech VPN access to Luna2 at 100.84.28.59. Use the private Agent Chat code supplied by the host; never commit it.

Close any other Unreal Editor after saving work. Run in PowerShell:

```powershell
git clone https://github.com/zzfadi/geotech-robotics-hackathon.git
cd geotech-robotics-hackathon/Unreal/TheHumanTouch
$project = (Get-Location).Path
$engine = 'C:/Program Files/Epic Games/UE_5.8/Engine'
$package = Join-Path $env:TEMP 'TheHumanTouch-CoPresenceFab'
& "$engine/Build/BatchFiles/RunUAT.bat" BuildPlugin "-Plugin=$project/Plugins/CoPresenceFab/CoPresenceFab.uplugin" "-Package=$package" -HostPlatforms=Win64 -TargetPlatforms=Win64
if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed; stop here.' }
Copy-Item "$package/Binaries" "$project/Plugins/CoPresenceFab/" -Recurse -Force
New-Item -ItemType Directory -Force Saved/CoPresenceLogs | Out-Null
$code = Read-Host 'Agent Chat code from host'
[IO.File]::WriteAllText("$project/Saved/CoPresenceLogs/hub_code", $code.Trim())
[IO.File]::WriteAllText("$project/Saved/CoPresenceLogs/host_address", '100.84.28.59')
$vpnIP = Read-Host 'Your computer VPN IPv4 address'
uv run --script Tools/join_human_touch.py --local-address $vpnIP --name Brian
```

The launcher opens one editor and connects to server/session **TheHumanTouch**, UDP port **9040**, chat port **8791**. It refuses to open a second editor. Agents and Chat should appear on the right. Chat with the existing session agent; use its Call button for voice. Brian does not need his own AI credential to call the host's agent. This does not start a separate Brian agent runner.

If Brian already cloned the repo, save/close Unreal, inspect git status, then pull main with `git pull --ff-only origin main`; do not clone over an existing directory or overwrite local changes. Rebuild the native plugin if its source changed.

Check the Multi-User Browser shows TheHumanTouch and both the other participants and the live agents appear. Host-agent setup is ongoing until verified by the host. A visible tab alone does not prove a working voice call.
