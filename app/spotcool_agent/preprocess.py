import asyncio
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np


ANALYSIS_HEIGHT = 720
# Same encoding as spotcool_contracts.occupants.VOICE_FORMAT and scripts/footage/extract_audio.py.
AUDIO_ARGS = ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
VIDEO_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_SIZE = re.compile(r"Stream #[^\n]*?Video:[^\n]*?\b(\d{2,5})x(\d{2,5})\b")
_AUDIO_STREAM = re.compile(r"Stream #[^\n]*?Audio:")


def ffmpeg_exe() -> str:
    """A system ffmpeg when one is on PATH, else the binary bundled with imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args: Sequence[str], timeout_s: float, stdin: Optional[bytes] = None) -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    # subprocess.run kills ffmpeg on timeout; asyncio.wait_for around to_thread could not.
    result = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout_s)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise RuntimeError(f"ffmpeg exited with {result.returncode}: {message}")


@dataclass
class ClipProbe:
    path: Path
    duration_s: float
    width: int
    height: int
    has_audio: bool


def probe(path: Path, timeout_s: float = 60.0) -> ClipProbe:
    """Duration from ffmpeg's Duration line (robust to variable frame rate), size and audio."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, timeout=timeout_s
    )
    info = result.stderr.decode("utf-8", errors="replace")
    duration: Optional[float] = None
    match = _DURATION.search(info)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    size = _VIDEO_SIZE.search(info)
    width, height = (int(size.group(1)), int(size.group(2))) if size else (0, 0)
    if duration is None or not width:
        capture = cv2.VideoCapture(str(path))
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if duration is None and fps > 0:
                duration = frames / fps
            if not width:
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        finally:
            capture.release()
    if not duration or duration <= 0 or not width:
        raise RuntimeError(f"Could not probe a video stream in {path}")
    return ClipProbe(path, duration, width, height, bool(_AUDIO_STREAM.search(info)))


def plan_segments(
    duration_s: float, segment_s: Optional[float], min_tail_s: float = 10.0
) -> list[tuple[float, float]]:
    """Split a clip into consecutive (start, length) turns; a short tail joins the last turn."""
    if not segment_s or segment_s <= 0 or duration_s <= segment_s:
        return [(0.0, duration_s)]
    segments: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_s - 1e-6:
        length = min(segment_s, duration_s - start)
        segments.append((start, length))
        start += length
    if len(segments) > 1 and segments[-1][1] < min_tail_s:
        tail_length = segments.pop()[1]
        last_start, last_length = segments.pop()
        segments.append((last_start, last_length + tail_length))
    return segments


