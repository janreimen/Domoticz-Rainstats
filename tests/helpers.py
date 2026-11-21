"""Shared test helpers: paths, a fixed time zone, controllable clocks, a scripted source, the whole stack."""
import atexit
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "tools")]

# Every test runs in central European time, whatever the machine says: local midnight, daylight saving and the
# local-time text of Domoticz are part of what the plugin does. A POSIX rule needs no time zone database.
os.environ["TZ"] = "CET-1CEST,M3.5.0/2,M10.5.0/3"
if hasattr(time, "tzset"):
    time.tzset()

import domoticz_stub as dz  # noqa: E402
from fake_domoticz import FakeDomoticz, rain_device  # noqa: E402,F401  (re-exported for the test modules)
from rain_stats.api import ApiError  # noqa: E402
from rain_stats.config import Settings  # noqa: E402
from rain_stats.controller import Controller  # noqa: E402
from rain_stats.devices import DeviceManager  # noqa: E402
from rain_stats.logger import Logger  # noqa: E402
from rain_stats.model import Reading  # noqa: E402


def local(text):
    """Unix time of a local time written "2026-11-18 06:48" (or with seconds)."""
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return time.mktime(time.strptime(text, fmt)[:8] + (-1,))


class Clock:
    """A controllable clock; used as the monotonic clock and as the wall clock."""

    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class SyncThread:
    """What ``spawn`` returns when the poll already ran in the calling thread."""

    @staticmethod
    def join(timeout=None):
        return None

    @staticmethod
    def is_alive():
        return False


def sync_spawn(target):
    target()
    return SyncThread()


class ScriptedClient:
    """Stands in for ``api.Client``: answers with what the test put in ``counter`` / ``rate`` / ``error``."""
    where = "scripted:0"

    def __init__(self, wall):
        self.wall = wall
        self.counter = 0.0
        self.rate = 0.0
        self.timed_out = False
        self.error = None
        self.reads = 0
        self.history = []                   # the rows of the daily rain history this client answers
        self.history_error = None
        self.history_reads = 0

    def read(self, rain_idx, rate_idx, divisor, now, halt=None):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return Reading(now(), self.counter, self.rate, self.timed_out, "scripted counter=%s rate=%s" % (self.counter, self.rate))


    def get_rain_history(self, idx):
        self.history_reads += 1
        if self.history_error is not None:
            raise self.history_error
        return list(self.history)


def make_logger(level=0):
    return Logger(dz, level)


class Stack:
    """Settings, scripted source, device manager and controller on controllable clocks."""

    def __init__(self, start="2026-11-18 06:00", state_file=None, level=0, with_peaks=True, interval=60, **settings_kw):
        dz.reset()
        self.tmp = tempfile.mkdtemp(prefix="rainstats-test-")
        atexit.register(shutil.rmtree, self.tmp, True)
        self.clock = Clock(1000.0)              # the monotonic clock: only its differences matter
        self.wall = Clock(local(start))
        self.log = make_logger(level)
        self.state_file = state_file or os.path.join(self.tmp, "state.json")
        self.settings = Settings(rain_idx=1161, interval=interval, state_file=self.state_file, **settings_kw)
        self.client = ScriptedClient(self.wall)
        self.devices = DeviceManager(dz, lambda: dz.Devices, self.log, with_peaks=with_peaks, clock=self.clock,
                                     windows=self.settings.windows)
        self.controller = Controller(self.settings, self.client, self.devices, self.log, clock=self.clock,
                                     wall_clock=self.wall, spawn=sync_spawn)

    def start(self):
        self.controller.start()
        self.beat()                              # the poll that start() began is collected on the next heartbeat
        return self

    def beat(self):
        """A heartbeat that starts a poll if one is due and then a second one that applies its result."""
        self.controller.on_heartbeat()
        self.controller.on_heartbeat()

    def step(self, seconds=60, counter=None, rate=None, error=None):
        """Let time pass, set what the sensor says, and run the poll that is due."""
        self.clock.advance(seconds)
        self.wall.advance(seconds)
        if counter is not None:
            self.client.counter = counter
        if rate is not None:
            self.client.rate = rate
        self.client.error = error
        self.beat()

    def value(self, unit):
        return dz.Devices[unit].sValue

    def errors(self):
        return dz.messages("error")


def fail(message="refused", kind="network"):
    return ApiError(message, kind)


def read_text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def write_legacy(path, last_save="2026-11-18 05:59", values=(), maxima=None, last_value=("2026-11-18 05:59", 0.0)):
    """A data file of the dzVents script RainStats V2.6, the way the script writes it (newest value first)."""
    import json
    rows = [{"date": d, "value": v} for d, v in sorted(values, reverse=True)]
    data = {"v": "2.6", "lastSave": last_save, "lastValue": {"date": last_value[0], "value": last_value[1]},
            "allValues": rows, "dailyMaxima": dict(maxima or {})}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def history_rows(today, count, mm=1.0):
    """What Domoticz answers for the daily rain history: ``count`` finished days before the local midnight ``today``,
    oldest first, ``mm`` each, and the row of today that is not finished."""
    rows, midnight = [], today
    for _ in range(count):
        t = time.localtime(midnight - 12 * 3600)                      # a time inside the day before, clock change or not
        midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        rows.append({"d": time.strftime("%Y-%m-%d", time.localtime(midnight)), "mm": "%.1f" % mm})
    rows.reverse()
    rows.append({"d": time.strftime("%Y-%m-%d", time.localtime(today)), "mm": "2.5"})
    return rows


def read_path(path, binary=False):
    """The whole content of a file, closed again."""
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as fh:
        return fh.read()


def load_json(path):
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path, data):
    import json
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
