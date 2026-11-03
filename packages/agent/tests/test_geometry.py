from pathlib import Path

import pytest

from spotcool_agent.domain.geometry import Detection, Detections, parse_sam_output

FIXTURE = Path(__file__).parent / "fixtures" / "meva_cafe_1280_humans.txt"
"""SAM 3.1's real answer for one person in a MEVA cafe frame (AG0, text only)."""


def test_the_real_fixture_gives_one_person_in_fractions_of_the_image() -> None:
    frames = parse_sam_output(FIXTURE.read_text(encoding="utf-8"))
    assert list(frames) == [0]
    (person,) = frames[0]
    assert person.object_id == 0
    assert person.as_list() == [
        round(653 / 1280, 4),
        round(84 / 714, 4),
        round(675 / 1280, 4),
        round(181 / 714, 4),
    ]
    assert person.confidence == pytest.approx(0.8476, abs=1e-4)


def box(object_id: int, x1: int, x2: int, c: float = 0.9) -> str:
    return (
        f"{object_id}<|box;x1={x1};y1=10;x2={x2};y2=90;w=100;h=100;c={c}|><|mask;x=0;y=0;data=xx|>"
    )


def test_frames_are_split_and_a_repeated_id_keeps_its_first_box() -> None:
    text = f"<0f>{box(0, 10, 20)},{box(0, 50, 60)},{box(1, 30, 40)}<1f>{box(0, 70, 80)}"
    frames = parse_sam_output(text)
    assert [(d.object_id, d.x1) for d in frames[0]] == [(0, 0.1), (1, 0.3)]
    assert [(d.object_id, d.x1) for d in frames[1]] == [(0, 0.7)]


def test_boxes_are_clipped_to_the_image_and_empty_ones_dropped() -> None:
    text = f"<0f>{box(0, -5, 120)},{box(1, 40, 40)}"
    (clipped,) = parse_sam_output(text)[0]
    assert (clipped.x1, clipped.x2) == (0.0, 1.0)


def test_the_legacy_format_is_read_without_ids_or_confidence() -> None:
    frames = parse_sam_output(
        "x1=10;y1=20;x2=30;y2=40;w=100;h=200 x1=50;y1=20;x2=60;y2=40;w=100;h=200"
    )
    assert [(d.object_id, d.as_list(), d.confidence) for d in frames[0]] == [
        (0, [0.1, 0.1, 0.3, 0.2], None),
        (1, [0.5, 0.1, 0.6, 0.2], None),
    ]


@pytest.mark.parametrize("text", ["", "   ", "<0f>", "no boxes here"])
def test_no_box_means_nobody(text: str) -> None:
    assert parse_sam_output(text) == {}


def test_polygons_are_rectangles_in_the_snapshots_pixels() -> None:
    found = Detections((Detection(0, 0.25, 0.5, 0.5, 1.0),), width=1280, height=720)
    assert found.polygons() == [[[320.0, 360.0], [640.0, 360.0], [640.0, 720.0], [320.0, 720.0]]]
