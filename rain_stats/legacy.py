"""One-time import of the data file of the dzVents script *RainStats V2.6* (``rain_stats_data.json``).

The script kept two things, and the import uses each where it is trustworthy:

    allValues     a list of {date, value} with the rain that fell since the previous run (newest first). The script
                  thinned it at every save: everything younger than 7 days is complete, older entries were cut down to
                  one per hour (up to 30 days) and one per day (older), and the rain of the entries it dropped was
                  not added to the ones it kept. Only the complete part is used: from the first local midnight inside
                  the last 7 days before the script's last save
    dailyMaxima   {date: highest counter value of the day}: for a counter that is reset at midnight this is the rain of
                  that day. It is used for every day before the complete part of allValues, spread evenly over the
                  24 hours of that day

The baseline of the counter (``lastValue``) is taken over, so the rain between the script's last run and the first read
of the plugin is counted. After the import the plugin writes its own state file; the script file is never changed and
is not read again.
"""
import json
import os
import time
from typing import Any, Dict

from .constants import DAY
from .store import State
from .util import local_midnight, next_local_midnight, parse_local_time, safe_float

MAX_LEGACY_BYTES = 64 * 1024 * 1024
FULL_RESOLUTION_DAYS = 7              # the script thinned everything older than this


class LegacyError(Exception):
    """The file is not a data file of the script."""


def import_legacy(path: str, state: State, now: float) -> Dict[str, Any]:
    """Fill ``state`` from the script's data file. Returns what was done: days, entries, skipped, since, last_save."""
    try:
        if os.path.getsize(path) > MAX_LEGACY_BYTES:
            raise LegacyError("%s is larger than %d bytes" % (path, MAX_LEGACY_BYTES))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise LegacyError("cannot read %s: %s" % (path, exc))
    except ValueError as exc:
        raise LegacyError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("allValues"), list):
        raise LegacyError("%s is not a data file of RainStats V2.6 (no 'allValues' list)" % path)

    entries = []
    skipped = 0
    for row in data["allValues"]:
        t = parse_local_time(row.get("date")) if isinstance(row, dict) else None
        v = safe_float(row.get("value"), None) if isinstance(row, dict) else None
        if t is None or v is None or v < 0 or t > now + 3600:
            skipped += 1
            continue
        entries.append((t, v))

    last_save = parse_local_time(data.get("lastSave"))
    if last_save is None:
        last_save = max((t for t, _ in entries), default=now)
    full_from = last_save - FULL_RESOLUTION_DAYS * DAY
    complete_from = local_midnight(full_from)
    if complete_from < full_from:
        complete_from = next_local_midnight(full_from)

    archive = state.archive
    earliest = None
    used_entries = 0
    for t, v in sorted(entries):
        if t < complete_from:
            continue
        used_entries += 1
        if v > 0:
            archive.add_rain(t, v)
        earliest = t if earliest is None else min(earliest, t)

    days = 0
    maxima = data.get("dailyMaxima")
    for key, value in sorted((maxima if isinstance(maxima, dict) else {}).items()):
        start, mm = parse_local_time(key), safe_float(value)
        if start is None or mm is None or mm < 0:
            skipped += 1
            continue
        end = next_local_midnight(start)
        if start >= complete_from or end > now:
            continue                                   # covered by allValues, or a day that is not over
        days += 1
        archive.add_spread(start, end, mm)
        earliest = start if earliest is None else min(earliest, start)

    state.since = earliest
    last = data.get("lastValue")
    if isinstance(last, dict):
        ts, value = parse_local_time(last.get("date")), safe_float(last.get("value"))
        if ts is not None and value is not None and value >= 0:
            state.counter_ts, state.counter_value = ts, value
    return {"days": days, "entries": used_entries, "skipped": skipped, "since": earliest, "last_save": last_save,
            "complete_from": complete_from}


def describe(result: Dict[str, Any]) -> str:
    """One line for the log: what an import found."""
    if result["since"] is None:
        return "The data file of RainStats V2.6 holds no usable data"
    return "Imported the data file of RainStats V2.6: %d daily totals, %d readings of the last days (from %s); %d line(s) " \
           "skipped" % (result["days"], result["entries"], time.strftime("%Y-%m-%d", time.localtime(result["since"])),
                        result["skipped"])
