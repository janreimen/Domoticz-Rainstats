"""The device layer: creation, unit labels, writes only when something changed, read-only devices, refused creation."""
import unittest

from helpers import Clock, make_logger
import domoticz_stub as dz
from rain_stats.constants import (ALL_DEVICES, FIXED_DEVICES, PEAK_DEVICES, RAIN_DEVICES, SUBTYPE_CUSTOM, UNIT_INFO,
                                  UNIT_STATUS, WINDOWS)
from rain_stats.devices import DeviceManager
from rain_stats.model import Stats


DEFAULT_WINDOWS = tuple(w for w in WINDOWS if w.default)         # what a plugin without a .env creates: 1 d ... 365 d


def stats(rain=0.0, peak=0.0):
    return Stats(0.0, {w.key: rain for w in WINDOWS}, {w.key: peak for w in WINDOWS}, None, ())


class Base(unittest.TestCase):
    def setUp(self):
        dz.reset()
        self.clock = Clock(1000.0)
        self.log = make_logger(3)
        self.mgr = DeviceManager(dz, lambda: dz.Devices, self.log, clock=self.clock, refresh_seconds=300,
                                 windows=DEFAULT_WINDOWS)

    def errors(self):
        return dz.messages("error")


class Creation(Base):
    def test_all_devices_are_created_in_use(self):
        self.mgr.ensure_devices()
        self.assertEqual(sorted(dz.Devices), list(range(1, 19)))
        self.assertTrue(all(d.Used == 1 for d in dz.Devices.values()))
        self.assertEqual(self.errors(), [])

    def test_the_kinds_become_the_right_domoticz_types(self):
        self.mgr.ensure_devices()
        rain, peak, status, info = dz.Devices[1], dz.Devices[9], dz.Devices[UNIT_STATUS], dz.Devices[UNIT_INFO]
        self.assertEqual((rain.Type, rain.SubType, rain.Options), (243, SUBTYPE_CUSTOM, {"Custom": "1;mm"}))
        self.assertEqual(peak.Options, {"Custom": "1;mm/h"})
        self.assertEqual((status.Type, status.kw["Switchtype"]), (244, 0))
        self.assertEqual((info.Type, info.SubType), (243, 5))

    def test_names_and_units_follow_the_model(self):
        self.mgr.ensure_devices()
        for spec in self.mgr.model:                            # the devices of the windows that are on
            self.assertEqual(dz.Devices[spec.unit].kw["Name"], spec.name)
        self.assertEqual(len(self.mgr.model), 18)

    def test_nothing_is_created_twice(self):
        self.mgr.ensure_devices()
        first = dict(dz.Devices)
        self.mgr.ensure_devices()
        self.assertEqual(dz.create_attempts, list(range(1, 19)))
        self.assertTrue(all(dz.Devices[u] is first[u] for u in first))

    def test_without_peaks_there_are_no_peak_devices(self):
        mgr = DeviceManager(dz, lambda: dz.Devices, self.log, with_peaks=False, clock=self.clock, windows=DEFAULT_WINDOWS)
        mgr.ensure_devices()
        self.assertEqual(sorted(dz.Devices), list(range(1, 9)) + [17, 18])
        mgr.show(stats(rain=1.0, peak=9.0))
        self.assertEqual(dz.Devices[1].sValue, "1.0")

    def test_a_device_that_is_deleted_comes_back(self):
        self.mgr.ensure_devices()
        dz.Devices[3].Delete()
        self.mgr.ensure_devices()
        self.assertIn(3, dz.Devices)

    def test_unit_numbers_never_change(self):
        # a unit is never renumbered or reused. The first eight windows have the units of the first build (1-8 rain,
        # 9-16 peak), the status and the info device 17 and 18, the windows that came later 19-22 and 23-26
        rain = {"1d": 1, "2d": 2, "7d": 3, "14d": 4, "30d": 5, "90d": 6, "180d": 7, "365d": 8,
                "24d": 19, "60d": 20, "120d": 21, "360d": 22}
        self.assertEqual({d.window: d.unit for d in RAIN_DEVICES}, rain)
        self.assertEqual({d.window: d.unit for d in PEAK_DEVICES}, {k: (v + 8 if v <= 8 else v + 4) for k, v in rain.items()})
        self.assertEqual([d.unit for d in FIXED_DEVICES], [17, 18])
        self.assertEqual(sorted(d.unit for d in ALL_DEVICES), list(range(1, 27)))
        self.assertEqual(len({d.key for d in ALL_DEVICES}), 26)
        self.assertEqual([d.name for d in RAIN_DEVICES][:3], ["Rain 1 d", "Rain 2 d", "Rain 7 d"])
        self.assertEqual([d.name for d in PEAK_DEVICES][:3], ["Peak Rain Rate 1 d", "Peak Rain Rate 2 d", "Peak Rain Rate 7 d"])

    def test_only_the_chosen_windows_get_devices(self):
        chosen = [w for w in WINDOWS if w.key in ("2d", "60d", "360d")]
        mgr = DeviceManager(dz, lambda: dz.Devices, self.log, clock=self.clock, windows=chosen)
        mgr.ensure_devices()
        self.assertEqual(sorted(dz.Devices), [2, 10, 17, 18, 20, 22, 24, 26])
        self.assertEqual(dz.Devices[20].kw["Name"], "Rain 60 d")
        self.assertEqual(dz.Devices[26].kw["Name"], "Peak Rain Rate 360 d")
        mgr.show(Stats(0.0, {w.key: float(w.days) for w in WINDOWS}, {w.key: 0.5 * w.days for w in WINDOWS}, None, ()))
        self.assertEqual((dz.Devices[2].sValue, dz.Devices[20].sValue, dz.Devices[22].sValue), ("2.0", "60.0", "360.0"))
        self.assertEqual((dz.Devices[10].sValue, dz.Devices[24].sValue), ("1.00", "30.00"))
        self.assertEqual(self.errors(), [])                    # a window that is off is not "a device that is missing"

    def test_a_window_that_is_switched_off_keeps_its_old_device_untouched(self):
        self.mgr.ensure_devices()                              # the default windows, 1 d ... 365 d
        mgr = DeviceManager(dz, lambda: dz.Devices, self.log, clock=self.clock,
                            windows=[w for w in DEFAULT_WINDOWS if w.key != "30d"])
        mgr.show(stats(rain=7.0))
        self.assertEqual(dz.Devices[1].sValue, "7.0")
        self.assertEqual(dz.Devices[5].sValue, "")             # unit 5 is the 30 d window: nothing was written
        self.assertEqual(dz.Devices[5].updates, 0)
        self.assertTrue(mgr.owns(5))                           # still ours: a command to it is answered

    def test_refused_creation_is_one_error_one_pause_and_a_recovery(self):
        dz.accept_new_devices = False
        self.mgr.ensure_devices()
        self.assertEqual(dz.create_attempts, [1])
        self.assertEqual(len([e for e in self.errors() if "Accept new Hardware Devices" in e]), 1)
        self.mgr.ensure_devices()
        self.clock.advance(299)
        self.mgr.ensure_devices()
        self.assertEqual(dz.create_attempts, [1])                       # asked once, then left alone for 5 minutes
        self.clock.advance(2)
        self.mgr.ensure_devices()
        self.assertEqual(dz.create_attempts, [1, 1])
        self.assertEqual(len([e for e in self.errors() if "Accept new Hardware Devices" in e]), 1)    # not said again
        dz.accept_new_devices = True
        self.clock.advance(301)
        self.mgr.ensure_devices()
        self.assertEqual(sorted(dz.Devices), list(range(1, 19)))
        self.assertTrue(any("creates the Rain Statistics devices now" in t for t in dz.messages("log")))


