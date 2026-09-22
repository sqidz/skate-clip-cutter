# Builds the Windows zip. Usage: .\build\build_portable.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Version = "0.1.0"
$PyTag = "20260901"
$PyAsset = "cpython-3.13.15+20260901-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
$PyUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$PyTag/$PyAsset"

$Dist = Join-Path $Root "dist"
$StageName = "Skate Clip Cutter"
$Stage = Join-Path $Dist $StageName
$Program = Join-Path $Stage "program"
$Cache = Join-Path $Root ".build-cache"
$ZipName = "SkateClipCutter-$Version-windows-x64.zip"
$ZipPath = Join-Path $Dist $ZipName

New-Item -ItemType Directory -Force -Path $Cache | Out-Null
if (Test-Path $Stage) {
  Remove-Item -LiteralPath $Stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Program | Out-Null

$tarball = Join-Path $Cache $PyAsset
if (-not (Test-Path $tarball)) {
  Write-Host "Downloading portable CPython..."
  Invoke-WebRequest -Uri $PyUrl -OutFile $tarball -UseBasicParsing
}

Write-Host "Extracting CPython..."
tar -xf $tarball -C $Program
$py = Join-Path $Program "python\python.exe"
if (-not (Test-Path $py)) {
  throw "Expected $py after extract. Archive layout changed?"
}

Write-Host "Installing pip + CPU torch + worker deps..."
& $py -m ensurepip --upgrade
& $py -m pip install --upgrade pip
& $py -m pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cpu
& $py -m pip install -r (Join-Path $Root "app\requirements.txt")

Write-Host "Copying app files..."
foreach ($name in @("session_ui.py", "person_clipper.py", "clipper.py", "paths.py", "ship.py")) {
  Copy-Item -LiteralPath (Join-Path $Root "app\$name") -Destination $Program -Force
}
Copy-Item -LiteralPath (Join-Path $Root "LICENSE") -Destination $Program -Force
Copy-Item -LiteralPath (Join-Path $Root "NOTICE.txt") -Destination $Program -Force
Copy-Item -LiteralPath (Join-Path $Root "app\assets") -Destination (Join-Path $Program "assets") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Skate Clip Cutter.bat") -Destination $Stage -Force

$models = Join-Path $Program "models"
New-Item -ItemType Directory -Force -Path $models | Out-Null
$srcModel = Join-Path $Root "yolo11n.pt"
$altModel = Join-Path $Root "models\yolo11n.pt"
if (Test-Path $srcModel) {
  Copy-Item $srcModel (Join-Path $models "yolo11n.pt") -Force
} elseif (Test-Path $altModel) {
  Copy-Item $altModel (Join-Path $models "yolo11n.pt") -Force
} else {
  throw "yolo11n.pt not found. Place it in the repo root or models\."
}

Write-Host "Copying FFmpeg..."
$ffDir = Join-Path $Program "vendor\ffmpeg"
New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
$gyanBin = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -like "Gyan.FFmpeg*" } |
  ForEach-Object { Get-ChildItem $_.FullName -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue } |
  Select-Object -First 1
if (-not $gyanBin) {
  $fromPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
  if ($fromPath) { $gyanBin = Get-Item $fromPath.Source }
}
if (-not $gyanBin) {
  throw "ffmpeg.exe not found. Install Gyan.FFmpeg via winget, then re-run."
}
Copy-Item $gyanBin.FullName (Join-Path $ffDir "ffmpeg.exe") -Force
$probe = Join-Path $gyanBin.DirectoryName "ffprobe.exe"
if (-not (Test-Path $probe)) {
  throw "ffprobe.exe missing next to $($gyanBin.FullName)"
}
Copy-Item $probe (Join-Path $ffDir "ffprobe.exe") -Force

Write-Host "Smoke imports..."
& $py -c "import tkinter, ultralytics, cv2, torch; print('ok')"
if ($LASTEXITCODE -ne 0) { throw "Portable Python smoke failed." }

Write-Host "Zipping $ZipName ..."
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Push-Location $Dist
tar -a -c -f $ZipName $StageName
Pop-Location
if (-not (Test-Path $ZipPath)) { throw "Zip was not created: $ZipPath" }

$item = Get-Item $ZipPath
Write-Host ("Built {0} ({1:N1} MB)" -f $item.FullName, ($item.Length / 1MB))