def encode_1fps(
    src: Path, dst: Path, start_s: float, duration_s: float, source_height: int, timeout_s: float
) -> Path:
    """1 FPS H.264 at up to 720p: the video SAM tracks, so its frame N is SAM's frame N."""
    height = min(ANALYSIS_HEIGHT, source_height - source_height % 2) or ANALYSIS_HEIGHT
    args = [
        "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}", "-i", str(src),
        "-map", "0:v:0", "-an", "-map_metadata", "-1",
        "-vf", f"fps=1,scale=-2:{height},format=yuv420p",
        *VIDEO_ARGS, "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    _run_ffmpeg(args, timeout_s)
    return dst


def extract_audio(
    src: Path, dst: Path, start_s: float = 0.0, duration_s: Optional[float] = None,
    timeout_s: float = 300.0,
) -> Path:
    """The first audio stream as a 16 kHz mono s16le WAV."""
    trim = ["-ss", f"{start_s:.3f}"] + (["-t", f"{duration_s:.3f}"] if duration_s else [])
    _run_ffmpeg([*trim, "-i", str(src), "-map", "0:a:0", "-map_metadata", "-1", *AUDIO_ARGS, str(dst)], timeout_s)
    return dst


def encode_frames(
    frames: Sequence[np.ndarray], dst: Path, fps: float = 1.0, timeout_s: float = 300.0
) -> Path:
    """Encode in-memory BGR frames (all the same size) to H.264, e.g. polled camera snapshots."""
    height, width = frames[0].shape[:2]
    data = b"".join(np.ascontiguousarray(frame, dtype=np.uint8).tobytes() for frame in frames)
    args = [
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:g}",
        "-i", "-", "-an", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        *VIDEO_ARGS, "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    _run_ffmpeg(args, timeout_s, stdin=data)
    return dst


def decode_frames(path: Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Could not open video: {path}")
        frames = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
        return frames
    finally:
        capture.release()


def motion_scores(
    frames: Sequence[np.ndarray],
    boxes_by_frame: dict[int, list[tuple[float, float, float, float]]],
    small_width: int = 160,
) -> list[float]:
    """Mean absolute pixel change inside person boxes between consecutive frames."""
    scores = [0.0] * len(frames)
    previous: Optional[np.ndarray] = None
    for index, frame in enumerate(frames):
        height, width = frame.shape[:2]
        small_height = max(1, round(small_width * height / width))
        small = cv2.resize(frame, (small_width, small_height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        boxes = boxes_by_frame.get(index) or []
        if previous is not None and boxes:
            diff = np.abs(gray - previous)
            mask = np.zeros(diff.shape, dtype=bool)
            for x1, y1, x2, y2 in boxes:
                left, top = int(x1 * small_width), int(y1 * small_height)
                right = max(left + 1, math.ceil(x2 * small_width))
                bottom = max(top + 1, math.ceil(y2 * small_height))
                mask[top:bottom, left:right] = True
            scores[index] = float(diff[mask].mean())
        previous = gray
    return scores


def select_keyframes(
    n_frames: int,
    scores: Optional[Sequence[float]] = None,
    count: int = 6,
    motion_picks: int = 2,
    min_motion_gap: int = 3,
) -> list[int]:
    """Evenly spaced keyframes plus the strongest motion peaks (which catch brief gestures)."""
    if n_frames <= count:
        return list(range(n_frames))
    use_motion = bool(scores) and any(score > 0 for score in scores)
    even_count = count - motion_picks if use_motion else count
    picks = {min(n_frames - 1, int((j + 0.5) * n_frames / even_count)) for j in range(even_count)}
    if use_motion:
        chosen: list[int] = []
        for index in sorted(range(n_frames), key=lambda i: scores[i], reverse=True):
            if len(chosen) == motion_picks or scores[index] <= 0:
                break
            if index in picks or any(abs(index - other) < min_motion_gap for other in chosen):
                continue
            chosen.append(index)
        picks.update(chosen)
    for index in [min(n_frames - 1, int((j + 0.5) * n_frames / count)) for j in range(count)]:
        if len(picks) >= count:
            break
        picks.add(index)
    index = 0
    while len(picks) < count:
        picks.add(index)
        index += 1
    return sorted(picks)


@dataclass
class TurnInput:
    """One turn's media: 1 FPS frames plus the matching video, audio and typed complaints."""

    label: str
    frames: list[np.ndarray]
    video_path: Path
    duration_s: float
    fps: float = 1.0
    audio_paths: list[Path] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    source: Optional[str] = None
    segment_start_s: float = 0.0
    gap_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    preprocess_s: float = 0.0

    @property
    def frame_size(self) -> tuple[int, int]:
        height, width = self.frames[0].shape[:2]
        return (width, height)


async def prepare_segment(
    clip: ClipProbe,
    workdir: Path,
    start_s: float,
    duration_s: float,
    *,
    label: str,
    attached_audio: Optional[Path] = None,
    texts: Sequence[str] = (),
    gap_s: float = 0.0,
    timeout_s: float = 300.0,
) -> TurnInput:
    started = time.perf_counter()
    stem = f"seg_{int(start_s * 1000):08d}"
    video = workdir / f"{stem}_1fps.mp4"
    jobs = [asyncio.to_thread(encode_1fps, clip.path, video, start_s, duration_s, clip.height, timeout_s)]
    audio_paths: list[Path] = []
    if clip.has_audio:
        audio_paths.append(workdir / f"{stem}_clip.wav")
        jobs.append(asyncio.to_thread(extract_audio, clip.path, audio_paths[-1], start_s, duration_s, timeout_s))
    if attached_audio is not None:
        audio_paths.append(workdir / f"{stem}_attached.wav")
        jobs.append(asyncio.to_thread(extract_audio, attached_audio, audio_paths[-1], 0.0, None, timeout_s))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    if isinstance(results[0], BaseException):
        raise results[0]
    warnings: list[str] = []
    for path, result in zip(list(audio_paths), results[1:]):
        if isinstance(result, BaseException):
            warnings.append(f"audio {path.name}: {result}")
            audio_paths.remove(path)
    frames = await asyncio.to_thread(decode_frames, video)
    if not frames:
        raise RuntimeError(f"No frames decoded from {clip.path} at {start_s:.1f}s")
    return TurnInput(
        label=label,
        frames=frames,
        video_path=video,
        duration_s=duration_s,
        audio_paths=audio_paths,
        texts=list(texts),
        source=str(clip.path),
        segment_start_s=start_s,
        gap_s=gap_s,
        warnings=warnings,
        preprocess_s=round(time.perf_counter() - started, 2),
    )
