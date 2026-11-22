"""The controller end to end: scripted source, real archive, real device manager, real state file, controllable clocks."""
import json
import os
import unittest

import helpers
from helpers import Stack, fail, history_rows, local, write_legacy
import domoticz_stub as dz
from rain_stats import store
from rain_stats.constants import MAX_BACKOFF, UNIT_INFO, UNIT_STATUS

RAIN_24H, RAIN_48H, RAIN_365D = 1, 2, 8
PEAK_24H, PEAK_48H = 9, 10


class FirstReading(unittest.TestCase):
    def test_start_creates_all_devices_before_the_first_poll_is_answered(self):
        s = Stack()
        s.controller.start()
        self.assertEqual(sorted(dz.Devices), list(range(1, 19)))
        self.assertTrue(all(d.Used == 1 for d in dz.Devices.values()))
        self.assertTrue(s.value(UNIT_INFO).startswith("Starting: no data collected yet"))

    def test_the_first_reading_of_a_daily_counter_credits_the_rain_of_today(self):
        s = Stack(start="2026-11-18 06:00")
        s.client.counter = 3.0
        s.start()
        for unit in range(1, 9):
            self.assertEqual(s.value(unit), "3.0", "unit %d" % unit)
        self.assertEqual(s.value(UNIT_STATUS), "On")
        self.assertIn("collecting since 2026-11-18 00:00", s.value(UNIT_INFO))
        self.assertTrue(s.value(UNIT_INFO).startswith("OK 06:00"))
        self.assertEqual(s.errors(), [])
        self.assertTrue(any("Connected to Domoticz at scripted:0: the rain counter reads 3.00 mm" in t
                            for t in dz.messages("log")))

    def test_a_total_counter_says_nothing_about_the_past(self):
        s = Stack(start="2026-11-18 06:00", counter="total")
        s.client.counter = 812.4
        s.start()
        self.assertEqual(s.value(RAIN_24H), "0.0")
        self.assertIn("collecting since 2026-11-18 06:00", s.value(UNIT_INFO))
        s.step(60, counter=812.9)
        self.assertEqual(s.value(RAIN_24H), "0.5")

    def test_a_dry_start_has_nothing_to_credit(self):
        s = Stack(start="2026-11-18 06:00")
        s.start()
        self.assertEqual(s.value(RAIN_24H), "0.0")
        self.assertEqual(s.value(PEAK_24H), "0.00")


