"""The Domoticz JSON API client against a fake Domoticz on a real HTTP / TLS server."""
import os
import unittest

import helpers  # noqa: F401  (puts the plugin on the import path and fixes the time zone)
import certs
from fake_domoticz import FakeDomoticz, custom_device, rain_device
from rain_stats import api
from rain_stats.api import ApiError, Client, interpret

NOW = lambda: 1_790_000_000.0       # noqa: E731


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.dz = FakeDomoticz().start()
        self.addCleanup(self.dz.stop)
        self.dz.add(rain_device(1161, "Weather - Rain", counter=3.4, rate=1.5))

    def client(self, **kw):
        kw.setdefault("timeout", 2)
        return Client("http", "127.0.0.1", self.dz.port, **kw)

    def read(self, rain_idx=1161, rate_idx=None, divisor=1, halt=None, **kw):
        return self.client(**kw).read(rain_idx, rate_idx, divisor, NOW, halt)

    # -- what is read ---------------------------------------------------------------------------------------
    def test_a_rain_device_gives_the_counter_and_the_rate(self):
        reading = self.read()
        self.assertEqual((reading.ts, reading.counter, reading.rate, reading.timed_out), (NOW(), 3.4, 1.5, False))
        self.assertIn("IDX 1161 Weather - Rain", reading.raw)
        path, query, _ = self.dz.requests[-1]
        self.assertEqual(path, "/json.htm")
        self.assertEqual(query, {"type": "command", "param": "getdevices", "rid": "1161"})

    # -- the daily rain history ----------------------------------------------------------------------------
    def test_the_history_is_the_year_graph_of_the_device(self):
        self.dz.add_history(1161, [("2026-11-19", 1.5), ("2026-11-20", 0.0)])
        rows = self.client().get_rain_history(1161)
        self.assertEqual(rows[:2], [{"d": "2026-11-19", "mm": "1.5"}, {"d": "2026-11-20", "mm": "0.0"}])
        self.assertEqual(len(rows), 3)                                   # and the row of today
        path, query, _ = self.dz.requests[-1]
        self.assertEqual((path, query), ("/json.htm", {"type": "command", "param": "graph", "sensor": "rain",
                                                       "idx": "1161", "range": "year"}))

    def test_a_device_without_history_gives_the_row_of_today_only(self):
        self.assertEqual(len(self.client().get_rain_history(1161)), 1)

    def test_a_device_domoticz_does_not_know_is_a_missing_device_with_a_hint(self):
        with self.assertRaises(ApiError) as caught:
            self.client().get_rain_history(999)
        self.assertEqual(caught.exception.kind, api.MISSING)
        self.assertIn("rain history of IDX 999", str(caught.exception))
        self.assertIn("Is 999 the IDX of a device of this Domoticz", str(caught.exception))

    def test_the_history_is_asked_with_the_login_and_a_refusal_says_so(self):
        dz = FakeDomoticz(user="jan", password="s3cret").start()
        self.addCleanup(dz.stop)
        dz.add_history(1161, [("2026-11-19", 1.5)])
        self.assertEqual(len(Client("http", "127.0.0.1", dz.port, "jan", "s3cret", 2).get_rain_history(1161)), 2)
        with self.assertRaises(ApiError) as caught:
            Client("http", "127.0.0.1", dz.port, "jan", "wrong", 2).get_rain_history(1161)
        self.assertEqual(caught.exception.kind, api.AUTH)
        self.assertNotIn("s3cret", str(caught.exception))

    def test_a_server_error_is_an_http_error_and_no_connection_a_network_error(self):
        self.dz.mode = "http500"
        with self.assertRaises(ApiError) as caught:
            self.client().get_rain_history(1161)
        self.assertEqual(caught.exception.kind, api.HTTP)
        self.dz.stop()
        with self.assertRaises(ApiError) as caught:
            self.client().get_rain_history(1161)
        self.assertEqual(caught.exception.kind, api.NETWORK)

    def test_an_answer_that_is_not_json_is_a_data_error(self):
        self.dz.mode = "html"
        with self.assertRaises(ApiError) as caught:
            self.client().get_rain_history(1161)
        self.assertEqual(caught.exception.kind, api.DATA)

    def test_a_refusal_with_a_message_names_the_message(self):
        self.dz.mode = "status_err"
        with self.assertRaises(ApiError) as caught:
            self.client().get_rain_history(1161)
        self.assertIn("status 'ERR': denied", str(caught.exception))

    def test_the_divisor_turns_a_rate_times_100_into_mm_per_hour(self):
        self.dz.set_rain(1161, rate=150.0)
        self.assertEqual(self.read(divisor=100).rate, 1.5)

    def test_a_timed_out_device_is_flagged(self):
        self.dz.set_rain(1161, timeout=True)
        self.assertTrue(self.read().timed_out)

    def test_a_custom_sensor_is_a_rain_counter_without_a_rate(self):
        self.dz.add(custom_device(1180, "Rain total", "12.3 mm"))
        reading = self.read(1180)
        self.assertEqual((reading.counter, reading.rate), (12.3, None))

    def test_a_separate_rate_device_gives_the_rate(self):
        self.dz.add(custom_device(1170, "Rate", "4.5 mm/h"))
        reading = self.read(1161, 1170)
        self.assertEqual((reading.counter, reading.rate), (3.4, 4.5))
        self.assertIn("IDX 1170", reading.raw)
        self.dz.add(rain_device(1171, "Other station", counter=9.0, rate=7.0))
        self.assertEqual(self.read(1161, 1171).rate, 7.0)

    def test_a_separate_rate_device_that_reports_no_rate_is_an_error(self):
        self.dz.add(custom_device(1170, "Rate", "no number"))
        with self.assertRaises(ApiError) as caught:
            self.read(1161, 1170)
        self.assertEqual(caught.exception.kind, api.DATA)
        self.assertIn("no rain rate", str(caught.exception))

    def test_the_rate_device_is_not_asked_when_the_plugin_is_stopping(self):
        self.dz.add(custom_device(1170, "Rate", "4.5 mm/h"))
        reading = self.read(1161, 1170, halt=lambda: True)
        self.assertEqual(reading.rate, 1.5)
        self.assertEqual(len(self.dz.requests), 1)

    def test_a_device_without_a_rain_value_is_an_error_that_shows_its_data(self):
        self.dz.add(custom_device(1190, "Temperature", "abc"))
        with self.assertRaises(ApiError) as caught:
            self.read(1190)
        self.assertEqual(caught.exception.kind, api.DATA)
        self.assertIn("no rain value", str(caught.exception))
        self.assertIn("abc", str(caught.exception))

    def test_a_missing_device_is_named_as_such(self):
        with self.assertRaises(ApiError) as caught:
            self.read(999)
        self.assertEqual(caught.exception.kind, api.MISSING)
        self.assertIn("no device with this IDX", str(caught.exception))

    # -- the login ------------------------------------------------------------------------------------------
    def test_credentials_are_sent_as_basic_authentication(self):
        self.dz.user, self.dz.password = "jan", "s3cret"
        reading = self.read(username="jan", password="s3cret")
        self.assertEqual(reading.counter, 3.4)
        self.assertTrue(self.dz.requests[-1][2].startswith("Basic "))

    def test_a_refused_login_says_what_to_do(self):
        self.dz.user, self.dz.password = "jan", "s3cret"
        for kwargs in ({}, {"username": "jan", "password": "wrong"}):
            with self.assertRaises(ApiError) as caught:
                self.read(**kwargs)
            self.assertEqual(caught.exception.kind, api.AUTH)
            self.assertIn("Local Networks", str(caught.exception))

    # -- an answer that is not one --------------------------------------------------------------------------
    def expect(self, mode, kind, text):
        self.dz.mode = mode
        with self.assertRaises(ApiError) as caught:
            self.read()
        self.assertEqual(caught.exception.kind, kind, str(caught.exception))
        self.assertIn(text, str(caught.exception))
        return str(caught.exception)

    def test_a_redirect_is_refused_and_leaks_neither_path_nor_query(self):
        message = self.expect("redirect", api.HTTP, "redirected to https://elsewhere.example: refused")
        self.assertNotIn("secret=1", message)
        self.assertNotIn("json.htm", message.split("redirected to")[1])

    def test_a_server_error(self):
        self.expect("http500", api.HTTP, "HTTP 500")

    def test_a_login_page_is_not_json(self):
        self.expect("html", api.DATA, "not JSON")

    def test_broken_json(self):
        self.expect("notjson", api.DATA, "not JSON")

    def test_json_that_is_not_an_object(self):
        self.expect("array", api.DATA, "not a JSON object")

    def test_a_status_other_than_ok(self):
        self.expect("status_err", api.DATA, "status 'ERR'")

    def test_an_answer_that_is_too_large_is_refused(self):
        self.expect("huge", api.TOO_LARGE, "larger than")

    def test_a_gzip_answer_is_understood(self):
        self.dz.mode = "gzip"
        self.assertEqual(self.read().counter, 3.4)

    def test_a_server_that_does_not_answer_is_a_time_out(self):
        self.dz.mode = "hang"
        self.dz.delay = 3.0
        with self.assertRaises(ApiError) as caught:
            self.read(timeout=1)
        self.assertEqual(caught.exception.kind, api.NETWORK)
        self.assertIn("no answer within 1 s", str(caught.exception))

    def test_nobody_listening(self):
        port = self.dz.port
        self.dz.stop()
        with self.assertRaises(ApiError) as caught:
            Client("http", "127.0.0.1", port, timeout=2).read(1161, None, 1, NOW)
        self.assertEqual(caught.exception.kind, api.NETWORK)
        self.assertIn("is Domoticz listening on 127.0.0.1:%d" % port, str(caught.exception))

    def test_a_proxy_in_the_environment_is_never_used(self):
        saved = {k: os.environ.get(k) for k in ("http_proxy", "HTTP_PROXY")}
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"      # nothing listens there
        try:
            self.assertEqual(self.read().counter, 3.4)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    # -- the device list ------------------------------------------------------------------------------------
    def test_the_rain_devices_of_domoticz_are_listed_for_the_command_line_tool(self):
        self.dz.add(custom_device(1180, "Rain total", "12.3 mm"))
        self.dz.add(rain_device(1162, "Garden - Rain", counter=0.2, rate=60.0))
        found = self.client().list_rain_devices(divisor=10)
        self.assertEqual([(d.idx, d.name, d.counter, d.rate, d.hardware) for d in found],
                         [(1161, "Weather - Rain", 3.4, 0.15, "Weather"), (1162, "Garden - Rain", 0.2, 6.0, "Weather")])
        self.assertEqual(self.dz.requests[-1][1]["filter"], "all")

    # -- the client itself ----------------------------------------------------------------------------------
    def test_only_http_and_https(self):
        with self.assertRaises(ApiError):
            Client("ftp", "127.0.0.1", 21)

    def test_an_ipv6_host_is_put_in_brackets(self):
        self.assertEqual(Client("http", "::1", 8080).url("a=b"), "http://[::1]:8080/json.htm?a=b")
        self.assertEqual(Client("http", "::1", 8080).where, "::1:8080")