class UnitLabels(Base):
    def make(self, unit, options, **kw):
        dz.Device(Name="Rain 1 d", Unit=unit, TypeName="Custom", Options=options, Used=1, **kw).Create()

    def test_a_sensor_without_a_unit_gets_mm(self):
        self.make(1, {"Custom": "0;"})
        self.mgr.ensure_devices()
        self.assertEqual(dz.Devices[1].Options, {"Custom": "1;mm"})
        self.assertTrue(any("Added the unit 'mm' to 'Rain 1 d'" in t for t in dz.messages("log")))
        call = dz.Devices[1].update_calls[-1]
        self.assertTrue(call["SuppressTriggers"])                       # only the options: the value stays

    def test_a_unit_you_chose_yourself_stays(self):
        self.make(1, {"Custom": "1;l/m2"})
        self.mgr.ensure_devices()
        self.assertEqual(dz.Devices[1].Options, {"Custom": "1;l/m2"})
        self.assertEqual(dz.Devices[1].update_calls, [])

    def test_a_device_that_is_no_custom_sensor_any_more_is_left_alone(self):
        dz.Device(Name="Rain 1 d", Unit=1, TypeName="Percentage", Used=1).Create()
        self.mgr.ensure_devices()
        self.assertEqual(dz.Devices[1].update_calls, [])

    def test_the_label_is_looked_at_once_per_start(self):
        self.make(1, {"Custom": "0;"})
        self.mgr.ensure_devices()
        dz.Devices[1].Options = {"Custom": "0;"}                         # someone removed the unit again
        self.mgr.ensure_devices()
        self.assertEqual(len(dz.Devices[1].update_calls), 1)


