"""Over a year of simulated weather against the exact truth.

A tipping-bucket gauge (0.1 mm per tip) is simulated for 383 days in Central European time, so both clock changes are
in it. The gauge counts "rain today": its counter starts again at every local midnight, like the ``Rain`` value of a
Domoticz rain device. The real counter logic and the real controller code (``Controller._apply``) read it, and every window
is compared with what really fell in the same slots.

What is known and allowed to differ from the truth:

    * the rain between the last reading before a midnight and the midnight itself is not visible to a daily counter
      (the counter is already reset at the next reading). The truth leaves it out the same way
    * a reading interval that crosses the edge of a slot spreads its rain evenly over the slot edge. With readings on whole
      minutes that never happens (slot edges are whole minutes), so those runs must match exactly; with irregular
      readings the difference is bounded by the rain of one reading interval
"""
import bisect
import random
import unittest

from helpers import Stack, local
from rain_stats.archive import Archive
from rain_stats.constants import DAY, WINDOWS, tier_of, window_slots
from rain_stats.model import Reading
from rain_stats.util import local_midnight, next_local_midnight

TIP = 0.1
INTENSITIES = (0.3, 0.6, 1.0, 2.0, 5.0, 12.0, 30.0)    # mm/h


class Weather:
    """Rain events, tips and the readings of a daily-reset counter."""

    def __init__(self, start, days, seed, midnight_rain=False):
        rnd = random.Random(seed)
        self.start = start
        self.end = start
        for _ in range(days):
            self.end = next_local_midnight(self.end)
        self.events = []                                  # (start, end, intensity), sorted, not overlapping
        self.tips = []
        if midnight_rain:                                 # it rains for 40 minutes around every midnight, nothing else
            m = next_local_midnight(start)
            while m + 1200 < self.end:
                self._event(m - 1200, 2400, 6.0)
                m = next_local_midnight(m)
            self._finish()
            return
        cursor, day = start, start
        while day < self.end:
            following = next_local_midnight(day)
            if rnd.random() < 0.3:
                for _ in range(rnd.randint(1, 2)):
                    s = max(cursor, day + rnd.uniform(0, following - day))
                    intensity = rnd.choice(INTENSITIES)
                    duration = max(rnd.uniform(300, 2 * 3600), TIP / intensity * 3600 * 1.5)
                    if s + duration >= self.end:
                        continue
                    self._event(s, duration, intensity)
                    cursor = s + duration + 1
            day = following
        self._finish()

    def _event(self, start, duration, intensity):
        self.events.append((start, start + duration, intensity))
        n = int(duration / 3600 * intensity / TIP)
        self.tips.extend(start + (k + 0.5) * duration / n for k in range(n))

    def _finish(self):
        self.tips.sort()
        self.event_starts = [e[0] for e in self.events]

    def counter(self, moment):
        """What the gauge shows at ``moment``: the rain since local midnight."""
        midnight = local_midnight(moment)
        n = bisect.bisect_right(self.tips, moment) - bisect.bisect_left(self.tips, midnight)
        return round(n * TIP, 1)

    def rate(self, moment):
        i = bisect.bisect_right(self.event_starts, moment) - 1
        if i >= 0 and self.events[i][0] <= moment < self.events[i][1]:
            return self.events[i][2]
        return 0.0

    def observable(self, polls):
        """The tips a daily counter lets us see: those before the last reading ahead of a midnight, and after it."""
        out = []
        midnights = []
        m = next_local_midnight(self.start)
        while m <= self.end:
            midnights.append(m)
            m = next_local_midnight(m)
        blind = []
        for m in midnights:
            i = bisect.bisect_left(polls, m) - 1               # the last poll before the midnight
            blind.append((polls[i], m))
        blind_starts = [b[0] for b in blind]
        for t in self.tips:
            i = bisect.bisect_right(blind_starts, t) - 1
            if i >= 0 and t < blind[i][1] and t > blind[i][0]:
                continue
            out.append(t)
        return out


