"""The round-robin buffer: a fixed number of equally long time slots that is reused in a circle.

A ring never grows. Slot number ``s = floor(unix time / resolution)`` lives at index ``s % slots``; when time
moves on, the slots that are skipped are cleared and the oldest slot is overwritten by the newest. What the ring
remembers is exactly the last ``slots`` slots up to ``head``, the newest slot written.

Two kinds of ring exist (RRDtool calls them consolidation functions):

    SUM   every value added to a slot is summed (rain in mm)
    MAX   a slot keeps the largest value added (peak rain rate in mm/h)

Invariant: ``data[s % slots]`` is valid for the slots ``head - slots < s <= head`` and zero for every later slot, so
a read never needs to change the ring and a quiet period costs nothing. A value of zero is never stored.

The ring is saved sparsely, as a mapping from the absolute slot number to its value; the absolute number makes the
file independent of where in the circle a slot happens to sit, so the size of a ring can change between versions.
"""
from typing import Any, Dict, List, Optional

from .util import safe_float, safe_int

SUM = "sum"
MAX = "max"
MAX_CONVERSION = 24         # a saved ring may have slots up to this many times longer than these (hours -> 15 minutes: 4)


class Ring:
    def __init__(self, slots: int, resolution: int, mode: str = SUM) -> None:
        if slots < 1 or resolution < 1 or mode not in (SUM, MAX):
            raise ValueError("a ring needs at least one slot of at least one second and the mode sum or max")
        self.slots = slots
        self.resolution = resolution
        self.mode = mode
        self.data: List[float] = [0.0] * slots
        self.head: Optional[int] = None                 # newest slot written; None = the ring is empty

    # -- writing --------------------------------------------------------------------------------------------
    def slot_of(self, moment: float) -> int:
        return int(moment // self.resolution)

    def _move_to(self, slot: int) -> None:
        """Make ``slot`` the newest slot, clearing the slots that time has skipped (older slots: no change)."""
        if self.head is None:
            self.head = slot
            return
        if slot <= self.head:
            return
        if slot - self.head >= self.slots:
            self.data = [0.0] * self.slots
        else:
            for skipped in range(self.head + 1, slot + 1):
                self.data[skipped % self.slots] = 0.0
        self.head = slot

    def add(self, moment: float, value: float) -> bool:
        """Add ``value`` to the slot of ``moment``. False when the slot is older than the ring remembers.

        Zero, negative and NaN values change nothing. A moment before ``head`` is fine as long as its slot is still
        inside the ring: the history import and a late value write into the past."""
        if not value > 0:
            return True
        slot = self.slot_of(moment)
        if self.head is not None and slot <= self.head - self.slots:
            return False
        self._move_to(slot)
        index = slot % self.slots
        if self.mode == SUM:
            self.data[index] += value
        elif value > self.data[index]:
            self.data[index] = value
        return True

    def spread(self, start: float, end: float, amount: float) -> None:
        """Add ``amount`` spread evenly over the time from ``start`` to ``end`` (SUM rings), in proportion to how
        much of each slot lies inside that time. Slots older than the ring remembers drop their share."""
        if self.mode != SUM or not amount > 0:
            return
        if end <= start:
            self.add(start, amount)
            return
        first = self.slot_of(start)
        last = max(first, int(-(-end // self.resolution)) - 1)
        length = end - start
        # Slots further back than the ring is long are dropped anyway: never walk through them. A span of decades
        # (a clock that was wrong at the first reading and then jumped) must cost no more than one ring.
        first = max(first, last - self.slots + 1)
        for slot in range(first, last + 1):
            overlap = min(end, (slot + 1) * self.resolution) - max(start, slot * self.resolution)
            if overlap > 0:
                self.add(slot * self.resolution, amount * overlap / length)

    # -- reading --------------------------------------------------------------------------------------------
    def window(self, now: float, count: int) -> float:
        """The sum (or maximum) over the slot of ``now`` and the ``count - 1`` slots before it."""
        if self.head is None or count < 1:
            return 0.0
        now_slot = self.slot_of(now)
        high = min(now_slot, self.head)
        low = max(now_slot - count + 1, self.head - self.slots + 1)
        if low > high:
            return 0.0
        values = self._values(low, high)
        return float(sum(values)) if self.mode == SUM else float(max(values))

    def edge(self, now: float, count: int) -> float:
        """The part of the slot before a window of ``count`` slots that still lies inside the period of ``count`` slots
        ending now, in proportion to the time of that slot that is inside it (SUM rings; a value is taken to have been
        spread evenly over its slot). 0 when that slot is not kept: the ring has no spare slot, or it is newer than
        the head (nothing was written yet) or older than the ring remembers."""
        if self.mode != SUM or self.head is None or count < 1:
            return 0.0
        now_slot = self.slot_of(now)
        slot = now_slot - count
        if not self.head - self.slots < slot <= self.head:
            return 0.0
        inside = 1.0 - (now - now_slot * self.resolution) / self.resolution       # the later part of that slot
        return float(self.data[slot % self.slots]) * inside

    def _values(self, low: int, high: int) -> List[float]:
        """The values of the absolute slots low..high (inclusive); the range is inside the ring."""
        start = low % self.slots
        end = start + (high - low + 1)
        if end <= self.slots:
            return self.data[start:end]
        return self.data[start:] + self.data[: end - self.slots]

    # -- saving ---------------------------------------------------------------------------------------------
    def to_dict(self, digits: int) -> Dict[str, Any]:
        values: Dict[str, float] = {}
        if self.head is not None:
            for index, value in enumerate(self.data):
                if value:
                    slot = self.head - ((self.head - index) % self.slots)
                    values[str(slot)] = round(value, digits)
        return {"resolution": self.resolution, "slots": self.slots, "head": self.head,
                "values": dict(sorted(values.items(), key=lambda item: int(item[0])))}

    @classmethod
    def from_dict(cls, data: Any, slots: int, resolution: int, mode: str) -> "Ring":
        """Rebuild a ring from ``to_dict`` output, for a ring of the given layout.

        A ring that was saved with longer slots, a whole number of times as long as these (hours, saved by an earlier
        version, where these are quarters of an hour), is converted: a SUM value is spread evenly over the slots that
        make up its time, a MAX value is kept in each of them. Nothing is lost, only the position inside the old slot
        is not known. Raises ValueError when the data was written with another resolution that cannot be converted (the
        slots would mean other times) or is not a ring at all. A single bad value is skipped; the number of skipped
        values is in ``.skipped``."""
        if not isinstance(data, dict):
            raise ValueError("not a ring")
        saved = safe_int(data.get("resolution"))
        if saved is None or saved < resolution or saved % resolution or saved // resolution > MAX_CONVERSION:
            raise ValueError("saved with %s second slots, this version uses %d" % (data.get("resolution"), resolution))
        parts = saved // resolution                         # 1 = the same resolution
        ring = cls(slots, resolution, mode)
        ring.skipped = 0
        head = safe_int(data.get("head"))
        values = data.get("values")
        if head is None or not isinstance(values, dict):
            return ring                                     # an empty ring
        saved_slots = safe_int(data.get("slots")) or slots
        ring.head = head * parts + parts - 1
        for key, raw in values.items():
            slot, value = safe_int(key), safe_float(raw)
            if slot is None or value is None or not value > 0 or not head - saved_slots < slot <= head:
                ring.skipped += 1
                continue
            share = value / parts if mode == SUM else value
            for part in range(parts):
                new = slot * parts + part
                if ring.head - slots < new <= ring.head:
                    ring.data[new % slots] = share
        return ring

    skipped = 0         # values of the last from_dict() that were not usable
