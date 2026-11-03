import pytest
import voice


def test_split_cuts_at_the_longest_inner_pauses_and_trims_the_ends():
    pauses = [(0.0, 0.5), (3.0, 3.4), (6.0, 8.0), (10.0, 10.5), (14.0, 15.0)]
    assert voice.split(pauses, 15.0, 2) == [(0.5, 6.0), (8.0, 14.0)]
    assert voice.split(pauses, 15.0, 3) == [(0.5, 6.0), (8.0, 10.0), (10.5, 14.0)]


def test_split_needs_a_pause_between_each_paragraph():
    with pytest.raises(voice.NarrationError, match="leave a pause"):
        voice.split([(2.0, 4.0)], 10.0, 3)


def test_place_starts_each_segment_at_its_beat(tiny_board):
    placements = voice.place(tiny_board(), [(0.5, 1.5), (3.0, 4.2)])
    assert [(p.beat_id, p.at_s, p.length_s) for p in placements] == [
        ("intro", voice.LEAD_S, 1.0),
        ("outro", 2 + voice.LEAD_S, pytest.approx(1.2)),
    ]


def test_place_names_every_segment_that_runs_past_its_beat(tiny_board):
    with pytest.raises(voice.NarrationError, match=r"intro \(1.9 s, room for 1.3 s\); outro"):
        voice.place(tiny_board(), [(0.0, 1.9), (2.0, 4.0)])


def test_estimate_paces_each_paragraph_and_stops_at_its_beat(tiny_board):
    placements = voice.estimate(tiny_board(), {"intro": 2, "outro": 40})
    assert placements[0].length_s == pytest.approx(2 / voice.ESTIMATE_WPM * 60)
    assert placements[1].end_s == pytest.approx(4 - voice.TAIL_S)


def test_write_track_mixes_each_segment_onto_a_silent_base(tiny_board, fake_run, tmp_path):
    placements = voice.place(tiny_board(), [(0.5, 1.5), (3.0, 4.2)])
    voice.write_track(tmp_path / "n.wav", placements, 4.0, tmp_path / "voice.wav")
    graph = fake_run.calls[0][fake_run.calls[0].index("-filter_complex") + 1]
    assert "anullsrc=r=48000:cl=mono,atrim=0:4.0[base]" in graph
    assert "[s0]atrim=0.5:1.5,asetpts=PTS-STARTPTS,adelay=400:all=1[d0]" in graph
    assert "adelay=2400:all=1[d1]" in graph
    assert "[base][d0][d1]amix=inputs=3:normalize=0:duration=first[out]" in graph


def test_placements_round_trip_as_json(tiny_board, tmp_path):
    placements = voice.place(tiny_board(), [(0.5, 1.5), (3.0, 4.2)])
    assert voice.load(voice.save(placements, tmp_path / "timing.json")) == placements


def test_a_placed_track_starts_on_time(ffmpeg, tiny_board, tmp_path):
    import media

    # Two tones with a 2 s pause, like a two-paragraph recording.
    recording = tmp_path / "narration.wav"
    media.ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1.2", "-filter_complex",
        "[1:a]atrim=0:2[gap];[0:a][gap][2:a]concat=n=3:v=0:a=1[out]", "-map", "[out]", str(recording),
    )  # fmt: skip
    board = tiny_board()
    placements = voice.placements_for(recording, board)
    track = voice.write_track(recording, placements, board.total_s, tmp_path / "voice.wav")
    assert media.duration(track) == pytest.approx(4.0, abs=0.01)
    quiet = media.silences(track, voice.PAUSE_DB, 0.2)
    assert quiet[0][0] == 0.0 and quiet[0][1] == pytest.approx(voice.LEAD_S, abs=0.03)
