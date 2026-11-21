"""The real plugin.py (manifest, glue, callbacks) against the fake Domoticz JSON API, with a Domoticz module stub."""
import ast
import importlib
import os
import re
import sys
import tempfile
import time
import unittest

import domoticz_stub as dz
from fake_domoticz import FakeDomoticz, rain_device
from helpers import ROOT
from rain_stats import __version__

RESERVED = {"Parameters", "Devices", "Images", "Settings"}          # injected into plugin.py by Domoticz
RAIN_24H, STATUS, INFO = 1, 17, 18


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDomoticz().start()
        self.addCleanup(self.fake.stop)
        self.fake.add(rain_device(1161, "Weather - Rain", counter=3.4, rate=1.5))
        self.tmp = tempfile.mkdtemp(prefix="rainstats-plugin-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.state_file = os.path.join(self.tmp, "state.json")
        self.saved_env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("RS_")}
        os.environ["RS_STATE_FILE"] = self.state_file                  # never write beside the source tree
        self.addCleanup(self.restore_env)
        self.saved = sys.modules.get("Domoticz")
        sys.modules["Domoticz"] = dz
        self.addCleanup(self.restore_module)
        self.plugins = []

    def restore_env(self):
        for key in [k for k in os.environ if k.startswith("RS_")]:
            del os.environ[key]
        os.environ.update(self.saved_env)

    def restore_module(self):
        for plugin in self.plugins:                                     # no poll may outlive its test
            if plugin._plugin.controller is not None:
                plugin.onStop()
        if self.saved is None:
            sys.modules.pop("Domoticz", None)
        else:
            sys.modules["Domoticz"] = self.saved

    def load(self, **params):
        dz.reset()
        sys.modules.pop("plugin", None)
        plugin = importlib.import_module("plugin")
        plugin.Parameters = {"Address": "127.0.0.1", "Port": str(self.fake.port), "Username": "", "Password": "",
                             "Mode1": "1161", "Mode2": "", "Mode3": "60", "Mode4": "daily", "Mode5": "rain_rate",
                             "Mode6": "0"}
        plugin.Parameters.update(params)
        plugin.Devices = dz.Devices
        plugin.Settings = {"Language": "en"}                            # injected by Domoticz like these
        plugin.Images = {}
        self.plugins.append(plugin)
        return plugin

    def beat_until(self, plugin, done, timeout=5.0):
        """Heartbeats until ``done()`` (the poll runs in a thread): the result of a poll is applied by a heartbeat."""
        end = time.time() + timeout
        while time.time() < end:
            plugin.onHeartbeat()
            if done():
                return True
            time.sleep(0.02)
        return False

    def errors(self):
        return dz.messages("error")

    # -- the normal life ------------------------------------------------------------------------------------
    def test_start_creates_the_devices_and_the_first_poll_fills_them(self):
        plugin = self.load()
        plugin.onStart()
        self.assertEqual(self.errors(), [])
        self.assertEqual(sorted(dz.Devices), list(range(1, 19)))
        self.assertIn(("heartbeat", 10), dz.logs)
        self.assertTrue(any("Rain Statistics %s starting" % __version__ in t for t in dz.messages("log")))
        self.assertTrue(self.beat_until(plugin, lambda: dz.Devices[STATUS].sValue == "On"))
        self.assertEqual(dz.Devices[RAIN_24H].sValue, "3.4")             # the rain of today so far, as the counter says
        self.assertEqual(dz.Devices[9].sValue, "1.50")
        self.assertEqual(self.errors(), [])
        plugin.onStop()
        self.assertTrue(any("Rain Statistics stopped" in t for t in dz.messages("log")))

    def test_stopping_saves_the_state_and_the_next_start_loads_it(self):
        plugin = self.load()
        plugin.onStart()
        self.assertTrue(self.beat_until(plugin, lambda: dz.Devices[STATUS].sValue == "On"))
        plugin.onStop()
        self.assertTrue(os.path.isfile(self.state_file))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "rainstats_state.json")))
        plugin = self.load()
        plugin.onStart()
        self.assertTrue(any("Loaded the state file" in t for t in dz.messages("log")))
        self.assertEqual(dz.Devices[RAIN_24H].sValue, "3.4")             # before the first poll: from the file
        self.assertTrue(self.beat_until(plugin, lambda: dz.Devices[STATUS].sValue == "On"))
        self.assertEqual(dz.Devices[RAIN_24H].sValue, "3.4")             # and still, not 6.8
        plugin.onStop()

    def test_a_separate_rate_device_and_rain_only(self):
        self.fake.add(rain_device(1323, "Weather - Rate", counter=0.0, rate=7.0))
        plugin = self.load(Mode2="1323", Mode5="rain")
        plugin.onStart()
        self.assertEqual(sorted(dz.Devices), list(range(1, 9)) + [17, 18])
        self.assertTrue(self.beat_until(plugin, lambda: dz.Devices[STATUS].sValue == "On"))
        self.assertEqual(self.errors(), [])
        plugin.onStop()

    def test_the_password_never_reaches_the_log_even_at_debug_level(self):
        self.fake.user, self.fake.password = "jan", "s3cretpw"
        plugin = self.load(Username="jan", Password="s3cretpw", Mode6="3")
        plugin.onStart()
        self.assertTrue(self.beat_until(plugin, lambda: dz.Devices[STATUS].sValue == "On"))
        plugin.onStop()
        self.assertNotIn("s3cretpw", dz.all_text())
        self.assertNotIn("czNjcmV0cHc=", dz.all_text())                  # nor the base64 of it
        self.assertTrue(any(t.startswith("[DEBUG]") for t in dz.messages("debug")))

    def test_a_wrong_password_is_one_clear_error(self):
        self.fake.user, self.fake.password = "jan", "s3cretpw"
        plugin = self.load(Username="jan", Password="wrong")
        plugin.onStart()
        self.assertTrue(self.beat_until(plugin, lambda: len(self.errors()) >= 1))
        plugin.onHeartbeat()
        self.assertEqual(len(self.errors()), 1)
        self.assertIn("Local Networks", self.errors()[0])
        self.assertEqual(dz.Devices[STATUS].sValue, "Off")
        self.assertNotIn("wrong", dz.all_text())
        plugin.onStop()

    # -- things that go wrong -------------------------------------------------------------------------------
    def test_a_bad_configuration_is_reported_and_the_plugin_stays_idle(self):
        plugin = self.load(Mode1="")
        plugin.onStart()
        self.assertTrue(any("Configuration error: The rain device IDX is required" in t for t in self.errors()))
        plugin.onHeartbeat()
        plugin.onCommand(17, "Off", 0, "")
        plugin.onStop()
        self.assertTrue(any("did not start" in t for t in self.errors()))
        self.assertEqual(dz.Devices, {})
        self.assertEqual(self.fake.requests, [])

    def test_an_unreachable_domoticz_is_one_error_and_the_devices_exist(self):
        port = self.fake.port
        self.fake.stop()
        plugin = self.load(Port=str(port))
        plugin.onStart()
        self.assertEqual(sorted(dz.Devices), list(range(1, 19)))
        self.assertTrue(self.beat_until(plugin, lambda: len(self.errors()) >= 1))
        plugin.onHeartbeat()
        plugin.onHeartbeat()
        self.assertEqual(len(self.errors()), 1)
        self.assertIn("Reading the rain device failed", self.errors()[0])
        self.assertEqual(dz.Devices[STATUS].sValue, "Off")
        self.assertTrue(dz.Devices[INFO].sValue.startswith("Source not reachable since"))
        plugin.onStop()

    def test_a_device_that_does_not_exist_names_the_idx_and_the_remedy(self):
        plugin = self.load(Mode1="999")
        plugin.onStart()
        self.assertTrue(self.beat_until(plugin, lambda: len(self.errors()) >= 1))
        self.assertIn("no device with this IDX", self.errors()[0])
        plugin.onStop()

    def test_commands_are_answered_not_obeyed(self):
        plugin = self.load()
        plugin.onStart()
        self.assertTrue(self.beat_until(plugin, lambda: dz.Devices[STATUS].sValue == "On"))
        plugin.onCommand(STATUS, "Off", 0, "")
        self.assertEqual(dz.Devices[STATUS].sValue, "On")
        self.assertTrue(any("Command received: unit 17" in t for t in dz.messages("log")))
        plugin.onStop()

    def test_an_unexpected_exception_does_not_escape_a_callback(self):
        plugin = self.load()
        plugin.onStart()

        def boom(*args):
            raise RuntimeError("boom")
        plugin._plugin.controller.on_heartbeat = boom
        plugin._plugin.controller.on_command = boom
        plugin.onHeartbeat()
        plugin.onCommand(17, "Off", 0, "")
        self.assertEqual(len([t for t in self.errors() if "RuntimeError: boom" in t]), 2)
        plugin._plugin.controller.stop = boom
        plugin.onStop()
        self.assertEqual(len([t for t in self.errors() if "RuntimeError: boom" in t]), 3)

    def test_debug_levels_wire_up_domoticz_debugging(self):
        plugin = self.load(Mode6="3")
        plugin.onStart()
        self.assertIn(("debugging", 1), dz.logs)
        self.assertTrue(any(t.startswith("[DEBUG]") for t in dz.messages("debug")))
        plugin.onStop()
        plugin = self.load(Mode6="0")
        plugin.onStart()
        self.assertNotIn(("debugging", 1), dz.logs)
        self.assertEqual(dz.messages("debug"), [])
        plugin.onStop()

    def test_the_corrections_made_to_the_settings_are_told(self):
        plugin = self.load(Mode3="2", Mode2="1161")
        plugin.onStart()
        text = "\n".join(dz.messages("log"))
        self.assertIn("Update interval 2 s is outside 10-3600 s, using 10 s", text)
        self.assertIn("rain rate device is the rain device", text)
        plugin.onStop()

    def test_a_technical_setting_from_the_environment_is_used(self):
        env = os.path.join(ROOT, ".env")
        self.assertFalse(os.path.exists(env), "a .env in the source tree would change every test")
        # the process environment is read like the .env file and wins over it
        os.environ["RS_SAVE_INTERVAL"] = "120"
        plugin = self.load(Mode6="1")
        plugin.onStart()
        self.assertTrue(any("saved every 120 s" in t for t in dz.messages("debug")))
        plugin.onStop()


