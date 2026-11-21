"""Poll scheduling, error policy, saving and logging. Knows neither Domoticz nor HTTP details.

A poll (one or two requests to the Domoticz JSON API) runs in a background thread, so the Domoticz plugin thread never
waits for the network: it starts a poll when one is due, and on every heartbeat it picks up the result of a finished
one. The thread never touches Domoticz, the archive or the log: what it has to say travels in its result. The plugin
thread then works out the rain that fell, adds it to the archive, recomputes the windows and writes the devices.

Error policy:

    * every problem is logged ONCE as an error, repeats go to the verbose log, and recovery is logged once
    * after a failed poll the next attempt waits longer (backoff, capped)
    * the "Rain Stats Status" switch follows the source: On while polls succeed, Off while they fail
    * a failed poll NEVER changes the statistics: they are computed from the archive, keep sliding with the clock, and
      the "Rain Stats Info" text says that the source is not reachable. Rain that falls while the source cannot be
      read is counted at the next successful read (the counter says how much fell meanwhile)
    * the state file is written every RS_SAVE_INTERVAL seconds when something changed, and when the plugin stops
    * RS_HISTORY_IDX: after the first reading the daily rain history of that device is read ONCE (in the background,
      like a poll) and added to the days before the plugin's own data. A reading that failed because of the network is
      tried again at the next successful poll, every other problem is logged once and the history is left alone until
      the next start. When it is imported the state file says so, and the device is never asked again
"""
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Union

from . import store
from .api import NETWORK, ApiError
from .constants import MAX_BACKOFF, MAX_PLAUSIBLE_RATE, MAX_RATE_GAP, STATS_SECONDS, STOP_TIMEOUT, UNIT_STATUS
from .counter import Counter
from .history import apply_history
from .history import describe as describe_history
from .legacy import LegacyError, describe, import_legacy
from .model import Change, Reading, Stats
from .stats import compute
from .store import State, StateError
from .util import format_local, number_text, shorten


class PollResult(NamedTuple):
    reading: Optional[Reading] = None
    error: Optional[ApiError] = None


class HistoryOutcome(NamedTuple):
    rows: Optional[List[Any]] = None
    error: Optional[ApiError] = None


def start_thread(target: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=target, name="RainStats-poll", daemon=True)
    thread.start()
    return thread


