"""Settings: the hardware fields, the plugin-local .env, and every way they can be wrong."""
import base64
import os
import tempfile
import unittest

import helpers
from rain_stats.config import Settings, SettingsError, default_state_file, load_env, parse_debug, parse_host, parse_idx


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rainstats-cfg-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("RS_")}
        self.addCleanup(self.restore)

    def restore(self):
        for key in [k for k in os.environ if k.startswith("RS_")]:
            del os.environ[key]
        os.environ.update(self.saved)

    def env(self, text):
        path = os.path.join(self.tmp, ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def settings(self, env_text=None, **params):
        params.setdefault("Mode1", "1161")
        path = self.env(env_text) if env_text is not None else os.path.join(self.tmp, "no.env")
        return Settings.from_parameters(params, env_path=path)

    def refused(self, fragment, env_text=None, **params):
        with self.assertRaises(SettingsError) as caught:
            self.settings(env_text, **params)
        self.assertIn(fragment, str(caught.exception))


class HardwareFields(Base):
    def test_defaults(self):
        s = self.settings()
        self.assertEqual((s.host, s.port, s.interval, s.counter, s.stats, s.debug), ("127.0.0.1", 8080, 60, "daily", "rain_rate", 0))
        self.assertEqual((s.rain_idx, s.rate_idx, s.scheme, s.username, s.password), (1161, None, "http", "", ""))
        self.assertTrue(s.with_peaks)
        self.assertIsNone(s.env_file)
        self.assertEqual(s.state_file, default_state_file())
        self.assertEqual(s.base_url, "http://127.0.0.1:8080")

    def test_everything_given(self):
        s = self.settings(Address="Domoticz.Lan", Port="8443", Username="jan", Password="s3cret", Mode1="1161", Mode2="1323",
                          Mode3="120", Mode4="total", Mode5="rain", Mode6="2")
        self.assertEqual((s.host, s.port, s.rate_idx, s.interval, s.counter, s.stats, s.debug),
                         ("domoticz.lan", 8443, 1323, 120, "total", "rain", 2))
        self.assertFalse(s.with_peaks)

    def test_the_rain_device_is_required(self):
        self.refused("rain device IDX is required", Mode1="")
        self.refused("rain device IDX is required", Mode1="   ")

    def test_an_idx_is_a_whole_number_of_one_or_more(self):
        for bad in ("abc", "0", "-3", "12.5", "1e3", "1234567890", "#1161"):
            self.refused("must be a device IDX", Mode1=bad)
        self.refused("rain rate device IDX must be", Mode2="x")

    def test_the_rate_device_that_is_the_rain_device_is_corrected_and_said(self):
        s = self.settings(Mode2="1161")
        self.assertIsNone(s.rate_idx)
        self.assertTrue(any("rain rate device is the rain device" in n for n in s.notes))

    def test_the_interval_is_clamped_and_said(self):
        s = self.settings(Mode3="2")
        self.assertEqual(s.interval, 10)
        self.assertIn("Update interval 2 s is outside 10-3600 s, using 10 s", s.notes)
        self.assertEqual(self.settings(Mode3="99999").interval, 3600)
        self.assertEqual(self.settings(Mode3="").interval, 60)
        self.refused("must be a number of seconds", Mode3="soon")

    def test_the_address(self):
        self.assertEqual(self.settings(Address="").host, "127.0.0.1")
        self.assertEqual(self.settings(Address="[::1]").host, "::1")
        self.assertEqual(self.settings(Address="::1").base_url, "http://[::1]:8080")
        self.assertEqual(self.settings(Address="192.168.1.5").host, "192.168.1.5")
        for bad in ("http://127.0.0.1", "127.0.0.1/json.htm", "host?x=1", "my host", "-bad.example", "a..b"):
            self.refused("host name or an IP address", Address=bad)

    def test_the_port(self):
        self.assertEqual(self.settings(Port="").port, 8080)
        for bad in ("0", "65536", "http", "80.5"):
            self.refused("port must be a number from 1 to 65535", Port=bad)

    def test_the_choices_are_case_insensitive_and_checked(self):
        self.assertEqual(self.settings(Mode4="TOTAL").counter, "total")
        self.assertEqual(self.settings(Mode4="").counter, "daily")
        self.refused("Rain counter must be one of daily, total", Mode4="weekly")
        self.refused("Statistics must be one of rain_rate, rain", Mode5="all")

    def test_the_debug_level_understands_the_values_of_older_plugins(self):
        for text, level in (("", 0), ("0", 0), ("1", 1), ("2", 2), ("3", 3), ("9", 3), ("-1", 0), ("Normal", 0), ("false", 0),
                            ("Debug", 3), ("true", 3), ("garbage", 0)):
            self.assertEqual(parse_debug(text), level, text)

    def test_username_without_password_is_dropped_with_a_warning(self):
        s = self.settings(Username="jan")
        self.assertEqual((s.username, s.password), ("", ""))
        self.assertTrue(any("Username is filled in but Password is empty" in w for w in s.warnings))
        s = self.settings(Password="s3cret")
        self.assertTrue(any("Password is filled in but Username is empty" in w for w in s.warnings))

    def test_a_password_over_plain_http_to_another_machine_is_warned_about(self):
        s = self.settings(Address="192.168.1.5", Username="jan", Password="s3cret")
        self.assertTrue(any("sent unencrypted to 192.168.1.5" in w for w in s.warnings))
        s = self.settings(Address="127.0.0.1", Username="jan", Password="s3cret")
        self.assertEqual(s.warnings, [])
        s = self.settings("RS_SCHEME=https\n", Address="192.168.1.5", Username="jan", Password="s3cret")
        self.assertEqual(s.warnings, [])

    def test_the_secrets_are_the_password_and_the_authorisation_header_value(self):
        s = self.settings(Username="jan", Password="s3cret")
        token = base64.b64encode(b"jan:s3cret").decode()
        self.assertEqual(s.secrets(), ["s3cret", token])
        self.assertEqual(self.settings().secrets(), [])

    def test_parse_helpers_on_their_own(self):
        self.assertEqual(parse_idx(" 7 ", "x", True), 7)
        self.assertIsNone(parse_idx("", "x", False))
        self.assertEqual(parse_host("LOCALHOST"), "localhost")


class EnvFile(Base):
    def test_the_values_of_the_file_are_applied(self):
        s = self.settings("RS_SCHEME=http\nRS_HTTP_TIMEOUT=20\nRS_SAVE_INTERVAL=600\nRS_REFRESH_INTERVAL=120\n"
                          "RS_RATE_DIVISOR=100\nRS_MAX_DELTA=50\nRS_STATE_FILE=/var/lib/rain.json\n"
                          "RS_LEGACY_FILE=/old/rain_stats_data.json\n")
        self.assertEqual((s.http_timeout, s.save_interval, s.refresh_interval, s.rate_divisor, s.max_delta),
                         (20, 600, 120, 100.0, 50.0))
        self.assertEqual((s.state_file, s.legacy_file), ("/var/lib/rain.json", "/old/rain_stats_data.json"))
        self.assertTrue(s.env_file.endswith(".env"))
        self.assertEqual(s.warnings, [])

    def test_quotes_comments_export_and_a_byte_order_mark(self):
        path = os.path.join(self.tmp, ".env")
        with open(path, "w", encoding="utf-8-sig") as fh:
            fh.write('# comment\n\nexport RS_HTTP_TIMEOUT = "15"   # seconds\nRS_SAVE_INTERVAL=300 # five minutes\n')
        s = Settings.from_parameters({"Mode1": "1"}, env_path=path)
        self.assertEqual((s.http_timeout, s.save_interval), (15, 300))

    def test_a_mistyped_key_is_reported_with_a_hint(self):
        s = self.settings("RS_SAVE_INTERVL=300\nSOMETHING=1\nnot an assignment\n")
        text = "\n".join(s.warnings)
        self.assertIn("unknown setting RS_SAVE_INTERVL ignored (did you mean RS_SAVE_INTERVAL?)", text)
        self.assertIn("unknown setting SOMETHING ignored", text)
        self.assertIn("line 3 ignored: expected KEY=VALUE", text)
        self.assertEqual(s.save_interval, 900)

    def test_the_process_environment_wins_over_the_file(self):
        os.environ["RS_HTTP_TIMEOUT"] = "30"
        self.assertEqual(self.settings("RS_HTTP_TIMEOUT=15\n").http_timeout, 30)

    def test_numbers_are_checked(self):
        self.refused("RS_HTTP_TIMEOUT must be between 3 and 60, got 2", "RS_HTTP_TIMEOUT=2\n")
        self.refused("RS_SAVE_INTERVAL must be between 60 and 86400", "RS_SAVE_INTERVAL=5\n")
        self.refused("RS_HTTP_TIMEOUT must be a number, got 'fast'", "RS_HTTP_TIMEOUT=fast\n")
        self.refused("RS_HTTP_TIMEOUT must be a whole number", "RS_HTTP_TIMEOUT=10.5\n")
        self.refused("RS_MAX_DELTA must be a number", "RS_MAX_DELTA=nan\n")
        self.refused("RS_MAX_DELTA must be a number", "RS_MAX_DELTA=inf\n")
        self.refused("RS_RATE_DIVISOR must be between 1 and 1000", "RS_RATE_DIVISOR=0\n")

    def test_the_scheme_is_http_or_https(self):
        self.assertEqual(self.settings("RS_SCHEME=HTTPS\n").scheme, "https")
        self.refused("RS_SCHEME must be one of http, https", "RS_SCHEME=ftp\n")

    def test_an_empty_value_keeps_the_default(self):
        self.assertEqual(self.settings("RS_HTTP_TIMEOUT=\n").http_timeout, 10)

    def test_the_ca_file_must_exist_and_is_only_used_for_https(self):
        self.refused("is not a readable file", "RS_CA_FILE=/nonexistent/ca.pem\n")
        ca = os.path.join(self.tmp, "ca.pem")
        open(ca, "w").close()
        s = self.settings("RS_CA_FILE=%s\n" % ca)
        self.assertTrue(any("RS_CA_FILE is set but RS_SCHEME is http" in w for w in s.warnings))
        s = self.settings("RS_CA_FILE=%s\nRS_SCHEME=https\n" % ca)
        self.assertEqual(s.warnings, [])

    def test_an_unreadable_file_stops_the_plugin(self):
        path = os.path.join(self.tmp, ".env")
        with open(path, "wb") as fh:
            fh.write(b"RS_STATE_FILE=\xff\xfe\n")
        with self.assertRaises(SettingsError) as caught:
            Settings.from_parameters({"Mode1": "1"}, env_path=path)
        self.assertIn("Cannot read the configuration file", str(caught.exception))

    def test_load_env_without_a_file(self):
        env = load_env(os.path.join(self.tmp, "none"))
        self.assertEqual((env.values, env.path, env.warnings), ({}, None, []))

    def test_the_sample_file_lists_every_setting_and_changes_nothing(self):
        sample = helpers.read_text(".env.sample")
        from rain_stats.config import ENV_SPECS
        from rain_stats.constants import WINDOWS
        for key in ENV_SPECS:
            self.assertIn("# %s=" % key, sample, key + " is not in .env.sample")
        for window in WINDOWS:                                  # the windows are not commented out: Y or N, as by default
            self.assertIn("\n%s=%s\n" % (window.env_key, "Y" if window.default else "N"), sample, window.env_key)
        s = Settings.from_parameters({"Mode1": "1"}, env_path=os.path.join(ROOT_SAMPLE(), ".env.sample"))
        self.assertEqual(s.warnings, [])
        self.assertEqual(s.windows, Settings().windows)


class Windows(Base):
    """Which windows get devices: the lines RS_LAST_..._DAYS of .env, Y or N."""

    def keys(self, env_text=None):
        return [w.key for w in self.settings(env_text).windows]

    def test_without_a_file_the_windows_of_the_first_version_are_on(self):
        self.assertEqual(self.keys(), ["1d", "2d", "7d", "14d", "30d", "90d", "180d", "365d"])

    def test_a_line_switches_a_window_on_or_off(self):
        self.assertEqual(self.keys("RS_LAST_002_DAYS=N\nRS_LAST_060_DAYS=Y\nRS_LAST_365_DAYS=N\n"),
                         ["1d", "7d", "14d", "30d", "60d", "90d", "180d"])

    def test_the_new_windows_come_in_their_place_in_the_order_of_the_days(self):
        self.assertEqual(self.keys("RS_LAST_060_DAYS=Y\nRS_LAST_120_DAYS=Y\nRS_LAST_360_DAYS=Y\n"),
                         ["1d", "2d", "7d", "14d", "30d", "60d", "90d", "120d", "180d", "360d", "365d"])

    def test_y_and_n_are_written_the_way_people_write_them(self):
        text = "\n".join(["RS_LAST_001_DAYS=y", "RS_LAST_002_DAYS=no", "RS_LAST_007_DAYS=Yes", "RS_LAST_014_DAYS=FALSE",
                          "RS_LAST_030_DAYS=0", "RS_LAST_060_DAYS=true", "RS_LAST_090_DAYS='N'",
                          "RS_LAST_120_DAYS=Y   # a comment", "RS_LAST_180_DAYS=off", "RS_LAST_360_DAYS=on",
                          "RS_LAST_365_DAYS= n "])
        self.assertEqual(self.keys(text), ["1d", "7d", "60d", "120d", "360d"])

    def test_a_line_without_a_value_keeps_the_default(self):
        self.assertEqual(self.keys("RS_LAST_001_DAYS=\nRS_LAST_060_DAYS=\n"), self.keys())

    def test_anything_but_y_or_n_is_refused_and_the_key_is_named(self):
        for bad in ("maybe", "2", "yy", "Y N"):
            self.refused("RS_LAST_030_DAYS must be Y or N, got %r" % bad, "RS_LAST_030_DAYS=%s\n" % bad)

    def test_at_least_one_window_must_stay(self):
        text = "".join("RS_LAST_%03d_DAYS=N\n" % d for d in (1, 2, 7, 14, 30, 60, 90, 120, 180, 360, 365))
        self.refused("All the windows are switched off", text)

    def test_a_single_window_is_enough(self):
        text = "".join("RS_LAST_%03d_DAYS=N\n" % d for d in (1, 2, 7, 14, 30, 90, 180))
        self.assertEqual(self.keys(text), ["365d"])

    def test_the_environment_of_the_service_wins(self):
        os.environ["RS_LAST_001_DAYS"] = "N"
        self.assertNotIn("1d", self.keys("RS_LAST_001_DAYS=Y\n"))

    def test_a_mistyped_line_is_reported_and_names_the_right_one(self):
        s = self.settings("RS_LAST_01_DAYS=N\nRS_LAST_100_DAYS=Y\n")
        self.assertEqual(len(s.warnings), 2)
        self.assertIn("unknown setting RS_LAST_01_DAYS ignored (did you mean RS_LAST_0", s.warnings[0])
        self.assertEqual([w.key for w in s.windows], [w.key for w in self.settings().windows])      # nothing changed

    def test_a_line_for_a_window_that_does_not_exist_is_only_a_warning(self):
        # an early copy of .env.sample had a line for 24 days: it is ignored, the other lines still count
        s = self.settings("RS_LAST_024_DAYS=Y\nRS_LAST_060_DAYS=Y\n")
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("unknown setting RS_LAST_024_DAYS ignored", s.warnings[0])
        self.assertEqual([w.key for w in s.windows], [w.key for w in self.settings("RS_LAST_060_DAYS=Y\n").windows])
        self.assertIn("60d", [w.key for w in s.windows])
        self.assertNotIn("24d", [w.key for w in s.windows])


class HistoryIdx(Base):
    def test_it_is_off_unless_a_device_is_named(self):
        self.assertIsNone(self.settings().history_idx)
        self.assertIsNone(self.settings("RS_HISTORY_IDX=\n").history_idx)

    def test_a_device_is_named_by_its_idx(self):
        self.assertEqual(self.settings("RS_HISTORY_IDX=1161\n").history_idx, 1161)
        self.assertEqual(self.settings("RS_HISTORY_IDX=7 # the old station\n").history_idx, 7)

    def test_an_idx_that_is_none_is_refused(self):
        for bad in ("abc", "0", "-3", "12.5", "1234567890", "#1161"):
            self.refused("RS_HISTORY_IDX", "RS_HISTORY_IDX=%s\n" % bad)


def ROOT_SAMPLE():
    return helpers.ROOT


if __name__ == "__main__":
    unittest.main()
