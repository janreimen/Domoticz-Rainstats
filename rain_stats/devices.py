"""The only module that talks to the Domoticz device API.

It is handed the ``Domoticz`` module and a callable returning the current ``Devices`` dict (Domoticz replaces that
dict before every callback, so it is never kept), and turns statistics into devices:

    * the rain sensors and the peak rain rate sensors (unless the setting *Statistics* is "Rain only") of the windows
      that the .env switches on (RS_LAST_..._DAYS), and the two fixed devices (units 17-18), created when missing -
      also later, when Domoticz did not accept new devices at first ("Accept new Hardware Devices" is off); a sensor
      that has no unit label gets one. A device of a window that is switched off is neither created nor written
    * every device is created "in use" (Used=1), so nothing has to be added by hand under Setup > Devices
    * values are written when they change, and every REFRESH_SECONDS even when they did not, so the "last seen" time
      stays fresh and Domoticz' sensor time-out never marks a quiet sensor as dead
    * a name or a visibility you set in Domoticz is never touched; the plugin only writes values

All devices are read-only: a command sent to one is answered by writing its real value back.
"""
import time
from typing import Any, Callable, Dict, Iterable, Optional

from .constants import (
    ALL_DEVICES, CREATE_RETRY_SECONDS, DECIMALS_RAIN, DECIMALS_RATE, FIXED_DEVICES, KIND_RAIN, KIND_RATE, KIND_TEXT,
    MAX_TEXT, PEAK_DEVICES, RAIN_DEVICES, REFRESH_SECONDS, SUBTYPE_CUSTOM, SWITCHTYPE_ONOFF, TYPENAME_CUSTOM,
    TYPENAME_SWITCH, TYPENAME_TEXT, UNIT_INFO, UNIT_STATUS, WINDOWS, Device, Window,
)
from .model import Stats
from .util import number_text, shorten


def _custom_unit(options_text: str) -> str:
    """The unit of a Custom Sensor: Domoticz keeps it as "<axis>;<unit>" in the device options."""
    return options_text.partition(";")[2].strip()


