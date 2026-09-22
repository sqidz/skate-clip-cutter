#!/usr/bin/env python3
"""Skate Clip Cutter - session UI.

Multi-select raw videos -> A.2 YOLO person_clipper -> named session folder
under a chosen output folder. Stdlib tkinter + subprocess.
Worker is bundled Python (portable zip) or the project .venv (dev).
Detect path unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clipper import find_tool
from paths import (
    ROOT,
    bundled_model_path,
    bundled_python,
    default_landing_dir,
    dev_venv_python,
    is_portable_layout,
)
from ship import APP_VERSION, GITHUB_REPO, latest_release_api_url, releases_url

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".MP4", ".MOV", ".M4V"}
DEFAULT_LANDING = default_landing_dir()
PERSON_CLIPPER = ROOT / "person_clipper.py"
USER_README = ROOT / "USER_README.md"
ICON_ICO = ROOT / "assets" / "skateboard.ico"
ICON_PNG = ROOT / "assets" / "skateboard.png"

ENGINE_LABEL = "A.2 YOLO person"
UPDATE_URL = releases_url()
TIP_URL = "https://buymeacoffee.com/sqidz"

HELP_COPY = (
    "Skate Clip Cutter turns rolling session footage into usable clips "
    "by cutting out the stretches you are not in frame.\n\n"
    "How to run\n"
    "1. Unzip the download. If Windows blocks it: right-click the zip or "
    "folder -> Properties -> Unblock -> Apply.\n"
    "2. Double-click Skate Clip Cutter.bat\n"
    "3. Select videos (.mp4 / .mov / .m4v)\n"
    "4. Enter a Session name (becomes the folder name)\n"
    "5. Choose an Output folder (parent for that session folder)\n"
    "6. Click Start. Progress shows file, clips found, and a rough "
    "total ETA for the whole selection\n"
    "7. When finished, use Open file location to jump to the session folder\n\n"
    "Tripod / rock-steady only. Handheld rolling footage is out of claims.\n\n"
    "Updates: Check for updates opens GitHub Releases. This app does not "
    "auto-install patches. Download the newer zip and unzip it next to this folder.\n\n"
    "Tip jar: https://buymeacoffee.com/sqidz"
)


WORKER_MISSING_TIP = (
    "The clip engine is missing.\n\n"
    "If you downloaded a zip: re-download Skate Clip Cutter from GitHub "
    "Releases and run Skate Clip Cutter.bat from the unzipped folder.\n\n"
    "If you are running from source: create the project .venv with "
    "ultralytics, then retry."
)

_ETA_RE = re.compile(r"eta~(\d+)s", re.IGNORECASE)
_SEGMENTS_RE = re.compile(r"^segments:\s*(\d+)\s*$", re.IGNORECASE)
_EXPORT_CLIP_RE = re.compile(r"export\s+[\d.]+\s*%\s+clip\s+(\d+)/(\d+)", re.IGNORECASE)
_YOLO_PCT_RE = re.compile(r"yolo\s+([\d.]+)\s*%", re.IGNORECASE)


def resolve_worker_python() -> Path | None:
    """Bundled portable Python, else project .venv, else this interpreter if it has ultralytics."""
    bundled = bundled_python()
    if bundled is not None:
        return bundled
    venv = dev_venv_python()
    if venv is not None:
        return venv
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        sibling = exe.with_name("python.exe")
        if sibling.is_file():
            exe = sibling
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        return None
    return exe if exe.is_file() else None


def preflight_errors() -> list[str]:
    """Start-time checks for a stranger's machine. Detection knobs unchanged."""
    errors: list[str] = []
    if resolve_worker_python() is None:
        errors.append(WORKER_MISSING_TIP)
    if find_tool("ffmpeg") is None or find_tool("ffprobe") is None:
        errors.append(
            "FFmpeg is missing.\n\n"
            "The Releases zip should include vendor\\ffmpeg. "
            "Re-download that zip, or install Gyan.FFmpeg via winget."
        )
    if is_portable_layout() and bundled_model_path() is None:
        errors.append(
            "Detection weights (models\\yolo11n.pt) are missing.\n\n"
            "Re-download the zip from GitHub Releases."
        )
    return errors


