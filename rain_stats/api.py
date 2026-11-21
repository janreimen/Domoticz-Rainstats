"""Client for the Domoticz JSON API: reads the rain device (and, when there is one, the rain rate device).

The plugin cannot see the devices of other hardware through the plugin interface, so it asks Domoticz itself, over
HTTP, for the device by its IDX:

    GET /json.htm?type=command&param=getdevices&rid=<idx>

Standard library only, no Domoticz module. The request goes to the address of the hardware settings (the machine that
runs Domoticz, normally 127.0.0.1) and never through a proxy, a redirect is refused, the answer is size-limited, and
for https the certificate is always verified (``RS_CA_FILE`` adds your own certificate authority to the trusted ones).

What is taken from a device:

    Type "Rain"      the counter from ``Rain`` (mm) and the rate from ``RainRate`` (mm/h; ``RS_RATE_DIVISOR`` for an
                     API that answers mm/h x 100)
    any other type   the first number of ``Data`` (for example a Custom Sensor showing ``12.3 mm``)

Once, when ``RS_HISTORY_IDX`` is set, the daily rain history of a device is read as well (``get_rain_history``).
"""
import base64
import json
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from typing import Any, Callable, Dict, List, NamedTuple, Optional
from urllib.parse import urlsplit

from .constants import MAX_ANSWER_BYTES, MAX_LIST_BYTES, USER_AGENT
from .model import RainDevice, Reading
from .util import first_number, safe_float, safe_int, shorten

# error kinds: the controller decides how long to wait from them
NETWORK, TLS, HTTP, AUTH, DATA, TOO_LARGE, CONFIG, MISSING = (
    "network", "tls", "http", "auth", "data", "too-large", "config", "missing")


class ApiError(Exception):
    def __init__(self, message: str, kind: str = NETWORK) -> None:
        super().__init__(message)
        self.kind = kind


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Domoticz does not redirect its API; a redirect means a wrong scheme or port. It is reported, not followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        target = "%s://%s" % (parts.scheme, parts.netloc)           # never the path or the query
        raise urllib.error.HTTPError(req.full_url, code, "redirected to %s: refused" % target, headers, fp)


def _read_limited(response: Any, limit: int, what: str) -> bytes:
    """The body of a response, decompressed, at most ``limit`` bytes (an ApiError otherwise)."""
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ApiError("%s is larger than %d bytes" % (what, limit), TOO_LARGE)
    encoding = (response.headers.get("Content-Encoding") or "").strip().lower()
    if encoding in ("gzip", "x-gzip"):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            out = decoder.decompress(raw, limit + 1)
        except zlib.error as exc:
            raise ApiError("%s is not valid gzip data: %s" % (what, exc), DATA)
        if len(out) > limit or decoder.unconsumed_tail:
            raise ApiError("%s is larger than %d bytes after decompression" % (what, limit), TOO_LARGE)
        return out
    if encoding not in ("", "identity"):
        raise ApiError("%s uses the unsupported content encoding %r" % (what, encoding), DATA)
    return raw


class Interpreted(NamedTuple):
    """What one device of the answer says, interpreted."""
    counter: Optional[float]
    rate: Optional[float]
    timed_out: bool
    raw: str


def interpret(device: Dict[str, Any], idx: int, divisor: float, role: str = "rain") -> Interpreted:
    """The rain counter (mm) and the rain rate (mm/h) of a device of the Domoticz answer.

    ``role`` is "rain" for the rain device and "rate" for a separate rain rate device: the number in the ``Data`` of
    a device that is not a Rain device is the counter of the first and the rate of the second."""
    kind = str(device.get("Type", ""))
    counter = rate = None
    if kind == "Rain":
        counter = safe_float(device.get("Rain"))
        raw_rate = safe_float(device.get("RainRate"))
        rate = None if raw_rate is None else raw_rate / divisor
    if role == "rain" and counter is None:
        counter = first_number(device.get("Data", ""))
    if role == "rate" and rate is None and kind != "Rain":
        rate = first_number(device.get("Data", ""))
    raw = "IDX %d %s '%s' (type %s): Rain=%r RainRate=%r Data=%r" % (
        idx, device.get("Name", "?"), device.get("LastUpdate", "?"), kind or "?", device.get("Rain"),
        device.get("RainRate"), device.get("Data"))
    return Interpreted(counter, rate, bool(device.get("HaveTimeout")), raw)


