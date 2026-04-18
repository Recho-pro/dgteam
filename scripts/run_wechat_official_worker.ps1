param(
  [switch]$RunOnce
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot "Set-Utf8Console.ps1")

Push-Location $ProjectRoot
try {
  $python = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
  if (-not (Test-Path $python)) {
    $python = "python"
  }
  $existingPythonPath = $env:PYTHONPATH
  $srcPath = Join-Path $ProjectRoot "src"
  if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
    $env:PYTHONPATH = $srcPath
  }
  else {
    $env:PYTHONPATH = "$srcPath;$existingPythonPath"
  }

  $args = @("-m", "dgteam.integrations.wechat_official.worker_cli")
  if ($RunOnce) {
    $args += "--run-once"
  }
  & $python @args
}
finally {
  Pop-Location
}
