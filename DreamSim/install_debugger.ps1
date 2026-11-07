param([string]$Bind='100.84.28.59',[int]$Port=8786)
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot -Parent
$runtime=Join-Path $repo 'Unreal/TheHumanTouch/Saved/DreamDebugger'
New-Item -ItemType Directory -Force "$runtime/app/web" | Out-Null
Copy-Item "$PSScriptRoot/debug_server.py" "$runtime/app/debug_server.py" -Force
Copy-Item "$PSScriptRoot/web/index.html" "$runtime/app/web/index.html" -Force
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port already listens; preserve any active command before restarting the existing debugger." }
$python='C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/ThirdParty/Python3/Win64/python.exe'
$action=New-ScheduledTaskAction -Execute $python -Argument "`"$runtime/app/debug_server.py`" --bind $Bind --port $Port --data `"$runtime`"" -WorkingDirectory "$runtime/app"
$principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'TheHumanTouch-DreamDebugger' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'TheHumanTouch-DreamDebugger'
Write-Output "Started Dream debugger at http://${Bind}:$Port/"
