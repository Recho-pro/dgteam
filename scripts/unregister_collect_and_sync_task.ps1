param(
    [string]$TaskName = "DGTEAM Production Auto Sync"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot\..
. .\scripts\Set-Utf8Console.ps1

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task: $TaskName"
} else {
    Write-Host "Scheduled task not found: $TaskName"
}

