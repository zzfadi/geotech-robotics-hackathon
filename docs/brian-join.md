# Join TheHumanTouch from Windows

Prerequisites: Unreal Engine 5.8.3 (CL 58210709), Git, uv, Visual Studio C++ build tools, and authorized GeoTech VPN access to Luna2 at 100.84.28.59. Use the private Agent Chat code supplied by the host; never commit it.

Close any other Unreal Editor after saving work. Run in PowerShell:

```powershell
git clone https://github.com/zzfadi/geotech-robotics-hackathon.git
cd geotech-robotics-hackathon/Unreal/TheHumanTouch
$project = (Get-Location).Path
$engine = 'C:/Program Files/Epic Games/UE_5.8/Engine'
foreach ($plugin in 'CoPresenceFab', 'HumanTouchPose') {
    $package = Join-Path $env:TEMP "TheHumanTouch-$plugin"
    & "$engine/Build/BatchFiles/RunUAT.bat" BuildPlugin "-Plugin=$project/Plugins/$plugin/$plugin.uplugin" "-Package=$package" -HostPlatforms=Win64 -TargetPlatforms=Win64
    if ($LASTEXITCODE -ne 0) { throw "Failed to build $plugin" }
    Copy-Item "$package/Binaries" "$project/Plugins/$plugin/" -Recurse -Force
}
New-Item -ItemType Directory -Force Saved/CoPresenceLogs | Out-Null
$code = Read-Host 'Agent Chat code from host'
[IO.File]::WriteAllText("$project/Saved/CoPresenceLogs/hub_code", $code.Trim())
[IO.File]::WriteAllText("$project/Saved/CoPresenceLogs/host_address", '100.84.28.59')
$vpnIP = Read-Host 'Your computer VPN IPv4 address'
uv run --script Tools/join_human_touch.py --local-address $vpnIP --name Brian
```

The launcher opens one editor and connects to server/session **TheHumanTouch**, UDP port **9040**, chat port **8791**. It refuses to open a second editor. Agents and Chat should appear on the right. Chat with the existing session agent; use its Call button for voice. Brian does not need his own AI credential to call the host's agent. This does not start a separate Brian agent runner.

For an existing checkout, save and close Unreal, inspect Git status and pull main with `git pull --ff-only origin main`. Rebuild the native plugins when their source changes. Install **Templates and Feature Packs** in the UE 5.8.3 installation for the default Manny and Quinn performer meshes.

Check the Multi-User Browser shows TheHumanTouch and both the other participants and the live agents appear. Host-agent setup is ongoing until verified by the host. A visible tab alone does not prove a working voice call.
