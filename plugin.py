"""
<plugin key="Rain-Stats" name="Rain Statistics" author="Jan Reimen" version="0.0.2" wikilink="https://github.com/janreimen/Domoticz-Rainstats" externallink="https://github.com/janreimen/Domoticz-Rainstats">
    <description>
        <h2>Rain Statistics</h2><br/>
        Reads the rain and the rain rate of your weather station from a Domoticz device and shows how much it rained
        in the last <b>1, 2, 7, 14, 24, 30, 60, 90, 120, 180, 360 or 365 days</b> (you choose in the file <tt>.env</tt>
        which of them get devices), and the highest rain rate in each of those windows. The data are kept in a
        fixed-size round-robin archive that survives restarts; nothing grows and nothing has to be cleaned up.
        Read-only; no account and no Python packages needed.<br/><br/>
        <h3>Features</h3>
        <ul style="list-style-type:square">
            <li>Rain 1 d ... Rain 365 d: the rain of the last N days in mm, one device for every window that
                <tt>.env</tt> switches on (<tt>RS_LAST_001_DAYS=Y</tt> ...)</li>
            <li>Peak Rain Rate 1 d ... 365 d: the highest rain rate in each of those windows in mm/h (switch them
                off with the setting <i>Statistics</i>)</li>
            <li>"Rain Stats Status" shows whether the rain device can be read, "Rain Stats Info" what the plugin
                knows (units 17-18)</li>
            <li>Once, the daily rain history that Domoticz keeps of a rain device can be read, so the long windows
                are complete from the first day (<tt>RS_HISTORY_IDX</tt>)</li>
            <li>A rain counter that starts again at midnight ("rain today") or one that never does ("total") is
                handled either way, and a reset never loses or duplicates rain</li>
            <li>A problem with the rain device is logged once; the windows keep sliding while it is down</li>
            <li>One-time import of the data file of the dzVents script RainStats V2.6 (<tt>RS_LEGACY_FILE</tt>)</li>
        </ul>
        <h3>Configuration</h3>
        Fill in the <b>IDX</b> of your rain device (Setup &gt; Devices, column Idx). The address defaults to this
        machine (127.0.0.1:8080); give a user and password only when the Domoticz API asks for them. More settings
        (state file, https, limits) are in the file <tt>.env</tt> beside the plugin.<br/><br/>
        <h3>Documentation</h3>
        <a href="https://github.com/janreimen/Domoticz-Rainstats">https://github.com/janreimen/Domoticz-Rainstats</a><br/>
    </description>
    <params>
        <param field="Address" label="Domoticz address" width="200px" required="true" default="127.0.0.1"/>
        <param field="Port" label="Domoticz port" width="60px" required="true" default="8080"/>
        <param field="Username" label="Domoticz user (optional)" width="200px" required="false" default=""/>
        <param field="Password" label="Domoticz password (optional)" width="200px" required="false" default="" password="true"/>
        <param field="Mode1" label="Rain device IDX" width="75px" required="true" default=""/>
        <param field="Mode2" label="Rain rate device IDX (optional)" width="75px" required="false" default=""/>
        <param field="Mode3" label="Update Interval (seconds)" width="75px" required="true" default="60"/>
        <param field="Mode4" label="Rain counter" width="300px">
            <options>
                <option label="Resets at midnight (rain today)" value="daily" default="true"/>
                <option label="Never resets (total)" value="total"/>
            </options>
        </param>
        <param field="Mode5" label="Statistics" width="300px">
            <options>
                <option label="Rain and peak rain rate" value="rain_rate" default="true"/>
                <option label="Rain only" value="rain"/>
            </options>
        </param>
        <param field="Mode6" label="Debug" width="120px">
            <options>
                <option label="Off" value="0" default="true"/>
                <option label="Basic" value="1"/>
                <option label="Verbose" value="2"/>
                <option label="Debug" value="3"/>
            </options>
        </param>
    </params>
</plugin>
"""
import os
import ssl
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import Domoticz  # noqa: E402  (provided by Domoticz)

