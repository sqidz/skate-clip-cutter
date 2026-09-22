# Skate Clip Cutter 0.1.0

Local Windows clip cutter: raw video(s) in -> YOLO person-in-frame -> folder of ordinary clip files out.

**Product name:** Skate Clip Cutter  
**Tip jar:** https://buymeacoffee.com/sqidz

## V1 product lock (still true for 0.1)

- **Tripod / rock-steady only.** Handheld continuous roll is out of claims.
- **No Split / review UI**, no phone app, no DRM, no watch-folder, **no silent auto-updater**.
- Detection: **A.2 YOLO person-in-frame** (`person_clipper.py`). Motion-only (A.1) is dead.

## How people download it

GitHub Releases zip. Unzip, double-click **Skate Clip Cutter.bat**. Check for updates opens this repo's Releases page and compares tags. It does not auto-patch.

Build that zip on a Windows machine:

```powershell
cd path\to\skate-clip-cutter
.\build_portable.ps1
```

Output: `dist\SkateClipCutter-0.1.0-windows-x64.zip`

## Dev launch (this repo)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python session_ui.py
```

The UI is stdlib tkinter. The worker needs ultralytics/torch (bundled Python in the zip, or `.venv` when you run from source). FFmpeg: bundled `vendor\ffmpeg` in the zip, or Gyan.FFmpeg on PATH.

Headless:

```powershell
python session_ui.py --inputs "path\to\a.mp4" --landing "$env:USERPROFILE\Videos" --session-name "Rathmines PM"
```

A.2 CLI:

```powershell
.\run_a2.ps1 --input "path\to\raw.mp4" --out "path\to\out_folder"
```

Defaults stay **1.5 fps**, person keep-windows, pre-roll 3.0 / post-roll 1.25. Do not retune for a ship bite.

## Empty / no activity

If nothing passes person keep-windows, the tool writes `NO_ACTIVITY.txt` (session UI: `{stem}_NO_ACTIVITY.txt`) and exits 0 for that file.

## License

AGPL-3.0. The worker uses Ultralytics YOLO. The Releases zip also includes FFmpeg and YOLO weights. See `NOTICE.txt`.