class Client:
    def __init__(self, scheme: str, host: str, port: int, username: str = "", password: str = "", timeout: int = 10,
                 ca_file: str = "", ssl_context: Optional[ssl.SSLContext] = None, user_agent: str = USER_AGENT) -> None:
        if scheme not in ("http", "https"):
            raise ApiError("only http and https are supported, got %r" % scheme, CONFIG)
        self.scheme = scheme
        self.host = host
        self.port = port
        self.timeout = timeout
        self.user_agent = user_agent
        self._auth = None
        if username and password:
            self._auth = "Basic " + base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
        handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]      # Domoticz is asked directly, never via a proxy
        if scheme == "https":
            context = ssl_context or ssl.create_default_context(cafile=ca_file or None)
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self._opener = urllib.request.build_opener(*handlers)

    @property
    def where(self) -> str:
        return "%s:%d" % (self.host, self.port)

    def url(self, query: str) -> str:
        host = "[%s]" % self.host if ":" in self.host else self.host
        return "%s://%s:%d/json.htm?%s" % (self.scheme, host, self.port, query)

    # -- requests -------------------------------------------------------------------------------------------
    def _get_result(self, query: str, what: str, limit: int) -> List[Any]:
        """The ``result`` list of the answer to a JSON API request. Raises ApiError."""
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self._auth:
            headers["Authorization"] = self._auth
        request = urllib.request.Request(self.url(query), headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = _read_limited(response, limit, what)
        except urllib.error.HTTPError as exc:
            exc.close()                                     # frees the connection of an answer we do not read
            if exc.code in (401, 403):
                raise ApiError("%s: HTTP %d %s: Domoticz refuses the request. Fill in Username and Password of a Domoticz "
                               "user, or add this machine to Setup > Settings > System > Local Networks (no "
                               "username/password)" % (what, exc.code, exc.reason), AUTH)
            if 300 <= exc.code < 400:
                raise ApiError("%s: HTTP %d %s. Check the scheme (RS_SCHEME) and the port: Domoticz answers at another "
                               "address" % (what, exc.code, exc.reason), HTTP)
            raise ApiError("%s: HTTP %d %s" % (what, exc.code, exc.reason), HTTP)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ApiError("%s: TLS error: the certificate of the server is not trusted (%s). Use RS_CA_FILE for your own "
                               "certificate authority" % (what, reason.verify_message), TLS)
            if isinstance(reason, ssl.SSLError):
                raise ApiError("%s: TLS error: %s. Is %s really an https port? (RS_SCHEME)" % (what, reason, self.where), TLS)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ApiError("%s: no answer within %d s" % (what, self.timeout), NETWORK)
            raise ApiError("%s: %s (is Domoticz listening on %s?)" % (what, reason, self.where), NETWORK)
        except (socket.timeout, TimeoutError):
            raise ApiError("%s: no answer within %d s" % (what, self.timeout), NETWORK)
        except ssl.SSLError as exc:
            raise ApiError("%s: TLS error: %s" % (what, exc), TLS)
        except (OSError, ValueError) as exc:               # connection reset, incomplete read, bad URL
            raise ApiError("%s: %s: %s" % (what, type(exc).__name__, exc), NETWORK)
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("%s: the answer is not JSON (is %s a Domoticz address and port?)" % (what, self.where), DATA)
        if not isinstance(data, dict):
            raise ApiError("%s: the answer is not a JSON object" % what, DATA)
        status = data.get("status")
        if status not in (None, "OK"):
            message = data.get("message")
            raise ApiError("%s: Domoticz answered status %r%s" % (
                what, status, ": %s" % shorten(message, 100) if isinstance(message, str) and message else ""), DATA)
        result = data.get("result")
        return result if isinstance(result, list) else []

    def get_device(self, idx: int) -> Dict[str, Any]:
        """The device ``idx`` as Domoticz describes it. Raises ApiError."""
        what = "Domoticz API (IDX %d)" % idx
        result = self._get_result("type=command&param=getdevices&rid=%d" % idx, what, MAX_ANSWER_BYTES)
        if not result or not isinstance(result[0], dict):
            raise ApiError("%s: Domoticz has no device with this IDX (see Setup > Devices, column Idx)" % what, MISSING)
        return result[0]

    def get_rain_history(self, idx: int) -> List[Any]:
        """The daily rain totals Domoticz keeps of device ``idx``, one row ``{"d": "2025-11-21", "mm": "3.2"}`` per
        day of the last year (the row of today comes last and is not finished). Raises ApiError."""
        what = "Domoticz API (rain history of IDX %d)" % idx
        try:
            return self._get_result("type=command&param=graph&sensor=rain&idx=%d&range=year" % idx, what, MAX_ANSWER_BYTES)
        except ApiError as exc:
            if exc.kind == HTTP and "HTTP 400" in str(exc):          # what Domoticz answers for a device it does not know
                raise ApiError("%s: Domoticz refuses the request. Is %d the IDX of a device of this Domoticz (Setup > "
                               "Devices, column Idx), and does this Domoticz know the graph command (type=command&"
                               "param=graph)?" % (what, idx), MISSING)
            raise

    def list_rain_devices(self, divisor: float = 1) -> List[RainDevice]:
        """The devices of type Rain that Domoticz has, for the command line tool. Raises ApiError."""
        result = self._get_result("type=command&param=getdevices&filter=all&used=true&order=Name",
                                  "Domoticz API (device list)", MAX_LIST_BYTES)
        found = []
        for device in result:
            if not isinstance(device, dict) or device.get("Type") != "Rain":
                continue
            idx = safe_int(device.get("idx"))
            if idx is None:
                continue
            rate = safe_float(device.get("RainRate"))
            found.append(RainDevice(idx, str(device.get("Name", "")), safe_float(device.get("Rain")),
                                    None if rate is None else rate / divisor, str(device.get("HardwareName", ""))))
        return found

    def read(self, rain_idx: int, rate_idx: Optional[int], divisor: float, now: Callable[[], float],
             halt: Optional[Callable[[], bool]] = None) -> Reading:
        """One reading: the rain counter, and the rate from the rain device or from the separate rate device."""
        rain = interpret(self.get_device(rain_idx), rain_idx, divisor)
        if rain.counter is None:
            raise ApiError("Domoticz API (IDX %d): the device has no rain value. Use a Rain device, or a sensor whose "
                           "value is the rain in mm (its data: %s)" % (rain_idx, rain.raw.split("Data=", 1)[-1]), DATA)
        rate, timed_out, raw = rain.rate, rain.timed_out, rain.raw
        if rate_idx is not None and not (halt is not None and halt()):
            other = interpret(self.get_device(rate_idx), rate_idx, divisor, "rate")
            if other.rate is None:
                raise ApiError("Domoticz API (IDX %d): the device has no rain rate. Use a Rain device, or a sensor whose "
                               "value is the rate in mm/h" % rate_idx, DATA)
            rate, timed_out, raw = other.rate, rain.timed_out or other.timed_out, raw + " | " + other.raw
        return Reading(now(), rain.counter, rate, timed_out, raw)
