$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot\..
. .\scripts\Set-Utf8Console.ps1
$python = Join-Path (Get-Location) ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}
$srcPath = (Resolve-Path ".\src").Path
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
  $env:PYTHONPATH = $srcPath
} else {
  $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
}
& $python -m dgteam.release.upload_cli @args
