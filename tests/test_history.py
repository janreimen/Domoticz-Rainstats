"""The one-time import of the daily rain history of Domoticz: what is taken, what is left out, what the windows say."""
import unittest

from helpers import local
from rain_stats.constants import DAY, WINDOWS
from rain_stats.history import apply_history, describe, parse_days
from rain_stats.stats import compute
from rain_stats.store import HistoryMark, State
from rain_stats.util import next_local_midnight

NOW = local("2026-11-21 09:00")
TODAY = local("2026-11-21 00:00")
SUMMER_NOW = local("2026-07-15 09:00")                  # a time of year whose last 30 days have no clock change
SUMMER = local("2026-07-15 00:00")
WIN = {w.key: w for w in WINDOWS}


def days_before(today, count, mm=1.0):
    """The rows Domoticz answers for ``count`` finished days before ``today``, oldest first, and today's own row."""
    rows, moment = [], today
    for _ in range(count):
        moment = local_day_before(moment)
        rows.append({"d": day_text(moment), "mm": "%.1f" % mm})
    rows.reverse()
    rows.append({"d": day_text(today), "mm": "2.5"})                       # today: not finished, never used
    return rows


def local_day_before(moment):
    t = moment - 12 * 3600                                                 # inside the day before, DST or not
    import time
    s = time.localtime(t)
    return time.mktime((s.tm_year, s.tm_mon, s.tm_mday, 0, 0, 0, 0, 0, -1))


def day_text(moment):
    import time
    return time.strftime("%Y-%m-%d", time.localtime(moment))


def fresh_state(since=TODAY):
    state = State()
    state.since = since
    return state


class Parsing(unittest.TestCase):
    def test_rows_become_days_oldest_first(self):
        days, skipped = parse_days([{"d": "2026-11-20", "mm": "3.2"}, {"d": "2026-11-19", "mm": "0.0"}])
        self.assertEqual(skipped, 0)
        self.assertEqual([(d.start, d.mm) for d in days], [(local("2026-11-19 00:00"), 0.0), (local("2026-11-20 00:00"), 3.2)])
        self.assertEqual(days[1].end, local("2026-11-21 00:00"))

    def test_a_day_with_a_time_in_it_is_that_day(self):
        days, _ = parse_days([{"d": "2026-11-20 00:00:00", "mm": "1.0"}])
        self.assertEqual(days[0].start, local("2026-11-20 00:00"))

    def test_rows_that_are_not_usable_are_counted_not_used(self):
        rows = [{"d": "2026-11-20", "mm": "1.0"},
                {"d": "2026-11-20", "mm": "9.0"},                         # the day twice: the first one counts
                {"d": "yesterday", "mm": "1.0"}, {"mm": "1.0"}, {"d": "2026-11-18"},
                {"d": "2026-11-17", "mm": "-1.0"}, {"d": "2026-11-16", "mm": "heavy"},
                {"d": "2026-11-15", "mm": "1000.1"}, {"d": "2026-11-14", "mm": "nan"},
                "text", None, 5]
        days, skipped = parse_days(rows)
        self.assertEqual([(d.start, d.mm) for d in days], [(local("2026-11-20 00:00"), 1.0)])
        self.assertEqual(skipped, len(rows) - 1)

    def test_a_day_of_1000_mm_is_still_believed(self):
        days, skipped = parse_days([{"d": "2026-11-20", "mm": "1000.0"}])
        self.assertEqual((len(days), skipped), (1, 0))

    def test_a_day_that_the_clock_makes_23_or_25_hours_long_is_that_long(self):
        days, _ = parse_days([{"d": "2026-03-29", "mm": "1.0"}, {"d": "2026-10-25", "mm": "1.0"}])
        self.assertEqual([(d.end - d.start) / 3600 for d in days], [23.0, 25.0])


