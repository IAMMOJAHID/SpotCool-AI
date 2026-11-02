from app.spotcool_agent.perception import build_tracks, occupancy_count, parse_sam_output, strip_masks

MASK = "<|mask;x=0;y=0;data=27,60,~!!!!M!0c*$[]`|>"


def box(frame: int, obj: int, x1: int, y1: int, x2: int, y2: int, extra: str = "") -> str:
    return f"<{frame}f>{obj}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w=100;h=100{extra}|>"


def test_several_records_per_line_with_mask_payloads():
    line = (
        f"<0f>0<|box;x1=353;y1=382;x2=453;y2=427;w=537;h=561|>{MASK},"
        f"4<|box;x1=10;y1=20;x2=39;y2=59;w=537;h=561;c=0.8|>{MASK}"
    )
    result = parse_sam_output(line, "person")
    assert [d.object_id for d in result.detections] == ["0", "4"]
    first = result.detections[0]
    assert (first.x1, first.x2) == (353 / 537, 454 / 537)  # inclusive x2 -> half-open
    assert first.y2 == 428 / 561
    assert first.has_mask and first.confidence is None
    assert result.detections[1].confidence == 0.8
    assert result.frame_size == (537, 561)
    assert result.warnings == []


def test_out_of_order_skipped_and_duplicate_frames():
    text = "\n".join(
        [
            box(5, 1, 0, 0, 9, 9, ";c=0.5"),
            box(0, 1, 0, 0, 9, 9, ";c=0.9"),
            box(5, 1, 10, 10, 19, 19, ";c=0.7"),  # same frame again: the higher confidence wins
        ]
    )
    result = parse_sam_output(text, "person")
    assert [(d.frame, d.object_id) for d in result.detections] == [(0, "1"), (5, "1")]
    assert result.detections[1].x1 == 0.10


def test_fields_are_read_by_name():
    detection = parse_sam_output("  <2f>7<|box; h=50 ;w=100;y2=29;x2=19;y1=10;x1=0|>", "person").detections[0]
    assert (detection.frame, detection.object_id, detection.y1) == (2, "7", 0.2)


def test_malformed_input_warns_without_failing():
    text = "\n".join(
        [
            "garbage",
            "<1f>3<|box;x1=a;y1=0;x2=9;y2=9;w=100;h=100|>",
            "<2f>3<|box;x1=0;y1=0;x2=9;y2=9;w=100;h=100;c=2|>",
            "<3f>",
        ]
    )
    result = parse_sam_output(text, "person")
    assert [d.frame for d in result.detections] == [2]
    assert result.detections[0].confidence is None  # an invalid c is ignored, the box kept
    assert len(result.warnings) == 4


def test_frame_token_only_counts_at_line_start():
    text = "<1f>2<|box;x1=0;y1=0;x2=9;y2=9;w=100;h=100|><|mask;x=0;y=0;data=4,4,~<9f>!!|>"
    assert [d.frame for d in parse_sam_output(text, "person").detections] == [1]


def test_strip_masks_keeps_boxes():
    stripped = strip_masks(box(0, 0, 1, 2, 3, 4) + MASK)
    assert "data=|>" in stripped and "~!!!!" not in stripped
    assert parse_sam_output(stripped, "person").detections[0].x2 == 0.04


def test_tracks_drop_flicker_and_low_confidence_and_merge_id_switches():
    lines = [box(f, 1, 10, 10, 29, 59, ";c=0.9") for f in range(10)]
    lines += [box(f, 8, 11, 10, 30, 59, ";c=0.9") for f in range(11, 20)]  # ID switch after a 1-frame gap
    lines += [box(3, 5, 70, 10, 89, 59, ";c=0.9")]  # one-frame flicker
    lines += [box(f, 9, 50, 10, 69, 59, ";c=0.2") for f in range(20)]  # below the confidence floor
    warnings: list[str] = []
    tracks = build_tracks(parse_sam_output("\n".join(lines), "person"), 20, 0.4, warnings)
    assert [t.track_id for t in tracks] == ["1"]
    assert tracks[0].merged_ids == ["8"] and len(tracks[0].detections) == 19
    assert occupancy_count(tracks, 20) == 1


def test_detections_beyond_the_clip_are_dropped_with_a_warning():
    warnings: list[str] = []
    text = "\n".join(box(f, 1, 10, 10, 29, 59) for f in range(12))
    tracks = build_tracks(parse_sam_output(text, "person"), 10, 0.4, warnings)
    assert len(tracks[0].detections) == 10
    assert any("beyond frame 9" in w for w in warnings)