# Domoticz injects the globals Parameters, Devices, Images and Settings into this module (and replaces them before
# every callback). Never bind one of those names here: the injected dict would silently shadow it. The modules are
# imported under other names.
from rain_stats import __version__  # noqa: E402
from rain_stats import config as rs_config  # noqa: E402
from rain_stats.api import ApiError, Client  # noqa: E402
from rain_stats.constants import HEARTBEAT_SECONDS, PLUGIN_NAME  # noqa: E402
from rain_stats.controller import Controller  # noqa: E402
from rain_stats.devices import DeviceManager  # noqa: E402
from rain_stats.logger import LEVEL_NAMES, Logger  # noqa: E402


class BasePlugin:
    def __init__(self):
        self.controller = None          # stays None until onStart completed: every other callback then does nothing
        self.log = Logger(Domoticz)

    # -- Domoticz callbacks ------------------------------------------------------------------------------
    def onStart(self):
        self.controller = None
        self.log = log = Logger(Domoticz)
        try:
            settings = rs_config.Settings.from_parameters(Parameters)
        except rs_config.SettingsError as exc:
            log.Error("Configuration error: %s" % exc)
            return
        log.set_level(settings.debug)
        for secret in settings.secrets():
            log.add_secret(secret)
        rate = ", rain rate IDX %d" % settings.rate_idx if settings.rate_idx else ""
        log.Log("%s %s starting (Domoticz at %s, rain IDX %d%s, update every %d s, %s counter, %s, debug level %d: %s)" % (
            PLUGIN_NAME, __version__, settings.base_url, settings.rain_idx, rate, settings.interval, settings.counter,
            "rain and peak rate" if settings.with_peaks else "rain only", settings.debug, LEVEL_NAMES[settings.debug]))
        log.Log("Windows: %s%s" % (", ".join(w.label for w in settings.windows),
                                   "; history of IDX %d is read once" % settings.history_idx if settings.history_idx else ""))
        for note in settings.notes:
            log.Log(note)
        for warning in settings.warnings:
            log.Log("Configuration warning: %s" % warning)
        log.Basic("Configuration file: %s" % (settings.env_file or "none (.env not found, built-in defaults)"))
        log.Basic("State file: %s, saved every %d s" % (settings.state_file, settings.save_interval))

        try:
            client = Client(settings.scheme, settings.host, settings.port, settings.username, settings.password,
                            settings.http_timeout, settings.ca_file)
        except (ApiError, OSError, ssl.SSLError) as exc:
            log.Error("Cannot set up the connection to Domoticz: %s" % exc)
            return
        devices = DeviceManager(Domoticz, lambda: Devices, log, with_peaks=settings.with_peaks,
                                refresh_seconds=settings.refresh_interval, windows=settings.windows)
        controller = Controller(settings, client, devices, log)
        Domoticz.Heartbeat(HEARTBEAT_SECONDS)
        try:
            controller.start()
        except Exception as exc:
            self._unexpected("start-up", exc)
            return
        self.controller = controller

    def onStop(self):
        if self.controller is not None:
            try:
                self.controller.stop()
            except Exception as exc:
                self._unexpected("shutdown", exc)
            self.controller = None
        self.log.Log("Rain Statistics stopped")

    def onHeartbeat(self):
        if self.controller is None:
            return
        try:
            self.controller.on_heartbeat()
        except Exception as exc:
            self._unexpected("heartbeat", exc)

    def onCommand(self, Unit, Command, Level, Hue):
        if self.controller is None:
            self.log.Error("Command for unit %s ignored: the plugin did not start, see the errors above" % Unit)
            return
        try:
            self.controller.on_command(Unit, Command, Level)
        except Exception as exc:
            self._unexpected("command for unit %s" % Unit, exc)

    # -- helpers -----------------------------------------------------------------------------------------
    def _unexpected(self, where, exc):
        self.log.Error("Unexpected error in %s: %s: %s" % (where, type(exc).__name__, exc))
        self.log.Debug(traceback.format_exc())


_plugin = BasePlugin()


def onStart():
    _plugin.onStart()


def onStop():
    _plugin.onStop()


def onHeartbeat():
    _plugin.onHeartbeat()


def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)
