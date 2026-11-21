"""From successive readings of the rain counter to the rain that fell."""
import unittest

from helpers import local
from rain_stats.constants import COUNTER_DAILY, COUNTER_TOTAL
from rain_stats.counter import Counter
from rain_stats.util import local_midnight


def daily(max_delta=100.0):
    return Counter(COUNTER_DAILY, max_delta)


def total(max_delta=100.0):
    return Counter(COUNTER_TOTAL, max_delta)


class FirstReading(unittest.TestCase):
    def test_a_daily_counter_credits_the_rain_of_today_from_midnight(self):
        c = daily()
        change = c.update(local("2026-11-18 14:00"), 3.2)
        self.assertEqual((change.mm, change.kind), (3.2, "first"))
        self.assertEqual(change.since, local("2026-11-18 00:00"))
        self.assertTrue(c.known)

    def test_a_dry_day_credits_nothing(self):
        change = daily().update(local("2026-11-18 14:00"), 0.0)
        self.assertEqual((change.mm, change.kind), (0.0, "first"))

    def test_a_total_counter_says_nothing_about_the_past(self):
        change = total().update(local("2026-11-18 14:00"), 1234.5)
        self.assertEqual((change.mm, change.kind), (0.0, "first"))

    def test_the_first_reading_is_the_baseline_for_the_next(self):
        c = total()
        c.update(local("2026-11-18 14:00"), 1234.5)
        change = c.update(local("2026-11-18 14:10"), 1235.0)
        self.assertAlmostEqual(change.mm, 0.5)

    def test_an_unknown_counter_is_not_known(self):
        self.assertFalse(daily().known)

    def test_a_baseline_from_the_state_file_is_used(self):
        c = daily()
        c.restore(local("2026-11-18 12:00"), 4.0)
        change = c.update(local("2026-11-18 12:30"), 4.6)
        self.assertEqual(change.kind, "delta")
        self.assertAlmostEqual(change.mm, 0.6)
        self.assertEqual(change.since, local("2026-11-18 12:00"))


class Increases(unittest.TestCase):
    def test_the_difference_is_the_rain_since_the_previous_reading(self):
        c = daily()
        c.update(local("2026-11-18 14:00"), 3.2)
        change = c.update(local("2026-11-18 14:10"), 3.8)
        self.assertEqual(change.kind, "delta")
        self.assertAlmostEqual(change.mm, 0.6)
        self.assertEqual(change.since, local("2026-11-18 14:00"))

    def test_an_unchanged_counter_is_no_rain(self):
        c = daily()
        c.update(local("2026-11-18 14:00"), 3.2)
        change = c.update(local("2026-11-18 14:10"), 3.2)
        self.assertEqual((change.mm, change.kind), (0.0, "none"))

    def test_float_noise_is_no_glitch(self):
        c = daily()
        c.update(local("2026-11-18 14:00"), 3.3)
        change = c.update(local("2026-11-18 14:10"), 3.3 - 1e-12)
        self.assertEqual((change.mm, change.kind), (0.0, "none"))

    def test_a_long_gap_credits_the_whole_difference_over_the_whole_gap(self):
        c = total()
        c.update(local("2026-11-10 14:00"), 100.0)
        change = c.update(local("2026-11-13 14:00"), 112.0)          # the plugin was not running for three days
        self.assertAlmostEqual(change.mm, 12.0)
        self.assertEqual(change.since, local("2026-11-10 14:00"))


class DailyResets(unittest.TestCase):
    def test_a_reset_at_midnight_makes_the_new_value_the_rain_since_midnight(self):
        c = daily()
        c.update(local("2026-11-17 23:55"), 5.0)
        change = c.update(local("2026-11-18 00:05"), 0.4)
        self.assertEqual(change.kind, "reset")
        self.assertAlmostEqual(change.mm, 0.4)
        self.assertEqual(change.since, local("2026-11-18 00:00"))

    def test_a_reset_to_zero_credits_nothing_and_resets_the_baseline(self):
        c = daily()
        c.update(local("2026-11-17 23:55"), 5.0)
        self.assertEqual(c.update(local("2026-11-18 00:05"), 0.0).mm, 0.0)
        self.assertAlmostEqual(c.update(local("2026-11-18 00:15"), 0.3).mm, 0.3)       # not 0.3 - 5.0

    def test_a_reset_a_little_late_is_still_a_reset(self):
        c = daily()
        c.update(local("2026-11-18 00:10"), 5.0)                    # the sensor still shows yesterday's total
        change = c.update(local("2026-11-18 00:40"), 0.2)
        self.assertEqual(change.kind, "reset")
        self.assertAlmostEqual(change.mm, 0.2)
        self.assertEqual(change.since, local("2026-11-18 00:10"))

    def test_a_reset_that_comes_much_later_is_taken_for_a_glitch_and_never_credits_yesterdays_total(self):
        c = daily()
        c.update(local("2026-11-18 01:00"), 5.0)
        change = c.update(local("2026-11-18 01:30"), 0.2)
        self.assertEqual((change.mm, change.kind), (0.0, "glitch"))
        self.assertIn("middle of the day", change.note)
        self.assertAlmostEqual(c.update(local("2026-11-18 01:40"), 0.5).mm, 0.3)    # the new value is the baseline

    def test_a_drop_in_the_middle_of_the_day_is_a_glitch(self):
        c = daily()
        c.update(local("2026-11-18 12:00"), 5.0)
        change = c.update(local("2026-11-18 12:10"), 4.0)
        self.assertEqual((change.mm, change.kind), (0.0, "glitch"))

    def test_after_a_day_without_readings_a_lower_value_is_a_reset(self):
        c = daily()
        c.update(local("2026-11-16 12:00"), 5.0)
        change = c.update(local("2026-11-18 12:00"), 1.5)           # a different day than the last reading
        self.assertEqual(change.kind, "reset")
        self.assertAlmostEqual(change.mm, 1.5)
        self.assertEqual(change.since, local("2026-11-18 00:00"))


