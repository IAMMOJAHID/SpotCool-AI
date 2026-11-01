"""CameraSceneProcessor: shows each zone's scenario scene on its camera."""

from spotcool_contracts.events import CameraSceneData
from spotcool_contracts.zones import ZONES
from spotcool_world.frames import CameraSceneFrame, Frame, ScenarioStateFrame, StartFrame
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor
from spotcool_world.services.camera_service import CameraSystem


class CameraSceneProcessor(FrameProcessor):
    """Sets the zone cameras' scenes from the scenario, tick by tick. A pass-through processor.

    On each downstream ``ScenarioStateFrame`` it passes the frame on, sets every zone camera's
    scene and people count (``ZONES[zone].camera_id``) on the camera system, then pushes a
    ``CameraSceneFrame`` for each camera whose scene changed, stamped with the frame's time. The
    first state of a run (after each ``StartFrame``) reports every camera. Every frame passes
    on unchanged.

    WP11 places it after ``bms.output()`` and shares the ``CameraSystem`` with the HTTP router.

    Args:
        cameras: The camera system; it must hold the five zone cameras.
        name: Display name.

    Raises:
        ValueError: If a zone camera is missing from ``cameras``.
    """

    def __init__(self, cameras: CameraSystem, *, name: str | None = None) -> None:
        super().__init__(name=name)
        held = {spec.id for spec in cameras.cameras}
        if missing := [info.camera_id for info in ZONES.values() if info.camera_id not in held]:
            raise ValueError(f"the camera system has no {', '.join(missing)}")
        self._cameras = cameras
        self._scenes: dict[str, str] = {}  # camera id -> the scene last reported this run

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Pass the frame on; after a scenario state, set the scenes and report the changes."""
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if direction is not FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, StartFrame):
            self._scenes = {}
        elif isinstance(frame, ScenarioStateFrame):
            for zone_scene in frame.state.zones:
                camera_id = ZONES[zone_scene.zone].camera_id
                self._cameras.set_scene(camera_id, zone_scene.scene, zone_scene.people)
                if self._scenes.get(camera_id) != zone_scene.scene:
                    self._scenes[camera_id] = zone_scene.scene
                    data = CameraSceneData(
                        camera_id=camera_id, zone=zone_scene.zone, scene=zone_scene.scene
                    )
                    await self.push_frame(CameraSceneFrame(data=data, sim_time_s=frame.sim_time_s))
