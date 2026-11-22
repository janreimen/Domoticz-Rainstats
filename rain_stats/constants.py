"""The device model, the round-robin layout, the windows and the timings of the plugin.

The units never change once released: they keep existing devices (and their history, scenes, scripts and schedules)
intact when upgrading. A new window or device gets a new unit and a new row here, never a reused one. Which windows
exist as devices is the choice of the user (the ``RS_LAST_..._DAYS`` lines of ``.env``); every window has its two
units for good.
"""
from typing import NamedTuple, Tuple

from . import __version__

PLUGIN_KEY = "Rain-Stats"
PLUGIN_NAME = "Rain Statistics"
PLUGIN_URL = "https://github.com/janreimen/Domoticz-Rainstats"
USER_AGENT = "Domoticz-Rainstats/%s" % __version__

DAY = 86400

# -- connection defaults ------------------------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
SCHEMES = ("http", "https")
MAX_ANSWER_BYTES = 1024 * 1024            # one device
MAX_LIST_BYTES = 16 * 1024 * 1024         # the device list of the command line tool

# -- settings -----------------------------------------------------------------------------------------------
COUNTER_DAILY = "daily"                   # the counter starts again at 0 every local midnight (rain today)
COUNTER_TOTAL = "total"                   # the counter only grows
COUNTER_MODES = (COUNTER_DAILY, COUNTER_TOTAL)
STATS_RAIN_RATE = "rain_rate"             # rain and peak rain rate per window
STATS_RAIN = "rain"                       # rain only
STATS_MODES = (STATS_RAIN_RATE, STATS_RAIN)

# -- timings ------------------------------------------------------------------------------------------------
HEARTBEAT_SECONDS = 10        # Domoticz caps the heartbeat at 30 s; polling is scheduled by time, not by count
DEFAULT_INTERVAL = 60         # seconds between readings of the rain sensor
MIN_INTERVAL = 10
MAX_INTERVAL = 3600
HTTP_TIMEOUT = 10             # seconds per HTTP request
STOP_TIMEOUT = 5              # seconds the plugin waits for a running poll when it stops
MAX_BACKOFF = 300             # longest wait between polls after failures
STATS_SECONDS = 60            # the windows slide with the clock: recomputed at least this often
REFRESH_SECONDS = 300         # rewrite an unchanged sensor value this often, so Domoticz' sensor time-out never fires
CREATE_RETRY_SECONDS = 300    # after Domoticz refused to create a device, ask again no sooner than this
DEFAULT_SAVE_INTERVAL = 900   # write the state file at most this often (and always when the plugin stops)

# -- plausibility -------------------------------------------------------------------------------------------
DEFAULT_MAX_DELTA = 100.0     # more rain than this (mm) between two readings is not believed: a glitch, not rain
DEFAULT_RATE_DIVISOR = 1      # Domoticz' RainRate in the JSON API is mm/h; 100 for an API that answers mm/h x 100
MAX_PLAUSIBLE_RATE = 3000.0   # mm/h; a higher rate is a unit or sensor error and never becomes a peak
MAX_RATE_GAP = 1800           # without a rate from the sensor, one is derived from readings at most this far apart
RESET_GRACE = 3600            # a daily counter that goes down within this many seconds after local midnight was reset
MAX_HISTORY_DAY_MM = 1000.0   # more rain than this (mm) in one day of the history is not believed

# -- the round-robin store ----------------------------------------------------------------------------------
# Two rings per measure (rain sum in mm, peak rain rate in mm/h). Every reading is added to both tiers, so no
# consolidation step is needed and no value is ever derived from a rounded one.
#
#   fine    5 minutes  x  2016 slots =   7 days   answers the windows of up to 7 days (1 d, 2 d, 7 d)
#   coarse  15 minutes x 35040 slots = 365 days   answers the windows of 14 days to 365 days
#
# The rain of a window is made of whole slots: the current (still running) slot plus the N-1 before it, which cover
# between N-1 and N slots of real time, plus the part of the slot before those that still lies inside the period (the
# edge), taken in proportion to how much of it does: ``Ring.edge``. The rain rings keep ``SPARE_SLOTS`` slots more than
# the longest window for that. The peak of a window is made of whole slots only: a peak cannot be taken in proportion.
FINE = "fine"
COARSE = "coarse"
SPARE_SLOTS = 1               # slots a rain ring keeps beyond the ones of the longest window of its tier