class Counting(unittest.TestCase):
    def test_the_difference_between_readings_is_the_rain(self):
        s = Stack().start()
        s.step(60, counter=0.4, rate=12.0)
        s.step(60, counter=1.0, rate=3.0)
        self.assertEqual(s.value(RAIN_24H), "1.0")
        self.assertEqual(s.value(PEAK_24H), "12.00")
        self.assertIn("3.0 mm/h", s.value(UNIT_INFO))

    def test_nothing_is_polled_before_the_interval_is_over(self):
        s = Stack(interval=60).start()
        reads = s.client.reads
        s.step(30, counter=0.4)
        self.assertEqual(s.client.reads, reads)
        s.step(30)
        self.assertEqual(s.client.reads, reads + 1)

    def test_rain_and_peak_leave_their_windows_in_order(self):
        s = Stack(start="2026-11-18 06:00").start()
        s.step(60, counter=3.4, rate=12.0)
        s.step(25 * 3600, counter=0.0, rate=0.0)             # the next day, the counter was reset
        self.assertEqual(s.value(RAIN_24H), "0.0")
        self.assertEqual(s.value(PEAK_24H), "0.00")
        self.assertEqual(s.value(RAIN_48H), "3.4")
        self.assertEqual(s.value(PEAK_48H), "12.00")
        self.assertEqual(s.value(RAIN_365D), "3.4")
        s.step(25 * 3600, counter=0.0)
        self.assertEqual(s.value(RAIN_48H), "0.0")
        self.assertEqual(s.value(PEAK_48H), "0.00")
        self.assertEqual(s.value(RAIN_365D), "3.4")

    def test_a_reset_at_midnight_never_loses_or_duplicates_rain(self):
        s = Stack(start="2026-11-18 06:00").start()
        s.step(1078 * 60, counter=5.0)                       # 23:58
        s.step(60, counter=5.2)                              # 23:59
        s.step(60, counter=0.0)                              # 00:00: the counter starts again
        self.assertEqual(s.value(RAIN_24H), "5.2")
        s.step(60, counter=0.3)                              # 00:01
        self.assertEqual(s.value(RAIN_24H), "5.5")
        self.assertEqual(s.value(RAIN_365D), "5.5")
        self.assertEqual(s.errors(), [])

    def test_the_rain_right_after_the_reset_is_counted(self):
        # the dzVents script threw it away: the first value after a reset was only compared, never added
        s = Stack(start="2026-11-18 23:58").start()
        s.step(60, counter=0.0)                              # 23:59
        s.step(60, counter=0.0)                              # 00:00
        s.step(60, counter=0.8)                              # 00:01: 0.8 mm fell in the first minute of the day
        self.assertEqual(s.value(RAIN_24H), "0.8")

    def test_a_glitch_in_the_middle_of_the_day_is_one_log_line_and_no_rain(self):
        s = Stack(start="2026-11-18 12:00").start()
        s.step(60, counter=2.0)
        s.step(60, counter=0.5)
        self.assertEqual(s.value(RAIN_24H), "2.0")
        self.assertEqual(len([t for t in dz.messages("log") if "in the middle of the day" in t]), 1)
        s.step(60, counter=0.9)
        self.assertEqual(s.value(RAIN_24H), "2.4")
        s.step(60, counter=0.2)
        s.step(60, counter=0.4)
        self.assertEqual(len([t for t in dz.messages("log") if "in the middle of the day" in t]), 1)
        self.assertEqual(s.errors(), [])

    def test_a_jump_beyond_the_limit_is_an_error_once_and_no_rain(self):
        s = Stack(start="2026-11-18 12:00").start()
        s.step(60, counter=500.0)
        s.step(60, counter=900.0)
        self.assertEqual(s.value(RAIN_24H), "0.0")
        self.assertEqual(len(s.errors()), 1)
        self.assertIn("RS_MAX_DELTA", s.errors()[0])

    def test_a_missing_rate_is_derived_from_close_readings(self):
        s = Stack().start()
        s.client.rate = None
        s.step(120, counter=0.5)                             # 0.5 mm in 2 minutes = 15 mm/h
        self.assertEqual(s.value(PEAK_24H), "15.00")

    def test_no_rate_is_derived_across_a_long_gap(self):
        s = Stack().start()
        s.client.rate = None
        s.step(3 * 3600, counter=0.5)
        self.assertEqual(s.value(PEAK_24H), "0.00")
        self.assertEqual(s.value(RAIN_24H), "0.5")

    def test_a_sensor_that_timed_out_has_no_rain_rate(self):
        s = Stack().start()
        s.client.timed_out = True
        s.step(60, counter=0.2, rate=40.0)
        s.step(60, counter=0.2, rate=40.0)
        self.assertEqual(s.value(PEAK_24H), "0.00")
        self.assertEqual(len([t for t in dz.messages("log") if "sensor time-out" in t]), 1)
        s.client.timed_out = False
        s.step(60, counter=0.3, rate=6.0)
        self.assertEqual(s.value(PEAK_24H), "6.00")

    def test_an_impossible_rate_never_becomes_a_peak(self):
        s = Stack().start()
        s.step(60, counter=0.1, rate=5000.0)
        s.step(60, counter=0.2, rate=5000.0)
        self.assertEqual(s.value(PEAK_24H), "0.00")
        self.assertEqual(len([t for t in dz.messages("log") if "RS_RATE_DIVISOR" in t]), 1)

    def test_rain_only_creates_no_peak_devices(self):
        s = Stack(with_peaks=False).start()
        s.step(60, counter=0.4, rate=12.0)
        self.assertEqual(sorted(dz.Devices), list(range(1, 9)) + [17, 18])
        self.assertEqual(s.value(RAIN_24H), "0.4")