class ManifestTests(unittest.TestCase):
    def source(self):
        with open(os.path.join(ROOT, "plugin.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_manifest_has_the_key_the_name_and_every_field_the_settings_read(self):
        manifest = self.source().split('"""')[1]
        self.assertIn('key="Rain-Stats"', manifest)                      # Domoticz stores the hardware by this key
        self.assertIn('name="Rain Statistics"', manifest)
        for field in ("Address", "Port", "Username", "Password", "Mode1", "Mode2", "Mode3", "Mode4", "Mode5", "Mode6"):
            self.assertIn('field="%s"' % field, manifest)
        self.assertEqual(len(re.findall(r"<param ", manifest)), 10)
        self.assertIn('password="true"', manifest)

    def test_the_options_of_the_manifest_are_the_values_the_settings_accept(self):
        from rain_stats.constants import COUNTER_MODES, STATS_MODES
        manifest = self.source().split('"""')[1]
        for value in COUNTER_MODES + STATS_MODES:
            self.assertIn('value="%s"' % value, manifest)
        for level in "0123":
            self.assertIn('value="%s"' % level, manifest)

    def test_the_defaults_of_the_manifest_are_the_defaults_of_the_code(self):
        from rain_stats.constants import DEFAULT_HOST, DEFAULT_INTERVAL, DEFAULT_PORT
        manifest = self.source().split('"""')[1]
        self.assertIn('field="Address" label="Domoticz address" width="200px" required="true" default="%s"' % DEFAULT_HOST,
                      manifest)
        self.assertIn('field="Port" label="Domoticz port" width="60px" required="true" default="%d"' % DEFAULT_PORT, manifest)
        self.assertIn('label="Update Interval (seconds)" width="75px" required="true" default="%d"' % DEFAULT_INTERVAL,
                      manifest)

    def test_plugin_py_never_binds_a_name_domoticz_injects(self):
        tree = ast.parse(self.source())
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound |= {(a.asname or a.name).split(".")[0] for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        self.assertEqual(bound & RESERVED, set(), "plugin.py would be shadowed by Domoticz' injected globals")

    def test_only_plugin_py_imports_domoticz(self):
        package = os.path.join(ROOT, "rain_stats")
        for name in sorted(os.listdir(package)):
            if name.endswith(".py"):
                with open(os.path.join(package, name), encoding="utf-8") as fh:
                    self.assertIsNone(re.search(r"^\s*(import|from)\s+Domoticz\b", fh.read(), re.M), name)

    def test_the_callbacks_domoticz_calls_exist(self):
        tree = ast.parse(self.source())
        top = {n.name: [a.arg for a in n.args.args] for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(top["onCommand"], ["Unit", "Command", "Level", "Hue"])      # Domoticz calls it positionally
        for name in ("onStart", "onStop", "onHeartbeat"):
            self.assertIn(name, top)

    def test_the_standard_library_is_all_that_is_needed(self):
        stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
        if stdlib is None:
            self.skipTest("sys.stdlib_module_names needs Python 3.10")
        package = os.path.join(ROOT, "rain_stats")
        for name in sorted(os.listdir(package)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(package, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    modules = [node.module.split(".")[0]]
                for module in modules:
                    self.assertIn(module, stdlib, "%s imports the third-party module %s" % (name, module))

    def test_the_polling_thread_never_touches_domoticz_or_the_log(self):
        # the Domoticz API is not thread safe: the thread only reads the answer and queues it
        with open(os.path.join(ROOT, "rain_stats", "controller.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_poll_in_thread":
                body = ast.dump(node)
                for forbidden in ("'log'", "'devices'", "'state'", "'archive'"):
                    self.assertNotIn("attr=" + forbidden, body)
                return
        self.fail("_poll_in_thread not found")


if __name__ == "__main__":
    unittest.main()
