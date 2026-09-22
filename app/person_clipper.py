#!/usr/bin/env python3
"""Find a person in the video and write the clips."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from clipper import (
    DEFAULT_POST_ROLL,
    DEFAULT_PRE_ROLL,
    export_segment,
    probe_input,
    refresh_path,
    which_tool,
    write_no_activity_note,
)
from paths import ROOT, bundled_model_path

# COCO class ids
PERSON_CLS = 0
SKATEBOARD_CLS = 36

SAMPLE_FPS = 1.5
SAMPLE_WIDTH = 640
DEFAULT_MERGE_GAP = 2.5
DEFAULT_MIN_SEGMENT = 3.0
DEFAULT_CONF = 0.35
DEFAULT_MODEL = "yolo11n.pt"


@dataclass
class DetectHit:
    t: float
    person: bool
    skateboard: bool
    person_conf: float
    skate_conf: float


def resolve_model(model_name: str) -> str:
    """Prefer an on-disk weights file so portable zips do not hit the network."""
    given = Path(model_name)
    if given.is_file():
        return str(given.resolve())
    if given.is_absolute():
        return str(given)
    bundled = bundled_model_path()
    if bundled is not None and (
        model_name in (DEFAULT_MODEL, bundled.name) or given.name == bundled.name
    ):
        return str(bundled)
    beside = ROOT / given.name
    if beside.is_file():
        return str(beside)
    return model_name


def load_yolo(model_name: str):
    try:
        from ultralytics import YOLO
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "ERROR: ultralytics is not installed in this Python.\n"
            "Use the GitHub Releases zip (Skate Clip Cutter.bat), or the project .venv."
        ) from exc
    return YOLO(resolve_model(model_name))



def sample_and_detect(
    model,
    path: Path,
    duration: float,
    sample_fps: float,
    sample_width: int,
    conf: float,
    progress_cb,
) -> list[DetectHit]:
    """Open video with OpenCV, subsample ~sample_fps, run YOLO on each sample."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"ERROR: OpenCV could not open {path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(src_fps / sample_fps)))
    expected = max(1, int(duration * sample_fps) + 2)

    hits: list[DetectHit] = []
    idx = 0
    sample_i = 0
    t0 = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % frame_interval != 0:
                idx += 1
                continue

            t = idx / src_fps
            h, w = frame.shape[:2]
            if w > sample_width:
                scale = sample_width / w
                frame = cv2.resize(
                    frame, (sample_width, int(h * scale)), interpolation=cv2.INTER_AREA
                )

            results = model.predict(
                frame, conf=conf, verbose=False, classes=[PERSON_CLS, SKATEBOARD_CLS]
            )
            person = skateboard = False
            person_conf = skate_conf = 0.0
            if results:
                boxes = results[0].boxes
                if boxes is not None and len(boxes):
                    cls = boxes.cls.cpu().numpy().astype(int)
                    confs = boxes.conf.cpu().numpy()
                    for c, cf in zip(cls, confs):
                        if c == PERSON_CLS:
                            person = True
                            person_conf = max(person_conf, float(cf))
                        elif c == SKATEBOARD_CLS:
                            skateboard = True
                            skate_conf = max(skate_conf, float(cf))

            hits.append(
                DetectHit(
                    t=t,
                    person=person,
                    skateboard=skateboard,
                    person_conf=person_conf,
                    skate_conf=skate_conf,
                )
            )
            sample_i += 1
            idx += 1

            if sample_i % 5 == 0 or sample_i == 1:
                elapsed = time.time() - t0
                pct = min(99.0, 100.0 * sample_i / expected)
                rate = sample_i / elapsed if elapsed > 0 else 0.0
                remain = (expected - sample_i) / rate if rate > 0 else 0.0
                progress_cb(
                    f"yolo {pct:5.1f}%  samples={sample_i}  "
                    f"person={sum(1 for h in hits if h.person)}  "
                    f"elapsed={elapsed:.0f}s  eta~{remain:.0f}s"
                )
    finally:
        cap.release()

    if not hits:
        raise SystemExit("ERROR: no frames sampled from input")
    progress_cb(
        f"yolo 100.0%  samples={len(hits)}  "
        f"person={sum(1 for h in hits if h.person)}  "
        f"elapsed={time.time() - t0:.0f}s  eta~0s"
    )
    return hits


def build_keep_windows(
    hits: list[DetectHit],
    duration: float,
    sample_fps: float,
    pre_roll: float,
    post_roll: float,
    merge_gap: float,
    min_segment: float,
    *,
    require_skateboard: bool = False,
) -> list[tuple[float, float]]:
    """Positive = person detected (optionally also skateboard). Merge + pad."""
    flags: list[bool] = []
    for h in hits:
        if require_skateboard:
            flags.append(h.person and h.skateboard)
        else:
            # Keep on person; skateboard alone is weak/noisy on distant boards.
            flags.append(h.person)

    windows: list[tuple[float, float]] = []
    start: float | None = None
    step = 1.0 / sample_fps
    for h, on in zip(hits, flags):
        if on and start is None:
            start = h.t
        elif not on and start is not None:
            windows.append((start, h.t))
            start = None
    if start is not None:
        windows.append((start, hits[-1].t + step))

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

    padded: list[tuple[float, float]] = []
    for a, b in merged:
        sa = max(0.0, a - pre_roll)
        sb = min(duration, b + post_roll)
        if sb - sa < min_segment:
            continue
        if padded and sa - padded[-1][1] <= merge_gap:
            pa, pb = padded[-1]
            padded[-1] = (pa, max(pb, sb))
        else:
            padded.append((sa, sb))
    return padded