class Tier(NamedTuple):
    key: str
    resolution: int           # seconds per slot
    slots: int                # slots the longest window of the tier is made of


TIERS = (
    Tier(FINE, 300, 2016),
    Tier(COARSE, 900, 35040),
)


def tier_of(key: str) -> Tier:
    return next(t for t in TIERS if t.key == key)


class Window(NamedTuple):
    key: str                  # used in the state file and in the device keys
    label: str                # used in device names
    seconds: int
    tier: str                 # the tier that answers it
    default: bool             # created when the .env does not say otherwise
    rain_unit: int            # the unit of its rain device ...
    peak_unit: int            # ... and of its peak rain rate device: both are fixed for good

    @property
    def days(self) -> int:
        return self.seconds // DAY

    @property
    def env_key(self) -> str:
        """The line of ``.env`` that switches it on or off: RS_LAST_001_DAYS."""
        return "RS_LAST_%03d_DAYS" % self.days


def _window(days: int, rain_unit: int, peak_unit: int, default: bool) -> Window:
    fine = tier_of(FINE)
    tier = FINE if days * DAY <= fine.resolution * fine.slots else COARSE      # the fine ring when it is long enough
    return Window("%dd" % days, "%d d" % days, days * DAY, tier, default, rain_unit, peak_unit)


# Every window that can exist. The first eight are the ones of the first version and are on by default; the others
# are off until the .env says Y. The units 17 and 18 are the status and info devices, so the later windows start at 19
# (rain 19-21, peak rate 22-24).
WINDOWS = (
    _window(1, 1, 9, True),
    _window(2, 2, 10, True),
    _window(7, 3, 11, True),
    _window(14, 4, 12, True),
    _window(30, 5, 13, True),
    _window(60, 19, 22, False),
    _window(90, 6, 14, True),
    _window(120, 20, 23, False),
    _window(180, 7, 15, True),
    _window(360, 21, 24, False),
    _window(365, 8, 16, True),
)

STATE_VERSION = 1             # version of the state file layout


def window_slots(window: Window) -> int:
    """How many slots of its tier the window is made of."""
    return window.seconds // tier_of(window.tier).resolution


# -- device kinds -------------------------------------------------------------------------------------------
KIND_RAIN = "rain"            # Custom Sensor "mm", one decimal (the rain sensor reports tenths of a millimetre)
KIND_RATE = "rate"            # Custom Sensor "mm/h", two decimals
KIND_FLAG = "flag"            # On/Off switch that mirrors the state of the rain sensor (read-only)
KIND_TEXT = "text"            # Text device (read-only)

# Domoticz TypeName of each kind (the documented way to create a device).
TYPENAME_CUSTOM = "Custom"
TYPENAME_SWITCH = "Switch"
TYPENAME_TEXT = "Text"
SWITCHTYPE_ONOFF = 0
SUBTYPE_CUSTOM = 31           # what Domoticz makes of TypeName "Custom" (sTypeCustom, 0x1F in hardwaretypes.h)
LABEL_RAIN = "mm"
LABEL_RATE = "mm/h"
DECIMALS_RAIN = 1
DECIMALS_RATE = 2
MAX_TEXT = 200                # a Domoticz text device holds this many characters


class Device(NamedTuple):
    unit: int
    key: str
    name: str
    kind: str
    label: str                # unit label of a Custom Sensor ("" for the others)
    window: str = ""          # key of the Window a statistics device shows ("" for status and info)


UNIT_STATUS = 17
UNIT_INFO = 18

RAIN_DEVICES: Tuple[Device, ...] = tuple(
    Device(w.rain_unit, "rain_" + w.key, "Rain " + w.label, KIND_RAIN, LABEL_RAIN, w.key) for w in WINDOWS)

PEAK_DEVICES: Tuple[Device, ...] = tuple(
    Device(w.peak_unit, "peak_" + w.key, "Peak Rain Rate " + w.label, KIND_RATE, LABEL_RATE, w.key) for w in WINDOWS)

FIXED_DEVICES: Tuple[Device, ...] = (
    Device(UNIT_STATUS, "status", "Rain Stats Status", KIND_FLAG, ""),
    Device(UNIT_INFO, "info", "Rain Stats Info", KIND_TEXT, ""),
)

ALL_DEVICES: Tuple[Device, ...] = RAIN_DEVICES + PEAK_DEVICES + FIXED_DEVICES
