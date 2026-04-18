$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot\..
. .\scripts\Set-Utf8Console.ps1
$python = Join-Path (Get-Location) ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}
& $python .\scripts\prune_storage.py @args
