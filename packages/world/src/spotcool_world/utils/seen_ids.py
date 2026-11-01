"""A set of recent ids for duplicate detection that never grows without bound."""

from collections import deque


class SeenIds:
    """Remembers the most recent ``maxlen`` ids.

    Duplicates of a frame arrive close together (several branches forwarding the same frame),
    so forgetting old ids is safe and keeps memory flat over long runs.

    Args:
        maxlen: How many ids to remember.
    """

    def __init__(self, maxlen: int = 4096) -> None:
        self._order: deque[int] = deque()
        self._ids: set[int] = set()
        self._maxlen = maxlen

    def add(self, item: int) -> bool:
        """Record ``item`` and report whether it was new.

        Args:
            item: The id to record.

        Returns:
            True the first time an id is seen, False for a repeat.
        """
        if item in self._ids:
            return False
        self._ids.add(item)
        self._order.append(item)
        if len(self._order) > self._maxlen:
            self._ids.discard(self._order.popleft())
        return True