class InterpretTests(unittest.TestCase):
    def test_a_rain_device(self):
        got = interpret(rain_device(5, counter=2.5, rate=0.9), 5, 1)
        self.assertEqual((got.counter, got.rate, got.timed_out), (2.5, 0.9, False))

    def test_a_rain_device_without_a_rate(self):
        device = rain_device(5, counter=2.5)
        del device["RainRate"]
        self.assertIsNone(interpret(device, 5, 1).rate)

    def test_the_comma_as_a_decimal_separator(self):
        got = interpret(custom_device(5, "x", "12,5 mm"), 5, 1)
        self.assertEqual(got.counter, 12.5)

    def test_a_rate_device_that_is_no_rain_device(self):
        got = interpret(custom_device(5, "x", "7.5 mm/h"), 5, 1, "rate")
        self.assertEqual((got.counter, got.rate), (None, 7.5))

    def test_the_raw_text_names_what_was_read(self):
        raw = interpret(rain_device(5, name="Rain"), 5, 1).raw
        for part in ("IDX 5", "Rain", "type Rain", "Rain='0.0'", "RainRate='0.0'"):
            self.assertIn(part, raw)


@unittest.skipUnless(certs.available(), "openssl is needed for the test certificates")
class TlsTests(unittest.TestCase):
    def setUp(self):
        self.folder, self.ca, self.other, self.cert, self.key = certs.get()
        self.dz = FakeDomoticz(tls=(self.cert, self.key)).start()
        self.addCleanup(self.dz.stop)
        self.dz.add(rain_device(1161, counter=3.4, rate=1.5))

    def test_a_certificate_of_the_given_authority_is_trusted(self):
        client = Client("https", "127.0.0.1", self.dz.port, ca_file=self.ca, timeout=3)
        self.assertEqual(client.read(1161, None, 1, NOW).counter, 3.4)

    def test_without_the_authority_the_certificate_is_not_trusted(self):
        client = Client("https", "127.0.0.1", self.dz.port, timeout=3)
        with self.assertRaises(ApiError) as caught:
            client.read(1161, None, 1, NOW)
        self.assertEqual(caught.exception.kind, api.TLS)
        self.assertIn("not trusted", str(caught.exception))
        self.assertIn("RS_CA_FILE", str(caught.exception))

    def test_another_authority_does_not_help(self):
        client = Client("https", "127.0.0.1", self.dz.port, ca_file=self.other, timeout=3)
        with self.assertRaises(ApiError) as caught:
            client.read(1161, None, 1, NOW)
        self.assertEqual(caught.exception.kind, api.TLS)

    def test_https_to_a_plain_http_port_is_explained(self):
        plain = FakeDomoticz().start()
        self.addCleanup(plain.stop)
        client = Client("https", "127.0.0.1", plain.port, ca_file=self.ca, timeout=3)
        with self.assertRaises(ApiError) as caught:
            client.read(1161, None, 1, NOW)
        self.assertIn(caught.exception.kind, (api.TLS, api.NETWORK))


if __name__ == "__main__":
    unittest.main()
