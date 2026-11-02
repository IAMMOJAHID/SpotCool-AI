import asyncio
import subprocess
import wave

import numpy as np
import pytest

from app.spotcool_agent.preprocess import (
    decode_frames,
    encode_frames,
    ffmpeg_exe,
    plan_segments,
    prepare_segment,
    probe,
)


def make_clip(path, seconds: int, audio: bool) -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-c:a", "aac"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def clip_with_audio(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    make_clip(path, 12, audio=True)
    return path


def test_probe_reads_duration_size_and_audio(clip_with_audio, tmp_path):
    clip = probe(clip_with_audio)
    assert clip.has_audio and abs(clip.duration_s - 12.0) < 0.2 and (clip.width, clip.height) == (320, 180)
    silent = tmp_path / "silent.mp4"
    make_clip(silent, 3, audio=False)
    assert not probe(silent).has_audio


def test_prepare_segment_gives_aligned_1fps_frames_and_voice_format_audio(clip_with_audio, tmp_path):
    turn_input = asyncio.run(prepare_segment(probe(clip_with_audio), tmp_path, 2.0, 8.0, label="clip[2-10s]"))
    assert len(turn_input.frames) in (8, 9) and turn_input.frame_size == (320, 180)
    assert len(decode_frames(turn_input.video_path)) == len(turn_input.frames)
    with wave.open(str(turn_input.audio_paths[0])) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert abs(wav.getnframes() / wav.getframerate() - 8.0) < 0.1
    assert turn_input.preprocess_s > 0


def test_plan_segments():
    assert plan_segments(98.0, 60.0) == [(0.0, 60.0), (60.0, 38.0)]
    assert plan_segments(30.0, 60.0) == [(0.0, 30.0)]
    assert plan_segments(125.0, 60.0) == [(0.0, 60.0), (60.0, 65.0)]  # a 5 s tail joins the last turn
    assert plan_segments(98.0, None) == [(0.0, 98.0)]


def test_encode_frames_round_trip(tmp_path):
    frames = [np.full((90, 160, 3), 20 * i, dtype=np.uint8) for i in range(5)]
    decoded = decode_frames(encode_frames(frames, tmp_path / "snapshots.mp4"))
    assert len(decoded) == 5 and decoded[0].shape == (90, 160, 3)
