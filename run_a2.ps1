# A.2 launcher — always uses project .venv (ultralytics lives there)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Error "Missing .venv. Create it or reinstall deps first."
  exit 1
}
if ($args.Count -eq 0) {
  Write-Host "Usage: .\run_a2.ps1 --input `"RAW ...\file.mp4`" --out `"out\a2-run`""
  Write-Host "Example:"
  Write-Host "  .\run_a2.ps1 --input `"RAW Rathmines session\PXL_20260914_224343618.mp4`" --out `"out\a2-check`""
  exit 2
}
& $py (Join-Path $PSScriptRoot "person_clipper.py") @args
exit $LASTEXITCODE