class DeviceManager:
    def __init__(self, domoticz: Any, get_devices: Callable[[], Dict[int, Any]], logger: Any, with_peaks: bool = True,
                 clock: Callable[[], float] = time.monotonic, refresh_seconds: int = REFRESH_SECONDS,
                 windows: Optional[Iterable[Window]] = None) -> None:
        self.dz = domoticz
        self.get_devices = get_devices
        self.log = logger
        self.clock = clock
        self.refresh_seconds = refresh_seconds
        self.with_peaks = with_peaks
        self.windows = tuple(WINDOWS if windows is None else windows)           # the windows that get devices
        keys = {w.key for w in self.windows}
        self.model = (tuple(d for d in RAIN_DEVICES if d.window in keys)
                      + (tuple(d for d in PEAK_DEVICES if d.window in keys) if with_peaks else ())
                      + tuple(FIXED_DEVICES))
        self._paused_until = 0.0                # Domoticz refused to create a device: no new attempt before this
        self._creation_refused = False
        self._unit_checked: set = set()         # units whose unit label was looked at since start
        self._written: Dict[int, float] = {}    # unit -> time of the last write
        self._warned: set = set()               # problems reported once

    # -- creating -------------------------------------------------------------------------------------------
    def ensure_devices(self) -> None:
        """Create the devices that are missing and give sensors without a unit label their label.

        Called at start and by every poll: a device Domoticz did not accept at first appears as soon as the setting
        "Accept new Hardware Devices" is on, without restarting the hardware."""
        for spec in self.model:
            device = self.get_devices().get(spec.unit)
            if device is not None:
                self._add_missing_unit(device, spec)
                continue
            if self.clock() < self._paused_until:
                return
            self.dz.Device(**self._kwargs(spec)).Create()
            if spec.unit not in self.get_devices():
                self._creation_failed("'%s' (unit %d)" % (spec.name, spec.unit))
                return                      # the reason is the same for all of them: ask Domoticz once, not many times
            self._creation_worked()
            self.log.Basic("Created device '%s' (unit %d)" % (spec.name, spec.unit))

    def _add_missing_unit(self, device: Any, spec: Device) -> None:
        """A sensor created without a unit label gets the label it should have; a label you set yourself stays."""
        if spec.kind not in (KIND_RAIN, KIND_RATE) or not spec.label or spec.unit in self._unit_checked:
            return
        self._unit_checked.add(spec.unit)
        if getattr(device, "SubType", None) != SUBTYPE_CUSTOM:
            return                          # not a Custom Sensor any more: not ours to change
        if _custom_unit((getattr(device, "Options", None) or {}).get("Custom", "")):
            return
        try:
            device.Update(nValue=device.nValue, sValue=device.sValue, Options={"Custom": "1;%s" % spec.label},
                          SuppressTriggers=True)        # only the options: the value and its triggers stay as they are
        except Exception as exc:
            self.log.Error("Could not add the unit '%s' to '%s': %s" % (spec.label, spec.name, exc))
            return
        self.log.Log("Added the unit '%s' to '%s' (unit %d)" % (spec.label, spec.name, spec.unit))

    def _creation_failed(self, what: str) -> None:
        self._paused_until = self.clock() + CREATE_RETRY_SECONDS
        if not self._creation_refused:
            self._creation_refused = True
            self.log.Error("Domoticz did not create the device %s. If its log says that the settings prevent accepting "
                           "new devices, switch on Setup > Settings > System > 'Accept new Hardware Devices'. "
                           "The plugin asks again every %d minutes" % (what, CREATE_RETRY_SECONDS // 60))

    def _creation_worked(self) -> None:
        if self._creation_refused:
            self._creation_refused = False
            self.log.Log("Domoticz creates the Rain Statistics devices now")

    @staticmethod
    def _kwargs(spec: Device) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = dict(Name=spec.name, Unit=spec.unit, Used=1)
        if spec.kind in (KIND_RAIN, KIND_RATE):
            kwargs.update(TypeName=TYPENAME_CUSTOM, Options={"Custom": "1;%s" % spec.label})
        elif spec.kind == KIND_TEXT:
            kwargs.update(TypeName=TYPENAME_TEXT)
        else:                                   # a flag: an On/Off switch that only shows a state
            kwargs.update(TypeName=TYPENAME_SWITCH, Switchtype=SWITCHTYPE_ONOFF)
        return kwargs

    # -- showing --------------------------------------------------------------------------------------------
    def show(self, stats: Stats) -> None:
        """Write the rain and peak rain rate sensors of every window."""
        now = self.clock()
        for spec in self.model:
            if spec.kind == KIND_RAIN:
                self._write(spec.unit, 0, number_text(stats.rain.get(spec.window, 0.0), DECIMALS_RAIN), now)
            elif spec.kind == KIND_RATE:
                self._write(spec.unit, 0, number_text(stats.peak.get(spec.window, 0.0), DECIMALS_RATE), now)

    def set_status(self, online: bool) -> None:
        self._write(UNIT_STATUS, 1 if online else 0, "On" if online else "Off", self.clock())

    def set_info(self, text: str) -> None:
        self._write(UNIT_INFO, 0, shorten(text, MAX_TEXT), self.clock())

    # -- commands -------------------------------------------------------------------------------------------
    def name_of(self, unit: int) -> str:
        device = self.get_devices().get(unit)
        return "" if device is None else str(device.Name)

    def restore(self, unit: int) -> None:
        """Put a device back to its real value after a command: all devices of this plugin are read-only."""
        device = self.get_devices().get(unit)
        if device is not None:
            # a normal write, not SuppressTriggers: that flag leaves the value alone, and the point is that the value
            # (the real one: Domoticz never changes a plugin device by itself) is written again, so the web interface
            # shows it again
            device.Update(nValue=device.nValue, sValue=device.sValue)

    def owns(self, unit: int) -> bool:
        return any(spec.unit == unit for spec in ALL_DEVICES)

    # -- writing --------------------------------------------------------------------------------------------
    def _write(self, unit: int, nvalue: int, svalue: str, now: Optional[float] = None) -> None:
        device = self.get_devices().get(unit)
        if device is None:
            if ("missing", unit) not in self._warned and not self._creation_refused:      # refused: already explained
                self._warned.add(("missing", unit))
                self.log.Error("Cannot update Domoticz unit %d: no such device. It was deleted or Domoticz did not "
                               "create it; the plugin creates it again as soon as Domoticz accepts new devices" % unit)
            return
        self._warned.discard(("missing", unit))
        svalue = shorten(svalue, MAX_TEXT)
        changed = device.nValue != nvalue or device.sValue != svalue
        stale = now is not None and now - self._written.get(unit, 0.0) >= self.refresh_seconds
        if not (changed or stale):
            return
        device.Update(nValue=nvalue, sValue=svalue)
        if now is not None:
            self._written[unit] = now
        if changed:
            self.log.Debug("Update unit %d: %d '%s'" % (unit, nvalue, svalue))