class Applying(unittest.TestCase):
    def test_the_days_before_the_data_of_the_plugin_are_added_and_today_is_not(self):
        state = fresh_state(SUMMER)
        result = apply_history(state, 1161, days_before(SUMMER, 365, 1.0), SUMMER_NOW)
        self.assertEqual((result.days, result.before, result.skipped), (365, 1, 0))     # 1 = today
        self.assertAlmostEqual(result.mm, 365.0)
        stats = compute(state.archive, SUMMER, state.since)                             # at midnight: whole days only
        self.assertAlmostEqual(stats.rain["1d"], 1.0, places=6)
        self.assertAlmostEqual(stats.rain["7d"], 7.0, places=6)
        self.assertAlmostEqual(stats.rain["30d"], 30.0, places=6)
        self.assertAlmostEqual(stats.rain["365d"], 365.0, places=6)
        self.assertAlmostEqual(stats.rain["360d"], 360.0, places=6)

    def test_a_window_is_a_number_of_hours_and_the_clock_changes_are_in_the_days(self):
        # 30 d are 720 hours. In November the last 30 days hold the 25 hours of the day the clock went back, so the window
        # starts one hour later than 30 local midnights ago and misses that hour (1 mm in 24 hours) of the first day
        state = fresh_state()
        apply_history(state, 1161, days_before(TODAY, 365, 1.0), NOW)
        stats = compute(state.archive, TODAY, state.since)
        self.assertAlmostEqual(stats.rain["30d"], 30.0 - 1 / 24.0, places=6)
        self.assertAlmostEqual(stats.rain["365d"], 365.0, places=6)                      # a year: both changes cancel

    def test_an_even_rain_gives_the_same_value_at_any_hour_of_the_day(self):
        # The history holds the days before today only. At the hour h of today the 30 d window reaches back to the hour
        # h of 30 days ago, so it holds 30 days minus h hours of the history: 2 mm a day, evenly spread, makes
        # 2 * (30 - h / 24) mm at every moment, whatever the slot the moment falls in
        state = fresh_state(SUMMER)
        apply_history(state, 1161, days_before(SUMMER, 40, 2.0), SUMMER_NOW)
        for seconds in (0, 3600, 7 * 3600 + 20 * 60 + 7, 9 * 3600, 86399):
            stats = compute(state.archive, SUMMER + seconds, state.since)
            self.assertAlmostEqual(stats.rain["30d"], 2.0 * (30 - seconds / 86400.0), places=6, msg=str(seconds))

    def test_the_days_the_plugin_collected_itself_are_never_added_again(self):
        since = local("2026-11-19 00:00")                               # collecting for two days (a daily counter)
        state = fresh_state(since)
        state.archive.add_spread(since, NOW, 6.0)                       # what the plugin has seen since then
        result = apply_history(state, 1161, days_before(TODAY, 365, 1.0), NOW)
        self.assertEqual((result.days, result.before), (363, 3))        # 2026-11-19 and 20 and today are left out
        self.assertEqual(result.last, since)
        self.assertEqual(result.first, local("2025-11-21 00:00"))
        stats = compute(state.archive, NOW, since)
        # the 6 mm the plugin saw, and the 363 days of the history minus the 9 hours of the first one that the year
        # window of 09:00 does not reach
        self.assertAlmostEqual(stats.rain["365d"], 6.0 + 363.0 - 9 / 24.0, places=6)

    def test_a_total_counter_that_started_at_noon_leaves_the_day_it_started_to_the_plugin(self):
        since = local("2026-11-19 12:00")
        state = fresh_state(since)
        result = apply_history(state, 1161, days_before(TODAY, 10, 1.0), NOW)
        self.assertEqual(result.last, local("2026-11-19 00:00"))        # days that end by the midnight before 12:00
        self.assertEqual(result.days, 8)

    def test_the_marker_says_what_was_read(self):
        state = fresh_state()
        apply_history(state, 1161, days_before(TODAY, 30, 1.0), NOW)
        self.assertEqual(state.history, HistoryMark(1161, local("2026-10-22 00:00"), TODAY, 30, NOW))

    def test_a_history_without_a_usable_day_leaves_no_marker(self):
        state = fresh_state()
        result = apply_history(state, 1161, [{"d": day_text(TODAY), "mm": "0.0"}], NOW)
        self.assertEqual((result.days, result.first, result.last), (0, None, None))
        self.assertIsNone(state.history)                                # a young sensor: ask again at the next start
        self.assertIn("no finished days", describe(result, 1161))
        self.assertIn("asked again at the next start", describe(result, 1161))

    def test_a_history_the_plugin_knows_completely_is_done_and_marked_without_adding_anything(self):
        # the plugin ran for more than a year (or an imported file covers the year): every finished day of Domoticz is
        # known already. Nothing is added, and the history is marked as done, so that it is not asked for again
        since = TODAY - 400 * 86400
        state = fresh_state(since)
        state.archive.add_spread(since, NOW, 40.0)
        before = compute(state.archive, NOW, since).rain["365d"]
        result = apply_history(state, 1161, days_before(TODAY, 365, 1.0), NOW)
        self.assertEqual((result.days, result.mm, result.first, result.last), (0, 0.0, None, None))
        self.assertEqual((result.known, result.before), (365, 366))                 # 365 finished days and today
        self.assertEqual(state.history, HistoryMark(1161, since, since, 0, NOW))
        self.assertIsNone(state.history.from_day)                                   # it reaches back no further
        self.assertEqual(compute(state.archive, NOW, since, WINDOWS, state.history.from_day).rain["365d"], before)
        text = describe(result, 1161)
        self.assertIn("365 finished day(s)", text)
        self.assertIn("nothing to add", text)
        self.assertIn("not read again", text)

    def test_some_days_added_and_some_known_gives_a_marker_of_the_added_ones(self):
        since = local("2026-11-19 00:00")
        state = fresh_state(since)
        result = apply_history(state, 1161, days_before(TODAY, 10, 1.0), NOW)
        self.assertEqual((result.days, result.known, result.before), (8, 2, 3))     # 11-19 and 11-20 are known, today is not over
        self.assertEqual(state.history.days, 8)
        self.assertEqual(state.history.from_day, state.history.first)

    def test_an_answer_with_no_finished_day_at_all_is_never_marked_as_done(self):
        # a new device or a wrong IDX: marking it would need a deleted state file to try again
        state = fresh_state()
        result = apply_history(state, 1161, [{"d": day_text(TODAY), "mm": "0.0"}], NOW)
        self.assertEqual((result.days, result.known), (0, 0))
        self.assertIsNone(state.history)
        state = fresh_state()
        result = apply_history(state, 1161, [], NOW)
        self.assertEqual((result.days, result.known, result.before), (0, 0, 0))
        self.assertIsNone(state.history)

    def test_without_a_first_reading_nothing_can_be_added(self):
        with self.assertRaises(ValueError):
            apply_history(State(), 1161, days_before(TODAY, 3), NOW)

    def test_history_older_than_the_ring_remembers_is_dropped_without_a_crash(self):
        state = fresh_state()
        result = apply_history(state, 1161, days_before(TODAY, 800, 1.0), NOW)         # more than the ring holds
        self.assertEqual(result.days, 800)
        stats = compute(state.archive, TODAY, state.since)
        self.assertAlmostEqual(stats.rain["365d"], 365.0, places=6)
        self.assertAlmostEqual(stats.rain["360d"], 360.0, places=6)

    def test_dst_days_keep_their_rain(self):
        state = fresh_state(local("2026-11-01 00:00"))
        rows = [{"d": "2026-03-29", "mm": "2.3"}, {"d": "2026-10-25", "mm": "5.0"}]
        apply_history(state, 1161, rows, local("2026-11-01 08:00"))
        stats = compute(state.archive, local("2026-11-01 00:00"), state.since)
        self.assertAlmostEqual(stats.rain["365d"], 7.3, places=6)


