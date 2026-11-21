"""``python3 -m rain_stats``: the engine of the plugin without Domoticz.

Five things, all read-only (nothing is written, no state file is created or moved):

    (default)       read the rain device from the Domoticz JSON API and show what the plugin makes of it: the fields
                    it takes the numbers from, the counter and the rate. Use it before adding the hardware, and
                    whenever the devices show something unexpected
    --find          list the rain devices of Domoticz with their IDX (the number the hardware setting asks for)
    --state FILE    show the statistics of a state file
    --legacy FILE   read the data file of the dzVents script RainStats V2.6 into an empty archive and show the
                    statistics the plugin would start with
    --history IDX   read the daily rain history that Domoticz keeps of the device IDX (what RS_HISTORY_IDX reads once)
                    and show the statistics the plugin would start with if it started today

The password for Domoticz is taken from the environment variable RS_PASSWORD (a command-line argument would be visible
in the process list).

Exit status: 0 ok, 1 bad options or files, 2 Domoticz cannot be read.
"""
import argparse
import json
import os
import sys
import time
from typing import List, Optional, Tuple

from . import __version__
from .api import ApiError, Client
from .config import Settings, SettingsError, default_state_file
from .constants import DEFAULT_HOST, DEFAULT_PORT, SCHEMES, WINDOWS, Window
from .history import apply_history
from .history import describe as describe_history
from .legacy import LegacyError, describe, import_legacy
from .model import Stats
from .stats import compute
from .store import State, StateError, load
from .util import format_local, local_midnight, number_text


class _Parser(argparse.ArgumentParser):
    """argparse that writes to the streams ``main`` was given, and whose usage errors exit with 1.

    The status 2 belongs to 'Domoticz cannot be read', as documented above; argparse' own 2 would mix the two up."""

    streams: tuple = ()

    def _print_message(self, message, file=None):          # argparse hook
        if message:
            out, err = self.streams or (sys.stdout, sys.stderr)
            (out if file is sys.stdout else err).write(message)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, "%s: error: %s\n" % (self.prog, message))


def _parser(out=None, err=None) -> argparse.ArgumentParser:
    p = _Parser(prog="python3 -m rain_stats", description="Show what the Rain Statistics plugin reads and computes.")
    p.streams = (out or sys.stdout, err or sys.stderr)
    p.add_argument("--rain-idx", type=int, metavar="IDX", help="IDX of the rain device (required to read Domoticz)")
    p.add_argument("--find", action="store_true", help="list the rain devices of Domoticz and their IDX")
    p.add_argument("--rate-idx", type=int, metavar="IDX", help="IDX of a separate rain rate device")
    p.add_argument("--host", default=DEFAULT_HOST, help="Domoticz address (default %s)" % DEFAULT_HOST)
    p.add_argument("--port", default=str(DEFAULT_PORT), help="Domoticz port (default %d)" % DEFAULT_PORT)
    p.add_argument("--scheme", choices=SCHEMES, default=None, help="http or https (default: RS_SCHEME or http)")
    p.add_argument("--user", default="", help="Domoticz user (the password is read from RS_PASSWORD)")
    p.add_argument("--ca-file", default="", help="CA bundle for https (default: RS_CA_FILE)")
    p.add_argument("--state", metavar="FILE", nargs="?", const=default_state_file(),
                   help="show the statistics of a state file (default: %s)" % default_state_file())
    p.add_argument("--legacy", metavar="FILE", help="import the data file of RainStats V2.6 and show the statistics")
    p.add_argument("--history", type=int, metavar="IDX",
                   help="read the daily rain history of the device IDX and show what the plugin would start with")
    p.add_argument("--json", action="store_true", help="machine readable output")
    p.add_argument("--version", action="version", version="rain_stats " + __version__)
    return p


def _windows() -> Tuple[Window, ...]:
    """The windows the plugin shows: the ones the .env switches on (all the default ones without a .env)."""
    try:
        return Settings.from_parameters({"Mode1": "1"}).windows
    except SettingsError:
        return WINDOWS


def _stats_rows(stats: Stats, windows: Tuple[Window, ...]) -> List[dict]:
    return [{"window": w.key, "rain_mm": round(stats.rain[w.key], 2), "peak_mm_h": round(stats.peak[w.key], 2),
             "partial": w.key in stats.partial, "peak_partial": w.key in stats.peak_partial} for w in windows]


def _print_stats(stats: Stats, windows: Tuple[Window, ...], out) -> None:
    print("window   rain (mm)   peak rate (mm/h)", file=out)
    for w in windows:
        notes = ("   (rain partial)" if w.key in stats.partial else "") + (
            "   (peak partial)" if w.key in stats.peak_partial else "")
        print("%-7s %10s   %10s%s" % (w.key, number_text(stats.rain[w.key], 2), number_text(stats.peak[w.key], 2), notes),
              file=out)
    print("\ncollecting since %s" % ("-" if stats.since is None else format_local(stats.since)), file=out)
    if stats.rain_from is not None and stats.since is not None and stats.rain_from < stats.since:
        print("rain known since %s (the history of Domoticz, one total per day)" % format_local(stats.rain_from), file=out)


