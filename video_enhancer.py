#!/usr/bin/env python3
"""Improve video quality with practical ffmpeg filter presets."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
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
    "ultra": {
        "description": "slow high quality denoise, deband, and detail pass",
        "filters": [
            "nlmeans=s=3.0:p=7:r=15",
            "deband=1thr=0.018:2thr=0.018:3thr=0.018:range=16:blur=true",
            "eq=contrast=1.04:saturation=1.05",
            "unsharp=5:5:0.35:3:3:0.15",
        ],
        "crf": 16,
    },
    "oldfilm": {
        "description": "cleanup for old, soft, flickery, or handheld footage",
        "filters": [
            "deflicker",
            "deshake=rx=12:ry=12:edge=mirror",
            "nlmeans=s=2.6:p=7:r=15",
            "eq=contrast=1.07:saturation=1.04:gamma=1.02",
            "unsharp=5:5:0.40:3:3:0.18",
        ],
        "crf": 17,
    },
    "compressed": {
        "description": "cleanup for messenger/social media compression artifacts",
        "filters": [
            "hqdn3d=2.2:2.2:7:7",
            "deband=1thr=0.024:2thr=0.024:3thr=0.024:range=18:blur=true",
            "eq=contrast=1.05:saturation=1.07",
            "unsharp=5:5:0.30:3:3:0.12",
        ],
        "crf": 18,
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
REALESRGAN_DEFAULT_MODEL = "realesrgan-x4plus"


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


def strength_value(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc

    if not 0 <= parsed <= 10:
        raise argparse.ArgumentTypeError("value must be between 0 and 10")
    return parsed


def scale_value(value: str) -> int:
    parsed = positive_int(value)
    if parsed not in {2, 3, 4}:
        raise argparse.ArgumentTypeError("AI scale must be 2, 3, or 4")
    return parsed


def denoise_filter(strength: float, high_quality: bool) -> str | None:
    if strength <= 0:
        return None

    if high_quality:
        nlmeans_strength = 1.0 + strength * 1.8
        return f"nlmeans=s={nlmeans_strength:.2f}:p=7:r=15"

    spatial = 0.8 + strength * 0.35
    temporal = 3.0 + strength * 0.9
    return f"hqdn3d={spatial:.2f}:{spatial:.2f}:{temporal:.2f}:{temporal:.2f}"


def sharpen_filter(strength: float) -> str | None:
    if strength <= 0:
        return None

    luma = min(1.2, strength * 0.12)
    chroma = min(0.45, strength * 0.04)
    return f"unsharp=5:5:{luma:.2f}:3:3:{chroma:.2f}"


def build_filters(args: argparse.Namespace) -> str:
    filters = list(PRESETS[args.preset]["filters"])

    if args.denoise is not None:
        filters = [item for item in filters if not (item.startswith("hqdn3d=") or item.startswith("nlmeans="))]
        custom_denoise = denoise_filter(args.denoise, args.high_quality_denoise)
        if custom_denoise:
            filters.insert(0, custom_denoise)

    if args.sharpen is not None:
        filters = [item for item in filters if not item.startswith("unsharp=")]
        custom_sharpen = sharpen_filter(args.sharpen)
        if custom_sharpen:
            filters.append(custom_sharpen)

    if args.deband and not any(item.startswith("deband=") for item in filters):
        filters.append("deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=true")

    if args.deshake and not any(item.startswith("deshake=") for item in filters):
        filters.insert(0, "deshake=rx=12:ry=12:edge=mirror")

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


def build_frame_encode_command(args: argparse.Namespace, frames_pattern: Path, fps: str) -> list[str]:
    preset = PRESETS[args.preset]
    speed_mode = SPEED_MODES[args.speed]
    codec = args.codec or speed_mode["codec"]

    command = [
        args.ffmpeg,
        "-hide_banner",
        "-y" if args.overwrite else "-n",
        "-framerate",
        fps,
        "-i",
        str(frames_pattern),
        "-i",
        str(args.input),
        "-map",
        "0:v:0",
    ]

    if args.no_audio:
        command.append("-an")
    else:
        command += ["-map", "1:a?", "-c:a", "copy" if args.copy_audio else "aac"]

    command += ["-c:v", codec]

    if codec in VIDEOTOOLBOX_CODECS:
        command += ["-b:v", args.bitrate, "-allow_sw", "true"]
    else:
        encoder_preset = args.encoder_preset or speed_mode["encoder_preset"]
        crf = args.crf if args.crf is not None else preset["crf"] + speed_mode["crf_delta"]
        command += ["-preset", encoder_preset, "-crf", str(crf)]

    command += ["-shortest", "-movflags", "+faststart", str(args.output)]
    return command


def shell_join(command: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in command)


def command_uses_videotoolbox(command: list[str]) -> bool:
    return any(codec in command for codec in VIDEOTOOLBOX_CODECS)


def run_checked(command: list[str]) -> int:
    print(shell_join(command))
    return subprocess.run(command).returncode


def detect_fps(args: argparse.Namespace) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return "30"

    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(args.input),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    fps = result.stdout.strip()
    if not fps or fps == "0/0":
        return "30"
    return fps


def run_ai_pipeline(args: argparse.Namespace) -> int:
    ai_executable = shutil.which(args.ai_executable) if Path(args.ai_executable).name == args.ai_executable else args.ai_executable
    if not ai_executable:
        if args.dry_run:
            ai_executable = args.ai_executable
        else:
            print(
                "AI executable not found. Install realesrgan-ncnn-vulkan or pass --ai-executable /path/to/binary.",
                file=sys.stderr,
            )
            return 2

    fps = detect_fps(args)
    with tempfile.TemporaryDirectory(prefix="video-refactor-ai-") as temp_dir:
        temp_path = Path(temp_dir)
        input_frames = temp_path / "frames"
        output_frames = temp_path / "ai_frames"
        input_frames.mkdir()
        output_frames.mkdir()

        extract_command = [
            args.ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(args.input),
            "-vf",
            build_filters(args),
            str(input_frames / "%08d.png"),
        ]
        ai_command = [
            ai_executable,
            "-i",
            str(input_frames),
            "-o",
            str(output_frames),
            "-n",
            args.ai_model,
            "-s",
            str(args.ai_scale),
            "-f",
            "png",
        ]
        encode_command = build_frame_encode_command(args, output_frames / "%08d.png", fps)

        for command in (extract_command, ai_command, encode_command):
            if args.dry_run:
                print(shell_join(command))
                continue

            result = run_checked(command)
            if result:
                return result

    return 0


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
    parser.add_argument("--denoise", type=strength_value, help="override denoise strength from 0 to 10")
    parser.add_argument("--sharpen", type=strength_value, help="override sharpening strength from 0 to 10")
    parser.add_argument("--deband", action="store_true", help="add debanding to reduce color banding")
    parser.add_argument("--deshake", action="store_true", help="add basic stabilization for shaky footage")
    parser.add_argument("--high-quality-denoise", action="store_true", help="use nlmeans for custom --denoise instead of hqdn3d")
    parser.add_argument("--crf", type=crf_value, help="quality value for x264/x265; lower is better and larger files")
    parser.add_argument("--speed", choices=sorted(SPEED_MODES), default="fast", help="encoding speed mode")
    parser.add_argument("--codec", help="ffmpeg video encoder; overrides --speed default codec")
    parser.add_argument("--encoder-preset", help="ffmpeg CPU encoder speed/quality preset")
    parser.add_argument("--bitrate", type=bitrate_value, default="12M", help="target bitrate for VideoToolbox hardware encoders")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="path to ffmpeg executable")
    parser.add_argument("--copy-audio", action=argparse.BooleanOptionalAction, default=True, help="copy audio without re-encoding")
    parser.add_argument("--no-audio", action="store_true", help="remove audio from the output")
    parser.add_argument("--ai-enhance", action="store_true", help="run optional Real-ESRGAN frame enhancement pipeline")
    parser.add_argument("--ai-executable", default="realesrgan-ncnn-vulkan", help="Real-ESRGAN compatible executable")
    parser.add_argument("--ai-model", default=REALESRGAN_DEFAULT_MODEL, help="AI model name passed to realesrgan-ncnn-vulkan")
    parser.add_argument("--ai-scale", type=scale_value, default=2, help="AI upscale factor")
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

    if args.ai_enhance and not shutil.which("ffprobe"):
        print("ffprobe is not installed or not in PATH; it is bundled with ffmpeg.", file=sys.stderr)
        return 2

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    validation_result = validate_args(args)
    if validation_result == 1:
        return 0
    if validation_result:
        return validation_result

    if args.ai_enhance:
        return run_ai_pipeline(args)

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
