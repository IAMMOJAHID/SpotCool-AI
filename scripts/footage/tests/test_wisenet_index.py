import json

import yaml
from normalize import SCENES, probe, read_flow_yaml
from wisenet_index import (
    MAPPING_FILE,
    MAX_S,
    best_run,
    load_manual_annotations,
    main,
    people_per_second,
    pick_segments,
    write_index,
)

from spotcool_contracts.zones import ZONES, Zone


def detection(person_id, algorithm="groundtruth"):
    return {
        "class": "person",
        "imageAlgorithm": algorithm,
        "xywh": [10, 10, 40, 90],
        "id": person_id,
        "regionOfInterest": "null",
        "visualDescriptors": [0.1, 0.2],
    }


def detections_file(video, frames, people, algorithm="groundtruth"):
    """A PD-MAN/PD-AUT file in the paper's Listing 2 shape."""
    return {
        "video": video,
        "resolution": [{"width": 160.0, "height": 120.0}],
        "frames": [
            {
                "frameNumber": n,
                "deviceID": "SmartCamera_4",
                "inXSDDateTime": "2017-05-20T12:15:05.464Z",
                "detections": [detection(i, algorithm) for i in range(people)],
            }
            for n in frames
        ],
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_camera_ids_match_the_contract():
    for zone in SCENES:
        assert f"cam-{zone}" == ZONES[Zone(zone)].camera_id


def test_shipped_mapping_reads_like_pyyaml_and_uses_five_cameras():
    mapping = read_flow_yaml(MAPPING_FILE)
    assert mapping == yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8"))
    assert set(mapping) == set(SCENES)
    assert len({target["camera"] for target in mapping.values()}) == 5


def test_best_run():
    counts = [0] * 12 + [5] * 30 + [1] * 5
    assert best_run(counts, 0, 0) == (0, 12)
    assert best_run(counts, 4, None) == (12, 42)
    assert best_run(counts, 1, 2) is None  # 5 s is shorter than MIN_S
    assert best_run([0] * 100, 0, 0) == (0, MAX_S)


def test_people_per_second_counts_distinct_ids_and_missing_frames_as_empty():
    frames = detections_file("v.avi", range(25), 2)["frames"]
    frames += [
        {"frameNumber": n, "detections": [detection(3), detection(3)]} for n in range(50, 75)
    ]
    assert people_per_second(frames, 25.0, 3.0) == [2, 0, 1]


def test_pick_segments_takes_the_longest_run_per_scene():
    videos = {
        4: [("video1_4.mp4", [0] * 12 + [5] * 30), ("video5_4.mp4", [0] * 20)],
        3: [("video5_3.mp4", [4] * 15)],
    }
    mapping = {
        "cor": {"camera": 4, "space": "s2"},
        "sou": {"camera": 3, "space": "s3"},
        "eas": {"camera": 6, "space": "s4"},  # no videos extracted for camera 6
    }
    assert pick_segments(videos, mapping) == {
        "cam-cor": {
            "empty": {"file": "video5_4.mp4", "start_s": 0.0, "end_s": 20.0, "people": 0},
            "many": {"file": "video1_4.mp4", "start_s": 12.0, "end_s": 42.0, "people": 5},
        },
        "cam-sou": {
            "meeting": {"file": "video5_3.mp4", "start_s": 0.0, "end_s": 15.0, "people": 4}
        },
    }


def test_index_yaml_round_trip(tmp_path):
    index = {
        "cam-cor": {"many": {"file": "video5_4.mp4", "start_s": 12.0, "end_s": 42.0, "people": 5}},
        "cam-wes": {
            "empty": {"file": "sub dir/video2_1.mp4", "start_s": 0.0, "end_s": 40.0, "people": 0}
        },
    }
    write_index(tmp_path / "index.yaml", index)
    loaded = yaml.safe_load((tmp_path / "index.yaml").read_text(encoding="utf-8"))
    assert loaded == {"version": 1, "dataset": "wisenet", "cameras": index}
    write_index(tmp_path / "empty.yaml", {})
    assert yaml.safe_load((tmp_path / "empty.yaml").read_text(encoding="utf-8"))["cameras"] == {}


def test_load_manual_annotations_skips_automatic_and_other_files(tmp_path):
    write_json(tmp_path / "manual" / "a.json", detections_file("video5_4.avi", [0], 1))
    write_json(tmp_path / "manual" / "b.json", detections_file("video5_6.avi", [], 0))
    write_json(tmp_path / "auto" / "a.json", detections_file("video5_4.avi", [0, 1], 1, "YOLO"))
    write_json(tmp_path / "tracking.json", {"set": 5, "tracks": []})
    (tmp_path / "notes.json").write_text("not json", encoding="utf-8")
    annotations = load_manual_annotations(tmp_path)
    assert set(annotations) == {"video5_4.avi", "video5_6.avi"}
    assert [f["frameNumber"] for f in annotations["video5_4.avi"]] == [0]


def test_main_indexes_and_normalizes_a_synthetic_wisenet_set(make_clip, tmp_path, capsys):
    root, out = tmp_path / "wisenet", tmp_path / "out"
    make_clip(root / "set5" / "video5_4.avi", seconds=24, size="160x120", audio=False)
    make_clip(root / "set5" / "video5_1.avi", seconds=12, size="160x120", audio=False)  # unmapped
    # Seconds 0-11 empty, 12-23 four people; the automatic file sees one person everywhere.
    write_json(root / "pd-man" / "v.json", detections_file("video5_4.avi", range(300, 600), 4))
    write_json(root / "pd-aut" / "v.json", detections_file("video5_4.avi", range(600), 1, "SSD"))
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("cor: {camera: 4, space: s2}\n", encoding="utf-8")

    assert main([str(root), "--out", str(out), "--mapping", str(mapping)]) == 0
    index = yaml.safe_load((out / "index.yaml").read_text(encoding="utf-8"))
    assert index["cameras"] == {
        "cam-cor": {
            "empty": {"file": "video5_4.mp4", "start_s": 0.0, "end_s": 12.0, "people": 0},
            "many": {"file": "video5_4.mp4", "start_s": 12.0, "end_s": 24.0, "people": 4},
        }
    }
    (video,) = probe(out / "video5_4.mp4")["streams"]
    assert (video["width"], video["height"], video["avg_frame_rate"]) == (1280, 720, "30/1")
    assert sorted(p.name for p in out.iterdir()) == ["index.yaml", "video5_4.mp4"]
    assert "cam-cor few: none" in capsys.readouterr().out
