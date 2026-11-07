$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$policy = Get-Content "$project/Config/CheckpointPolicy.json" | ConvertFrom-Json
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -File `"$PSScriptRoot/run_checkpoint.ps1`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($policy.interval_minutes) -RepetitionInterval (New-TimeSpan -Minutes $policy.interval_minutes)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable
Register-ScheduledTask -TaskName 'TheHumanTouch-Checkpoint' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Get-ScheduledTaskInfo 'TheHumanTouch-Checkpoint' | Select-Object NextRunTime,LastTaskResult