class Controller:
    def __init__(self, settings: Any, client: Any, devices: Any, logger: Any,
                 clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time,
                 spawn: Callable[[Callable[[], None]], Any] = start_thread) -> None:
        self.settings = settings
        self.client = client
        self.devices = devices
        self.log = logger
        self.clock = clock
        self.wall_clock = wall_clock
        self.spawn = spawn
        self.counter = Counter(settings.counter, settings.max_delta)
        self.state = State()
        self.stats: Optional[Stats] = None
        self.rate = 0.0
        self.next_poll_at = 0.0
        self.next_stats_at = 0.0
        self.next_save_at = 0.0
        self.failures = 0
        self.connected = False
        self._thread: Any = None                        # the running poll; set and cleared on the plugin thread only
        self._history_thread: Any = None                # the running read of the history; plugin thread only
        self._history_closed = False                    # no (further) attempt to read the history in this run
        self._results: "queue.Queue[Union[PollResult, HistoryOutcome]]" = queue.Queue()
        self._halt = threading.Event()
        self._problems: Dict[str, str] = {}             # problem key -> kind of the last one logged
        self._told: set = set()                         # kinds of counter notes the log already told once
        self._dirty = False                             # the state differs from the state file
        self._down_since: Optional[float] = None
        self._timeout_told = False

    # -- life cycle -----------------------------------------------------------------------------------------
    def start(self) -> None:
        self.devices.ensure_devices()
        self._load_state()
        self._show_stats()
        self._begin_poll()

    def stop(self) -> None:
        self._halt.set()
        thread = self._thread
        if thread is not None:
            thread.join(STOP_TIMEOUT)
            if thread.is_alive():
                self.log.Error("A poll of the rain device did not end within %d s; it is abandoned" % STOP_TIMEOUT)
        self._thread = None
        history = self._history_thread
        if history is not None:
            history.join(STOP_TIMEOUT)
            if history.is_alive():
                self.log.Error("Reading the rain history did not end within %d s; it is abandoned" % STOP_TIMEOUT)
        self._history_thread = None
        self._save(force=True)

    def on_heartbeat(self) -> None:
        self._collect()
        if self._thread is None and self.clock() >= self.next_poll_at:
            self._begin_poll()
        if self.clock() >= self.next_stats_at:
            self._show_stats()
        self._save()

    @property
    def polling(self) -> bool:
        return self._thread is not None

    # -- state ----------------------------------------------------------------------------------------------
    def _load_state(self) -> None:
        path, now = self.settings.state_file, self.wall_clock()
        state: Optional[State] = None
        try:
            state = store.load(path, now)
        except StateError as exc:
            self.log.Error(str(exc))
        if state is not None:
            self.state = state
            self.log.Log("Loaded the state file %s: %s" % (path, self._since_text(state.since)))
            if self.settings.legacy_file:
                self.log.Basic("RS_LEGACY_FILE is set, but a state file exists: the data file is not imported again")
            mark = state.history
            if mark is not None and self.settings.history_idx is not None:
                self.log.Basic("RS_HISTORY_IDX is set, but the history of IDX %d (%d days from %s) is in the state file: "
                               "it is not read again. To read it again, reset the statistics (delete the state file)"
                               % (mark.idx, mark.days, format_local(mark.first).split(" ")[0]))
        else:
            self.state = State()
            if self.settings.legacy_file and not os.path.exists(path):
                self._import_legacy()
            else:
                self.log.Log("No usable state file yet (%s): starting with an empty archive" % path)
        self.counter.restore(self.state.counter_ts, self.state.counter_value)

    def _import_legacy(self) -> None:
        path = self.settings.legacy_file
        try:
            result = import_legacy(path, self.state, self.wall_clock())
        except LegacyError as exc:
            self.state = State()
            self.log.Error("Cannot import the data file of RainStats V2.6: %s. Starting with an empty archive" % exc)
            return
        self.log.Log(describe(result))
        self._dirty = True
        self._save(force=True)                          # from now on the plugin has its own file: the import runs once

    def _save(self, force: bool = False) -> None:
        if not self._dirty or (not force and self.clock() < self.next_save_at):
            return
        path = self.settings.state_file
        try:
            store.save(path, self.state, self.wall_clock())
        except OSError as exc:
            self.next_save_at = self.clock() + min(self.settings.save_interval, 300)
            self._report("save", "Cannot write the state file %s: %s. The statistics stay in memory and are lost when the "
                         "plugin stops" % (path, exc), "write")
            return
        self._dirty = False
        self.next_save_at = self.clock() + self.settings.save_interval
        self._recovered("save", "The state file can be written again")
        self.log.Verbose("State saved to %s" % path)

    # -- polling --------------------------------------------------------------------------------------------
    def _begin_poll(self) -> None:
        self.next_poll_at = self.clock() + self.settings.interval     # an unexpected exception must not cause a retry storm
        self.log.Debug("Reading the rain device")
        self._thread = self.spawn(self._poll_in_thread)

    def _poll_in_thread(self) -> None:
        """Runs in the background thread: reads the device, queues ONE result, never raises, never logs."""
        try:
            s = self.settings
            result = PollResult(reading=self.client.read(s.rain_idx, s.rate_idx, s.rate_divisor, self.wall_clock,
                                                         self._halt.is_set))
        except ApiError as exc:
            result = PollResult(error=exc)
        except Exception as exc:
            result = PollResult(error=ApiError("%s: %s" % (type(exc).__name__, exc), "unexpected"))
        self._results.put(result)

    def _collect(self) -> None:
        """Apply the results of finished polls (plugin thread)."""
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                return
            if isinstance(result, HistoryOutcome):
                self._history_thread = None
                if not self._halt.is_set():
                    self._history_arrived(result)
                continue
            self._thread = None
            if self._halt.is_set():
                continue
            if result.error is not None:
                self._poll_failed(result.error)
            else:
                self._poll_worked(result.reading)

    def _poll_worked(self, reading: Reading) -> None:
        self.failures = 0
        self._down_since = None
        if not self.connected:
            self.connected = True
            self.log.Log("Connected to Domoticz at %s: the rain counter reads %s mm" % (
                self.client.where, number_text(reading.counter, 2)))
        self._recovered("source", "The rain device can be read again")
        self.log.Debug("Read: %s" % reading.raw)
        self.devices.ensure_devices()                   # devices Domoticz did not accept at first, unit labels
        self.devices.set_status(True)
        self._apply(reading)
        self._begin_history()
        self._show_stats()

    def _poll_failed(self, exc: ApiError) -> None:
        self.failures += 1
        self.connected = False
        if self._down_since is None:
            self._down_since = self.wall_clock()
        self.devices.ensure_devices()
        self.devices.set_status(False)
        self._report("source", "Reading the rain device failed: %s" % exc, exc.kind)
        interval = self.settings.interval
        delay = max(interval, min(interval * 2 ** min(self.failures - 1, 6), MAX_BACKOFF))
        self.next_poll_at = self.clock() + delay
        self.log.Basic("Failure %d, next attempt in %d s" % (self.failures, delay))
        self._show_stats()

    # -- the history of Domoticz ----------------------------------------------------------------------------
    def _begin_history(self) -> None:
        """Start reading the daily rain history, once, when it is wanted and the plugin knows where its own data begin."""
        idx = self.settings.history_idx
        if (idx is None or self.state.history is not None or self._history_closed or self._history_thread is not None
                or self.state.since is None):
            return
        self.log.Basic("Reading the daily rain history of IDX %d (once)" % idx)
        self._history_thread = self.spawn(self._history_in_thread)

    def _history_in_thread(self) -> None:
        """Runs in the background thread: ONE request, ONE result, never raises, never logs."""
        try:
            outcome = HistoryOutcome(rows=self.client.get_rain_history(self.settings.history_idx))
        except ApiError as exc:
            outcome = HistoryOutcome(error=exc)
        except Exception as exc:
            outcome = HistoryOutcome(error=ApiError("%s: %s" % (type(exc).__name__, exc), "unexpected"))
        self._results.put(outcome)

    def _history_arrived(self, outcome: HistoryOutcome) -> None:
        idx = self.settings.history_idx
        if outcome.error is not None:
            if outcome.error.kind != NETWORK:
                self._history_closed = True             # a refusal or nonsense does not get better by asking again
            self._report("history", "Reading the rain history failed: %s%s" % (
                outcome.error, "" if self._history_closed else ". It is tried again at the next reading"),
                outcome.error.kind)
            return
        try:
            result = apply_history(self.state, idx, outcome.rows or [], self.wall_clock())
        except ValueError as exc:
            self._history_closed = True
            self._report("history", "Cannot use the rain history: %s" % exc, "unusable")
            return
        self._recovered("history", "The rain history could be read")
        self._history_closed = True                     # done (or nothing to add: not asked again in this run)
        self.log.Log(describe_history(result, idx))
        if result.days:
            self._dirty = True
            self._save(force=True)                      # from now on the state file says: the history is in
        self._show_stats()

    # -- the rain -------------------------------------------------------------------------------------------
    def _apply(self, reading: Reading) -> None:
        """What a reading adds: the rain since the previous one and the rain rate."""
        ts, archive = reading.ts, self.state.archive
        previous = self.counter.last_ts
        change = self.counter.update(ts, reading.counter)
        self.state.counter_ts, self.state.counter_value = self.counter.last_ts, self.counter.last_value
        self._tell(change)
        if self.state.since is None:
            self.state.since = change.since if change.mm > 0 else ts
        if change.mm > 0:
            archive.add_spread(change.since, ts, change.mm)
        rate = self._rate_of(reading, change, previous)
        if rate > 0:
            archive.note_rate(ts, rate)
        self.rate = rate
        self._dirty = True
        self.log.Verbose("Counter %s mm, rate %s mm/h, %s mm added (%s)" % (
            number_text(reading.counter, 2), number_text(rate, 2), number_text(change.mm, 2), change.kind))

    def _rate_of(self, reading: Reading, change: Change, previous: Optional[float]) -> float:
        """The rain rate in mm/h: from the source; without one, from the rain between two close readings."""
        if reading.timed_out:
            if not self._timeout_told:
                self._timeout_told = True
                self.log.Log("Domoticz marks the rain device as not reporting (sensor time-out): its rain rate is taken as 0")
            return 0.0
        self._timeout_told = False
        rate = 0.0
        if reading.rate is not None:
            rate = max(0.0, reading.rate)
        elif change.kind == "delta" and previous is not None and 0 < reading.ts - previous <= MAX_RATE_GAP:
            rate = change.mm / (reading.ts - previous) * 3600.0
        if rate > MAX_PLAUSIBLE_RATE:
            self._tell(Change(0.0, reading.ts, "rate", "A rain rate of %s mm/h is more than %d mm/h: taken for a unit or "
                              "sensor error (see RS_RATE_DIVISOR), it is not counted as a peak"
                              % (number_text(rate, 1), MAX_PLAUSIBLE_RATE)))
            return 0.0
        return rate

    def _tell(self, change: Change) -> None:
        """Say once what is unusual about a reading; the next time the same thing happens it goes to the verbose log."""
        if not change.note:
            return
        if change.kind in self._told:
            self.log.Verbose(change.note)
            return
        self._told.add(change.kind)
        (self.log.Error if change.kind == "implausible" else self.log.Log)(change.note)

    # -- showing --------------------------------------------------------------------------------------------
    @staticmethod
    def _since_text(since: Optional[float]) -> str:
        return "no data collected yet" if since is None else "collecting since %s" % format_local(since)

    def _show_stats(self) -> None:
        """Recompute the windows (they slide with the clock) and write the devices."""
        now = self.wall_clock()
        mark = self.state.history
        stats = compute(self.state.archive, now, self.state.since, self.settings.windows,
                        None if mark is None else mark.first)
        self.stats = stats
        self.devices.show(stats)
        self.devices.set_info(self._info(stats))
        self.next_stats_at = self.clock() + STATS_SECONDS

    def _info(self, stats: Stats) -> str:
        if self._down_since is not None:
            return "Source not reachable since %s: the statistics go on from the stored data" % format_local(
                self._down_since, with_date=False)
        if not self.connected:
            return "Starting: %s" % self._since_text(stats.since)
        text = "OK %s: %s mm/h; %s" % (format_local(stats.now, with_date=False), number_text(self.rate, 1),
                                       self._since_text(stats.since))
        mark = self.state.history
        if mark is not None:
            text += "; history from %s" % format_local(mark.first).split(" ")[0]
        if stats.partial:
            text += "; windows from %s on are partial" % stats.partial[0]
        if self.settings.with_peaks and stats.peak_partial and stats.peak_partial != stats.partial:
            text += "; peaks from %s on are partial" % stats.peak_partial[0]
        return shorten(text, 200)

    # -- problems -------------------------------------------------------------------------------------------
    def _report(self, key: str, message: str, kind: str) -> None:
        if self._problems.get(key) == kind:
            self.log.Verbose(message)                           # same problem as before: not an error again
            return
        self._problems[key] = kind
        self.log.Error(message)

    def _recovered(self, key: str, message: str) -> None:
        if self._problems.pop(key, None) is not None:
            self.log.Log(message)

    # -- commands -------------------------------------------------------------------------------------------
    def on_command(self, unit: int, command: Any, level: Any = None) -> None:
        name = self.devices.name_of(unit)
        self.log.Log("Command received: unit %d (%s) %s" % (unit, name or "unknown", command))
        if self.devices.owns(unit):
            what = "shows whether the rain device can be read" if unit == UNIT_STATUS else "only shows what the rain data say"
            self.log.Log("'%s' %s; it cannot be switched" % (name or "unit %d" % unit, what))
        else:
            self.log.Log("Unit %d is not a Rain Statistics device, the command is ignored" % unit)
        self.devices.restore(unit)
