"""Small helpers shared by the modules."""
import datetime
import re
import time
from typing import Any, Optional

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """int(float(value)) for numbers and numeric text; ``default`` for everything else (also for bool)."""
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """float(value) for numbers and numeric text; ``default`` for everything else (also for bool, nan and inf)."""
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def first_number(text: Any) -> Optional[float]:
    """The first number in a text such as "12.3 mm" or "0;4,5"; None when there is none."""
    match = _NUMBER.search(str(text))
    return safe_float(match.group(0).replace(",", ".")) if match else None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def number_text(value: float, digits: int) -> str:
    """A number with a fixed count of decimals, never "-0.0": stable text, so an unchanged value compares equal."""
    text = "%.*f" % (digits, value)
    return "%.*f" % (digits, 0.0) if float(text) == 0 else text


def format_local(moment: float, with_date: bool = True) -> str:
    """"2026-11-17 08:34" (or "08:34") in local time."""
    return time.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M", time.localtime(moment))


def local_midnight(moment: float) -> float:
    """The start of the local calendar day of a Unix time."""
    t = time.localtime(moment)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def next_local_midnight(moment: float) -> float:
    """The start of the next local calendar day (the day is 23 or 25 hours long when the clock changes)."""
    t = time.localtime(moment)
    following = datetime.date(t.tm_year, t.tm_mon, t.tm_mday) + datetime.timedelta(days=1)
    return time.mktime((following.year, following.month, following.day, 0, 0, 0, 0, 0, -1))


def parse_local_time(text: Any) -> Optional[float]:
    """"2026-11-17 08:34" or "2026-11-17 08:34:12" (local time, as Domoticz and the dzVents script write it) ->
    Unix time; a bare day "2026-11-17" is its midnight. None when it is no such time."""
    text = str(text).strip()
    for pattern, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        if len(text) < size:
            continue
        try:
            return time.mktime(time.strptime(text[:size], pattern)[:8] + (-1,))
        except (ValueError, OverflowError):
            continue
    return None
