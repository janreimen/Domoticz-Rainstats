"""Plugin settings, parsed and validated from the Domoticz ``Parameters`` dict and the plugin-local ``.env``.

Hardware-page fields (see the manifest in plugin.py):

    Address   the Domoticz host that is asked for the weather data (default 127.0.0.1)
    Port      its port (default 8080)
    Username  a Domoticz user, only when the API asks for one (optional)
    Password  its password (optional; masked in the log)
    Mode1     IDX of the rain device (required)
    Mode2     IDX of a separate rain rate device (optional; empty = the rate of the rain device itself)
    Mode3     update interval in seconds
    Mode4     rain counter: daily (reset at local midnight) | total (never reset)
    Mode5     statistics: rain_rate (rain and peak rain rate per window) | rain (rain only)
    Mode6     debug level: 0 Off | 1 Basic | 2 Verbose | 3 Debug

Technical settings live in ``.env`` beside plugin.py (see ``.env.sample``); the process environment overrides it. Among
them: which windows exist as devices (``RS_LAST_001_DAYS=Y`` ... ``RS_LAST_365_DAYS=N``) and the IDX of the device whose
daily rain history is read once (``RS_HISTORY_IDX``).
"""
import base64
import difflib
import ipaddress
import os
import re
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from .constants import (
    COUNTER_DAILY, COUNTER_MODES, DEFAULT_HOST, DEFAULT_INTERVAL, DEFAULT_MAX_DELTA, DEFAULT_PORT, DEFAULT_RATE_DIVISOR,
    DEFAULT_SAVE_INTERVAL, HTTP_TIMEOUT, MAX_INTERVAL, MIN_INTERVAL, REFRESH_SECONDS, SCHEMES, STATS_MODES,
    STATS_RAIN_RATE, WINDOWS, Window,
)
from .util import clamp, safe_int

_LEGACY_DEBUG = {"normal": 0, "false": 0, "off": 0, "true": 3, "debug": 3}
_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$")


class SettingsError(ValueError):
    """Raised for an invalid configuration: the plugin does not start."""


