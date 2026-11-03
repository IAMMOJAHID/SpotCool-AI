import os
from pathlib import Path

import pytest

from spotcool_world.services import WorldSettings, create_plant
from spotcool_world.services.boptest.hosted import HostedBoptestPlant
from spotcool_world.services.boptest.local import LocalBoptestPlant
from spotcool_world.services.rc import RcPlant
from spotcool_world.services.replay import ReplayPlant


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)  # no stray world.yaml


def test_rc_is_the_default_plant() -> None:
    assert isinstance(create_plant(WorldSettings(), "spotcool"), RcPlant)


@pytest.mark.asyncio
async def test_boptest_plants_point_at_their_services() -> None:
    local = create_plant(WorldSettings(plant="boptest-local"), "spotcool")
    hosted = create_plant(WorldSettings(plant="boptest-hosted"), "baseline")
    assert isinstance(local, LocalBoptestPlant) and isinstance(hosted, HostedBoptestPlant)
    assert local.client.base_url.rstrip("/") == "http://127.0.0.1:8000"  # compose's web port
    assert hosted.client.base_url.rstrip("/") == "http://api.boptest.net"
    await local.stop()  # never started: only closes the client
    await hosted.stop()


def test_replay_reads_the_named_run_for_its_run(tmp_path: Path) -> None:
    plant = create_plant(WorldSettings(plant="replay", replay_run="r1"), "baseline")
    assert isinstance(plant, ReplayPlant)
    with pytest.raises(ValueError, match="replay_run"):
        create_plant(WorldSettings(plant="replay"), "baseline")
