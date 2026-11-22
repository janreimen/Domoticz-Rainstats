"""One-time import of the daily rain history that Domoticz keeps of a rain device (``RS_HISTORY_IDX``).

Domoticz keeps, besides the readings of the last days, one rain total for every day it has run (the graph "Last
year"). The plugin asks for them once::

    GET /json.htm?type=command&param=graph&sensor=rain&idx=<idx>&range=year

and answers ``result: [{"d": "2025-11-21", "mm": "3.2"}, ...]``, one row per day from the same date a year ago to
today; the row of today is not finished and never used. The import **completes** the archive, it never repeats it:

    * only days that are over before the first local midnight of the data the plugin collected itself are taken
      (``state.since``); what the plugin saw since then is exact and stays untouched
    * a day's rain is spread evenly over its 24 hours (23 or 25 on the days the clock changes): the history knows no
      hours, so the windows of up to 7 days are an estimate for the part that comes from it, and exact as soon as the
      plugin has collected that long
    * the peak rain rate is not in the history: the peak windows only know the time the plugin ran

When it is done, a marker (the IDX, the days, the time) goes into the state file, and the device is never asked again
as long as the state file holds the values: only a deleted or damaged state file brings the import back.
"""
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

from .constants import MAX_HISTORY_DAY_MM
from .store import HistoryMark, State
from .util import format_local, local_midnight, next_local_midnight, parse_local_time, safe_float


class Day(NamedTuple):
    start: float                  # local midnight that starts the day
    end: float                    # local midnight that ends it
    mm: float


def parse_days(rows: Iterable[Any]) -> Tuple[List[Day], int]:
    """The days of a Domoticz answer, oldest first, and how many rows were not usable.

    A row is not usable when it has no valid date or no number of mm, a negative or an impossible (more than 1000 mm)
    rain, or when its day is already in the list (the first one wins)."""
    days: Dict[float, Day] = {}
    skipped = 0
    for row in rows:
        text = row.get("d") if isinstance(row, dict) else None
        start = parse_local_time(text) if isinstance(text, str) and len(text.strip()) >= 10 else None
        mm = safe_float(row.get("mm")) if isinstance(row, dict) else None
        if start is None or mm is None or mm < 0 or mm > MAX_HISTORY_DAY_MM:
            skipped += 1
            continue
        start = local_midnight(start)
        if start in days:
            skipped += 1
            continue
        days[start] = Day(start, next_local_midnight(start), mm)
    return [days[start] for start in sorted(days)], skipped


class HistoryResult(NamedTuple):
    days: int                     # days that were added to the archive
    mm: float                     # the rain of those days
    first: Optional[float]        # start of the first one
    last: Optional[float]         # end of the last one
    skipped: int                  # rows of the answer that were not usable
    before: int                   # days that were not added because the plugin has its own data for them (or they are
                                  # not over yet)
    known: int = 0                # of those, the finished days: the plugin has its own data for them


def apply_history(state: State, idx: int, rows: Iterable[Any], now: float) -> HistoryResult:
    """Add the days before the plugin's own data to the archive and put the marker into ``state``.

    ``state.since`` must be known (the plugin has read its first value); the days that end after the first local
    midnight of the plugin's own data are left out."""
    if state.since is None:
        raise ValueError("the plugin has not collected anything yet: the history is added after the first reading")
    days, skipped = parse_days(rows)
    limit = local_midnight(state.since)
    added = left_out = known = 0
    total = 0.0
    first = last = None
    for day in days:
        if day.end > limit or day.end > now:
            left_out += 1
            if day.end <= now:
                known += 1                      # a finished day that the plugin knows itself: proof of a real history
            continue
        added += 1
        total += day.mm
        state.archive.add_spread(day.start, day.end, day.mm)
        first = day.start if first is None else first
        last = day.end
    if added:
        state.history = HistoryMark(idx, first, last, added, now)
    elif known:
        # Domoticz has finished days and the plugin knows every one of them already (it ran for a year, or an imported
        # file covers them): there is nothing to add and nothing to wait for, so it is done and not asked again.
        # An answer without any finished day (a new device, a wrong IDX) is NOT marked: it may be asked again.
        state.history = HistoryMark(idx, state.since, state.since, 0, now)
    return HistoryResult(added, total, first, last, skipped, left_out, known)


def describe(result: HistoryResult, idx: int) -> str:
    """One line for the log and the command line tool."""
    if not result.days and result.known:
        return "Domoticz holds %d finished day(s) of rain for IDX %d and the plugin has its own data for all of them: " \
               "there is nothing to add, and the history is not read again (%d row(s) skipped)" % (
                   result.known, idx, result.skipped)
    if not result.days:
        return "Domoticz holds no finished days of rain for IDX %d: nothing was added, and the history is asked again " \
               "at the next start (%d row(s) skipped)" % (idx, result.skipped)
    return "Imported the daily rain history of IDX %d: %d days from %s to %s, %.1f mm (%d day(s) left out because the " \
           "plugin has its own data for them, %d row(s) skipped)" % (
               idx, result.days, _day(result.first), _day(result.last - 1), result.mm, result.before, result.skipped)


def _day(moment: float) -> str:
    return format_local(moment).split(" ")[0]
