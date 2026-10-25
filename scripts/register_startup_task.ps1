# ==============================================================================
# JARVIS — Register Auto-Start on Windows User Logon
# Creates a Scheduled Task that starts JARVIS automatically whenever you log in
# after turning your laptop back on!
# ==============================================================================

$TaskName = "JARVIS_Personal_AI_Assistant"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$StartScript = Join-Path $ProjectRoot "scripts\start_jarvis.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$StartScript`"" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Starts JARVIS WhatsApp AI Assistant and runs overdue reminder recovery on login." `
    -Force

Write-Host "[OK] Registered Windows Scheduled Task '$TaskName' to auto-start on logon (including on battery power)!" -ForegroundColor Green
Write-Host "To remove auto-start later, run: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Gray