class SourceProblems(unittest.TestCase):
    def test_a_failed_poll_is_logged_once_and_recovery_once(self):
        s = Stack(start="2026-11-18 06:00").start()
        s.step(60, counter=1.0)
        s.step(60, error=fail("refused"))
        self.assertEqual(len(s.errors()), 1)
        self.assertIn("Reading the rain device failed: refused", s.errors()[0])
        self.assertEqual(s.value(UNIT_STATUS), "Off")
        self.assertTrue(s.value(UNIT_INFO).startswith("Source not reachable since 06:02"))
        s.step(120, error=fail("refused"))
        s.step(240, error=fail("refused"))
        self.assertEqual(len(s.errors()), 1)
        s.step(480, counter=1.5)
        self.assertEqual(len(s.errors()), 1)
        self.assertEqual(s.value(UNIT_STATUS), "On")
        self.assertTrue(any("can be read again" in t for t in dz.messages("log")))
        self.assertEqual(s.value(RAIN_24H), "1.5")             # the rain that fell meanwhile is counted

    def test_another_kind_of_problem_is_an_error_again(self):
        s = Stack().start()
        s.step(60, error=fail("refused", "network"))
        s.step(120, error=fail("Domoticz refuses the request", "auth"))
        self.assertEqual(len(s.errors()), 2)

    def test_the_wait_grows_after_each_failure_and_is_capped(self):
        s = Stack(interval=60).start()
        s.step(60, error=fail())
        reads = s.client.reads
        s.step(60, error=fail())                 # due 60 s after the first failure
        self.assertEqual(s.client.reads, reads + 1)
        s.step(60, error=fail())                 # the next one is due after 120 s: not yet
        self.assertEqual(s.client.reads, reads + 1)
        s.step(60, error=fail())
        self.assertEqual(s.client.reads, reads + 2)
        for _ in range(12):
            s.step(MAX_BACKOFF, error=fail())
        reads = s.client.reads
        s.step(MAX_BACKOFF, error=fail())
        self.assertEqual(s.client.reads, reads + 1)      # never longer than MAX_BACKOFF between two tries

    def test_the_windows_keep_sliding_while_the_source_is_down(self):
        s = Stack(start="2026-11-18 06:00").start()
        s.step(60, counter=3.4)
        self.assertEqual(s.value(RAIN_24H), "3.4")
        s.step(25 * 3600, error=fail())
        self.assertEqual(s.value(UNIT_STATUS), "Off")
        self.assertEqual(s.value(RAIN_24H), "0.0")
        self.assertEqual(s.value(RAIN_48H), "3.4")

    def test_the_windows_slide_without_any_poll_at_all(self):
        s = Stack(start="2026-11-18 06:00")
        s.client.counter = 3.4
        s.start()
        self.assertEqual(s.value(RAIN_24H), "3.4")
        reads = s.client.reads
        s.controller.next_poll_at = float("inf")            # no further poll: only the clock moves
        for _ in range(26 * 60):                            # 26 hours of heartbeats
            s.clock.advance(60)
            s.wall.advance(60)
            s.controller.on_heartbeat()
        self.assertEqual(s.client.reads, reads)
        self.assertEqual(s.value(RAIN_24H), "0.0")
        self.assertEqual(s.value(RAIN_48H), "3.4")

    def test_an_unexpected_exception_in_the_poll_is_a_failed_poll_not_a_crash(self):
        s = Stack().start()

        def boom(*args, **kwargs):
            raise RuntimeError("boom")
        s.client.read = boom
        s.step(60)
        self.assertEqual(s.value(UNIT_STATUS), "Off")
        self.assertIn("RuntimeError: boom", s.errors()[0])

    def test_devices_domoticz_did_not_accept_appear_later(self):
        dz.reset()
        s = Stack()
        dz.accept_new_devices = False
        s.start()
        self.assertEqual(dz.Devices, {})
        self.assertEqual(len([t for t in s.errors() if "Accept new Hardware Devices" in t]), 1)
        s.step(60, counter=0.4)
        self.assertEqual(dz.create_attempts, [1])            # one try, then Domoticz is left alone for a while
        dz.accept_new_devices = True
        s.step(301, counter=0.6)
        self.assertEqual(sorted(dz.Devices), list(range(1, 19)))
        self.assertEqual(s.value(RAIN_24H), "0.6")
        self.assertTrue(any("creates the Rain Statistics devices now" in t for t in dz.messages("log")))