def sanitize_session_name(name: str) -> str:
    """Folder-safe session name (Windows-friendly)."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or "Session"


def unique_session_dir(landing: Path, session_name: str) -> Path:
    """landing / Name, or Name 1, Name 2, ... if the folder already exists."""
    base = sanitize_session_name(session_name)
    candidate = landing / base
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = landing / f"{base} {n}"
        if not candidate.exists():
            return candidate
        n += 1


def default_session_name() -> str:
    return datetime.now().strftime("Session %Y-%m-%d %H%M")


def _safe_stem(path: Path) -> str:
    return re.sub(r"[^\w.\-]+", "_", path.stem)[:80] or "clip"


def _preserve_sidecar(out_dir: Path, stem: str, name: str) -> Path | None:
    """If clipper left a shared sidecar, rename it to a per-source name."""
    src = out_dir / name
    if not src.is_file():
        return None
    dst = out_dir / f"{stem}_{name}"
    if dst.exists():
        dst.unlink()
    src.rename(dst)
    return dst


def merge_manifests(out_dir: Path, sources: list[Path]) -> Path | None:
    """Build a session-level clips.json from per-file manifests."""
    files: list[dict] = []
    for src in sources:
        stem = _safe_stem(src)
        man = out_dir / f"{stem}_clips.json"
        if not man.is_file():
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        files.append(
            {
                "input": data.get("input", str(src)),
                "mode": data.get("mode", "a2_yolo_person"),
                "duration_s": data.get("duration_s"),
                "pre_roll_s": data.get("pre_roll_s"),
                "post_roll_s": data.get("post_roll_s"),
                "segment_count": data.get("segment_count", 0),
                "segments": data.get("segments") or [],
                "manifest": man.name,
            }
        )
    if not files:
        return None
    total = sum(int(f.get("segment_count") or 0) for f in files)
    merged = {
        "session_out": str(out_dir),
        "engine": "a2_yolo_person",
        "source_count": len(sources),
        "clip_count": total,
        "files": files,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    path = out_dir / "clips.json"
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return path


def run_one_file(
    src: Path,
    out_dir: Path,
    *,
    worker_python: Path,
    pre_roll: float | None = None,
    post_roll: float | None = None,
    fps: float | None = None,
    reencode: bool = False,
    on_line=None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Run person_clipper.py (A.2) on one file into out_dir. Returns (exit_code, summary)."""
    if not PERSON_CLIPPER.is_file():
        return 2, f"person_clipper.py not found at {PERSON_CLIPPER}"
    if not worker_python.is_file():
        return 2, "Clip engine Python is missing."

    cmd = [
        str(worker_python),
        str(PERSON_CLIPPER),
        "--input",
        str(src),
        "--out",
        str(out_dir),
    ]
    model = bundled_model_path()
    if model is not None:
        cmd.extend(["--model", str(model)])
    if pre_roll is not None:
        cmd.extend(["--pre-roll", str(pre_roll)])
    if post_roll is not None:
        cmd.extend(["--post-roll", str(post_roll)])
    if fps is not None:
        cmd.extend(["--fps", str(fps)])
    if reencode:
        cmd.append("--reencode")

    if on_line:
        on_line(f"$ {' '.join(cmd)}")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(ROOT),
        creationflags=creationflags,
    )
    assert proc.stdout is not None
    last = ""
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return 130, "cancelled"
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                last = line.rstrip()
                if on_line:
                    on_line(last)
        code = proc.wait()
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass
        return 1, f"error: {exc}"

    stem = _safe_stem(src)
    _preserve_sidecar(out_dir, stem, "clips.json")
    _preserve_sidecar(out_dir, stem, "NO_ACTIVITY.txt")

    if code == 0:
        return 0, last or "ok"
    return code, last or f"exit {code}"



def which_ffprobe() -> str | None:
    """Locate ffprobe (bundled, PATH, or winget)."""
    return find_tool("ffprobe")


