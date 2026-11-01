import importlib
import sys
from pathlib import Path

import pytest

from spotcool_world.services.datasets import DatasetIndex, Segment, load_indexes

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts" / "footage"


def write_index(
    folder: Path, cameras: str, *, header: str = "version: 1\ndataset: wisenet\n"
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "index.yaml"
    path.write_text(f"{header}cameras:\n{cameras}", encoding="utf-8")
    return path


def test_segments_with_their_file_are_served(tmp_path: Path, logs: list[str]) -> None:
    (tmp_path / "wisenet").mkdir()
    (tmp_path / "wisenet" / "video5_4.mp4").write_bytes(b"x")
    path = write_index(
        tmp_path / "wisenet",
        "  cam-cor:\n"
        '    empty: {file: "video5_4.mp4", start_s: 0.0, end_s: 12.0, people: 0}\n'
        '    many: {file: "video9_4.mp4", start_s: 14.0, end_s: 40.0, people: 5}\n',
    )
    index = DatasetIndex.load(path)
    assert index.name == "wisenet"
    assert index.segment("cam-cor", "empty") == Segment(
        tmp_path / "wisenet" / "video5_4.mp4", 0, 12
    )
    assert index.segment("cam-cor", "many") is None  # its file is missing
    assert index.segment("cam-nor", "empty") is None
    assert [line for line in logs if "WARNING" in line and "video9_4.mp4" in line]


def test_the_index_wisenet_index_py_writes_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not (SCRIPTS / "wisenet_index.py").is_file():
        pytest.skip("scripts/footage is not in this checkout")
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delitem(sys.modules, "wisenet_index", raising=False)
    wisenet_index = importlib.import_module("wisenet_index")
    entry = {"file": "video1_3.mp4", "start_s": 2.0, "end_s": 30.0, "people": 5}
    wisenet_index.write_index(tmp_path / "index.yaml", {"cam-sou": {"meeting": entry}})
    (tmp_path / "video1_3.mp4").write_bytes(b"x")
    index = DatasetIndex.load(tmp_path / "index.yaml")
    assert index.segment("cam-sou", "meeting") == Segment(tmp_path / "video1_3.mp4", 2.0, 30.0)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("version: 2\ndataset: wisenet\ncameras: {}\n", "version: Input should be 1"),
        ("version: 1\ncameras: {}\n", "dataset: Field required"),
        ("version: 1\ndataset: wisenet\ncameras: {}\nextra: 1\n", "extra: Extra inputs"),
        ("version: 1\ndataset: wisenet\ncameras: {cam-core: {}}\n", "unknown camera(s) cam-core"),
        (
            "version: 1\ndataset: wisenet\ncameras: {cam-cor: {crowded: "
            "{file: a.mp4, start_s: 0, end_s: 1, people: 9}}}\n",
            "crowded",
        ),
        (
            "version: 1\ndataset: wisenet\ncameras: {cam-cor: {empty: "
            "{file: a.mp4, start_s: 5, end_s: 5, people: 0}}}\n",
            "after start_s",
        ),
        (
            "version: 1\ndataset: wisenet\ncameras: {cam-cor: {empty: "
            "{file: ../a.mp4, start_s: 0, end_s: 1, people: 0}}}\n",
            "inside the index's folder",
        ),
        (
            "version: 1\ndataset: wisenet\ncameras: {cam-cor: {empty: "
            "{file: /tmp/a.mp4, start_s: 0, end_s: 1, people: 0}}}\n",
            "inside the index's folder",
        ),
        (
            "version: 1\ndataset: wisenet\ncameras: {cam-cor: {empty: "
            "{file: a.mp4, start_s: -1, end_s: 1, people: 0}}}\n",
            "start_s: Input should be greater than or equal to 0",
        ),
        ("", "index: Input should be a valid dictionary"),
        ("version: [1\n", "is not YAML"),
    ],
)
def test_a_malformed_index_is_refused(tmp_path: Path, text: str, problem: str) -> None:
    path = tmp_path / "index.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        DatasetIndex.load(path)
    assert problem in str(caught.value)


def test_load_indexes_takes_every_dataset_in_name_order_and_skips_broken_ones(
    tmp_path: Path, logs: list[str]
) -> None:
    entry = '  cam-wes:\n    few: {file: "a.mp4", start_s: 0.0, end_s: 5.0, people: 1}\n'
    write_index(tmp_path / "wisenet", entry)
    write_index(tmp_path / "meva", entry, header="version: 1\ndataset: meva\n")
    write_index(tmp_path / "broken", entry, header="version: 9\ndataset: broken\n")
    (tmp_path / "loose.yaml").write_text("not an index", encoding="utf-8")
    assert [index.name for index in load_indexes(tmp_path)] == ["meva", "wisenet"]
    assert any("broken" in line and "left out" in line for line in logs)
    assert load_indexes(tmp_path / "missing") == []
