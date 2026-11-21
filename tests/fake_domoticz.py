"""A fake Domoticz JSON API on a real HTTP (or TLS) server, for tests.

It answers the three requests the plugin makes:

    GET /json.htm?type=command&param=getdevices&rid=<idx>                      one device
    GET /json.htm?type=command&param=getdevices&filter=all&used=true&order=Name the device list
    GET /json.htm?type=command&param=graph&sensor=rain&idx=<idx>&range=year     the daily rain totals of a device

in the shape Domoticz uses (every number is a string, a device that does not exist is an answer without ``result``).
``mode`` makes it misbehave on purpose.
"""
import gzip
import json
import ssl
import threading
import time
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

NOW_TEXT = "2026-11-18 06:48:12"


def rain_device(idx, name="Rain", counter=0.0, rate=0.0, last_update=NOW_TEXT, timeout=False, hardware="Weather"):
    return {"idx": str(idx), "Name": name, "Type": "Rain", "SubType": "Rain", "Rain": "%.1f" % counter,
            "RainRate": "%.1f" % rate, "Data": "%.1f mm" % counter, "LastUpdate": last_update, "HaveTimeout": timeout,
            "HardwareName": hardware, "Used": 1}


def custom_device(idx, name, data, last_update=NOW_TEXT):
    return {"idx": str(idx), "Name": name, "Type": "General", "SubType": "Custom Sensor", "Data": data,
            "LastUpdate": last_update, "HaveTimeout": False, "HardwareName": "Weather", "Used": 1}


class FakeDomoticz:
    def __init__(self, user=None, password=None, tls=None):
        self.devices = {}               # idx -> the device as Domoticz describes it
        self.history = {}               # idx -> [(day "2025-11-21", mm), ...] the daily rain totals Domoticz keeps
        self.requests = []              # (path, query dict) of every request
        self.user, self.password = user, password
        self.mode = "ok"                # ok | html | redirect | http500 | status_err | gzip | huge | hang | notjson | array
        self.delay = 5.0                # how long mode "hang" waits
        self.tls = tls                  # (certificate file, key file) for https
        self.port = None
        self._server = None
        self._thread = None

    # -- the devices ----------------------------------------------------------------------------------------
    def add(self, device):
        self.devices[int(device["idx"])] = device
        return device

    def set_rain(self, idx, counter=None, rate=None, timeout=None, last_update=None):
        device = self.devices[idx]
        if counter is not None:
            device["Rain"] = "%.1f" % counter
            device["Data"] = "%.1f mm" % counter
        if rate is not None:
            device["RainRate"] = "%.1f" % rate
        if timeout is not None:
            device["HaveTimeout"] = timeout
        if last_update is not None:
            device["LastUpdate"] = last_update

    def add_history(self, idx, days):
        """The daily totals ``days`` = [("2025-11-21", 3.2), ...] of the rain device ``idx``."""
        self.history[int(idx)] = list(days)

    # -- the server -----------------------------------------------------------------------------------------
    @property
    def scheme(self):
        return "https" if self.tls else "http"

    @property
    def url(self):
        return "%s://127.0.0.1:%d" % (self.scheme, self.port)

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.handle(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        if self.tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*self.tls)
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- one request ----------------------------------------------------------------------------------------
    def handle(self, h):
        parts = urlsplit(h.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.requests.append((parts.path, query, h.headers.get("Authorization")))
        if self.user is not None and not self._authorised(h.headers.get("Authorization")):
            return self._send(h, 401, b"Unauthorized", "text/plain", {"WWW-Authenticate": 'Basic realm="Domoticz"'})
        if self.mode == "redirect":
            return self._send(h, 302, b"", "text/plain", {"Location": "https://elsewhere.example/json.htm?secret=1"})
        if self.mode == "http500":
            return self._send(h, 500, b"boom", "text/plain")
        if self.mode == "html":
            return self._send(h, 200, b"<html><body>Login</body></html>", "text/html")
        if self.mode == "notjson":
            return self._send(h, 200, b"{not json", "application/json")
        if self.mode == "array":
            return self._send(h, 200, b"[1, 2, 3]", "application/json")
        if self.mode == "hang":
            time.sleep(self.delay)
        if parts.path == "/json.htm" and query.get("type") == "command" and query.get("param") == "graph":
            return self._graph(h, query)
        if parts.path != "/json.htm" or query.get("type") != "command" or query.get("param") != "getdevices":
            return self._send(h, 200, json.dumps({"status": "ERR"}).encode(), "application/json")
        if self.mode == "status_err":
            return self._send(h, 200, json.dumps({"status": "ERR", "message": "denied"}).encode(), "application/json")
        answer = {"status": "OK", "title": "GetDevices", "ServerTime": NOW_TEXT}
        if "rid" in query:
            found = self.devices.get(int(query["rid"]))
            if found is not None:
                answer["result"] = [found]
        else:
            answer["result"] = [self.devices[i] for i in sorted(self.devices)]
        body = json.dumps(answer).encode()
        if self.mode == "huge":
            body = json.dumps(dict(answer, padding="x" * (2 * 1024 * 1024))).encode()
        if self.mode == "gzip":
            return self._send(h, 200, gzip.compress(body), "application/json", {"Content-Encoding": "gzip"})
        return self._send(h, 200, body, "application/json")

    def _graph(self, h, query):
        """The year graph of a rain device the way Domoticz answers it: a row per finished day (the date and the mm as
        text with one decimal), then a row for today; a device it does not know gets HTTP 400 and no result."""
        idx = int(query.get("idx", "0") or 0)
        if self.mode == "status_err":
            return self._send(h, 200, json.dumps({"status": "ERR", "message": "denied"}).encode(), "application/json")
        if query.get("sensor") != "rain" or query.get("range") != "year" or (idx not in self.history
                                                                              and idx not in self.devices):
            body = json.dumps({"status": "ERR", "message": "Unknown device %d" % idx}).encode()
            return self._send(h, 400, body, "application/json")
        rows = [{"d": day, "mm": "%.1f" % mm} for day, mm in self.history.get(idx, [])]
        rows.append({"d": NOW_TEXT[:10], "mm": "0.0"})
        body = json.dumps({"status": "OK", "title": "Graph rain year", "result": rows}).encode()
        return self._send(h, 200, body, "application/json")

    def _authorised(self, header):
        if not header or not header.startswith("Basic "):
            return False
        try:
            user, _, password = b64decode(header[6:]).decode("utf-8").partition(":")
        except ValueError:
            return False
        return user == self.user and password == self.password

    @staticmethod
    def _send(h, status, body, content_type, headers=None):
        h.send_response(status)
        h.send_header("Content-Type", content_type)
        h.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            h.send_header(key, value)
        h.end_headers()
        try:
            h.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):       # the client gave up (a size limit): not the server's problem
            pass
