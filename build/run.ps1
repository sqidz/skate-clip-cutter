# Runs person_clipper.py with the repo .venv.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Error "Missing .venv in the repo folder."
  exit 1
}
if ($args.Count -eq 0) {
  Write-Host "Usage: .\build\run.ps1 --input `"path\to\file.mp4`" --out `"path\to\out`""
  exit 2
}
& $py (Join-Path $Root "app\person_clipper.py") @args
exit $LASTEXITCODE
