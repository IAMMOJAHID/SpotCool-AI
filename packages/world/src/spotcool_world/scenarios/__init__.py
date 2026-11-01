"""Scenarios: the film script of a demo day, as YAML files in a small DSL.

``schema`` defines the DSL, ``loader`` reads a file with line numbers, ``linter`` checks it and
``compiler`` turns it into functions of simulation time for the ``ScenarioProcessor``. The
library (``library/*.yaml``) holds the demo days. Needs only the core dependencies.
"""

from spotcool_world.scenarios.compiler import (
    SIM_EPOCH,
    CompiledScenario,
    compile_scenario,
    sim_datetime,
)
from spotcool_world.scenarios.linter import Finding, check, lint
from spotcool_world.scenarios.loader import (
    LoadedScenario,
    ScenarioFileError,
    library_names,
    load_scenario,
)
from spotcool_world.scenarios.schema import Scenario

__all__ = [
    "SIM_EPOCH",
    "CompiledScenario",
    "Finding",
    "LoadedScenario",
    "Scenario",
    "ScenarioFileError",
    "check",
    "compile_scenario",
    "library_names",
    "lint",
    "load_scenario",
    "sim_datetime",
]
