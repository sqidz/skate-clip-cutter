#!/usr/bin/env python3
"""Skate session clip cutter - Week 38 Slice A.1 (merge3 retune).

One raw video in -> coarse local motion detect -> folder of clip files out.
Stdlib + ffmpeg/ffprobe only. No review UI, no phone app, no DRM, no batch.

V1 product lock: tripod / rock-steady only (handheld continuous roll out of claims).
Merge3 (2026-09-17): tighten MERGE/PAD; short lead-in absorb; keep blank drop + pre-roll.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from paths import find_bundled_ffmpeg_tool


SAMPLE_FPS = 1.5
SAMPLE_WIDTH = 320
# Merge3 defaults (Zac: avoid over-glue of distinct tricks; keep blank drop + lead-in).
DEFAULT_PRE_ROLL = 3.0
DEFAULT_POST_ROLL = 1.25
MERGE_GAP = 4.0
MIN_SEGMENT = 3.0
MOTION_FLOOR = 5.0
ACTIVE_PERCENTILE = 55.0
VARIANCE_FLOOR = 20.0
# After pad: merge clips whose gap is still small (near-adjacent attempts).
PAD_MERGE_GAP = 2.0
# Fold a short padded lead-in into the *following* attempt when the gap is still modest
# (breaks the 2-vs-4 symmetry where setup and trailing fakie share ~equal gaps).
SHORT_LEAD_IN = 6.0
SHORT_LEAD_GAP = 3.25
# Drop padded segments whose in-window frames look blank/shutter (mean variance).
BLANK_MEAN_VARIANCE = 28.0


def refresh_path() -> None:
    """Pull Machine+User PATH so winget ffmpeg is visible in this process."""
    if os.name != "nt":
        return
    try:
        import winreg

        parts: list[str] = []
        for root, sub in (
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, "Environment"),
        ):
            try:
                with winreg.OpenKey(root, sub) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    if val:
                        parts.append(val)
            except OSError:
                pass
        if parts:
            os.environ["Path"] = ";".join(parts)
    except Exception:
        pass


def find_tool(name: str) -> str | None:
    """Locate ffmpeg/ffprobe: bundled zip copy, PATH, then winget Gyan layout."""
    bundled = find_bundled_ffmpeg_tool(name)
    if bundled is not None:
        return str(bundled)
    path = shutil.which(name)
    if path:
        return path
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        winget = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if winget.is_dir():
            for p in winget.glob("Gyan.FFmpeg*/ffmpeg-*/bin"):
                cand = p / f"{name}.exe"
                if cand.is_file():
                    return str(cand)
    return None


def which_tool(name: str) -> str:
    found = find_tool(name)
    if found:
        return found
    raise SystemExit(
        f"ERROR: '{name}' not found. This download should include vendor\\ffmpeg. "
        "Re-download from GitHub Releases, or install Gyan.FFmpeg via winget."
    )


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


@dataclass
class Probe:
    path: Path
    duration: float
    width: int
    height: int
    video_codec: str
    has_audio: bool


def probe_input(ffprobe: str, path: Path) -> Probe:
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type,codec_name,width,height",
        "-of", "json",
        str(path),
    ]
    proc = run_capture(cmd)
    data = json.loads(proc.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0)
    width = height = 0
    video_codec = "unknown"
    has_audio = False
    for s in data.get("streams") or []:
        ctype = s.get("codec_type")
        cname = s.get("codec_name") or ""
        if ctype == "video" or s.get("width"):
            if s.get("width"):
                width = int(s["width"])
                height = int(s.get("height") or 0)
                if cname:
                    video_codec = cname
        if ctype == "audio" or cname in {"aac", "mp3", "opus", "vorbis"}:
            has_audio = True
    if duration <= 0:
        raise SystemExit(f"ERROR: could not read duration for {path}")
    return Probe(path, duration, width, height, video_codec, has_audio)


@dataclass
class FrameScore:
    t: float
    motion: float
    variance: float


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _frame_stats(curr: array, prev: array | None) -> tuple[float, float]:
    """Return (motion_mad, variance) for a gray frame."""
    n = len(curr)
    total = 0
    for v in curr:
        total += v
    mean = total / n
    var_acc = 0.0
    for v in curr:
        d = v - mean
        var_acc += d * d
    variance = var_acc / n
    if prev is None:
        return 0.0, variance
    mad_acc = 0
    for a, b in zip(curr, prev):
        mad_acc += a - b if a >= b else b - a
    return mad_acc / n, variance


def sample_motion_scores(
    ffmpeg: str,
    path: Path,
    duration: float,
    sample_fps: float,
    progress_cb,
) -> list[FrameScore]:
    """Decode low-res gray frames via ffmpeg pipe; score motion + variance."""
    vf = f"fps={sample_fps},scale={SAMPLE_WIDTH}:-2,format=gray"
    probe_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-an", "-vf", vf,
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    one = subprocess.run(probe_cmd, capture_output=True, check=True)
    frame_bytes = len(one.stdout)
    if frame_bytes <= 0:
        raise SystemExit("ERROR: ffmpeg produced no sample frames")
    if frame_bytes % SAMPLE_WIDTH != 0:
        raise SystemExit(f"ERROR: unexpected frame size {frame_bytes} (w={SAMPLE_WIDTH})")

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-an", "-vf", vf,
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None

    scores: list[FrameScore] = []
    prev: array | None = None
    idx = 0
    t0 = time.time()
    expected = max(1, int(duration * sample_fps) + 2)

    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            curr = array("B")
            curr.frombytes(buf)
            motion, variance = _frame_stats(curr, prev)
            t = idx / sample_fps
            scores.append(FrameScore(t=t, motion=motion, variance=variance))
            prev = curr
            idx += 1
            if idx % 5 == 0 or idx == 1:
                elapsed = time.time() - t0
                pct = min(99.0, 100.0 * idx / expected)
                rate = idx / elapsed if elapsed > 0 else 0.0
                remain = (expected - idx) / rate if rate > 0 else 0.0
                progress_cb(
                    f"detect {pct:5.1f}%  frames={idx}  "
                    f"elapsed={elapsed:.0f}s  eta~{remain:.0f}s"
                )
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    if not scores:
        raise SystemExit("ERROR: no frames sampled from input")
    progress_cb(
        f"detect 100.0%  frames={len(scores)}  "
        f"elapsed={time.time() - t0:.0f}s  eta~0s"
    )
    return scores


def _mean_variance_in_range(
    scores: list[FrameScore], start: float, end: float
) -> float:
    """Mean frame variance for scores whose t falls in [start, end]."""
    vals = [s.variance for s in scores if start <= s.t <= end]
    if not vals:
        vals = [s.variance for s in scores if start - 0.5 <= s.t <= end + 0.5]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def find_active_segments(
    scores: list[FrameScore],
    duration: float,
    sample_fps: float,
    pre_roll: float,
    post_roll: float,
    *,
    merge_gap: float = MERGE_GAP,
    min_segment: float = MIN_SEGMENT,
    motion_floor: float = MOTION_FLOOR,
    variance_floor: float = VARIANCE_FLOOR,
    pad_merge_gap: float = PAD_MERGE_GAP,
    blank_mean_variance: float = BLANK_MEAN_VARIANCE,
    short_lead_in: float = SHORT_LEAD_IN,
    short_lead_gap: float = SHORT_LEAD_GAP,
) -> list[tuple[float, float]]:
    if len(scores) < 2:
        return []

    motions = [s.motion for s in scores[1:]]
    positive = sorted(m for m in motions if m > motion_floor * 0.5)
    if not positive:
        return []
    adaptive = _percentile(positive, ACTIVE_PERCENTILE)
    threshold = max(motion_floor, adaptive * 0.85)

    active_flags: list[bool] = []
    for s in scores:
        present = s.variance >= variance_floor
        moving = s.motion >= threshold
        active_flags.append(present and moving)

    windows: list[tuple[float, float]] = []
    start: float | None = None
    for s, on in zip(scores, active_flags):
        if on and start is None:
            start = s.t
        elif not on and start is not None:
            windows.append((start, s.t))
            start = None
    if start is not None:
        windows.append((start, scores[-1].t + (1.0 / sample_fps)))

    if not windows:
        return []

    merged: list[tuple[float, float]] = []
    for a, b in windows:
        if not merged:
            merged.append((a, b))
            continue
        pa, pb = merged[-1]
        if a - pb <= merge_gap:
            merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))

    # Pad, then drop blank/shutter and undersized cores before neighbor merge.
    candidates: list[tuple[float, float]] = []
    for a, b in merged:
        sa = max(0.0, a - pre_roll)
        sb = min(duration, b + post_roll)
        if sb - sa < min_segment:
            continue
        core_var = _mean_variance_in_range(scores, a, b)
        if core_var < blank_mean_variance:
            continue
        candidates.append((sa, sb))

    padded: list[tuple[float, float]] = []
    for sa, sb in candidates:
        if not padded:
            padded.append((sa, sb))
            continue
        pa, pb = padded[-1]
        gap = sa - pb
        left_len = pb - pa
        if gap <= pad_merge_gap:
            padded[-1] = (pa, max(pb, sb))
        elif left_len <= short_lead_in and gap <= short_lead_gap:
            # Short setup/roll-in before the next attempt -> absorb forward only.
            padded[-1] = (pa, max(pb, sb))
        else:
            padded.append((sa, sb))
    return padded


def export_segment(
    ffmpeg: str,
    src: Path,
    dst: Path,
    start: float,
    end: float,
    reencode: bool = False,
) -> None:
    dur = max(0.05, end - start)
    if not reencode:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-c", "copy",
            "-map", "0:v:0", "-map", "0:a:0?",
            "-avoid_negative_ts", "make_zero",
            str(dst),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 1000:
            return

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(src),
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:400]
        raise RuntimeError(f"ffmpeg export failed for {dst.name}: {err}")


def write_no_activity_note(out_dir: Path, src: Path, reason: str) -> Path:
    note = out_dir / "NO_ACTIVITY.txt"
    note.write_text(
        f"No active skate segments found.\n"
        f"Input: {src}\n"
        f"Reason: {reason}\n"
        f"Time: {datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )
    return note


def main(argv: list[str] | None = None) -> int:
    refresh_path()
    parser = argparse.ArgumentParser(
        description="Coarse local skate clip cutter (motion-based, with pre-roll)."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to raw video file")
    parser.add_argument(
        "--out", "-o", required=True,
        help="Output folder for clips (created if missing)",
    )
    parser.add_argument(
        "--fps", type=float, default=SAMPLE_FPS,
        help=f"Sample rate for detection (default {SAMPLE_FPS})",
    )
    parser.add_argument(
        "--pre-roll", type=float, default=DEFAULT_PRE_ROLL,
        help=f"Seconds of lead-in before detected motion (default {DEFAULT_PRE_ROLL})",
    )
    parser.add_argument(
        "--post-roll", type=float, default=DEFAULT_POST_ROLL,
        help=f"Seconds of trail after motion ends (default {DEFAULT_POST_ROLL})",
    )
    parser.add_argument(
        "--merge-gap", type=float, default=MERGE_GAP,
        help=f"Merge active windows closer than this many seconds (default {MERGE_GAP})",
    )
    parser.add_argument(
        "--min-segment", type=float, default=MIN_SEGMENT,
        help=f"Drop padded clips shorter than this (default {MIN_SEGMENT})",
    )
    parser.add_argument(
        "--motion-floor", type=float, default=MOTION_FLOOR,
        help=f"Minimum motion MAD to count as active (default {MOTION_FLOOR})",
    )
    parser.add_argument(
        "--pad-merge-gap", type=float, default=PAD_MERGE_GAP,
        help=f"After pad, merge clips closer than this many seconds (default {PAD_MERGE_GAP})",
    )
    parser.add_argument(
        "--blank-mean-variance", type=float, default=BLANK_MEAN_VARIANCE,
        help=f"Drop segments whose core mean variance is below this (default {BLANK_MEAN_VARIANCE})",
    )
    parser.add_argument(
        "--detect-only", action="store_true",
        help="Print segments only; skip export (for tuning)",
    )
    parser.add_argument(
        "--reencode", action="store_true",
        help="Force light H.264 re-encode instead of stream-copy",
    )
    args = parser.parse_args(argv)

    src = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    if not src.is_file():
        print(f"ERROR: input not found: {src}", file=sys.stderr)
        return 2
    if args.pre_roll < 0 or args.post_roll < 0:
        print("ERROR: --pre-roll and --post-roll must be >= 0", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = which_tool("ffmpeg")
    ffprobe = which_tool("ffprobe")

    print(f"input:  {src}")
    print(f"out:    {out_dir}")
    print(f"ffmpeg: {ffmpeg}")
    print(
        f"pads:   pre-roll={args.pre_roll:.2f}s  post-roll={args.post_roll:.2f}s  "
        f"merge-gap={args.merge_gap:.2f}s  pad-merge-gap={args.pad_merge_gap:.2f}s  "
        f"min-segment={args.min_segment:.2f}s  motion-floor={args.motion_floor:.2f}  "
        f"blank-mean-var={args.blank_mean_variance:.1f}  "
        f"short-lead-in={SHORT_LEAD_IN:.1f}/{SHORT_LEAD_GAP:.2f}s"
    )

    info = probe_input(ffprobe, src)
    print(
        f"probe:  {info.duration:.1f}s  {info.width}x{info.height}  "
        f"codec={info.video_codec}  audio={'yes' if info.has_audio else 'no'}"
    )

    def progress(msg: str) -> None:
        print(msg, flush=True)

    t_detect0 = time.time()
    scores = sample_motion_scores(ffmpeg, src, info.duration, args.fps, progress)
    detect_s = time.time() - t_detect0

    motions = [s.motion for s in scores[1:]]
    print(
        f"motion: n={len(scores)}  "
        f"min={min(motions):.2f}  med={_percentile(sorted(motions), 50):.2f}  "
        f"p90={_percentile(sorted(motions), 90):.2f}  max={max(motions):.2f}  "
        f"({detect_s:.1f}s)"
    )

    segments = find_active_segments(
        scores,
        info.duration,
        args.fps,
        args.pre_roll,
        args.post_roll,
        merge_gap=args.merge_gap,
        min_segment=args.min_segment,
        motion_floor=args.motion_floor,
        pad_merge_gap=args.pad_merge_gap,
        blank_mean_variance=args.blank_mean_variance,
    )
    if not segments:
        note = write_no_activity_note(
            out_dir, src, "motion/presence below thresholds after merge/pad/blank-drop"
        )
        print(f"No activity detected. Wrote {note}")
        return 0

    print(f"segments: {len(segments)}")
    for i, (a, b) in enumerate(segments, 1):
        print(f"  [{i:02d}] {a:7.2f}s -> {b:7.2f}s  ({b - a:.2f}s)")

    if args.detect_only:
        manifest = out_dir / "clips.json"
        manifest.write_text(
            json.dumps(
                {
                    "input": str(src),
                    "duration_s": info.duration,
                    "sample_fps": args.fps,
                    "pre_roll_s": args.pre_roll,
                    "post_roll_s": args.post_roll,
                    "merge_gap_s": args.merge_gap,
                    "pad_merge_gap_s": args.pad_merge_gap,
                    "min_segment_s": args.min_segment,
                    "motion_floor": args.motion_floor,
                    "blank_mean_variance": args.blank_mean_variance,
                    "segment_count": len(segments),
                    "detect_only": True,
                    "segments": [
                        {"index": i, "start": a, "end": b}
                        for i, (a, b) in enumerate(segments, 1)
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"detect-only: wrote {manifest}")
        return 0

    stem = src.stem
    written: list[Path] = []
    t_exp0 = time.time()
    for i, (a, b) in enumerate(segments, 1):
        name = f"{stem}_clip{i:02d}_{a:.1f}-{b:.1f}.mp4"
        dst = out_dir / name
        export_segment(ffmpeg, src, dst, a, b, reencode=args.reencode)
        written.append(dst)
        elapsed = time.time() - t_exp0
        pct = 100.0 * i / len(segments)
        rate = i / elapsed if elapsed > 0 else 0.0
        remain = (len(segments) - i) / rate if rate > 0 else 0.0
        size_mb = dst.stat().st_size / (1024 * 1024)
        print(
            f"export {pct:5.1f}%  clip {i}/{len(segments)}  "
            f"{dst.name}  {size_mb:.1f}MB  "
            f"elapsed={elapsed:.0f}s  eta~{remain:.0f}s",
            flush=True,
        )

    manifest = out_dir / "clips.json"
    manifest.write_text(
        json.dumps(
            {
                "input": str(src),
                "duration_s": info.duration,
                "sample_fps": args.fps,
                "pre_roll_s": args.pre_roll,
                "post_roll_s": args.post_roll,
                "merge_gap_s": args.merge_gap,
                "pad_merge_gap_s": args.pad_merge_gap,
                "min_segment_s": args.min_segment,
                "motion_floor": args.motion_floor,
                "blank_mean_variance": args.blank_mean_variance,
                "short_lead_in_s": SHORT_LEAD_IN,
                "short_lead_gap_s": SHORT_LEAD_GAP,
                "segment_count": len(segments),
                "segments": [
                    {
                        "index": i,
                        "start": a,
                        "end": b,
                        "file": p.name,
                        "bytes": p.stat().st_size,
                    }
                    for i, ((a, b), p) in enumerate(zip(segments, written), 1)
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"done: {len(written)} clip(s) -> {out_dir}")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())