def main(argv: list[str] | None = None) -> int:
    refresh_path()
    parser = argparse.ArgumentParser(
        description="Find a person in a video and write clips."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to raw video file")
    parser.add_argument(
        "--out", "-o", required=True, help="Output folder for clips (created if missing)"
    )
    parser.add_argument(
        "--fps", type=float, default=SAMPLE_FPS,
        help=f"Sample rate for detection (default {SAMPLE_FPS})",
    )
    parser.add_argument(
        "--pre-roll", type=float, default=DEFAULT_PRE_ROLL,
        help=f"Seconds of lead-in before person (default {DEFAULT_PRE_ROLL})",
    )
    parser.add_argument(
        "--post-roll", type=float, default=DEFAULT_POST_ROLL,
        help=f"Seconds of trail after person leaves (default {DEFAULT_POST_ROLL})",
    )
    parser.add_argument(
        "--merge-gap", type=float, default=DEFAULT_MERGE_GAP,
        help=f"Merge person windows closer than this (default {DEFAULT_MERGE_GAP})",
    )
    parser.add_argument(
        "--min-segment", type=float, default=DEFAULT_MIN_SEGMENT,
        help=f"Drop padded clips shorter than this (default {DEFAULT_MIN_SEGMENT})",
    )
    parser.add_argument(
        "--conf", type=float, default=DEFAULT_CONF,
        help=f"YOLO confidence threshold (default {DEFAULT_CONF})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Ultralytics model weights (default {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--width", type=int, default=SAMPLE_WIDTH,
        help=f"Resize width before YOLO (default {SAMPLE_WIDTH})",
    )
    parser.add_argument(
        "--require-skateboard",
        action="store_true",
        help="Require person AND skateboard (stricter; often misses distant boards)",
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Print segments only; skip export",
    )
    parser.add_argument(
        "--reencode",
        action="store_true",
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
        f"yolo:   model={args.model}  conf={args.conf:.2f}  fps={args.fps}  "
        f"width={args.width}  require_skate={args.require_skateboard}"
    )
    print(
        f"pads:   pre-roll={args.pre_roll:.2f}s  post-roll={args.post_roll:.2f}s  "
        f"merge-gap={args.merge_gap:.2f}s  min-segment={args.min_segment:.2f}s"
    )

    info = probe_input(ffprobe, src)
    print(
        f"probe:  {info.duration:.1f}s  {info.width}x{info.height}  "
        f"codec={info.video_codec}  audio={'yes' if info.has_audio else 'no'}"
    )

    def progress(msg: str) -> None:
        print(msg, flush=True)

    print("loading YOLO…", flush=True)
    t_load0 = time.time()
    model = load_yolo(args.model)
    print(f"model loaded in {time.time() - t_load0:.1f}s", flush=True)

    t_detect0 = time.time()
    hits = sample_and_detect(
        model,
        src,
        info.duration,
        args.fps,
        args.width,
        args.conf,
        progress,
    )
    detect_s = time.time() - t_detect0

    n_person = sum(1 for h in hits if h.person)
    n_skate = sum(1 for h in hits if h.skateboard)
    n_both = sum(1 for h in hits if h.person and h.skateboard)
    print(
        f"hits:   samples={len(hits)}  person={n_person}  "
        f"skateboard={n_skate}  both={n_both}  ({detect_s:.1f}s)"
    )

    segments = build_keep_windows(
        hits,
        info.duration,
        args.fps,
        args.pre_roll,
        args.post_roll,
        args.merge_gap,
        args.min_segment,
        require_skateboard=args.require_skateboard,
    )
    if not segments:
        note = write_no_activity_note(
            out_dir, src, "no person(+skate) windows after merge/pad"
        )
        print(f"No activity detected. Wrote {note}")
        return 0

    print(f"segments: {len(segments)}")
    for i, (a, b) in enumerate(segments, 1):
        print(f"  [{i:02d}] {a:7.2f}s -> {b:7.2f}s  ({b - a:.2f}s)")

    manifest_base = {
        "mode": "a2_yolo_person",
        "input": str(src),
        "duration_s": info.duration,
        "sample_fps": args.fps,
        "model": args.model,
        "conf": args.conf,
        "sample_width": args.width,
        "pre_roll_s": args.pre_roll,
        "post_roll_s": args.post_roll,
        "merge_gap_s": args.merge_gap,
        "min_segment_s": args.min_segment,
        "require_skateboard": args.require_skateboard,
        "detect_s": round(detect_s, 2),
        "hit_summary": {
            "samples": len(hits),
            "person": n_person,
            "skateboard": n_skate,
            "both": n_both,
        },
        "segment_count": len(segments),
        "created": datetime.now().isoformat(timespec="seconds"),
    }

    if args.detect_only:
        manifest = out_dir / "clips.json"
        manifest.write_text(
            json.dumps(
                {
                    **manifest_base,
                    "detect_only": True,
                    "segments": [
                        {"index": i, "start": a, "end": b}
                        for i, (a, b) in enumerate(segments, 1)
                    ],
                    "hits": [
                        {
                            "t": round(h.t, 3),
                            "person": h.person,
                            "skateboard": h.skateboard,
                            "person_conf": round(h.person_conf, 3),
                            "skate_conf": round(h.skate_conf, 3),
                        }
                        for h in hits
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

    body = {
        **manifest_base,
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
    }
    text = json.dumps(body, indent=2)
    manifest = out_dir / "clips.json"
    per_stem = out_dir / f"{stem}_clips.json"
    manifest.write_text(text, encoding="utf-8")
    per_stem.write_text(text, encoding="utf-8")
    print(f"done: {len(written)} clip(s) -> {out_dir}")
    print(f"manifest: {manifest}")
    print(f"per-file: {per_stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

