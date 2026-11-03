"""Every external program the video scripts run, behind one function the tests replace.

``run`` executes a command and returns its output; the tests monkeypatch ``media.run``. The
helpers here wrap ffmpeg and ffprobe, and parse what they print (pure functions, tested on
captured output).
"""

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

FFMPEG = ("ffmpeg", "-hide_banner", "-nostdin", "-y")
TARGET_LUFS = -14.0
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=20"
"""The target: -14 LUFS integrated, -1.5 dBTP; a wide LRA so speech is normalised linearly."""
VOICE_CHAIN = "highpass=f=70,acompressor=threshold=-26dB:ratio=4:attack=3:release=150:knee=6"
"""The narration's processing: no rumble or DC, and its peaks brought nearer its body."""
LIMITER = "alimiter=limit=0.75:attack=2:release=60:level=false"
"""A peak limiter at -2.5 dBFS, so the final linear ``loudnorm`` has headroom to -1.5 dBTP."""


class ToolError(RuntimeError):
    """A program the scripts ran failed."""


def run(
    args: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a program to its end and return what it printed.

    Args:
        args: The program and its arguments (no shell).
        cwd: The working directory.
        env: The environment, or ``None`` for this process's.

    Raises:
        ToolError: If it is missing or exits non-zero; the message ends with its last output.
    """
    try:
        done = subprocess.run(
            list(args),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"{args[0]} is not installed or not on PATH") from exc
    if done.returncode:
        tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-12:])
        raise ToolError(f"{Path(args[0]).name} exited {done.returncode}:\n{tail}")
    return done


def ffmpeg(*args: str, cwd: Path | None = None) -> str:
    """Run ffmpeg (overwriting outputs) and return its log, where its measurements are."""
    return run([*FFMPEG, *args], cwd=cwd).stderr


def probe(path: Path) -> dict[str, Any]:
    """ffprobe's description of a media file: its ``format`` and ``streams``."""
    args = ["-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    return json.loads(run(["ffprobe", *args, str(path)]).stdout)


def duration(path: Path) -> float:
    """A media file's length in seconds."""
    return float(probe(path)["format"]["duration"])


def silences(path: Path, noise_db: float, min_s: float) -> list[tuple[float, float]]:
    """The stretches of an audio file quieter than ``noise_db`` for at least ``min_s``."""
    detect = f"silencedetect=noise={noise_db}dB:d={min_s}"
    log = ffmpeg("-i", str(path), "-af", detect, "-f", "null", "-")
    return parse_silences(log, duration(path))


def parse_silences(log: str, total_s: float) -> list[tuple[float, float]]:
    """The silences in ffmpeg's ``silencedetect`` log; one still open at the end ends there."""
    found: list[tuple[float, float]] = []
    start: float | None = None
    for key, value in re.findall(r"silence_(start|end): (-?[\d.]+)", log):
        if key == "start":
            start = max(0.0, float(value))
        elif start is not None:
            found.append((start, float(value)))
            start = None
    if start is not None:
        found.append((start, total_s))
    return found


def loudness(path: Path, prefix: str = "") -> dict[str, float]:
    """``loudnorm``'s first-pass measurement of an audio file, for a second, linear pass.

    Args:
        path: The audio.
        prefix: Filters to apply first, ending with a comma.

    Returns:
        ``input_i`` (LUFS), ``input_tp`` (dBTP), ``input_lra`` (LU), ``input_thresh`` and
        ``target_offset``.
    """
    log = ffmpeg("-i", str(path), "-af", f"{prefix}{LOUDNORM}:print_format=json", "-f", "null", "-")
    return parse_loudnorm(log)


def parse_loudnorm(log: str) -> dict[str, float]:
    """The JSON block ``loudnorm`` prints with ``print_format=json``.

    Raises:
        ToolError: If the log holds none.
    """
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", log)
    if not match:
        raise ToolError("loudnorm printed no measurement")
    data = json.loads(match.group(0))
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    return {key: float(data[key]) for key in keys}


def loudnorm_second_pass(measured: dict[str, float]) -> str:
    """The ``loudnorm`` filter that brings audio measured by ``loudness`` to the target."""
    return (
        f"{LOUDNORM}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )


def limiter_gain(path: Path, target: float = TARGET_LUFS - 0.3, tries: int = 8) -> float:
    """The gain into ``LIMITER`` that brings an audio file to about ``target`` LUFS.

    Speech peaks far above its loudness, so plain gain would clip before reaching the target;
    the limiter takes the peaks, and the gain is found by measuring (the limiter takes back part
    of each step). The same file always gives the same gain.
    """
    gain = target - loudness(path)["input_i"]
    for _ in range(tries):
        got = loudness(path, f"volume={gain:.2f}dB,{LIMITER},")["input_i"]
        if abs(got - target) <= 0.2:
            break
        gain += (target - got) * 1.5
    return round(gain, 2)


def ebur128(path: Path) -> tuple[float, float]:
    """A file's integrated loudness (LUFS) and true peak (dBTP), by ffmpeg's EBU R128 meter."""
    meter = ["-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"]
    log = ffmpeg("-nostats", "-i", str(path), *meter)
    return parse_ebur128(log)


def parse_ebur128(log: str) -> tuple[float, float]:
    """The integrated loudness and true peak from ``ebur128``'s summary.

    Raises:
        ToolError: If the summary is missing.
    """
    summary = log[log.rfind("Summary:") :]
    loud = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if not loud or not peak:
        raise ToolError("ebur128 printed no summary")
    return float(loud.group(1)), float(peak.group(1))