class Restarts(unittest.TestCase):
    def test_a_restart_counts_nothing_twice(self):
        a = Stack(start="2026-11-18 06:00")
        a.client.counter = 3.0
        a.start()
        a.step(60, counter=3.4)
        a.controller.stop()
        b = Stack(start="2026-11-18 06:30", state_file=a.state_file)
        b.client.counter = 3.4
        b.start()
        self.assertEqual(b.value(RAIN_24H), "3.4")
        self.assertIn("collecting since 2026-11-18 00:00", b.value(UNIT_INFO))
        self.assertTrue(any("Loaded the state file" in t for t in dz.messages("log")))
        b.step(60, counter=3.9)
        self.assertEqual(b.value(RAIN_24H), "3.9")

    def test_the_rain_of_the_time_the_plugin_was_not_running_is_counted_when_it_comes_back(self):
        a = Stack(start="2026-11-18 06:00")
        a.client.counter = 1.0
        a.start()
        a.controller.stop()
        b = Stack(start="2026-11-18 09:00", state_file=a.state_file)
        b.client.counter = 2.5
        b.start()
        self.assertEqual(b.value(RAIN_24H), "2.5")

    def test_a_restart_over_midnight_counts_the_old_day_and_the_new_one(self):
        a = Stack(start="2026-11-18 23:50")
        a.client.counter = 4.0
        a.start()
        a.controller.stop()
        b = Stack(start="2026-11-19 00:20", state_file=a.state_file)
        b.client.counter = 0.4
        b.start()
        self.assertEqual(b.value(RAIN_48H), "4.4")
        self.assertEqual(b.value(RAIN_365D), "4.4")
        # the 24 h window of 00:20 does not reach back to the first minutes of yesterday: 25 minutes of the 4.0 mm,
        # which the first reading spread evenly over the day, are outside it
        self.assertEqual(b.value(RAIN_24H), "4.3")

    def test_stopping_saves_the_state_and_the_file_is_plain_json(self):
        s = Stack(start="2026-11-18 06:00", save_interval=900)
        s.client.counter = 3.0
        s.start()
        s.controller.stop()
        with open(s.state_file, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["v"], 1)
        self.assertEqual(data["counter"]["value"], 3.0)
        self.assertLess(os.path.getsize(s.state_file), 20000)       # a few kilobytes, not the whole ring

    def test_the_state_is_written_at_most_every_save_interval(self):
        s = Stack(start="2026-11-18 06:00", save_interval=900)
        s.client.counter = 0.0
        s.start()
        first = store.load(s.state_file, s.wall()).counter_ts
        s.step(60, counter=0.1)
        s.step(60, counter=0.2)
        self.assertEqual(store.load(s.state_file, s.wall()).counter_ts, first)          # not yet
        s.step(900, counter=0.3)
        # the heartbeat that starts a poll also saves, before the answer is applied: the file is one heartbeat behind,
        # never more than that, and always newer than before
        self.assertGreater(store.load(s.state_file, s.wall()).counter_ts, first)

    def test_a_state_file_that_cannot_be_written_is_one_error_and_the_statistics_stay_in_memory(self):
        s = Stack(start="2026-11-18 06:00", save_interval=60)
        blocker = os.path.join(s.tmp, "blocker")
        open(blocker, "w").close()
        s.controller.settings.state_file = os.path.join(blocker, "state.json")
        s.client.counter = 1.0
        s.start()
        s.step(60, counter=1.2)
        s.step(300, counter=1.4)
        self.assertEqual(len([t for t in s.errors() if "Cannot write the state file" in t]), 1)
        self.assertEqual(s.value(RAIN_24H), "1.4")
        os.remove(blocker)
        s.step(300, counter=1.5)
        self.assertTrue(any("can be written again" in t for t in dz.messages("log")))
        self.assertTrue(os.path.isfile(s.controller.settings.state_file))

    def test_a_corrupt_state_file_is_moved_aside_and_the_plugin_starts_empty(self):
        s = Stack(start="2026-11-18 06:00")
        with open(s.state_file, "w") as fh:
            fh.write("{not json")
        s.client.counter = 1.0
        s.start()
        self.assertEqual(len(s.errors()), 1)
        self.assertIn("moved to", s.errors()[0])
        leftovers = [n for n in os.listdir(s.tmp) if ".corrupt-" in n]
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(s.value(RAIN_24H), "1.0")                  # and it works

    def test_a_state_file_of_a_newer_version_is_never_overwritten(self):
        s = Stack(start="2026-11-18 06:00")
        with open(s.state_file, "w") as fh:
            json.dump({"v": 99, "archive": {}}, fh)
        s.start()
        self.assertIn("newer version", s.errors()[0])
        self.assertTrue(any(".corrupt-" in n for n in os.listdir(s.tmp)))

    def test_a_poll_that_does_not_end_is_abandoned_when_the_plugin_stops(self):
        class Hanging:
            @staticmethod
            def join(timeout=None):
                return None

            @staticmethod
            def is_alive():
                return True

        s = Stack()
        s.controller.spawn = lambda target: Hanging()
        s.controller.start()
        s.controller.stop()
        self.assertTrue(any("did not end within" in t for t in s.errors()))

    def test_a_result_that_arrives_after_stopping_changes_nothing(self):
        s = Stack(start="2026-11-18 06:00")
        s.client.counter = 2.0
        s.controller.start()
        s.controller.stop()
        s.controller.on_heartbeat()
        self.assertEqual(s.value(RAIN_24H), "0.0")


