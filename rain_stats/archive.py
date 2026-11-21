"""The round-robin archive: a rain ring (sum, mm) and a peak ring (maximum, mm/h) for each tier.

Every reading is added to the rings of both tiers, so each window is answered by the ring whose slots are fine enough
for it and long enough to hold it (see ``constants.TIERS``). Nothing grows and nothing is ever trimmed: a slot that
becomes too old is overwritten by the one that takes its place. Pure data structure: no Domoticz, no clock - every call
is told the time.
"""
from typing import Any, Dict, Tuple

from .constants import SPARE_SLOTS, TIERS, Window, window_slots
from .ring import MAX, SUM, Ring

RAIN_DIGITS = 4               # decimals kept of the rain of a slot when saving
PEAK_DIGITS = 2               # decimals kept of the peak rate of a slot when saving


class Archive:
    def __init__(self) -> None:
        # the rain rings keep a spare slot: the edge of the longest window of the tier (see Ring.edge)
        self.rain: Dict[str, Ring] = {t.key: Ring(t.slots + SPARE_SLOTS, t.resolution, SUM) for t in TIERS}
        self.peak: Dict[str, Ring] = {t.key: Ring(t.slots, t.resolution, MAX) for t in TIERS}

    # -- writing --------------------------------------------------------------------------------------------
    def add_rain(self, moment: float, mm: float) -> None:
        """``mm`` of rain that fell at ``moment``."""
        for ring in self.rain.values():
            ring.add(moment, mm)

    def add_spread(self, start: float, end: float, mm: float) -> None:
        """``mm`` of rain that fell evenly between ``start`` and ``end``."""
        for ring in self.rain.values():
            ring.spread(start, end, mm)

    def note_rate(self, moment: float, rate: float) -> None:
        """A rain rate (mm/h) seen at ``moment``: each slot keeps the highest one."""
        for ring in self.peak.values():
            ring.add(moment, rate)

    # -- reading --------------------------------------------------------------------------------------------
    def window(self, window: Window, now: float) -> Tuple[float, float]:
        """(rain in mm, peak rate in mm/h) of a window that ends now.

        The rain is the whole slots of the window plus the part of the slot before them that is still inside the period
        (the edge, taken in proportion): with rain spread evenly within a slot the window is exactly the last N hours or
        days. The peak is made of whole slots only, so it never reaches further back than the window is long."""
        count = window_slots(window)
        rain = self.rain[window.tier]
        return rain.window(now, count) + rain.edge(now, count), self.peak[window.tier].window(now, count)

    # -- saving ---------------------------------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"rain": {key: ring.to_dict(RAIN_DIGITS) for key, ring in self.rain.items()},
                "peak": {key: ring.to_dict(PEAK_DIGITS) for key, ring in self.peak.items()}}

    @classmethod
    def from_dict(cls, data: Any) -> "Archive":
        """Rebuild an archive from ``to_dict`` output. Raises ValueError with the reason when it cannot be done."""
        if not isinstance(data, dict):
            raise ValueError("the archive is not an object")
        archive = cls()
        for measure, mode, rings in (("rain", SUM, archive.rain), ("peak", MAX, archive.peak)):
            saved = data.get(measure)
            if not isinstance(saved, dict):
                raise ValueError("the archive has no '%s' rings" % measure)
            for tier in TIERS:
                entry = saved.get(tier.key)
                if entry is not None:
                    try:
                        slots = tier.slots + SPARE_SLOTS if mode == SUM else tier.slots
                        rings[tier.key] = Ring.from_dict(entry, slots, tier.resolution, mode)
                    except ValueError as exc:
                        raise ValueError("%s ring '%s': %s" % (measure, tier.key, exc))
        return archive
