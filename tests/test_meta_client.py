import asyncio
import email
import json

from app.spotcool_agent.meta_client import MockBackend, _multipart
from app.spotcool_agent.perception import build_tracks, parse_sam_output


def test_multipart_matches_the_asr_contract():
    request = {"model": "muse-voice-transcribe-1.0", "mode": "DIARIZATION", "audioEncoding": "WAV"}
    body, content_type = _multipart(
        [("request", None, json.dumps(request).encode(), "application/json"), ("audio", "clip.wav", b"RIFF0000WAVE", "audio/wav")]
    )
    message = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    parts = {part.get_param("name", header="content-disposition"): part for part in message.get_payload()}
    assert json.loads(parts["request"].get_payload(decode=True)) == request
    assert parts["request"].get_content_type() == "application/json"
    assert parts["audio"].get_filename() == "clip.wav" and parts["audio"].get_payload(decode=True) == b"RIFF0000WAVE"


def test_mock_sam_output_parses_into_one_track():
    backend = MockBackend()
    backend.begin_turn(1, 12, (640, 360))
    result = asyncio.run(backend.sam_track("file-mock", "person"))
    tracks = build_tracks(parse_sam_output(result.text, "person"), 12, 0.4, [])
    assert len(tracks) == 1 and len(tracks[0].detections) == 12
    assert asyncio.run(backend.sam_track("file-mock", "open door")).text == ""
