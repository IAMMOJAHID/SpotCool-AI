"""Public dataset footage (WiseNET, MEVA) for the zone cameras: the dataset indexes.

Needs only the core dependencies; ``services/clips/`` plays the segments.
"""

from spotcool_world.services.datasets.index import DatasetIndex, Segment, load_indexes

__all__ = ["DatasetIndex", "Segment", "load_indexes"]