def default_state_file() -> str:
    """``rainstats_state.json`` beside plugin.py (or beside the package, for the command line tool)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rainstats_state.json")


class Settings:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, username: str = "", password: str = "",
                 rain_idx: int = 1, rate_idx: Optional[int] = None, interval: int = DEFAULT_INTERVAL,
                 counter: str = COUNTER_DAILY, stats: str = STATS_RAIN_RATE, debug: int = 0, scheme: str = "http",
                 ca_file: str = "", http_timeout: int = HTTP_TIMEOUT, state_file: Optional[str] = None,
                 save_interval: int = DEFAULT_SAVE_INTERVAL, refresh_interval: int = REFRESH_SECONDS,
                 rate_divisor: float = DEFAULT_RATE_DIVISOR, max_delta: float = DEFAULT_MAX_DELTA,
                 legacy_file: str = "", history_idx: Optional[int] = None, windows: Optional[Tuple[Window, ...]] = None,
                 env_file: Optional[str] = None, notes: Optional[List[str]] = None,
                 warnings: Optional[List[str]] = None) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.rain_idx = rain_idx
        self.rate_idx = rate_idx
        self.interval = interval
        self.counter = counter
        self.stats = stats
        self.debug = debug
        self.scheme = scheme
        self.ca_file = ca_file
        self.http_timeout = http_timeout
        self.state_file = state_file or default_state_file()
        self.save_interval = save_interval
        self.refresh_interval = refresh_interval
        self.rate_divisor = rate_divisor
        self.max_delta = max_delta
        self.legacy_file = legacy_file                  # the data file of the dzVents script, imported once
        self.history_idx = history_idx                  # the device whose daily rain history is read once (None: not)
        self.windows = tuple(windows) if windows is not None else tuple(w for w in WINDOWS if w.default)
        self.env_file = env_file                        # the .env that was read; None = built-in defaults
        self.notes = list(notes or [])                  # things worth a log line at start-up (corrections made)
        self.warnings = list(warnings or [])            # problems that did not stop the plugin

    @property
    def with_peaks(self) -> bool:
        return self.stats == STATS_RAIN_RATE

    @property
    def base_url(self) -> str:
        host = "[%s]" % self.host if ":" in self.host else self.host
        return "%s://%s:%d" % (self.scheme, host, self.port)

    def secrets(self) -> List[str]:
        """What must never reach the log: the password and the Basic authentication header made from it."""
        if not self.password:
            return []
        token = base64.b64encode(("%s:%s" % (self.username, self.password)).encode("utf-8")).decode("ascii")
        return [self.password, token]

    @classmethod
    def from_parameters(cls, params: Mapping[str, Any], env_path: Optional[str] = None) -> "Settings":
        def get(name: str, default: str = "") -> str:
            value = params.get(name, default)
            return default if value is None else str(value).strip()

        notes: List[str] = []
        warnings: List[str] = []
        env = load_env(env_path)
        warnings.extend(env.warnings)

        interval = DEFAULT_INTERVAL
        interval_text = get("Mode3")
        if interval_text:
            number = whole_number(interval_text)
            if number is None:
                raise SettingsError("Update interval must be a number of seconds, got %r" % interval_text)
            interval = clamp(number, MIN_INTERVAL, MAX_INTERVAL)
            if interval != number:
                notes.append("Update interval %d s is outside %d-%d s, using %d s"
                             % (number, MIN_INTERVAL, MAX_INTERVAL, interval))

        rain_idx = parse_idx(get("Mode1"), "The rain device IDX", required=True)
        rate_idx = parse_idx(get("Mode2"), "The rain rate device IDX", required=False)
        if rate_idx is not None and rate_idx == rain_idx:
            notes.append("The rain rate device is the rain device: the rate is read from the rain device only")
            rate_idx = None

        s = cls(host=parse_host(get("Address")), port=parse_port(get("Port")), username=get("Username"),
                password=get("Password"), rain_idx=rain_idx, rate_idx=rate_idx, interval=interval,
                counter=parse_choice(get("Mode4"), COUNTER_MODES, "Rain counter"),
                stats=parse_choice(get("Mode5"), STATS_MODES, "Statistics"), debug=parse_debug(get("Mode6")),
                env_file=env.path, notes=notes, warnings=warnings)
        _apply_env(s, env.values)
        _check_connection(s)
        return s


# -- hardware fields ------------------------------------------------------------------------------------------
def whole_number(text: str) -> Optional[int]:
    """A whole number written with digits only ("60", "-5"); None for everything else ("60.5", "1e3", "soon")."""
    text = str(text).strip()
    return int(text) if re.fullmatch(r"[+-]?\d{1,9}", text) else None


def parse_debug(text: str) -> int:
    text = str(text).strip().lower()
    if text in _LEGACY_DEBUG:
        return _LEGACY_DEBUG[text]
    return clamp(safe_int(text, 0) or 0, 0, 3)


def parse_idx(text: str, label: str, required: bool) -> Optional[int]:
    text = str(text).strip()
    if not text:
        if required:
            raise SettingsError("%s is required: fill in the IDX of your rain device (Setup > Devices, column Idx)" % label)
        return None
    if not re.fullmatch(r"\d{1,9}", text) or int(text) < 1:
        raise SettingsError("%s must be a device IDX (a whole number of 1 or more), got %r" % (label, text))
    return int(text)


def parse_host(text: str) -> str:
    text = str(text).strip()
    if not text:
        return DEFAULT_HOST
    if "://" in text or "/" in text or "?" in text:
        raise SettingsError("The Domoticz address must be a host name or an IP address, without http:// and without a "
                            "path, got %r" % text)
    bare = text.strip("[]")
    try:
        ipaddress.ip_address(bare)
        return bare
    except ValueError:
        pass
    if _HOSTNAME.match(text):
        return text.lower()
    raise SettingsError("The Domoticz address must be a host name or an IP address, got %r" % text)


def parse_port(text: str) -> int:
    text = str(text).strip()
    if not text:
        return DEFAULT_PORT
    number = whole_number(text)
    if number is None or not 1 <= number <= 65535:
        raise SettingsError("The Domoticz port must be a number from 1 to 65535, got %r" % text)
    return number


def parse_choice(text: str, choices: tuple, label: str) -> str:
    text = str(text).strip().lower()
    if not text:
        return choices[0]
    if text in choices:
        return text
    raise SettingsError("%s must be one of %s, got %r" % (label, ", ".join(choices), text))


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_connection(s: Settings) -> None:
    if bool(s.username) != bool(s.password):
        s.warnings.append("%s is filled in but %s is empty: the Domoticz API is asked without a login"
                          % (("Username", "Password") if s.username else ("Password", "Username")))
        s.username = s.password = ""
    if s.username and s.scheme == "http" and not is_loopback(s.host):
        s.warnings.append("The password for Domoticz is sent unencrypted to %s: use RS_SCHEME=https (and RS_CA_FILE if "
                          "its certificate is not public)" % s.host)
    if s.ca_file and s.scheme != "https":
        s.warnings.append("RS_CA_FILE is set but RS_SCHEME is http: the file is not used")


# -- .env -----------------------------------------------------------------------------------------------------
class _Spec(NamedTuple):
    attr: str
    kind: str                     # "int" | "float" | "choice" | "path"
    low: float = 0
    high: float = 0


# env key -> where it goes. The names carry the prefix RS_, so that no other program's variable of the same name
# can change the plugin.
ENV_SPECS: Dict[str, _Spec] = {
    "RS_SCHEME": _Spec("scheme", "choice"),
    "RS_CA_FILE": _Spec("ca_file", "path"),
    "RS_HTTP_TIMEOUT": _Spec("http_timeout", "int", 3, 60),
    "RS_STATE_FILE": _Spec("state_file", "path"),
    "RS_SAVE_INTERVAL": _Spec("save_interval", "int", 60, 86400),
    "RS_REFRESH_INTERVAL": _Spec("refresh_interval", "int", 30, 86400),
    "RS_RATE_DIVISOR": _Spec("rate_divisor", "float", 1, 1000),
    "RS_MAX_DELTA": _Spec("max_delta", "float", 1, 1000),
    "RS_LEGACY_FILE": _Spec("legacy_file", "path"),
    "RS_HISTORY_IDX": _Spec("history_idx", "int", 1, 999999999),
}
WINDOW_KEYS = tuple(w.env_key for w in WINDOWS)      # RS_LAST_001_DAYS ...: Y or N, whether the window gets devices
KNOWN_KEYS = tuple(ENV_SPECS) + WINDOW_KEYS
YES = ("y", "yes", "true", "1", "on")
NO = ("n", "no", "false", "0", "off")

_ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class Env(NamedTuple):
    values: Dict[str, str]
    path: Optional[str]            # the file that was read (None: no .env, built-in defaults)
    warnings: List[str]


def _clean_value(text: str) -> str:
    """The value of a KEY=VALUE line: quotes removed, a trailing ' # comment' dropped."""
    text = text.strip()
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        if end != -1:
            rest = text[end + 1:].strip()
            if rest and not rest.startswith("#"):
                return text                  # text after the closing quote: keep it all, so the value is rejected
            return text[1:end]
        return text
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def load_env(env_path: Optional[str] = None) -> Env:
    """Read the plugin-local .env; the process environment takes precedence.

    Problems that do not stop the plugin (an unknown key, a line that is not KEY=VALUE) are returned as warnings,
    because a typo in a key name would otherwise leave the default silently in force.
    """
    path = env_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    values: Dict[str, str] = {}
    warnings: List[str] = []
    found = None
    if os.path.isfile(path):
        found = path
        try:
            with open(path, encoding="utf-8-sig") as fh:       # tolerate a BOM from Windows editors
                content = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise SettingsError("Cannot read the configuration file %s: %s" % (path, exc))
        name = os.path.basename(path)
        for lineno, raw in enumerate(content.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _ASSIGN.match(line)
            if not match:
                warnings.append("%s line %d ignored: expected KEY=VALUE" % (name, lineno))
                continue
            key, value = match.group(1), _clean_value(match.group(2))
            if key not in KNOWN_KEYS:
                close = difflib.get_close_matches(key.upper(), KNOWN_KEYS, n=1, cutoff=0.6)
                warnings.append("%s line %d: unknown setting %s ignored%s" % (
                    name, lineno, key, " (did you mean %s?)" % close[0] if close else ""))
                continue
            values[key] = value
    for key in KNOWN_KEYS:
        if key in os.environ:
            values[key] = os.environ[key].strip()
    return Env(values, found, warnings)


def _apply_env(s: Settings, values: Mapping[str, str]) -> None:
    for key, spec in ENV_SPECS.items():
        raw = values.get(key)
        if raw is None or raw == "":
            continue
        if spec.kind == "int":
            value: Any = _to_number(raw, key, spec.low, spec.high, integer=True)
        elif spec.kind == "float":
            value = _to_number(raw, key, spec.low, spec.high, integer=False)
        elif spec.kind == "choice":
            value = raw.strip().lower()
            if value not in SCHEMES:
                raise SettingsError("%s must be one of %s, got %r" % (key, ", ".join(SCHEMES), raw))
        else:
            value = raw
        setattr(s, spec.attr, value)
    if s.ca_file and not os.path.isfile(s.ca_file):
        raise SettingsError("RS_CA_FILE %r is not a readable file" % s.ca_file)
    s.windows = _chosen_windows(values)


def _chosen_windows(values: Mapping[str, str]) -> Tuple[Window, ...]:
    """The windows that get devices: the lines RS_LAST_..._DAYS of .env (Y or N); a window without a line keeps its
    default. At least one window must stay."""
    chosen = []
    for window in WINDOWS:
        raw = values.get(window.env_key)
        if raw is None or raw == "":
            wanted = window.default
        else:
            text = raw.strip().lower()
            if text not in YES + NO:
                raise SettingsError("%s must be Y or N, got %r" % (window.env_key, raw))
            wanted = text in YES
        if wanted:
            chosen.append(window)
    if not chosen:
        raise SettingsError("All the windows are switched off: at least one of the lines %s ... %s of .env must be Y"
                            % (WINDOW_KEYS[0], WINDOW_KEYS[-1]))
    return tuple(chosen)


def _to_number(text: str, label: str, low: float, high: float, integer: bool) -> Any:
    try:
        value = float(text)
    except (ValueError, OverflowError):
        raise SettingsError("%s must be a number, got %r" % (label, text))
    if value != value or value in (float("inf"), float("-inf")):                # "nan" and "inf"
        raise SettingsError("%s must be a number, got %r" % (label, text))
    if integer:
        if value != int(value):
            raise SettingsError("%s must be a whole number, got %r" % (label, text))
        value = int(value)
    if not low <= value <= high:
        raise SettingsError("%s must be between %g and %g, got %g" % (label, low, high, value))
    return value
