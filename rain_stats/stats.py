"""From the archive to one value per window. Pure functions of the archive and the time."""
from typing import Iterable, Optional

from .archive import Archive
from .constants import WINDOWS, Window
from .model import Stats


def compute(archive: Archive, now: float, since: Optional[float], windows: Iterable[Window] = WINDOWS,
            history_from: Optional[float] = None) -> Stats:
    """The rain (mm) and the peak rain rate (mm/h) of every window, and which windows reach back further than the data.

    A window is partial while the archive knows less time than the window is long: its value is the rain of the time
    that is known, never an estimate of the rest. The rain is known from ``history_from`` (the start of the imported
    daily history) when there is one and it is earlier than ``since``; the peak rain rate only from ``since``, because
    the history has no rates."""
    rain_from = since if history_from is None or since is None else min(since, history_from)
    if since is None:
        rain_from = history_from
    rain, peak, partial, peak_partial = {}, {}, [], []
    for window in windows:
        rain[window.key], peak[window.key] = archive.window(window, now)
        if rain_from is None or rain_from > now - window.seconds:
            partial.append(window.key)
        if since is None or since > now - window.seconds:
            peak_partial.append(window.key)
    return Stats(now, rain, peak, since, tuple(partial), tuple(peak_partial), rain_from)
