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


class Pacer:
    """Relay a stereo pair only while MAC-VO has fewer than `max_inflight`
    pairs it has not answered with a pose.

    MAC-VO subscribes with depth-1 queues and an approximate-time pairing.
    Fed faster than it computes, it drops left and right frames independently
    and can pair a left image with the next right one, which corrupts its
    stereo depth. Pacing on its own output keeps every pair intact at
    whatever rate the GPU allows. A frame MAC-VO loses track on yields no
    pose, so an unanswered pair expires after `timeout_s`.
    """

    def __init__(self, max_inflight: int = 2, timeout_s: float = 1.0):
        self.max_inflight = max(1, int(max_inflight))
        self.timeout_s = float(timeout_s)
        self._inflight: OrderedDict[int, float] = OrderedDict()  # stamp ns -> sent at

    def _expire(self, now: float) -> None:
        while self._inflight and now - next(iter(self._inflight.values())) > self.timeout_s:
            self._inflight.popitem(last=False)

    def ready(self, now: float) -> bool:
        self._expire(now)
        return len(self._inflight) < self.max_inflight

    def sent(self, stamp_ns: int, now: float) -> None:
        self._inflight[stamp_ns] = now

    def answered(self, stamp_ns: int) -> None:
        """A pose for `stamp_ns` arrived: it and every older pair are done."""
        while self._inflight and next(iter(self._inflight)) <= stamp_ns:
            self._inflight.popitem(last=False)