def probe_duration(path: Path, ffprobe: str | None = None) -> float | None:
    """Invisible duration probe for whole-selection ETA. Returns seconds or None."""
    tool = ffprobe or which_ffprobe()
    if not tool:
        return None
    try:
        proc = subprocess.run(
            [
                tool,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        val = float((proc.stdout or "").strip() or 0)
        return val if val > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


# Rough wall-clock seconds per second of video for A.2 YOLO @ ~1.5 fps (CPU-ish).
# Recalibrated from live per-file ETA once the worker reports eta~.
_ETA_SEC_PER_VIDEO_SEC = 1.25


def run_session(
    inputs: list[Path],
    out_dir: Path,
    *,
    pre_roll: float | None = None,
    post_roll: float | None = None,
    fps: float | None = None,
    reencode: bool = False,
    on_status=None,
    on_line=None,
    on_progress=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Process inputs sequentially into one session folder via A.2 person_clipper."""
    worker_python = resolve_worker_python()
    if worker_python is None:
        msg = WORKER_MISSING_TIP.replace("\n", " | ")
        if on_status:
            on_status("Clip engine Python is missing.")
        if on_line:
            on_line(msg)
        return {
            "out_dir": str(out_dir),
            "results": [],
            "ok": 0,
            "fail": 1,
            "cancelled": False,
            "clip_count": 0,
            "merged_manifest": None,
            "elapsed_s": 0.0,
            "error": "missing_venv",
            "engine": "a2_yolo_person",
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    t0 = time.time()
    total = len(inputs)
    clips_found = 0
    file_etas: list[float | None] = [None] * total
    durations: list[float | None] = [None] * total
    sec_per_video = _ETA_SEC_PER_VIDEO_SEC

    if on_line:
        on_line(f"engine: {ENGINE_LABEL}  worker={worker_python}")

    # Invisible pre-pass: probe durations once so we can show a whole-selection ETA.
    if on_status:
        on_status("Probing durations for selection ETA...")
    if on_progress:
        on_progress(
            {
                "file_index": 0,
                "file_total": total,
                "file_name": "",
                "phase": "probing",
                "clips_found": 0,
                "eta_text": "Estimating selection ETA...",
            }
        )
    ffprobe = which_ffprobe()
    for di, src in enumerate(inputs):
        if cancel_event is not None and cancel_event.is_set():
            break
        dur = probe_duration(src, ffprobe)
        durations[di] = dur
        if dur is not None:
            file_etas[di] = max(5.0, dur * sec_per_video)
        if on_line and dur is not None:
            on_line(f"probe: {src.name}  {dur:.1f}s")

    def selection_eta_text(
        *,
        index: int,
        file_eta: float | None = None,
    ) -> str:
        """Sum remaining per-file estimates into one whole-selection ETA."""
        remaining: list[float] = []
        for j in range(max(0, index - 1), total):
            if j == index - 1 and file_eta is not None:
                remaining.append(max(0.0, float(file_eta)))
            elif file_etas[j] is not None:
                remaining.append(max(0.0, float(file_etas[j])))  # type: ignore[arg-type]
            elif durations[j] is not None:
                remaining.append(max(5.0, float(durations[j]) * sec_per_video))  # type: ignore[arg-type]
            else:
                # No duration yet — keep a small placeholder so UI still shows a total.
                remaining.append(30.0)
        if not remaining:
            return ""
        total_left = int(round(sum(remaining)))
        return f"~{total_left}s left (selection)"

    def emit_progress(
        *,
        index: int,
        name: str,
        phase: str = "",
        file_eta: float | None = None,
    ) -> None:
        if not on_progress:
            return
        eta_text = selection_eta_text(index=index, file_eta=file_eta)
        on_progress(
            {
                "file_index": index,
                "file_total": total,
                "file_name": name,
                "phase": phase,
                "clips_found": clips_found,
                "eta_text": eta_text,
            }
        )

    for i, src in enumerate(inputs, 1):
        if cancel_event is not None and cancel_event.is_set():
            if on_status:
                on_status(f"Cancelled before file {i}/{total}")
            break
        if on_status:
            on_status(f"File {i}/{total}: {src.name}")
        if on_line:
            on_line(f"--- [{i}/{total}] {src} ---")
        emit_progress(index=i, name=src.name, phase="starting")

        def make_on_line(file_index: int, file_name: str):
            def _on_line(msg: str) -> None:
                nonlocal clips_found
                if on_line:
                    on_line(msg)
                nonlocal sec_per_video
                m_eta = _ETA_RE.search(msg)
                file_eta = float(m_eta.group(1)) if m_eta else None
                if file_eta is not None:
                    file_etas[file_index - 1] = file_eta
                    # Recalibrate remaining untouched files from live remaining ETA + duration.
                    dur = durations[file_index - 1]
                    m_yolo_pct = _YOLO_PCT_RE.search(msg)
                    if dur and dur > 0 and m_yolo_pct:
                        try:
                            pct = float(m_yolo_pct.group(1))
                        except ValueError:
                            pct = 0.0
                        if 5.0 <= pct < 100.0:
                            # Full-file wall estimate from detect % + remaining eta~.
                            done_frac = pct / 100.0
                            est_full = file_eta / max(1e-3, (1.0 - done_frac))
                            sec_per_video = max(0.2, est_full / dur)
                            for j in range(file_index, total):
                                if durations[j] is not None and file_etas[j] != 0.0:
                                    file_etas[j] = max(
                                        5.0, float(durations[j]) * sec_per_video
                                    )
                m_seg = _SEGMENTS_RE.match(msg.strip())
                if m_seg:
                    clips_found += int(m_seg.group(1))
                phase = ""
                m_yolo = _YOLO_PCT_RE.search(msg)
                m_exp = _EXPORT_CLIP_RE.search(msg)
                if m_yolo:
                    phase = f"detect {m_yolo.group(1)}%"
                elif m_exp:
                    phase = f"export clip {m_exp.group(1)}/{m_exp.group(2)}"
                elif msg.lower().startswith("loading yolo"):
                    phase = "loading model"
                elif msg.lower().startswith("segments:"):
                    phase = "building clips"
                elif msg.lower().startswith("done:"):
                    phase = "file done"
                emit_progress(
                    index=file_index,
                    name=file_name,
                    phase=phase,
                    file_eta=file_eta,
                )

            return _on_line

        code, summary = run_one_file(
            src,
            out_dir,
            worker_python=worker_python,
            pre_roll=pre_roll,
            post_roll=post_roll,
            fps=fps,
            reencode=reencode,
            on_line=make_on_line(i, src.name),
            cancel_event=cancel_event,
        )
        ok = code == 0
        results.append(
            {
                "input": str(src),
                "ok": ok,
                "code": code,
                "summary": summary,
            }
        )
        if on_line:
            tag = "OK" if ok else ("CANCELLED" if code == 130 else "FAIL")
            on_line(f"[{tag}] {src.name}: {summary}")
        # After a file finishes, drop its ETA from the sum.
        file_etas[i - 1] = 0.0
        emit_progress(index=i, name=src.name, phase="file done", file_eta=0.0)

    merged = merge_manifests(out_dir, inputs)
    clip_count = 0
    if merged and merged.is_file():
        try:
            clip_count = int(json.loads(merged.read_text(encoding="utf-8")).get("clip_count") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            clip_count = len(list(out_dir.glob("*_clip*.mp4")))
    else:
        clip_count = len(list(out_dir.glob("*_clip*.mp4")))

    ok_n = sum(1 for r in results if r["ok"])
    fail_n = sum(1 for r in results if not r["ok"] and r["code"] != 130)
    cancelled = any(r["code"] == 130 for r in results)
    elapsed = time.time() - t0
    report = {
        "out_dir": str(out_dir),
        "results": results,
        "ok": ok_n,
        "fail": fail_n,
        "cancelled": cancelled,
        "clip_count": clip_count,
        "merged_manifest": str(merged) if merged else None,
        "elapsed_s": round(elapsed, 1),
        "engine": "a2_yolo_person",
    }
    if on_status:
        if cancelled:
            on_status(
                f"Cancelled. {ok_n} ok, {fail_n} failed, {clip_count} clips -> {out_dir}"
            )
        else:
            on_status(
                f"Done. {ok_n}/{total} ok, {fail_n} failed, "
                f"{clip_count} clips in {elapsed:.0f}s -> {out_dir}"
            )
    if on_progress:
        on_progress(
            {
                "file_index": total,
                "file_total": total,
                "file_name": "",
                "phase": "done",
                "clips_found": clip_count,
                "eta_text": "",
            }
        )
    return report


class SessionApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Skate Clip Cutter v{APP_VERSION}")
        self.geometry("720x520")
        self.minsize(560, 400)
        self._set_icon()

        self.files: list[Path] = []
        self.session_name = tk.StringVar(value=default_session_name())
        self.landing_dir = tk.StringVar(value=str(DEFAULT_LANDING))
        self.status = tk.StringVar(value="Pick raw videos, name the session, then Start.")
        self.progress_line = tk.StringVar(value="")
        self.clips_line = tk.StringVar(value="")
        self.eta_line = tk.StringVar(value="")
        self.spinner_char = tk.StringVar(value="")
        self._spinner_frames = ["|", "/", "-", "\\"]
        self._spinner_i = 0
        self._spinner_job: str | None = None
        self._running = False
        self._details_visible = False

        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._msg_q: queue.Queue[tuple[str, object]] = queue.Queue()
        self._icon_img = None  # keep PhotoImage ref alive

        self._build()
        self.after(100, self._drain_queue)

    def _set_icon(self) -> None:
        try:
            if ICON_ICO.is_file() and sys.platform == "win32":
                self.iconbitmap(default=str(ICON_ICO))
                self.iconbitmap(str(ICON_ICO))
                return
        except Exception:
            pass
        try:
            if ICON_PNG.is_file():
                self._icon_img = tk.PhotoImage(file=str(ICON_PNG))
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        # Files
        row1 = ttk.Frame(frm)
        row1.pack(fill=tk.X, **pad)
        ttk.Button(row1, text="Select videos", command=self._pick_files).pack(side=tk.LEFT)
        ttk.Button(row1, text="Clear list", command=self._clear_files).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.file_label = ttk.Label(row1, text="0 file(s) selected")
        self.file_label.pack(side=tk.LEFT, padx=(12, 0))

        # Session name
        row_name = ttk.Frame(frm)
        row_name.pack(fill=tk.X, **pad)
        ttk.Label(row_name, text="Session name:").pack(side=tk.LEFT)
        ttk.Entry(row_name, textvariable=self.session_name).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )

        # Output folder (parent for session-named subfolder)
        row_land = ttk.Frame(frm)
        row_land.pack(fill=tk.X, **pad)
        ttk.Label(row_land, text="Output folder:").pack(side=tk.LEFT)
        ttk.Entry(row_land, textvariable=self.landing_dir).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8)
        )
        ttk.Button(row_land, text="Browse", command=self._pick_landing).pack(side=tk.LEFT)

        # Actions
        row3 = ttk.Frame(frm)
        row3.pack(fill=tk.X, **pad)
        self.start_btn = ttk.Button(row3, text="Start", command=self._start)
        self.start_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(
            row3, text="Cancel", command=self._cancel_run, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row3, text="Check for updates", command=self._check_updates).pack(
            side=tk.LEFT, padx=(16, 0)
        )
        ttk.Button(row3, text="Buy me a coffee", command=self._open_tip).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(row3, text="Help", command=self._show_help).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # Progress (main status — not the verbose log)
        prog = ttk.Frame(frm)
        prog.pack(fill=tk.X, **pad)
        ttk.Label(prog, textvariable=self.spinner_char, width=2).pack(side=tk.LEFT)
        ttk.Label(prog, textvariable=self.progress_line).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(frm, textvariable=self.clips_line).pack(anchor=tk.W, padx=10)
        ttk.Label(frm, textvariable=self.eta_line).pack(anchor=tk.W, padx=10)
        ttk.Label(frm, textvariable=self.status).pack(anchor=tk.W, **pad)

        # Collapsed Details (verbose log)
        self.details_btn = ttk.Button(
            frm, text="Details ▸", command=self._toggle_details
        )
        self.details_btn.pack(anchor=tk.W, padx=10, pady=(4, 0))
        self.details_frame = ttk.Frame(frm)
        self.log = tk.Text(self.details_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True, padx=0, pady=4)
        # Start collapsed — do not pack details_frame yet.

    def _toggle_details(self) -> None:
        if self._details_visible:
            self.details_frame.pack_forget()
            self.details_btn.configure(text="Details ▸")
            self._details_visible = False
        else:
            self.details_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
            self.details_btn.configure(text="Details ▾")
            self._details_visible = True

    def _append_log(self, line: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _refresh_file_count(self) -> None:
        n = len(self.files)
        self.file_label.configure(text=f"{n} file(s) selected")

    def _pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select raw skate videos",
            filetypes=[
                ("Video", "*.mp4 *.mov *.m4v *.MP4 *.MOV *.M4V"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        added = 0
        existing = {p.resolve() for p in self.files}
        for raw in paths:
            p = Path(raw)
            if p.suffix not in VIDEO_EXTS:
                continue
            if p.resolve() in existing:
                continue
            self.files.append(p)
            existing.add(p.resolve())
            added += 1
        self._refresh_file_count()
        if added:
            self.status.set(f"Added {added} file(s). {len(self.files)} ready.")

    def _clear_files(self) -> None:
        self.files.clear()
        self._refresh_file_count()
        self.status.set("File list cleared.")

    def _pick_landing(self) -> None:
        initial = self.landing_dir.get().strip() or str(DEFAULT_LANDING)
        path = filedialog.askdirectory(
            title="Output folder (parent for session folder)",
            initialdir=initial if Path(initial).is_dir() else str(DEFAULT_LANDING),
        )
        if path:
            self.landing_dir.set(path)

    def _start_spinner(self) -> None:
        self._running = True
        self._tick_spinner()

    def _stop_spinner(self) -> None:
        self._running = False
        if self._spinner_job is not None:
            try:
                self.after_cancel(self._spinner_job)
            except Exception:
                pass
            self._spinner_job = None
        self.spinner_char.set("")

    def _tick_spinner(self) -> None:
        if not self._running:
            return
        self.spinner_char.set(self._spinner_frames[self._spinner_i % 4])
        self._spinner_i += 1
        self._spinner_job = self.after(120, self._tick_spinner)

    def _apply_progress(self, info: dict) -> None:
        idx = info.get("file_index") or 0
        total = info.get("file_total") or 0
        name = info.get("file_name") or ""
        phase = info.get("phase") or ""
        clips = info.get("clips_found")
        eta = info.get("eta_text") or ""
        if name:
            bit = f"{idx}/{total}: {name}"
            if phase:
                bit = f"{bit} — {phase}"
            self.progress_line.set(bit)
        elif info.get("phase") == "done":
            self.progress_line.set("Finished.")
        if clips is not None:
            self.clips_line.set(f"Clips found: {clips}")
        self.eta_line.set(eta)

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        if not self.files:
            messagebox.showwarning(
                "No files", "Select one or more .mp4 / .mov / .m4v files."
            )
            return
        problems = preflight_errors()
        if problems:
            messagebox.showerror("Can't start", "\n\n".join(problems))
            return
        landing = Path(self.landing_dir.get().strip())
        if not landing.parts:
            messagebox.showerror("Output folder", "Choose an output folder.")
            return
        name = self.session_name.get().strip()
        if not name:
            messagebox.showerror("Session name", "Enter a session name.")
            return
        out = unique_session_dir(landing, name)

        self._cancel.clear()
        self.start_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.status.set(f"Starting… session folder: {out.name}")
        self.progress_line.set(f"0/{len(self.files)}")
        self.clips_line.set("Clips found: 0")
        self.eta_line.set("Estimating selection ETA...")
        self._append_log(f"Session out: {out}")
        self._append_log(f"Engine: {ENGINE_LABEL}")
        self._start_spinner()

        inputs = list(self.files)

        def worker() -> None:
            def on_status(msg: str) -> None:
                self._msg_q.put(("status", msg))

            def on_line(msg: str) -> None:
                self._msg_q.put(("log", msg))

            def on_progress(info: dict) -> None:
                self._msg_q.put(("progress", info))

            report = run_session(
                inputs,
                out,
                on_status=on_status,
                on_line=on_line,
                on_progress=on_progress,
                cancel_event=self._cancel,
            )
            self._msg_q.put(("done", report))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _cancel_run(self) -> None:
        self._cancel.set()
        self.status.set("Cancelling…")
        self.cancel_btn.configure(state=tk.DISABLED)

    def _parse_version(self, raw: str) -> tuple[int, ...]:
        text = raw.strip().lstrip("vV")
        parts: list[int] = []
        for chunk in text.split("."):
            digits = ""
            for ch in chunk:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            parts.append(int(digits or 0))
        return tuple(parts) if parts else (0,)

    def _fetch_latest_tag(self) -> tuple[str | None, str | None]:
        """Return (tag, error). tag is None on failure."""
        if not GITHUB_REPO or GITHUB_REPO.startswith("OWNER/"):
            return None, "GitHub repo is not configured yet."
        req = urllib.request.Request(
            latest_release_api_url(),
            headers={
                "User-Agent": f"SkateClipCutter/{APP_VERSION}",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return None, str(exc)
        tag = str(data.get("tag_name") or "").strip()
        return (tag or None), None

    def _check_updates(self) -> None:
        tag, err = self._fetch_latest_tag()
        current = APP_VERSION
        if err and tag is None:
            go = messagebox.askyesno(
                "Check for updates",
                f"You are on {current}.\n\n"
                f"Could not reach GitHub ({err}).\n\n"
                "Open the Releases page in your browser?",
            )
            if go:
                webbrowser.open(UPDATE_URL)
            return
        assert tag is not None
        remote = tag.lstrip("vV")
        try:
            newer = self._parse_version(remote) > self._parse_version(current)
        except Exception:
            newer = remote != current
        if newer:
            go = messagebox.askyesno(
                "Update available",
                f"You are on {current}.\n"
                f"Latest on GitHub is {tag}.\n\n"
                "This app does not auto-install. Open the download page?",
            )
        else:
            go = messagebox.askyesno(
                "You're up to date",
                f"You are on {current}, which matches the latest Release ({tag}).\n\n"
                "Open the Releases page anyway?",
            )
        if go:
            webbrowser.open(UPDATE_URL)

    def _open_tip(self) -> None:
        webbrowser.open(TIP_URL)

    def _show_help(self) -> None:
        """In-app Help popup (not Notepad / os.startfile)."""
        win = tk.Toplevel(self)
        win.title("Help")
        win.transient(self)
        win.resizable(True, True)
        win.geometry("520x360")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
        txt = tk.Text(frm, wrap=tk.WORD, height=16, width=60)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", HELP_COPY)
        txt.configure(state=tk.DISABLED)
        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(
            btns, text="Tip jar", command=lambda: webbrowser.open(TIP_URL)
        ).pack(side=tk.LEFT)
        ttk.Button(btns, text="Close", command=win.destroy).pack(side=tk.RIGHT)

    def _open_file_location(self, folder: str) -> None:
        """Open the session output folder in Explorer (or OS file manager)."""
        p = Path(folder)
        if not p.is_dir():
            messagebox.showwarning(
                "Open file location", f"Folder not found:\n{folder}"
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Open file location", f"Could not open:\n{p}\n\n{exc}"
            )

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msg_q.get_nowait()
                if kind == "status":
                    self.status.set(str(payload))
                elif kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    if isinstance(payload, dict):
                        self._apply_progress(payload)
                elif kind == "done":
                    self._stop_spinner()
                    self.start_btn.configure(state=tk.NORMAL)
                    self.cancel_btn.configure(state=tk.DISABLED)
                    report = payload if isinstance(payload, dict) else {}
                    if report.get("error") == "missing_venv":
                        messagebox.showerror("Clip engine missing", WORKER_MISSING_TIP)
                    else:
                        clip_n = report.get("clip_count", 0)
                        out_dir = report.get("out_dir", "")
                        self.clips_line.set(f"Clips found: {clip_n}")
                        self.eta_line.set("")
                        if report.get("fail"):
                            fails = [
                                Path(r["input"]).name
                                for r in report.get("results") or []
                                if not r.get("ok") and r.get("code") != 130
                            ]
                            self.status.set(
                                f"Finished with errors. {clip_n} clips -> {out_dir}"
                            )
                            messagebox.showwarning(
                                "Some files failed",
                                "Failed:\n" + "\n".join(fails) if fails else "See Details.",
                            )
                            # Keep selection on fail
                        elif report.get("cancelled"):
                            self.status.set(
                                f"Cancelled. {clip_n} clips -> {out_dir}"
                            )
                            # Keep selection on cancel
                        else:
                            self.status.set(
                                f"Done. {clip_n} clips -> {out_dir}"
                            )
                            open_it = messagebox.askyesno(
                                "Session complete",
                                f"{report.get('ok', 0)} file(s) ok\n"
                                f"{clip_n} clip(s)\n"
                                f"{out_dir}\n\n"
                                "Open file location?",
                            )
                            if open_it and out_dir:
                                self._open_file_location(str(out_dir))
                            # Clear selection only after successful Done
                            self.files.clear()
                            self._refresh_file_count()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Session UI for Skate Clip Cutter (multi-file -> YOLO person clips)."
    )
    p.add_argument(
        "--inputs",
        nargs="+",
        help="Headless: process these videos (skip GUI).",
    )
    p.add_argument(
        "--out",
        help="Explicit session output folder (overrides --landing/--session-name).",
    )
    p.add_argument(
        "--landing",
        help="Output folder: parent for the named session folder (default: Videos).",
    )
    p.add_argument(
        "--session-name",
        help="Session folder name under output folder (duplicates get 'Name 1', 'Name 2', …).",
    )
    p.add_argument("--pre-roll", type=float, default=None)
    p.add_argument("--post-roll", type=float, default=None)
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--reencode", action="store_true")
    return p.parse_args(argv)


def resolve_out_dir(args: argparse.Namespace) -> Path:
    if args.out:
        return Path(args.out).expanduser().resolve()
    landing = Path(args.landing).expanduser().resolve() if args.landing else DEFAULT_LANDING
    name = args.session_name or default_session_name()
    return unique_session_dir(landing, name)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.inputs:
        if resolve_worker_python() is None:
            print(WORKER_MISSING_TIP, file=sys.stderr)
            return 2
        inputs = [Path(x).expanduser().resolve() for x in args.inputs]
        missing = [str(p) for p in inputs if not p.is_file()]
        if missing:
            print("ERROR: missing input(s):", file=sys.stderr)
            for m in missing:
                print(f"  {m}", file=sys.stderr)
            return 2
        out = resolve_out_dir(args)
        print(f"session out: {out}", flush=True)
        print(f"engine: {ENGINE_LABEL}", flush=True)

        def on_status(msg: str) -> None:
            print(f"[status] {msg}", flush=True)

        def on_line(msg: str) -> None:
            print(msg, flush=True)

        def on_progress(info: dict) -> None:
            print(
                f"[progress] {info.get('file_index')}/{info.get('file_total')} "
                f"{info.get('file_name')} | clips={info.get('clips_found')} | "
                f"{info.get('eta_text')} | {info.get('phase')}",
                flush=True,
            )

        report = run_session(
            inputs,
            out,
            pre_roll=args.pre_roll,
            post_roll=args.post_roll,
            fps=args.fps,
            reencode=args.reencode,
            on_status=on_status,
            on_line=on_line,
            on_progress=on_progress,
        )
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "out_dir",
                        "ok",
                        "fail",
                        "clip_count",
                        "elapsed_s",
                        "merged_manifest",
                        "engine",
                    )
                    if k in report
                },
                indent=2,
            )
        )
        return 1 if report["fail"] and report["ok"] == 0 else 0

    app = SessionApp()
    if args.landing:
        app.landing_dir.set(str(Path(args.landing).expanduser()))
    if args.session_name:
        app.session_name.set(args.session_name)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
