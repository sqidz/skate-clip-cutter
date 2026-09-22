# Skate Clip Cutter

Cuts raw skate footage into separate clips when you're in frame. Leave the camera running, pick the files, name the session.

[Download the Windows zip](https://github.com/sqidz/skate-clip-cutter/releases/latest). Unzip it and double-click `Skate Clip Cutter.bat`. Python is already in there. If Windows blocks the folder, right-click it, open Properties, and choose Unblock.

Use a tripod. A moving camera cuts in the wrong places.

[Buy me a coffee](https://buymeacoffee.com/sqidz) if it saves you time in the editor.

Check for updates in the app opens this Releases page. It does not install the update for you.

To run from this repo: Python 3.10+, FFmpeg on PATH, then

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python session_ui.py
```

AGPL-3.0. The zip also includes Python, PyTorch, Ultralytics, OpenCV, and FFmpeg. See `NOTICE.txt`.
