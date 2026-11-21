"""From successive readings of a rain counter to the rain that fell. Pure logic, no Domoticz, no clock.

Two kinds of counter exist (hardware setting *Rain counter*):

    daily   the counter starts again at 0 every local midnight ("rain today"); this is what the dzVents script this
            plugin replaces assumed
    total   the counter only grows (a lifetime total) and is never reset

Rules, in this order (``update`` returns what was decided as a ``Change``):

    * the first reading ever is a baseline. A daily counter has already counted the rain of today, so that rain is
      credited, spread from local midnight to now; a total counter says nothing about the past, so nothing is credited
    * a reading that is not newer than the previous one is ignored
    * a counter that did not go down: the difference is the rain, spread over the time between the two readings
    * a daily counter that went down shortly after local midnight (or since the day of the previous reading): it was
      reset, and its new value is the rain since the reset. A reset that comes a little late, after a reading that
      still showed yesterday's total, is recognised the same way, so yesterday's total is never counted twice
    * any other drop (a daily counter in the middle of the day, a total counter at any time) is a glitch or a restart of
      the sensor: nothing is credited and the new value becomes the baseline
    * more rain than ``max_delta`` mm between two readings is taken for a sensor error: nothing is credited and the new
      value becomes the baseline

What is lost: the rain that fell between the last reading before a reset and the reset itself (at most one polling
interval), and the rain of a time in which the plugin did not run, for a daily counter that was reset meanwhile.
"""
import math
from typing import Optional

from .constants import COUNTER_DAILY, COUNTER_TOTAL, DEFAULT_MAX_DELTA, RESET_GRACE
from .model import Change
from .util import local_midnight

EPSILON = 1e-9


class Counter:
    def __init__(self, mode: str = COUNTER_DAILY, max_delta: float = DEFAULT_MAX_DELTA) -> None:
        self.mode = mode
        self.max_delta = max_delta
        self.last_ts: Optional[float] = None
        self.last_value: Optional[float] = None

    def restore(self, ts: Optional[float], value: Optional[float]) -> None:
        """Take the baseline over from the state file."""
        self.last_ts, self.last_value = ts, value

    @property
    def known(self) -> bool:
        return self.last_ts is not None and self.last_value is not None

    def update(self, ts: float, value: float) -> Change:
        if not math.isfinite(value) or value < 0:
            return Change(0.0, ts, "glitch", "The rain counter reads %r: ignored" % value)
        if self.last_ts is None or self.last_value is None:
            self.last_ts, self.last_value = ts, value
            if self.mode == COUNTER_DAILY and value > 0:
                return self._checked(Change(value, local_midnight(ts), "first"))
            return Change(0.0, ts, "first")
        if ts <= self.last_ts:
            return Change(0.0, ts, "clock", "A reading is not newer than the previous one (did the clock go back?): ignored")
        last_ts, last_value = self.last_ts, self.last_value
        self.last_ts, self.last_value = ts, value
        if value >= last_value - EPSILON:
            mm = max(0.0, value - last_value)
            return self._checked(Change(mm, last_ts, "delta" if mm > 0 else "none"))
        if self.mode == COUNTER_DAILY and self._reset_expected(last_ts, ts):
            return self._checked(Change(value, max(last_ts, local_midnight(ts)), "reset"))
        if self.mode == COUNTER_TOTAL:
            return Change(0.0, ts, "restart", "The rain counter went down from %.1f to %.1f mm: the sensor was restarted, "
                          "its new value is the baseline" % (last_value, value))
        return Change(0.0, ts, "glitch", "The daily rain counter went down from %.1f to %.1f mm in the middle of the day: "
                      "ignored, its new value is the baseline" % (last_value, value))

    @staticmethod
    def _reset_expected(last_ts: float, ts: float) -> bool:
        midnight = local_midnight(ts)
        return midnight != local_midnight(last_ts) or ts - midnight <= RESET_GRACE

    def _checked(self, change: Change) -> Change:
        if change.mm > self.max_delta:
            return Change(0.0, change.since, "implausible",
                          "%.1f mm of rain between two readings is more than the limit of %.0f mm (RS_MAX_DELTA): "
                          "taken for a sensor error, ignored" % (change.mm, self.max_delta))
        return change
