"""The stamp cache, kept free of ROS imports so it can be tested alone."""

from __future__ import annotations

from collections import OrderedDict

STAMP_CACHE = 512


class StampCache:
    """nanosec field -> full stamp of recently relayed inputs. MAC-VO keeps
    only the nanosecond part, which is unambiguous while its latency stays
    under a second."""

    def __init__(self, size: int = STAMP_CACHE):
        self.size = size
        self._by_nanosec: OrderedDict[int, tuple[int, int]] = OrderedDict()

    def add(self, sec: int, nanosec: int) -> None:
        self._by_nanosec[nanosec] = (sec, nanosec)
        self._by_nanosec.move_to_end(nanosec)
        while len(self._by_nanosec) > self.size:
            self._by_nanosec.popitem(last=False)

    def restore(self, sec: int, nanosec: int) -> tuple[int, int] | None:
        if sec != 0:
            return sec, nanosec  # upstream fixed: the stamp is whole already
        return self._by_nanosec.get(nanosec)
