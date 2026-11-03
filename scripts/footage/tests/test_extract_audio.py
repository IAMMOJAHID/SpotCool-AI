import wave

import pytest
from extract_audio import CHANNELS, CODEC, SAMPLE_RATE_HZ, main

from spotcool_contracts.occupants import VOICE_FORMAT


def test_settings_match_the_voice_contract():
    assert VOICE_FORMAT.container == "wav"
    assert (SAMPLE_RATE_HZ, CHANNELS) == (VOICE_FORMAT.sample_rate_hz, VOICE_FORMAT.channels)
    assert CODEC == f"pcm_{VOICE_FORMAT.sample_format}"


def test_extract_audio_writes_a_voice_format_wav(make_clip, tmp_path):
    src = make_clip(tmp_path / "sou_complaint.mp4", seconds=4)  # 44.1 kHz stereo AAC
    out = tmp_path / "audio"
    options = ["--name", "south_complaint", "--out-dir", str(out)]
    assert main([str(src), "--start", "1", "--end", "3", *options]) == 0
    # The wave module reads little-endian PCM only, so opening the file checks "s16le" too.
    with wave.open(str(out / "south_complaint.wav"), "rb") as wav:
        assert wav.getframerate() == VOICE_FORMAT.sample_rate_hz
        assert wav.getnchannels() == VOICE_FORMAT.channels
        assert wav.getsampwidth() == 2  # 16-bit
        assert wav.getnframes() == pytest.approx(2 * VOICE_FORMAT.sample_rate_hz, rel=0.02)


def test_extract_audio_rejects_an_empty_range(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path / "x.m4a"), "--start", "3", "--end", "3"])
