"""Plain data objects handed between the layers."""
from typing import Dict, NamedTuple, Optional, Tuple


class Reading(NamedTuple):
    """One reading of the rain device (``GET json.htm?type=command&param=getdevices&rid=<idx>``)."""
    ts: float                     # Unix time of the reading
    counter: float                # mm: the rain counter ("rain today" for a daily counter)
    rate: Optional[float]         # mm/h; None when the device does not report a rate
    timed_out: bool               # Domoticz' own sensor time-out flag of the device
    raw: str                      # what the device said, for the log and the command line tool


class Change(NamedTuple):
    """What a reading of the counter means: ``mm`` of rain that fell since ``since`` (Unix time)."""
    mm: float
    since: float
    kind: str                     # first | delta | none | reset | glitch | restart | clock | implausible
    note: str = ""                # something unusual worth a log line


class Stats(NamedTuple):
    """The value of every window at one moment."""
    now: float
    rain: Dict[str, float]        # window key -> mm
    peak: Dict[str, float]        # window key -> mm/h
    since: Optional[float]        # when the archive started to collect (None: not yet)
    partial: Tuple[str, ...]      # keys of the windows whose rain reaches back further than the data
    peak_partial: Tuple[str, ...] = ()      # the same for the peak rain rate, which the daily history does not have
    rain_from: Optional[float] = None       # since, or the start of the imported history when that is earlier


class RainDevice(NamedTuple):
    """A rain device of Domoticz, for the command line tool that finds the IDX."""
    idx: int
    name: str
    counter: Optional[float]
    rate: Optional[float]
    hardware: str = ""
