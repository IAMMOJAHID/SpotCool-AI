"""Dataset indexes: which segment of a public dataset's video shows a camera's scene.

An index sits at ``footage_dir/datasets/<name>/index.yaml``. ``scripts/footage/wisenet_index.py``
writes WiseNET's and documents the format (version 1); a MEVA index is written by hand::

    version: 1
    dataset: wisenet
    cameras:
      cam-cor:
        empty: {file: "video5_4.mp4", start_s: 0.0, end_s: 12.0, people: 0}

``file`` is relative to the index and must stay inside its folder. Segments whose file is
missing are left out.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from spotcool_contracts.zones import ZONES
from spotcool_world.scenarios.schema import Scene

INDEX_FILE = "index.yaml"
"""The index's file name inside a dataset's folder."""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    start_s: float = Field(ge=0)
    end_s: float
    people: int = Field(ge=0)

    @field_validator("file")
    @classmethod
    def _inside(cls, file: str) -> str:
        path = Path(file)
        if path.anchor or ".." in path.parts:
            raise ValueError(f"{file!r} must be a path inside the index's folder")
        return file

    @model_validator(mode="after")
    def _ordered(self) -> "_Entry":
        if self.end_s <= self.start_s:
            raise ValueError(f"end_s {self.end_s} must be after start_s {self.start_s}")
        return self


class _IndexFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    dataset: str = Field(min_length=1)
    cameras: dict[str, dict[Scene, _Entry]]

    @field_validator("cameras")
    @classmethod
    def _zone_cameras(
        cls, cameras: dict[str, dict[Scene, _Entry]]
    ) -> dict[str, dict[Scene, _Entry]]:
        known = sorted(info.camera_id for info in ZONES.values())
        if unknown := sorted(set(cameras) - set(known)):
            raise ValueError(
                f"unknown camera(s) {', '.join(unknown)}; zone cameras: {', '.join(known)}"
            )
        return cameras


@dataclass(frozen=True)
class Segment:
    """The part of a dataset video that shows a camera's scene.

    Parameters:
        path: The video file.
        start_s: Where the segment starts, in seconds from the start of the file.
        end_s: Where it ends.
    """

    path: Path
    start_s: float
    end_s: float


class DatasetIndex:
    """One dataset's segments, by camera and scene.

    Args:
        name: The dataset's name (``dataset`` in the index).
        segments: ``(camera id, scene)`` to segment.
    """

    def __init__(self, name: str, segments: dict[tuple[str, str], Segment]) -> None:
        self.name = name
        """The dataset's name."""
        self._segments = segments

    @classmethod
    def load(cls, path: Path) -> "DatasetIndex":
        """Read an index file, keeping the segments whose video file exists.

        Args:
            path: The ``index.yaml``.

        Raises:
            ValueError: If it is not a version 1 dataset index; the message says why.
            OSError: If it cannot be read.
        """
        try:
            index = _IndexFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} is not YAML: {exc}") from None
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, error['loc'])) or 'index'}: {error['msg']}"
                for error in exc.errors()
            )
            raise ValueError(f"{path} is not a version 1 dataset index: {problems}") from None
        segments: dict[tuple[str, str], Segment] = {}
        missing: set[str] = set()
        for camera_id, scenes in index.cameras.items():
            for scene, entry in scenes.items():
                file = path.parent / entry.file
                if file.is_file():
                    segments[camera_id, scene] = Segment(file, entry.start_s, entry.end_s)
                else:
                    missing.add(entry.file)
        if missing:
            logger.warning(
                f"dataset {index.dataset}: segments left out, their files are missing from "
                f"{path.parent}: {', '.join(sorted(missing))}"
            )
        return cls(index.dataset, segments)

    def segment(self, camera_id: str, scene: str) -> Segment | None:
        """The segment that shows ``scene`` on camera ``camera_id``, or ``None``.

        Args:
            camera_id: The camera.
            scene: The scene.
        """
        return self._segments.get((camera_id, scene))


def load_indexes(datasets_dir: Path) -> list[DatasetIndex]:
    """Every dataset index in ``datasets_dir/*/index.yaml``, in folder-name order.

    An index that cannot be read or is malformed is logged and left out.

    Args:
        datasets_dir: ``footage_dir/datasets``; it may be missing.
    """
    indexes: list[DatasetIndex] = []
    for path in sorted(datasets_dir.glob(f"*/{INDEX_FILE}")):
        try:
            indexes.append(DatasetIndex.load(path))
        except (OSError, ValueError) as exc:
            logger.warning(f"dataset index left out: {exc}")
    return indexes