class LegacyImport(unittest.TestCase):
    def legacy(self, s):
        path = os.path.join(s.tmp, "rain_stats_data.json")
        return write_legacy(path, last_save="2026-11-18 05:59", last_value=("2026-11-18 05:59", 2.0),
                            values=[("2026-11-18 03:00", 1.5), ("2026-11-18 05:59", 0.5),
                                    ("2026-11-17 12:00", 4.0)],
                            maxima={"2026-11-10": 6.0, "2026-11-17": 4.0, "2026-11-18": 2.0})

    def test_the_script_s_data_file_is_imported_once_at_the_first_start(self):
        s = Stack(start="2026-11-18 06:00", level=1)
        s.settings.legacy_file = self.legacy(s)
        s.client.counter = 2.0
        s.start()
        self.assertTrue(any("Imported the data file of RainStats V2.6" in t for t in dz.messages("log")))
        self.assertEqual(s.value(RAIN_24H), "6.0")           # 4.0 yesterday noon + 1.5 + 0.5 today
        self.assertEqual(s.value(RAIN_365D), "12.0")         # plus the 6.0 mm day outside the complete part
        self.assertTrue(os.path.isfile(s.state_file))
        self.assertEqual(s.errors(), [])
        s.controller.stop()
        again = Stack(start="2026-11-18 06:10", state_file=s.state_file, level=1)
        again.settings.legacy_file = s.settings.legacy_file
        again.client.counter = 2.0
        again.start()
        self.assertTrue(any("is not imported again" in t for t in dz.messages("debug")))
        self.assertEqual(again.value(RAIN_365D), "12.0")

    def test_the_counter_baseline_of_the_script_is_taken_over(self):
        s = Stack(start="2026-11-18 06:00")
        s.settings.legacy_file = self.legacy(s)
        s.client.counter = 2.6                               # 0.6 mm fell between the script's last run and now
        s.start()
        self.assertEqual(s.value(RAIN_24H), "6.6")

    def test_a_missing_data_file_is_one_error_and_an_empty_start(self):
        s = Stack(start="2026-11-18 06:00")
        s.settings.legacy_file = os.path.join(s.tmp, "nope.json")
        s.client.counter = 1.0
        s.start()
        self.assertEqual(len(s.errors()), 1)
        self.assertIn("Cannot import the data file of RainStats V2.6", s.errors()[0])
        self.assertEqual(s.value(RAIN_24H), "1.0")


