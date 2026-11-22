"""The state file: the archive, the baseline of the rain counter, when collecting started and whether the daily rain
history of Domoticz was imported.

The file is written atomically (a temporary file in the same directory, then ``os.replace``), so a crash or a power
cut leaves either the old or the new file, never half of one. Only buckets that hold something are written, so the file
is a few kilobytes. A file that cannot be read is moved aside (``.corrupt-<time>``) and the plugin starts with an empty
archive; a file written by a newer version of the plugin is moved aside in the same way, never overwritten.
"""
import json
import math
import os
import time
from typing import Any, Dict, NamedTuple, Optional

from .archive import Archive
from .constants import STATE_VERSION
from .util import safe_float, safe_int

MAX_STATE_BYTES = 32 * 1024 * 1024


class StateError(Exception):
    """The state file cannot be used; the message says why and where the file went."""


class HistoryMark(NamedTuple):
    """The daily rain history of a Domoticz device was imported (see history.py): it is never read again."""
    idx: int                  # the device that was read
    first: float              # start of the first day imported
    last: float               # end of the last day imported
    days: int                 # how many days were imported (0: Domoticz had days, but the plugin knew all of them)
    at: float                 # when

    @property
    def from_day(self) -> Optional[float]:
        """The start of the first day the import added; None when it added none."""
        return self.first if self.days else None


class State:
    def __init__(self) -> None:
        self.archive = Archive()
        self.since: Optional[float] = None              # when the archive started to collect
        self.history: Optional[HistoryMark] = None      # the imported history of Domoticz, once it was read
        self.counter_ts: Optional[float] = None         # the baseline of the rain counter ...
        self.counter_value: Optional[float] = None      # ... and its value then

    def to_dict(self, now: float) -> Dict[str, Any]:
        counter = None
        if self.counter_ts is not None and self.counter_value is not None:
            counter = {"ts": round(self.counter_ts, 1), "value": round(self.counter_value, 4)}
        data: Dict[str, Any] = {"v": STATE_VERSION, "saved": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                                "since": None if self.since is None else round(self.since, 1), "counter": counter,
                                "archive": self.archive.to_dict()}
        if self.history is not None:
            data["history"] = {"idx": self.history.idx, "first": round(self.history.first, 1),
                               "last": round(self.history.last, 1), "days": self.history.days,
                               "at": round(self.history.at, 1)}
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "State":
        if not isinstance(data, dict):
            raise ValueError("the file does not hold a JSON object")
        version = data.get("v")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("the file has no version")
        if version > STATE_VERSION:
            raise ValueError("the file was written by a newer version of the plugin (state version %d, this one reads up to %d)"
                             % (version, STATE_VERSION))
        state = cls()
        state.archive = Archive.from_dict(data.get("archive"))
        since = data.get("since")
        if since is not None:
            state.since = safe_float(since)
            if state.since is None:
                raise ValueError("'since' is not a number")
        counter = data.get("counter")
        if counter is not None:
            if not isinstance(counter, dict):
                raise ValueError("'counter' is not an object")
            ts, value = safe_float(counter.get("ts")), safe_float(counter.get("value"))
            if ts is None or value is None or not math.isfinite(ts) or value < 0:
                raise ValueError("'counter' holds an invalid number")
            state.counter_ts, state.counter_value = ts, value
        history = data.get("history")
        if history is not None:
            state.history = _history_mark(history)
        return state


def _history_mark(data: Any) -> HistoryMark:
    if not isinstance(data, dict):
        raise ValueError("'history' is not an object")
    idx, days = safe_int(data.get("idx")), safe_int(data.get("days"))
    first, last, at = safe_float(data.get("first")), safe_float(data.get("last")), safe_float(data.get("at"))
    if idx is None or idx < 1 or days is None or days < 0 or first is None or last is None or at is None or last < first:
        raise ValueError("'history' holds an invalid value")
    return HistoryMark(idx, first, last, days, at)


def _move_aside(path: str, now: float) -> str:
    target = "%s.corrupt-%s" % (path, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
    n = 1
    while os.path.exists(target):
        n += 1
        target = "%s.corrupt-%s-%d" % (path, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), n)
    os.replace(path, target)
    return target


def load(path: str, now: float, move: bool = True) -> Optional[State]:
    """The saved state, or None when there is no file.

    Raises StateError when it is unusable; the file was moved aside then, unless ``move`` is False (the command line
    tool only reads)."""
    if not os.path.isfile(path):
        return None
    try:
        if os.path.getsize(path) > MAX_STATE_BYTES:
            raise ValueError("the file is larger than %d bytes" % MAX_STATE_BYTES)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return State.from_dict(data)
    except (OSError, ValueError) as exc:                # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
        if not move:
            raise StateError("The state file %s cannot be used (%s)" % (path, exc))
        try:
            moved = _move_aside(path, now)
        except OSError as move_exc:
            raise StateError("The state file %s cannot be used (%s) and could not be moved aside (%s): starting with an "
                             "empty archive, the file is overwritten at the next save" % (path, exc, move_exc))
        raise StateError("The state file %s cannot be used (%s): it was moved to %s, starting with an empty archive"
                         % (path, exc, moved))


def save(path: str, state: State, now: float) -> None:
    """Write the state atomically. Raises OSError when that is not possible."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = "%s.tmp" % path
    text = json.dumps(state.to_dict(now), separators=(",", ":"))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
