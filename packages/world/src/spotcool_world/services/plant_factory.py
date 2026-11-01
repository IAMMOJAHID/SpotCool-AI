"""create_plant: the plant provider ``WorldSettings.plant`` names, for one run."""

from spotcool_contracts.events import Run
from spotcool_world.serializers.jsonl import EVENTS_FILE
from spotcool_world.services.plant_service import PlantService
from spotcool_world.services.settings import WorldSettings


def create_plant(settings: WorldSettings, run: Run) -> PlantService:
    """Build the plant for one run.

    Providers are imported here, not at module level, so the core runs without the extras of
    the providers it does not use (``boptest`` for the BOPTEST plants, ``rc`` for the RC one).

    Args:
        settings: ``plant`` picks the provider: ``rc``, ``boptest-hosted`` (``boptest_url``),
            ``boptest-local`` (``boptest_local_url``) or ``replay`` (``replay_run`` under
            ``run_dir``).
        run: Which run the plant is for; the replay plant feeds back that run's recording.

    Returns:
        A plant that has not been started.

    Raises:
        ValueError: If ``plant`` is ``replay`` and ``replay_run`` is not set.
    """
    match settings.plant:
        case "rc":
            from spotcool_world.services.rc import RcPlant

            return RcPlant()
        case "boptest-hosted":
            from spotcool_world.services.boptest.hosted import HostedBoptestPlant

            return HostedBoptestPlant(settings.boptest_url, settings.testcase)
        case "boptest-local":
            from spotcool_world.services.boptest.local import LocalBoptestPlant

            return LocalBoptestPlant(settings.boptest_local_url, settings.testcase)
        case "replay":
            from spotcool_world.services.replay import ReplayPlant

            if settings.replay_run is None:
                raise ValueError("plant 'replay' needs replay_run, the recorded run to feed back")
            return ReplayPlant(settings.run_dir / settings.replay_run / EVENTS_FILE, run)