class Commands(unittest.TestCase):
    def test_a_command_is_logged_explained_and_the_real_value_written_back(self):
        s = Stack().start()
        s.controller.on_command(UNIT_STATUS, "Off")
        self.assertTrue(any("Command received: unit 17" in t for t in dz.messages("log")))
        self.assertTrue(any("cannot be switched" in t for t in dz.messages("log")))
        self.assertEqual(s.value(UNIT_STATUS), "On")
        s.controller.on_command(RAIN_24H, "Set Level", 30)
        self.assertTrue(any("only shows what the rain data say" in t for t in dz.messages("log")))

    def test_a_command_for_a_unit_that_is_not_ours_is_ignored(self):
        s = Stack().start()
        s.controller.on_command(99, "On")
        self.assertTrue(any("is not a Rain Statistics device" in t for t in dz.messages("log")))
        self.assertEqual(s.errors(), [])


class HistoryImport(unittest.TestCase):
    """RS_HISTORY_IDX: the daily rain history of a Domoticz device is read ONCE and completes the archive."""
    TODAY = local("2026-11-18 00:00")

    def stack(self, days=300, **kw):
        s = Stack(start="2026-11-18 06:00", level=2, history_idx=1161, **kw)
        s.client.history = history_rows(self.TODAY, days, 1.0)
        return s

    def logs(self, kind="log"):
        return dz.messages(kind)

    def test_it_is_read_once_after_the_first_reading_and_the_long_windows_are_complete(self):
        s = self.stack()
        s.client.counter = 3.0
        s.start()
        self.assertEqual(s.client.history_reads, 1)
        # the 3.0 mm that the counter had already counted today (the plugin's own data) and the days of the history
        self.assertEqual(s.value(RAIN_24H), "3.8")                  # 3.0 + the last 18 of yesterday's 24 hours (0.75)
        self.assertEqual(s.value(RAIN_365D), "303.0")               # 3.0 + 300 days of 1 mm: all of them in the year
        self.assertTrue(any("Imported the daily rain history of IDX 1161: 300 days" in t for t in self.logs()))
        self.assertEqual(s.errors(), [])
        info = s.value(UNIT_INFO)
        self.assertIn("history from 2026-01-22", info)
        self.assertIn("windows from 365d on are partial", info)      # 300 days of history: the year is not complete
        self.assertIn("peaks from 1d on are partial", info)          # the history has no rain rates

    def test_the_state_file_says_it_was_read_and_it_is_written_at_once(self):
        s = self.stack()
        s.start()
        data = helpers.load_json(s.state_file)
        self.assertEqual(data["history"]["idx"], 1161)
        self.assertEqual(data["history"]["days"], 300)

    def test_it_is_never_read_again_while_it_runs(self):
        s = self.stack()
        s.start()
        for _ in range(5):
            s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 1)

    def test_it_is_never_read_again_after_a_restart_as_long_as_the_state_file_holds_it(self):
        s = self.stack()
        s.start()
        s.controller.stop()
        again = self.stack(state_file=s.state_file)
        again.wall.advance(600)
        again.start()
        self.assertEqual(again.client.history_reads, 0)
        self.assertTrue(any("is in the state file: it is not read again" in t for t in self.logs("debug")))
        self.assertEqual(again.value(RAIN_365D), s.value(RAIN_365D))

    def test_a_lost_state_file_brings_the_import_back_and_it_does_not_count_twice(self):
        s = self.stack()
        s.start()
        s.controller.stop()
        os.remove(s.state_file)
        again = self.stack(state_file=s.state_file)
        again.start()
        self.assertEqual(again.client.history_reads, 1)
        self.assertEqual(again.value(RAIN_365D), s.value(RAIN_365D))

    def test_without_the_setting_the_history_is_never_asked_for(self):
        s = Stack(start="2026-11-18 06:00")
        s.client.history = history_rows(self.TODAY, 30)
        s.start()
        s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 0)
        self.assertEqual(s.value(RAIN_365D), "0.0")

    def test_a_network_problem_is_tried_again_at_the_next_reading_and_said_once(self):
        s = self.stack()
        s.client.history_error = fail("no answer within 10 s", "network")
        s.start()
        s.step(60, counter=0.0)
        s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 3)
        self.assertEqual(len([e for e in s.errors() if "Reading the rain history failed" in e]), 1)
        self.assertIn("It is tried again at the next reading", s.errors()[0])
        s.client.history_error = None
        s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 4)
        self.assertTrue(any("The rain history could be read" in t for t in self.logs()))
        self.assertTrue(any("Imported the daily rain history" in t for t in self.logs()))
        s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 4)                 # and now never again

    def test_a_refusal_is_said_once_and_not_asked_again_in_this_run(self):
        s = self.stack()
        s.client.history_error = fail("Is 1161 the IDX of a device of this Domoticz?", "missing")
        s.start()
        for _ in range(3):
            s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 1)
        self.assertEqual(len([e for e in s.errors() if "Reading the rain history failed" in e]), 1)
        self.assertNotIn("tried again", s.errors()[0])
        self.assertEqual(s.value(UNIT_STATUS), "On")                # the rain device itself is fine
        self.assertEqual(s.value(RAIN_365D), "0.0")
        self.assertIsNone(store.load(s.state_file, s.wall()).history)
        # the next start tries again (the refusal may have been a wrong IDX that was corrected)
        s.controller.stop()
        again = self.stack(state_file=s.state_file)
        again.wall.advance(600)
        again.start()
        self.assertEqual(again.client.history_reads, 1)
        self.assertEqual(again.value(RAIN_365D), "300.0")           # 300 days of 1 mm; nothing fell today

    def test_a_young_device_without_finished_days_is_not_marked_and_not_asked_again_in_this_run(self):
        s = self.stack(days=0)
        s.start()
        s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 1)
        self.assertIsNone(store.load(s.state_file, s.wall()).history)
        self.assertTrue(any("no finished days" in t for t in self.logs()))
        self.assertEqual(s.errors(), [])

    def test_the_log_gives_the_rain_of_every_window_longer_than_one_day_after_the_import(self):
        s = Stack(start="2026-11-18 00:00", level=2, history_idx=1161)      # at midnight: the windows hold whole days
        s.client.history = history_rows(self.TODAY, 300, 1.0)
        s.start()
        lines = [t for t in self.logs() if t.startswith("The rain of the windows longer than 1 day after the import")]
        self.assertEqual(len(lines), 1)
        self.assertIn(": 2 d 2.0 mm, 7 d 7.0 mm, 14 d 14.0 mm, ", lines[0])
        self.assertIn("365 d 300.0 mm", lines[0])                           # 300 days of 1 mm: all that Domoticz holds
        shown = [part.split(" mm")[0].rsplit(" ", 1)[0] for part in lines[0].split(": ", 1)[1].split(", ")]
        self.assertEqual(shown, ["2 d", "7 d", "14 d", "30 d", "90 d", "180 d", "365 d"])    # every active one above 1 d
        self.assertEqual(s.errors(), [])

    def test_with_only_the_one_day_window_on_the_history_is_not_needed_and_not_read(self):
        from rain_stats.constants import WINDOWS
        one_day = tuple(w for w in WINDOWS if w.days == 1)
        s = Stack(start="2026-11-18 06:00", level=2, history_idx=1161, windows=one_day)
        s.client.history = history_rows(self.TODAY, 300, 1.0)
        s.start()
        s.step(60, counter=0.0)
        s.step(60, counter=0.0)
        self.assertEqual(s.client.history_reads, 0)
        say = [t for t in self.logs() if "no window longer than 1 day is switched on" in t]
        self.assertEqual(len(say), 1)                                       # said once
        self.assertIsNone(store.load(s.state_file, s.wall()).history)
        s.controller.stop()
        # a window longer than a day is switched on later: the history is read, once, then
        again = self.stack(state_file=s.state_file)
        again.wall.advance(600)
        again.start()
        self.assertEqual(again.client.history_reads, 1)
        self.assertEqual(again.value(RAIN_365D), "300.0")

    def test_a_history_that_adds_nothing_because_the_plugin_knows_every_day_is_done_and_not_asked_again(self):
        first = Stack(start="2025-10-01 06:00")                             # runs, with no history setting, and stops
        first.client.counter = 4.0
        first.start()
        first.controller.stop()
        again = Stack(start="2026-11-18 06:00", level=2, state_file=first.state_file, history_idx=1161)
        again.client.history = history_rows(self.TODAY, 300, 1.0)
        again.start()
        self.assertEqual(again.client.history_reads, 1)
        self.assertTrue(any("nothing to add" in t for t in self.logs()))
        self.assertFalse(any(t.startswith("The rain of the windows longer than 1 day") for t in self.logs()))
        mark = store.load(first.state_file, again.wall()).history
        self.assertEqual((mark.idx, mark.days), (1161, 0))
        self.assertNotIn("history from", again.value(UNIT_INFO))
        self.assertEqual(again.errors(), [])
        again.controller.stop()
        third = Stack(start="2026-11-18 07:00", level=2, state_file=first.state_file, history_idx=1161)
        third.client.history = history_rows(self.TODAY, 300, 1.0)
        third.start()
        self.assertEqual(third.client.history_reads, 0)                     # once, as asked
        self.assertTrue(any("was read before" in t for t in self.logs("debug")))

    def test_the_rain_the_plugin_collects_afterwards_adds_to_the_history(self):
        s = self.stack()
        s.start()
        self.assertEqual(s.value(RAIN_24H), "0.8")                  # the last 18 hours of yesterday: 0.75 mm
        s.step(60, counter=1.5)
        self.assertEqual(s.value(RAIN_24H), "2.2")                  # + the 1.5 mm that fell, - a minute of yesterday

    def test_a_stop_while_the_history_is_being_read_abandons_it_and_says_so(self):
        class Hanging:
            @staticmethod
            def join(timeout=None):
                return None

            @staticmethod
            def is_alive():
                return True

        s = self.stack()
        s.start()
        s.controller._history_thread = Hanging()
        s.controller.stop()
        self.assertTrue(any("Reading the rain history did not end within" in t for t in s.errors()))

    def test_a_history_that_arrives_after_stopping_changes_nothing(self):
        from rain_stats.controller import HistoryOutcome
        s = self.stack()
        s.controller.start()
        s.controller.stop()
        s.controller._results.put(HistoryOutcome(rows=history_rows(self.TODAY, 30)))
        s.controller.on_heartbeat()
        self.assertIsNone(s.controller.state.history)
        self.assertEqual(s.value(RAIN_365D), "0.0")


class Windows(unittest.TestCase):
    def test_unused_helper_clock_is_in_central_european_time(self):
        # the tests rely on local midnight in a fixed zone: this fails loudly if the zone were not applied
        self.assertEqual(local("2026-11-18 00:00"), local("2026-11-17 23:00") + 3600)
        self.assertEqual(helpers.time.strftime("%H", helpers.time.gmtime(local("2026-07-01 12:00"))), "10")


if __name__ == "__main__":
    unittest.main()