def window_start(window, now):
    tier = tier_of(window.tier)
    return (int(now // tier.resolution) - (window_slots(window) - 1)) * tier.resolution


class Run:
    """Feed a Weather to the real controller code at the given poll times. At the checkpoints the windows are read
    straight away: a round-robin archive only answers for the present, older slots are overwritten by design."""

    def __init__(self, weather, polls, checkpoints=()):
        self.weather, self.polls = weather, polls
        self.stack = Stack(start="2026-01-01 00:00")
        self.controller = self.stack.controller
        self.archive = self.controller.state.archive
        self.rates = []
        self.seen = {}                                    # checkpoint -> {window key: (rain, peak)}
        wanted = set(checkpoints)
        for p in polls:
            rate = weather.rate(p)
            self.rates.append(rate)
            self.controller._apply(Reading(p, weather.counter(p), rate, False, ""))
            if p in wanted:
                self.seen[p] = {w.key: self.archive.window(w, p) for w in WINDOWS}
        self.tips = weather.observable(polls)

    def true_rain(self, window, now):
        """What the plugin is meant to show: the rain of the whole slots of the window, plus the part of the slot before
        them that lies inside the period, in proportion (rain taken as evenly spread within a slot)."""
        res = tier_of(window.tier).resolution
        lo = window_start(window, now)
        whole = bisect.bisect_right(self.tips, now) - bisect.bisect_left(self.tips, lo)
        inside = 1.0 - (now - (now // res) * res) / res
        return TIP * (whole + inside * self.edge_tips(window, now))

    def edge_tips(self, window, now):
        """The tips in the slot just before the window."""
        lo = window_start(window, now)
        return bisect.bisect_left(self.tips, lo) - bisect.bisect_left(self.tips, lo - tier_of(window.tier).resolution)

    def exact_rain(self, window, now):
        """The rain of exactly the last ``window.seconds`` seconds."""
        return TIP * (bisect.bisect_right(self.tips, now) - bisect.bisect_right(self.tips, now - window.seconds))

    def true_peak(self, window, now):
        lo = window_start(window, now)
        i0, i1 = bisect.bisect_left(self.polls, lo), bisect.bisect_right(self.polls, now)
        return max(self.rates[i0:i1], default=0.0)

    def max_interval_rain(self):
        best, previous = 0.0, self.polls[0]
        for p in self.polls[1:]:
            best = max(best, TIP * (bisect.bisect_right(self.tips, p) - bisect.bisect_right(self.tips, previous)))
            previous = p
        return best

    def worst_rain_error(self):
        """The largest difference between a window and the truth over all checkpoints; asserts the peaks on the way."""
        worst = 0.0
        for now, windows in self.seen.items():
            for window in WINDOWS:
                rain, peak = windows[window.key]
                worst = max(worst, abs(rain - self.true_rain(window, now)))
                if peak != self.true_peak(window, now):
                    raise AssertionError("peak %s at %s: %s, truth %s" % (window.key, now, peak, self.true_peak(window, now)))
        return worst


def whole_minutes(start, end, step):
    return [start + k * step for k in range(int((end - start) // step) + 1)]


class YearOfWeather(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start = local("2025-11-17 00:00")
        cls.weather = Weather(cls.start, 383, seed=2026)
        cls.polls = whole_minutes(cls.start, cls.weather.end, 300)
        rnd = random.Random(7)
        late = [p for p in cls.polls if p >= cls.start + 366 * DAY]       # every window is full by then
        early = [p for p in cls.polls if p < cls.start + 20 * DAY]        # and now the very first days
        cls.checkpoints = [cls.polls[-1]] + rnd.sample(late, 60) + rnd.sample(early, 20)
        cls.sim = Run(cls.weather, cls.polls, cls.checkpoints)

    def test_the_weather_is_a_real_test(self):
        total = TIP * len(self.weather.tips)
        self.assertGreater(total, 100)                                  # a year with something to count
        self.assertLess(total, 4000)
        self.assertGreater(len(self.weather.events), 100)
        # DST: the year contains a 23 h and a 25 h day
        lengths = set()
        day = self.start
        while day < self.weather.end:
            following = next_local_midnight(day)
            lengths.add(round((following - day) / 3600))
            day = following
        self.assertEqual(lengths, {23, 24, 25})

    def test_every_window_matches_the_truth_at_five_minute_readings(self):
        self.assertEqual(len(self.sim.seen), len(set(self.checkpoints)))
        self.assertLess(self.sim.worst_rain_error(), 1e-6)              # also asserts every peak, exactly

    def test_a_window_is_close_to_the_exact_last_n_hours_and_unbiased(self):
        # The comparison above is with the model: the whole slots and the edge in proportion. Against the exact "last N
        # hours" the window is wrong only by what the assumption "even within the slot" gets wrong: never by more than the
        # rain of the one slot at the edge, and, on average, by nothing
        errors = {w.key: [] for w in WINDOWS}
        exacts = {w.key: [] for w in WINDOWS}
        for now, windows in self.sim.seen.items():
            for window in WINDOWS:
                if now - self.start < window.seconds:
                    continue                                          # the plugin has not collected that long yet
                rain, exact = windows[window.key][0], self.sim.exact_rain(window, now)
                slot_rain = TIP * self.sim.edge_tips(window, now)
                self.assertLessEqual(abs(rain - exact), slot_rain + 1e-6, "%s at %s" % (window.key, now))
                errors[window.key].append(rain - exact)
                exacts[window.key].append(exact)
        for window in WINDOWS:
            n = len(errors[window.key])
            self.assertGreater(n, 60, window.key)
            bias, mean_exact = sum(errors[window.key]) / n, sum(exacts[window.key]) / n
            self.assertLess(abs(bias), 0.01 * mean_exact + 0.01, "%s: bias %.4f mm of %.2f mm" % (window.key, bias, mean_exact))

    def test_the_last_reading_of_the_year(self):
        now = self.polls[-1]
        rain, peak = self.sim.seen[now]["365d"]
        self.assertGreater(rain, 50)
        self.assertGreater(peak, 0)
        self.assertAlmostEqual(rain, self.sim.true_rain(WINDOWS[-1], now), places=6)

    def test_rain_that_was_blind_is_at_most_one_reading_interval_per_midnight(self):
        lost = len(self.weather.tips) - len(self.sim.tips)
        midnights = 383
        per_midnight = 0.4 * 3                                       # three tips: more than any 5 minutes can carry here
        self.assertLessEqual(lost * TIP, midnights * per_midnight)

    def test_a_saved_archive_gives_the_same_answers(self):
        copy = Archive.from_dict(self.sim.archive.to_dict())
        now = self.polls[-1]
        for window in WINDOWS:
            for mine, theirs in zip(copy.window(window, now), self.sim.archive.window(window, now)):
                self.assertAlmostEqual(mine, theirs, places=2)

    def test_old_rain_leaves_the_year_and_the_windows_empty_out(self):
        later = self.polls[-1] + 400 * DAY
        for window in WINDOWS:
            self.assertEqual(self.sim.archive.window(window, later), (0.0, 0.0), window.key)


class DenseReadings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = local("2026-03-20 00:00")                               # includes the change to summer time
        cls.weather = Weather(start, 25, seed=11)
        cls.polls = whole_minutes(start, cls.weather.end, 60)
        rnd = random.Random(3)
        cls.sim = Run(cls.weather, cls.polls, rnd.sample(cls.polls[8 * 1440:], 40) + [cls.polls[-1]])

    def test_readings_every_minute_match_exactly(self):
        self.assertGreater(len(self.sim.seen), 30)
        self.assertLess(self.sim.worst_rain_error(), 1e-6)

    def test_the_blind_spot_is_at_most_the_last_minute_before_midnight(self):
        lost = len(self.weather.tips) - len(self.sim.tips)
        # a minute of the heaviest intensity (30 mm/h) is 0.5 mm: at most 5 tips, and rain at midnight is rare
        self.assertLessEqual(lost, 5 * 25)


class IrregularReadings(unittest.TestCase):
    def test_readings_at_odd_times_stay_within_the_rain_of_one_interval(self):
        start = local("2026-10-10 00:00")                               # includes the change back to winter time
        weather = Weather(start, 40, seed=5)
        rnd = random.Random(9)
        polls, t = [], start
        while t < weather.end:
            polls.append(t)
            t += rnd.choice((47, 60, 61, 73, 89, 120, 300))
        sim = Run(weather, polls, rnd.sample(polls[10 * 1000:], 60))
        tolerance = sim.max_interval_rain() + 1e-6
        self.assertGreater(tolerance, 0.05)
        self.assertLessEqual(sim.worst_rain_error(), tolerance)


class RainAtMidnight(unittest.TestCase):
    """The rain that falls right after a reset is the case the dzVents script lost: its first value after midnight was
    compared with the old counter and thrown away. Every midnight of this run has rain on both sides, and the readings
    come at odd times, so the reading that sees the reset also sees rain."""

    def run_it(self, seed):
        start = local("2026-10-20 00:00")
        weather = Weather(start, 30, seed=0, midnight_rain=True)
        rnd = random.Random(seed)
        polls, t = [], start
        while t < weather.end:
            polls.append(t)
            t += rnd.choice((37, 47, 60, 61, 73, 89))
        return weather, Run(weather, polls)

    def test_all_the_rain_that_was_visible_is_counted(self):
        for seed in (1, 2, 3):
            weather, sim = self.run_it(seed)
            expected = TIP * len(sim.tips)
            counted = sim.archive.window(WINDOWS[-1], sim.polls[-1])[0]
            self.assertGreater(expected, 100)
            self.assertAlmostEqual(counted, expected, places=6, msg="seed %d" % seed)

    def test_what_was_not_visible_is_only_the_time_before_each_midnight_reading(self):
        weather, sim = self.run_it(1)
        lost = TIP * (len(weather.tips) - len(sim.tips))
        # 30 midnights; the last reading before a midnight is at most 89 s earlier; 6 mm/h is 0.15 mm in 89 s
        self.assertLessEqual(lost, 30 * 0.2)
        self.assertGreater(lost, 0)

    def test_rain_after_the_reset_is_in_the_first_minutes_of_the_new_day(self):
        weather, sim = self.run_it(2)
        # the fine ring remembers 7 days: the first slot after the midnight of three days ago holds rain
        midnight = local_midnight(sim.polls[-1] - 3 * DAY)
        ring = sim.archive.rain["fine"]
        self.assertGreater(ring.data[int(midnight // 300) % ring.slots], 0.0)


class TheEdgeOfAWindow(unittest.TestCase):
    """The one place where a window is an estimate: the slot at its edge, taken in proportion, shown with one storm."""

    def test_a_storm_fades_out_of_a_24_hour_window_through_one_slot(self):
        archive = Archive()
        archive.add_rain(local("2026-11-10 08:01:00"), 10.0)
        day = WINDOWS[0]                                            # 24 h, 5-minute slots
        self.assertEqual(day.key, "1d")
        # the storm is in the 08:00-08:05 slot. While the slot is a whole slot of the window, all of it counts ...
        self.assertEqual(archive.window(day, local("2026-11-11 07:59:59"))[0], 10.0)
        # ... then it is the edge slot, and counts as far as it still lies inside the 24 hours
        self.assertAlmostEqual(archive.window(day, local("2026-11-11 08:00:00"))[0], 10.0, places=9)
        self.assertAlmostEqual(archive.window(day, local("2026-11-11 08:00:30"))[0], 9.0, places=9)    # exact: 10 (08:01)
        self.assertAlmostEqual(archive.window(day, local("2026-11-11 08:02:30"))[0], 5.0, places=9)    # exact: 0
        self.assertAlmostEqual(archive.window(day, local("2026-11-11 08:04:30"))[0], 1.0, places=9)    # exact: 0
        self.assertEqual(archive.window(day, local("2026-11-11 08:05:00"))[0], 0.0)

    def test_the_same_storm_fades_out_of_a_14_day_window_through_one_quarter_hour(self):
        archive = Archive()
        archive.add_rain(local("2026-11-01 08:59:00"), 10.0)         # in the quarter 08:45-09:00
        window = [w for w in WINDOWS if w.key == "14d"][0]
        self.assertEqual(archive.window(window, local("2026-11-15 08:44:59"))[0], 10.0)
        self.assertAlmostEqual(archive.window(window, local("2026-11-15 08:45:00"))[0], 10.0, places=9)
        self.assertAlmostEqual(archive.window(window, local("2026-11-15 08:52:30"))[0], 5.0, places=9)
        self.assertAlmostEqual(archive.window(window, local("2026-11-15 08:59:00"))[0], 10.0 / 15, places=9)  # exact: 0, from here
        self.assertEqual(archive.window(window, local("2026-11-15 09:00:00"))[0], 0.0)


if __name__ == "__main__":
    unittest.main()
