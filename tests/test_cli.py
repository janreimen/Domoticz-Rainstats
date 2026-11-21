"""``python3 -m rain_stats`` against the fake Domoticz API: read, find, show a state file, show an import."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import helpers
import certs
from fake_domoticz import FakeDomoticz, custom_device, rain_device
from helpers import ROOT, local, write_legacy
from rain_stats import __version__, store
from rain_stats.cli import main
from rain_stats.store import State


class CliTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDomoticz().start()
        self.addCleanup(self.fake.stop)
        self.fake.add(rain_device(1161, "Weather - Rain", counter=3.4, rate=1.5))
        self.fake.add(custom_device(1180, "Rain total", "12.3 mm"))
        self.tmp = tempfile.mkdtemp(prefix="rainstats-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved_env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("RS_")}
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for key in [k for k in os.environ if k.startswith("RS_")]:
            del os.environ[key]
        os.environ.update(self.saved_env)

    def run_cli(self, *argv, expect=0):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), out, err)
        self.assertEqual(code, expect, "stdout:\n%s\nstderr:\n%s" % (out.getvalue(), err.getvalue()))
        return out.getvalue(), err.getvalue()

    def domoticz(self):
        return ["--host", "127.0.0.1", "--port", str(self.fake.port)]

    def run_at(self, moment, *argv, expect=0):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), out, err, now=moment)
        self.assertEqual(code, expect, "stdout:\n%s\nstderr:\n%s" % (out.getvalue(), err.getvalue()))
        return out.getvalue(), err.getvalue()

    # -- the daily history -----------------------------------------------------------------------------------
    def test_history_shows_what_the_plugin_would_start_with(self):
        days = [("2026-11-%02d" % d, 2.0) for d in range(1, 18)]               # 17 finished days of 2 mm
        self.fake.add_history(1161, days)
        out, _ = self.run_at(local("2026-11-18 09:00"), "--history", "1161", *self.domoticz())
        self.assertIn("Imported the daily rain history of IDX 1161: 17 days from 2026-11-01 to 2026-11-17, 34.0 mm", out)
        self.assertIn("1 day(s) left out because the plugin has its own data for them", out)     # the row of today
        self.assertIn("window   rain (mm)   peak rate (mm/h)", out)
        self.assertIn("rain known since 2026-11-01 00:00", out)
        path, query, _ = self.fake.requests[-1]
        self.assertEqual((query["param"], query["sensor"], query["range"], query["idx"]), ("graph", "rain", "year", "1161"))

    def test_history_in_json(self):
        self.fake.add_history(1161, [("2026-11-16", 1.0), ("2026-11-17", 2.5)])
        out, _ = self.run_at(local("2026-11-18 09:00"), "--history", "1161", "--json", *self.domoticz())
        data = json.loads(out)
        self.assertEqual((data["history"]["days"], data["history"]["mm"], data["history"]["idx"]), (2, 3.5, 1161))
        self.assertEqual(data["rain_from"], local("2026-11-16 00:00"))
        by_window = {w["window"]: w for w in data["windows"]}
        self.assertTrue(by_window["365d"]["partial"] and by_window["365d"]["peak_partial"])

    def test_history_of_a_device_domoticz_does_not_know_is_status_2(self):
        _, err = self.run_at(local("2026-11-18 09:00"), "--history", "999", *self.domoticz(), expect=2)
        self.assertIn("rain history of IDX 999", err)

    def test_history_changes_nothing_on_disk(self):
        self.fake.add_history(1161, [("2026-11-17", 2.5)])
        before = sorted(os.listdir(helpers.ROOT))
        self.run_at(local("2026-11-18 09:00"), "--history", "1161", *self.domoticz())
        self.assertEqual(sorted(os.listdir(helpers.ROOT)), before)

    # -- reading --------------------------------------------------------------------------------------------
    def test_it_shows_what_the_plugin_reads(self):
        out, _ = self.run_cli("--rain-idx", "1161", *self.domoticz())
        self.assertIn("rain counter : 3.40 mm (daily counter)", out)
        self.assertIn("rain rate    : 1.50 mm/h", out)
        self.assertIn("IDX 1161 Weather - Rain", out)

    def test_it_says_when_the_rate_is_derived(self):
        out, _ = self.run_cli("--rain-idx", "1180", *self.domoticz())
        self.assertIn("12.30 mm", out)
        self.assertIn("none (the plugin derives it from the counter)", out)

    def test_json_output(self):
        out, _ = self.run_cli("--rain-idx", "1161", "--json", *self.domoticz())
        data = json.loads(out)
        self.assertEqual((data["counter_mm"], data["rate_mm_h"], data["timed_out"]), (3.4, 1.5, False))

    def test_a_timed_out_device_is_said(self):
        self.fake.set_rain(1161, timeout=True)
        out, _ = self.run_cli("--rain-idx", "1161", *self.domoticz())
        self.assertIn("timed out", out)

    def test_a_separate_rate_device(self):
        self.fake.add(custom_device(1170, "Rate", "4.5 mm/h"))
        out, _ = self.run_cli("--rain-idx", "1161", "--rate-idx", "1170", *self.domoticz())
        self.assertIn("4.50 mm/h", out)

    def test_find_lists_the_rain_devices(self):
        out, _ = self.run_cli("--find", *self.domoticz())
        self.assertIn("1161", out)
        self.assertIn("Weather - Rain", out)
        self.assertNotIn("Rain total", out)                    # a Custom Sensor is no device of type Rain
        out, _ = self.run_cli("--find", "--json", *self.domoticz())
        self.assertEqual([d["idx"] for d in json.loads(out)], [1161])

    def test_find_when_there_is_none(self):
        del self.fake.devices[1161]
        out, _ = self.run_cli("--find", *self.domoticz())
        self.assertIn("has no device of type Rain", out)

    def test_the_password_comes_from_the_environment_not_the_command_line(self):
        self.fake.user, self.fake.password = "jan", "s3cret"
        self.run_cli("--rain-idx", "1161", "--user", "jan", *self.domoticz(), expect=2)
        os.environ["RS_PASSWORD"] = "s3cret"
        out, _ = self.run_cli("--rain-idx", "1161", "--user", "jan", *self.domoticz())
        self.assertIn("3.40 mm", out)
        self.assertNotIn("s3cret", out)

    # -- failures and exit status ---------------------------------------------------------------------------
    def test_domoticz_that_cannot_be_read_is_exit_status_2(self):
        port = self.fake.port
        self.fake.stop()
        _, err = self.run_cli("--rain-idx", "1161", "--host", "127.0.0.1", "--port", str(port), expect=2)
        self.assertIn("Domoticz cannot be read", err)

    def test_a_device_that_is_not_there_is_exit_status_2(self):
        _, err = self.run_cli("--rain-idx", "999", *self.domoticz(), expect=2)
        self.assertIn("no device with this IDX", err)

    def test_nothing_to_do_is_exit_status_1(self):
        _, err = self.run_cli(expect=1)
        self.assertIn("--rain-idx is required", err)

    def test_usage_errors_are_status_1_not_argparse_2(self):
        _, err = self.run_cli("--bogus", expect=1)
        self.assertIn("unrecognized arguments", err)
        _, err = self.run_cli("--rain-idx", "abc", expect=1)
        self.assertIn("invalid int value", err)

    def test_bad_settings_are_status_1(self):
        _, err = self.run_cli("--rain-idx", "1161", "--port", "99999", expect=1)
        self.assertIn("port must be a number", err)

    def test_the_version_and_the_help_are_status_0_on_stdout(self):
        out, err = self.run_cli("--version")
        self.assertIn(__version__, out)
        self.assertEqual(err, "")
        out, err = self.run_cli("--help")
        self.assertIn("--rain-idx", out)
        self.assertIn("RS_PASSWORD", out)                          # the password is never an argument: the help says where
        self.assertEqual(err, "")

    # -- a state file and an import -------------------------------------------------------------------------
    def test_a_state_file_is_shown_and_never_changed(self):
        state = State()
        now = local("2026-11-18 08:00")
        state.archive.add_rain(now - 3600, 2.5)
        state.archive.note_rate(now - 1800, 14.0)
        state.since = now - 86400 * 400
        path = os.path.join(self.tmp, "state.json")
        store.save(path, state, now)
        before = helpers.read_path(path, binary=True)
        out, _ = self.run_cli("--state", path, "--json")
        data = json.loads(out)
        self.assertEqual([w["window"] for w in data["windows"]],
                         ["1d", "2d", "7d", "14d", "30d", "90d", "180d", "365d"])
        self.assertEqual(helpers.read_path(path, binary=True), before)
        out, _ = self.run_cli("--state", path)
        self.assertIn("window   rain (mm)   peak rate (mm/h)", out)

    def test_a_missing_or_corrupt_state_file_is_status_1_and_corrupt_is_not_moved(self):
        _, err = self.run_cli("--state", os.path.join(self.tmp, "nope.json"), expect=1)
        self.assertIn("there is no state file", err)
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{nope")
        _, err = self.run_cli("--state", bad, expect=1)
        self.assertIn("cannot be used", err)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["bad.json"])

    def test_the_import_of_the_script_s_file_is_shown_without_saving_anything(self):
        path = write_legacy(os.path.join(self.tmp, "rain_stats_data.json"), values=[("2026-11-17 12:00", 4.0)],
                            maxima={"2026-11-01": 8.0})
        out, _ = self.run_cli("--legacy", path, "--json")
        data = json.loads(out)
        self.assertEqual(data["import"]["entries"], 1)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["rain_stats_data.json"])
        out, _ = self.run_cli("--legacy", path)
        self.assertIn("Imported the data file of RainStats V2.6", out)

    def test_a_legacy_file_that_is_not_one_is_status_1(self):
        path = os.path.join(self.tmp, "x.json")
        with open(path, "w") as fh:
            fh.write("[1]")
        _, err = self.run_cli("--legacy", path, expect=1)
        self.assertIn("not a data file of RainStats V2.6", err)

    def test_it_runs_as_a_module_the_way_the_documentation_says(self):
        # no -I here: "python3 -m rain_stats" in the plugin folder is exactly what the documentation tells to type, and
        # the folder is found because -m puts the current directory on the path
        env = {k: v for k, v in os.environ.items() if not k.startswith("RS_")}
        proc = subprocess.run([sys.executable, "-m", "rain_stats", "--version"], cwd=ROOT, capture_output=True, text=True,
                              timeout=30, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(__version__, proc.stdout)
        proc = subprocess.run([sys.executable, "-m", "rain_stats", "--bogus"], cwd=ROOT, capture_output=True, text=True,
                              timeout=30, env=env)
        self.assertEqual(proc.returncode, 1)                        # a usage error is 1, never argparse' own 2
        self.assertIn("unrecognized arguments", proc.stderr)


@unittest.skipUnless(certs.available(), "openssl is needed for the test certificates")
class CliTls(unittest.TestCase):
    def test_https_with_the_authority_file(self):
        folder, ca, other, cert, key = certs.get()
        fake = FakeDomoticz(tls=(cert, key)).start()
        self.addCleanup(fake.stop)
        fake.add(rain_device(1161, counter=3.4, rate=1.5))
        args = ["--rain-idx", "1161", "--host", "127.0.0.1", "--port", str(fake.port), "--scheme", "https"]
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(main(args + ["--ca-file", ca], out, err), 0, err.getvalue())
        self.assertIn("3.40 mm", out.getvalue())
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(main(args, out, err), 2)
        self.assertIn("not trusted", err.getvalue())


if __name__ == "__main__":
    unittest.main()