class WhatTheWindowsSay(unittest.TestCase):
    def test_the_rain_windows_are_complete_after_the_history_the_peaks_are_not(self):
        state = fresh_state(TODAY)
        apply_history(state, 1161, days_before(TODAY, 366, 1.0), NOW)
        mark = state.history
        stats = compute(state.archive, NOW, state.since, WINDOWS, mark.first)
        self.assertEqual(stats.partial, ())                              # the rain is known for the whole year
        self.assertEqual(stats.peak_partial, tuple(w.key for w in WINDOWS))     # the plugin ran for 9 hours
        self.assertEqual(stats.rain_from, mark.first)
        self.assertEqual(stats.since, TODAY)

    def test_a_short_history_completes_only_what_it_reaches(self):
        state = fresh_state(TODAY)
        apply_history(state, 1161, days_before(TODAY, 20, 1.0), NOW)
        stats = compute(state.archive, NOW, state.since, WINDOWS, state.history.first)
        self.assertEqual(stats.partial, tuple(w.key for w in WINDOWS if w.days > 20))

    def test_without_a_history_nothing_changes(self):
        stats = compute(State().archive, NOW, TODAY)
        self.assertEqual(stats.partial, stats.peak_partial)
        self.assertEqual(stats.rain_from, TODAY)

    def test_the_end_of_the_last_day_is_the_next_midnight(self):
        state = fresh_state(TODAY)
        result = apply_history(state, 1161, days_before(TODAY, 3, 1.0), NOW)
        self.assertEqual(result.last, next_local_midnight(TODAY - 1))
        self.assertEqual(DAY, 86400)


if __name__ == "__main__":
    unittest.main()