class TotalCounter(unittest.TestCase):
    def test_a_total_counter_that_goes_down_was_restarted(self):
        c = total()
        c.update(local("2026-11-18 12:00"), 1234.5)
        change = c.update(local("2026-11-18 12:10"), 3.0)
        self.assertEqual((change.mm, change.kind), (0.0, "restart"))
        self.assertAlmostEqual(c.update(local("2026-11-18 12:20"), 3.4).mm, 0.4)

    def test_a_total_counter_is_not_reset_at_midnight(self):
        c = total()
        c.update(local("2026-11-17 23:55"), 1234.5)
        change = c.update(local("2026-11-18 00:05"), 1234.9)
        self.assertEqual(change.kind, "delta")
        self.assertAlmostEqual(change.mm, 0.4)


class Sanity(unittest.TestCase):
    def test_more_rain_than_the_limit_is_taken_for_a_sensor_error(self):
        c = total(max_delta=100.0)
        c.update(local("2026-11-18 12:00"), 50.0)
        change = c.update(local("2026-11-18 12:10"), 250.0)
        self.assertEqual((change.mm, change.kind), (0.0, "implausible"))
        self.assertIn("RS_MAX_DELTA", change.note)
        self.assertAlmostEqual(c.update(local("2026-11-18 12:20"), 250.5).mm, 0.5)    # the new value is the baseline

    def test_the_limit_applies_to_the_first_reading_of_a_daily_counter_and_to_a_reset(self):
        self.assertEqual(daily(max_delta=10.0).update(local("2026-11-18 14:00"), 50.0).kind, "implausible")
        c = daily(max_delta=10.0)
        c.update(local("2026-11-17 23:55"), 5.0)
        self.assertEqual(c.update(local("2026-11-18 00:05"), 50.0).kind, "implausible")

    def test_exactly_the_limit_is_still_rain(self):
        c = total(max_delta=10.0)
        c.update(local("2026-11-18 12:00"), 0.0)
        self.assertEqual(c.update(local("2026-11-18 12:10"), 10.0).kind, "delta")

    def test_garbage_is_ignored_and_keeps_the_baseline(self):
        c = daily()
        c.update(local("2026-11-18 12:00"), 5.0)
        for junk in (float("nan"), float("inf"), -1.0):
            change = c.update(local("2026-11-18 12:05"), junk)
            self.assertEqual((change.mm, change.kind), (0.0, "glitch"), junk)
        self.assertAlmostEqual(c.update(local("2026-11-18 12:10"), 5.5).mm, 0.5)

    def test_a_reading_that_is_not_newer_is_ignored_and_keeps_the_baseline(self):
        c = daily()
        c.update(local("2026-11-18 12:00"), 5.0)
        for when in ("2026-11-18 12:00", "2026-11-18 11:50"):
            change = c.update(local(when), 9.0)
            self.assertEqual((change.mm, change.kind), (0.0, "clock"), when)
        self.assertAlmostEqual(c.update(local("2026-11-18 12:10"), 5.5).mm, 0.5)


class DaylightSaving(unittest.TestCase):
    """The clocks go back on 2026-10-25 (03:00 CEST becomes 02:00 CET): that day is 25 hours long."""

    def test_the_day_is_25_hours_long(self):
        start = local("2026-10-25 00:00")
        self.assertEqual(local_midnight(start + 12 * 3600), start)
        self.assertEqual(local("2026-10-26 00:00") - start, 25 * 3600)

    def test_a_reset_over_the_longer_night_is_a_reset(self):
        c = daily()
        c.update(local("2026-10-24 23:55"), 6.0)
        change = c.update(local("2026-10-25 00:05"), 0.2)
        self.assertEqual(change.kind, "reset")
        self.assertEqual(change.since, local("2026-10-25 00:00"))

    def test_readings_either_side_of_the_repeated_hour_are_one_day(self):
        c = daily()
        t = local("2026-10-25 00:30") + 2 * 3600 + 20 * 60                # 02:50 CEST, the first time it is 02:50
        c.update(t, 3.0)
        change = c.update(t + 3600, 3.4)                                   # 02:50 CET, an hour later
        self.assertEqual(change.kind, "delta")
        self.assertAlmostEqual(change.mm, 0.4)

    def test_a_reset_after_the_shorter_night_is_a_reset(self):
        c = daily()
        c.update(local("2026-03-28 23:55"), 6.0)
        change = c.update(local("2026-03-29 00:05"), 0.2)                  # 2026-03-29 is 23 hours long
        self.assertEqual(change.kind, "reset")
        self.assertEqual(local("2026-03-30 00:00") - local("2026-03-29 00:00"), 23 * 3600)


if __name__ == "__main__":
    unittest.main()