class Writing(Base):
    def setUp(self):
        super().setUp()
        self.mgr.ensure_devices()

    def test_the_rain_is_written_with_one_decimal_and_the_peak_with_two(self):
        self.mgr.show(stats(rain=3.456, peak=12.3456))
        self.assertEqual((dz.Devices[1].nValue, dz.Devices[1].sValue), (0, "3.5"))
        self.assertEqual(dz.Devices[9].sValue, "12.35")
        self.assertEqual(dz.Devices[8].sValue, "3.5")
        self.assertEqual(dz.Devices[16].sValue, "12.35")

    def test_every_window_goes_to_its_own_device(self):
        stat = Stats(0.0, {w.key: float(i + 1) for i, w in enumerate(DEFAULT_WINDOWS)},
                     {w.key: 10.0 * (i + 1) for i, w in enumerate(DEFAULT_WINDOWS)}, None, ())
        self.mgr.show(stat)
        self.assertEqual([dz.Devices[u].sValue for u in range(1, 9)], ["%.1f" % v for v in range(1, 9)])
        self.assertEqual([dz.Devices[u].sValue for u in range(9, 17)], ["%.2f" % (10.0 * v) for v in range(1, 9)])

    def test_never_minus_zero(self):
        self.mgr.show(stats(rain=-0.0004, peak=-0.0))
        self.assertEqual(dz.Devices[1].sValue, "0.0")
        self.assertEqual(dz.Devices[9].sValue, "0.00")

    def test_an_unchanged_value_is_not_written_again_until_the_refresh_time(self):
        self.mgr.show(stats(rain=1.0))
        updates = dz.Devices[1].updates
        self.clock.advance(299)
        self.mgr.show(stats(rain=1.0))
        self.assertEqual(dz.Devices[1].updates, updates)
        self.clock.advance(2)
        self.mgr.show(stats(rain=1.0))
        self.assertEqual(dz.Devices[1].updates, updates + 1)

    def test_a_change_is_written_at_once(self):
        self.mgr.show(stats(rain=1.0))
        updates = dz.Devices[1].updates
        self.mgr.show(stats(rain=1.1))
        self.assertEqual(dz.Devices[1].updates, updates + 1)
        self.assertEqual(dz.Devices[1].sValue, "1.1")

    def test_the_status_switch(self):
        self.mgr.set_status(True)
        self.assertEqual((dz.Devices[UNIT_STATUS].nValue, dz.Devices[UNIT_STATUS].sValue), (1, "On"))
        self.mgr.set_status(False)
        self.assertEqual((dz.Devices[UNIT_STATUS].nValue, dz.Devices[UNIT_STATUS].sValue), (0, "Off"))

    def test_the_info_text_is_cut_to_what_a_text_device_holds(self):
        self.mgr.set_info("x" * 500)
        self.assertEqual(len(dz.Devices[UNIT_INFO].sValue), 200)
        self.assertTrue(dz.Devices[UNIT_INFO].sValue.endswith("..."))

    def test_writing_to_a_device_that_does_not_exist_is_one_error(self):
        dz.Devices[2].Delete()
        self.mgr.show(stats(rain=1.0))
        self.mgr.show(stats(rain=2.0))
        self.assertEqual(len([e for e in self.errors() if "Cannot update Domoticz unit 2" in e]), 1)
        self.mgr.ensure_devices()
        self.mgr.show(stats(rain=3.0))
        dz.Devices[2].Delete()
        self.mgr.show(stats(rain=4.0))
        self.assertEqual(len([e for e in self.errors() if "Cannot update Domoticz unit 2" in e]), 2)


class ReadOnly(Base):
    def setUp(self):
        super().setUp()
        self.mgr.ensure_devices()
        self.mgr.set_status(True)

    def test_a_command_writes_the_real_value_again(self):
        device = dz.Devices[UNIT_STATUS]
        updates = device.updates
        self.mgr.restore(UNIT_STATUS)
        self.assertEqual((device.nValue, device.sValue), (1, "On"))
        self.assertEqual(device.updates, updates + 1)
        self.assertFalse(device.update_calls[-1]["SuppressTriggers"])      # that flag would leave the value unwritten

    def test_restoring_an_unknown_unit_does_nothing(self):
        self.mgr.restore(99)

    def test_ownership_and_names(self):
        self.assertTrue(self.mgr.owns(1) and self.mgr.owns(18) and self.mgr.owns(26))
        self.assertFalse(self.mgr.owns(27) or self.mgr.owns(0))
        self.assertEqual(self.mgr.name_of(1), "Rain 1 d")
        self.assertEqual(self.mgr.name_of(99), "")


if __name__ == "__main__":
    unittest.main()
