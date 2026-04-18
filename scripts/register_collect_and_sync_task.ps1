param(
    [string]$TaskName = "DGTEAM Production Auto Sync",
    [string[]]$Times = @("09:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00")
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot\..
. .\scripts\Set-Utf8Console.ps1

$scriptPath = (Resolve-Path ".\scripts\run_collect_and_sync.ps1").Path
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$triggers = @()
foreach ($timeText in $Times) {
    $normalized = $timeText.Trim()
    if (-not $normalized) {
        continue
    }
    $triggers += New-ScheduledTaskTrigger -Daily -At $normalized
}

if ($triggers.Count -eq 0) {
    throw "At least one schedule time is required."
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Run DGTEAM local collection and sync it to the production publish API." `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
Write-Host "Times: $($Times -join ', ')"

