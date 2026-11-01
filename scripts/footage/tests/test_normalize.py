from pathlib import Path

import pytest
import yaml
from normalize import (
    CUTS_FILE,
    SCENES,
    clip_name,
    clip_names,
    main,
    normalize_clip,
    probe,
    read_cuts,
    read_flow_yaml,
)

from spotcool_contracts.zones import Zone


def test_scene_zones_are_the_contract_zones():
    assert set(SCENES) == {zone.value for zone in Zone}
    assert len(clip_names()) == 13


@pytest.mark.parametrize(
    "name", ["sou_meeting.mp4", "wes_door_open.mp4", "cor_many.MOV", "wes_empty.mov"]
)
def test_clip_name_accepts_shot_list_names(name):
    assert clip_name(Path(name)) == f"{Path(name).stem}.mp4"


@pytest.mark.parametrize(
    "name",
    [
        "south_meeting.mp4",  # display name instead of the zone id
        "sou_lunch.mp4",  # unknown scene
        "nor_many.mp4",  # scene of another zone
        "SOU_meeting.mp4",
        "sou-meeting.mp4",
        "cor.mp4",
        "sou_meeting.avi",
    ],
)
def test_clip_name_rejects_other_names(name):
    with pytest.raises(ValueError):
        clip_name(Path(name))


def test_flow_yaml_reads_like_pyyaml(tmp_path):
    text = "# comment\ncor: {camera: 4, space: s2}  # note\n\nsou_meeting.mp4: {in: 3.5, out: 38}\n"
    path = tmp_path / "x.yaml"
    path.write_text(text, encoding="utf-8")
    assert read_flow_yaml(path) == yaml.safe_load(text)


@pytest.mark.parametrize(
    "text",
    [
        "cor:\n  camera: 4\n",  # block style
        "  cor: {camera: 4}\n",
        "cor: {camera:4}\n",
        "cor: {camera: yes}\n",  # PyYAML reads True
        "cor: {camera: 012}\n",  # PyYAML reads octal 10
        'cor: {space: "s2"}\n',
        "cor: {}\n",
        "cor: {camera: 4}\ncor: {camera: 5}\n",
    ],
)
def test_flow_yaml_rejects_anything_outside_the_subset(tmp_path, text):
    path = tmp_path / "x.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_flow_yaml(path)


def test_shipped_cuts_file_reads_like_pyyaml():
    text = CUTS_FILE.read_text(encoding="utf-8")
    assert read_flow_yaml(CUTS_FILE) == (yaml.safe_load(text) or {})
    read_cuts(CUTS_FILE)


def test_read_cuts(tmp_path):
    path = tmp_path / "cuts.yaml"
    path.write_text("sou_meeting.mp4: {in: 3.5, out: 38}\nwes_door_open.mp4: {in: 1}\n")
    assert read_cuts(path) == {"sou_meeting.mp4": (3.5, 38.0), "wes_door_open.mp4": (1.0, None)}
    assert read_cuts(tmp_path / "missing.yaml") == {}


@pytest.mark.parametrize(
    "line",
    [
        "sou_meting.mp4: {in: 1}",  # typo in the clip name
        "sou_meeting.mov: {in: 1}",  # keys are clip names, always .mp4
        "sou_meeting.mp4: {start: 1}",
        "sou_meeting.mp4: {in: 5, out: 2}",
        "sou_meeting.mp4: {in: -1}",
        "sou_meeting.mp4: {in: one}",
    ],
)
def test_read_cuts_rejects_bad_entries(tmp_path, line):
    path = tmp_path / "cuts.yaml"
    path.write_text(line + "\n")
    with pytest.raises(ValueError):
        read_cuts(path)


def test_normalize_clip_format_trim_and_privacy(make_clip, tmp_path):
    src = make_clip(tmp_path / "raw.mp4", seconds=4, size="640x480")  # 4:3, 25 fps, audio, GPS tag
    dst = normalize_clip(src, tmp_path / "clips" / "cor_few.mp4", 1.0, 3.0)
    info = probe(dst)
    (video,) = info["streams"]  # no audio stream
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1280, 720)
    assert (video["pix_fmt"], video["avg_frame_rate"]) == ("yuv420p", "30/1")
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.1)
    assert "location" not in info["format"].get("tags", {})
    data = dst.read_bytes()
    assert data.find(b"moov") < data.find(b"mdat")  # faststart
    assert [p.name for p in dst.parent.iterdir()] == ["cor_few.mp4"]


def test_main_normalizes_shot_list_names_and_rejects_others(make_clip, tmp_path, capsys):
    raw, clips = tmp_path / "raw", tmp_path / "clips"
    make_clip(raw / "sou_meeting.mp4", seconds=3)
    (raw / "south_complaint.m4a").write_bytes(b"")  # voice memos are not clips
    cuts = tmp_path / "cuts.yaml"
    cuts.write_text("sou_meeting.mp4: {in: 0.5, out: 1.5}\n")
    assert main([str(raw), str(clips), "--cuts", str(cuts)]) == 0
    assert float(probe(clips / "sou_meeting.mp4")["format"]["duration"]) == pytest.approx(
        1.0, abs=0.1
    )
    assert "Not filmed yet: cor_empty.mp4" in capsys.readouterr().out

    make_clip(raw / "south_meeting.mp4", seconds=1)
    assert main([str(raw), str(clips), "--cuts", str(cuts)]) == 1
    assert "south_meeting.mp4: expected <zone>_<scene>.mp4" in capsys.readouterr().err
