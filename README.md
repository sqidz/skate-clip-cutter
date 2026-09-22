# skate-clip-cutter

A simple tool that processes raw footage into separated clips based on a person being detected. Intended to help clip raw skate footage.

I leave the phone recording, get the files onto the PC, and pick which ones to run. It writes out smaller clips for the stretches a person is in frame. I take those into DaVinci. Tripod only. Handheld has been messy.

Download: https://github.com/sqidz/skate-clip-cutter/releases/latest

Unzip it and run Skate Clip Cutter.bat. Python is in the zip. If Windows blocks the folder, right-click it, Properties, Unblock.

Check for updates opens the releases page. You download the new zip yourself.

https://buymeacoffee.com/sqidz

From this repo, instead of the zip:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python session_ui.py
```

FFmpeg needs to be installed too. AGPL-3.0. NOTICE.txt is the other programs that come in the zip.
