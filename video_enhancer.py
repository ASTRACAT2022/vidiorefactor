#!/usr/bin/env python3
"""Improve video quality with practical ffmpeg filter presets."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PRESETS = {
    "balanced": {
        "description": "moderate denoise, contrast, and sharpening",
        "filters": ["hqdn3d=1.5:1.5:6:6", "eq=contrast=1.06:saturation=1.08", "unsharp=5:5:0.55:3:3:0.25"],
        "crf": 18,
    },
    "soft": {
        "description": "gentle cleanup for already decent footage",
        "filters": ["hqdn3d=1.0:1.0:4:4", "eq=contrast=1.03:saturation=1.04", "unsharp=5:5:0.35:3:3:0.15"],
        "crf": 19,
    },
    "strong": {
        "description": "stronger cleanup for noisy or compressed footage",
        "filters": ["hqdn3d=2.5:2.5:8:8", "eq=contrast=1.08:saturation=1.10", "unsharp=7:7:0.75:5:5:0.35"],
        "crf": 17,
    },
    "upscale": {
        "description": "cleanup plus high quality 2x upscale",
        "filters": [
            "hqdn3d=1.8:1.8:7:7",
            "eq=contrast=1.05:saturation=1.08",
            "scale=iw*2:ih*2:flags=lanczos",
            "unsharp=5:5:0.45:3:3:0.20",
        ],
        "crf": 18,
    },
}

SPEED_MODES = {
    "quality": {"codec": "libx264", "encoder_preset": "slow", "crf_delta": 0},
    "balanced": {"codec": "libx264", "encoder_preset": "medium", "crf_delta": 1},
    "fast": {"codec": "libx264", "encoder_preset": "veryfast", "crf_delta": 2},
    "turbo": {"codec": "h264_videotoolbox", "encoder_preset": None, "crf_delta": 0},
}

VIDEOTOOLBOX_CODECS = {"h264_videotoolbox", "hevc_videotoolbox"}


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def crf_value(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc

    if not 0 <= parsed <= 51:
        raise argparse.ArgumentTypeError("CRF must be between 0 and 51")
    return parsed


def bitrate_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise argparse.ArgumentTypeError("bitrate cannot be empty")

    number = normalized[:-1] if normalized[-1:] in {"k", "K", "m", "M"} else normalized
    if not number.isdigit() or int(number) <= 0:
        raise argparse.ArgumentTypeError("bitrate must look like 8000k or 12M")
    return normalized


def build_filters(args: argparse.Namespace) -> str:
    filters = list(PRESETS[args.preset]["filters"])

    if args.width or args.height:
        width = args.width if args.width else -2
        height = args.height if args.height else -2
        scale_filter = f"scale={width}:{height}:flags=lanczos"

        filters = [item for item in filters if not item.startswith("scale=")]
        filters.append(scale_filter)

    if args.fps:
        filters.append(f"fps={args.fps}")

    return ",".join(filters)


def build_command(args: argparse.Namespace, force_cpu: bool = False, force_overwrite: bool = False) -> list[str]:
    preset = PRESETS[args.preset]
    speed_mode = SPEED_MODES[args.speed]
    video_filters = build_filters(args)
    output_path = args.output
    codec = "libx264" if force_cpu else args.codec or speed_mode["codec"]
    overwrite = args.overwrite or force_overwrite

    command = [
        args.ffmpeg,
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(args.input),
        "-vf",
        video_filters,
        "-c:v",
        codec,
    ]

    if codec in VIDEOTOOLBOX_CODECS:
        command += ["-b:v", args.bitrate, "-allow_sw", "true"]
    else:
        encoder_preset = "veryfast" if force_cpu else args.encoder_preset or speed_mode["encoder_preset"]
        crf_delta = 2 if force_cpu else speed_mode["crf_delta"]
        crf = args.crf if args.crf is not None else preset["crf"] + crf_delta
        command += ["-preset", encoder_preset, "-crf", str(crf)]

    if args.no_audio:
        command.append("-an")
    else:
        command += ["-c:a", "copy" if args.copy_audio else "aac"]

    command += ["-movflags", "+faststart", str(output_path)]
    return command


def shell_join(command: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in command)


def command_uses_videotoolbox(command: list[str]) -> bool:
    return any(codec in command for codec in VIDEOTOOLBOX_CODECS)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Improve a video with ffmpeg denoise, sharpening, color, and upscale presets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", nargs="?", type=Path, help="source video path")
    parser.add_argument("output", nargs="?", type=Path, help="enhanced video path")
    parser.add_argument(
        "-p",
        "--preset",
        choices=sorted(PRESETS),
        default="balanced",
        help="enhancement preset",
    )
    parser.add_argument("--width", type=positive_int, help="target width; keeps aspect ratio if height is omitted")
    parser.add_argument("--height", type=positive_int, help="target height; keeps aspect ratio if width is omitted")
    parser.add_argument("--fps", type=positive_int, help="optional output frame rate")
    parser.add_argument("--crf", type=crf_value, help="quality value for x264/x265; lower is better and larger files")
    parser.add_argument("--speed", choices=sorted(SPEED_MODES), default="fast", help="encoding speed mode")
    parser.add_argument("--codec", help="ffmpeg video encoder; overrides --speed default codec")
    parser.add_argument("--encoder-preset", help="ffmpeg CPU encoder speed/quality preset")
    parser.add_argument("--bitrate", type=bitrate_value, default="12M", help="target bitrate for VideoToolbox hardware encoders")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="path to ffmpeg executable")
    parser.add_argument("--copy-audio", action=argparse.BooleanOptionalAction, default=True, help="copy audio without re-encoding")
    parser.add_argument("--no-audio", action="store_true", help="remove audio from the output")
    parser.add_argument("--overwrite", action="store_true", help="overwrite output if it already exists")
    parser.add_argument("--dry-run", action="store_true", help="print the ffmpeg command and exit")
    parser.add_argument("--list-presets", action="store_true", help="show available presets and exit")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> int:
    if args.list_presets:
        for name in sorted(PRESETS):
            print(f"{name:8} {PRESETS[name]['description']}")
        return 1

    if not args.input or not args.output:
        print("Input and output paths are required unless --list-presets is used.", file=sys.stderr)
        return 2

    if not args.input.exists():
        print(f"Input file does not exist: {args.input}", file=sys.stderr)
        return 2

    if args.input.resolve() == args.output.resolve():
        print("Input and output paths must be different.", file=sys.stderr)
        return 2

    if args.output.parent and not args.output.parent.exists():
        print(f"Output directory does not exist: {args.output.parent}", file=sys.stderr)
        return 2

    if args.output.exists() and not args.overwrite and not args.dry_run:
        print(f"Output already exists, use --overwrite to replace it: {args.output}", file=sys.stderr)
        return 2

    ffmpeg_path = shutil.which(args.ffmpeg) if Path(args.ffmpeg).name == args.ffmpeg else args.ffmpeg
    if not ffmpeg_path and not args.dry_run:
        print(
            "ffmpeg is not installed or not in PATH. Install it first, for example: brew install ffmpeg",
            file=sys.stderr,
        )
        return 2

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    validation_result = validate_args(args)
    if validation_result == 1:
        return 0
    if validation_result:
        return validation_result

    command = build_command(args)
    print(shell_join(command))

    if args.dry_run:
        return 0

    result = subprocess.run(command)
    if result.returncode == 0:
        return 0

    if command_uses_videotoolbox(command):
        print("VideoToolbox failed; falling back to libx264 veryfast.", file=sys.stderr)
        fallback_command = build_command(args, force_cpu=True, force_overwrite=True)
        print(shell_join(fallback_command))
        return subprocess.run(fallback_command).returncode

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