def _show_state(state: State, now: float, as_json: bool, out, extra: Optional[dict] = None) -> int:
    windows = _windows()
    mark = state.history
    stats = compute(state.archive, now, state.since, windows, None if mark is None else mark.first)
    if as_json:
        json.dump(dict(extra or {}, since=state.since, rain_from=stats.rain_from, windows=_stats_rows(stats, windows)),
                  out, indent=2)
        out.write("\n")
    else:
        _print_stats(stats, windows, out)
    return 0


def _find(client: Client, divisor: float, as_json: bool, out) -> int:
    devices = client.list_rain_devices(divisor)
    if as_json:
        json.dump([d._asdict() for d in devices], out, indent=2)
        out.write("\n")
        return 0
    if not devices:
        print("Domoticz at %s has no device of type Rain that is in use." % client.where, file=out)
        return 0
    print("  IDX  name                              rain (mm)  rate (mm/h)  hardware", file=out)
    for d in devices:
        print("%5d  %-32s %9s  %11s  %s" % (d.idx, d.name[:32], "-" if d.counter is None else number_text(d.counter, 2),
                                           "-" if d.rate is None else number_text(d.rate, 2), d.hardware), file=out)
    return 0


def main(argv: Optional[List[str]] = None, out=None, err=None, now: Optional[float] = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        args = _parser(out, err).parse_args(argv)
    except SystemExit as exc:                               # --help, --version (0) and usage errors (1): argparse exits
        return exc.code if isinstance(exc.code, int) else 1
    now = time.time() if now is None else now          # a test tells the time

    if args.legacy:
        state = State()
        try:
            result = import_legacy(args.legacy, state, now)
        except LegacyError as exc:
            print("error: %s" % exc, file=err)
            return 1
        if not args.json:
            print(describe(result) + "\n", file=out)
        return _show_state(state, now, args.json, out, {"import": {k: v for k, v in result.items() if k != "since"}})

    if args.state:
        try:
            state = load(args.state, now, move=False)
        except StateError as exc:
            print("error: %s" % exc, file=err)
            return 1
        if state is None:
            print("error: there is no state file %s" % args.state, file=err)
            return 1
        return _show_state(state, now, args.json, out)

    if args.rain_idx is None and not args.find and args.history is None:
        print("error: --rain-idx is required to read Domoticz (or use --find / --history / --state / --legacy)", file=err)
        return 1
    params = {"Address": args.host, "Port": args.port, "Username": args.user,
              "Password": os.environ.get("RS_PASSWORD", ""), "Mode1": str(args.rain_idx or args.history or 1),
              "Mode2": "" if args.rate_idx is None else str(args.rate_idx)}
    try:
        settings = Settings.from_parameters(params)
    except SettingsError as exc:
        print("error: %s" % exc, file=err)
        return 1
    if args.scheme:
        settings.scheme = args.scheme
    if args.ca_file:
        settings.ca_file = args.ca_file
    for warning in settings.warnings + settings.notes:
        print("note: %s" % warning, file=err)
    try:
        client = Client(settings.scheme, settings.host, settings.port, settings.username, settings.password,
                        settings.http_timeout, settings.ca_file)
        if args.find:
            return _find(client, settings.rate_divisor, args.json, out)
        if args.history is not None:
            rows = client.get_rain_history(args.history)
            state = State()
            state.since = local_midnight(now)                   # as if the plugin read its first value today
            result = apply_history(state, args.history, rows, now)
            if not args.json:
                print(describe_history(result, args.history) + "\n", file=out)
            return _show_state(state, now, args.json, out, {"history": {
                "idx": args.history, "days": result.days, "mm": round(result.mm, 1), "skipped": result.skipped,
                "first": result.first, "last": result.last}})
        reading = client.read(settings.rain_idx, settings.rate_idx, settings.rate_divisor, time.time)
    except (ApiError, OSError) as exc:
        print("error: Domoticz cannot be read: %s" % exc, file=err)
        return 2
    if args.json:
        json.dump({"counter_mm": reading.counter, "rate_mm_h": reading.rate, "timed_out": reading.timed_out,
                   "raw": reading.raw}, out, indent=2)
        out.write("\n")
        return 0
    print("Domoticz at %s answered." % client.where, file=out)
    print("  %s" % reading.raw, file=out)
    print("  rain counter : %s mm (%s counter)" % (number_text(reading.counter, 2), settings.counter), file=out)
    print("  rain rate    : %s" % ("none (the plugin derives it from the counter)" if reading.rate is None
                                  else "%s mm/h" % number_text(reading.rate, 2)), file=out)
    if reading.timed_out:
        print("  Domoticz marks this device as timed out: the plugin takes the rate as 0", file=out)
    return 0
