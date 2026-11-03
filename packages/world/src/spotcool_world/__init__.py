"""SpotCool's simulated building, built as a pipecat-style frame pipeline.

Frames carry simulation news, processors handle one step each, pipelines order the work of a
tick, workers run on a bus and the runner owns their lifecycle. See ``AGENTS.md``.
"""

from importlib.metadata import version

__version__ = version("spotcool-world")
