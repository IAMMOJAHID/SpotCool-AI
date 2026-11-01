"""Observers watch frames move between processors without changing anything."""

from spotcool_world.observers.base_observer import BaseObserver, FramePushed
from spotcool_world.observers.log_observer import LogObserver
from spotcool_world.observers.step_timing_observer import StepTimingObserver

__all__ = ["BaseObserver", "FramePushed", "LogObserver", "StepTimingObserver"]
