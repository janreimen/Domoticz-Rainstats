"""The archive (rain and peak rings of both tiers) and the windows computed from it."""
import unittest

import helpers  # noqa: F401
from rain_stats.archive import Archive
from rain_stats.constants import COARSE, FINE, TIERS, WINDOWS, Window, tier_of, window_slots
from rain_stats.stats import compute

B = 1_800_000_000          # a multiple of 300, of 900 and of 3600: slot edges of both tiers and of the hour
FINE_SLOT, COARSE_SLOT = 300, 900
WIN = {w.key: w for w in WINDOWS}


class Layout(unittest.TestCase):
    def test_the_windows_the_user_asked_for(self):
        days = [1, 2, 7, 14, 24, 30, 60, 90, 120, 180, 360, 365]
        self.assertEqual([w.key for w in WINDOWS], ["%dd" % d for d in days])
        self.assertEqual([w.seconds for w in WINDOWS], [d * 86400 for d in days])
        self.assertEqual([w.label for w in WINDOWS], ["%d d" % d for d in days])
        self.assertEqual([w.env_key for w in WINDOWS], ["RS_LAST_%03d_DAYS" % d for d in days])
        # up to a week the fine ring answers, everything longer the coarse one
        self.assertEqual([w.tier for w in WINDOWS], ["fine"] * 3 + ["coarse"] * 9)
        # the first eight (the windows of the first version) are on by default, the new ones are not
        self.assertEqual([w.key for w in WINDOWS if w.default], ["1d", "2d", "7d", "14d", "30d", "90d", "180d", "365d"])

    def test_every_window_fits_its_tier_exactly_or_less(self):
        for window in WINDOWS:
            tier = tier_of(window.tier)
            self.assertEqual(window.seconds % tier.resolution, 0, window.key)
            self.assertLessEqual(window_slots(window), tier.slots, window.key)

    def test_the_finest_tier_that_holds_a_window_answers_it(self):
        fine, coarse = tier_of(FINE), tier_of(COARSE)
        for window in WINDOWS:
            fits_fine = window.seconds <= fine.resolution * fine.slots
            self.assertEqual(window.tier, FINE if fits_fine else COARSE, window.key)
        self.assertEqual(fine.resolution * fine.slots, 7 * 86400)
        self.assertEqual(coarse.resolution * coarse.slots, 365 * 86400)

    def test_the_archive_has_a_rain_and_a_peak_ring_per_tier(self):
        archive = Archive()
        self.assertEqual(sorted(archive.rain), sorted(t.key for t in TIERS))
        self.assertEqual(sorted(archive.peak), sorted(t.key for t in TIERS))


class WindowEdges(unittest.TestCase):
    """The rain of a window is the running slot and the N-1 before it, plus the part of the slot before those that is
    still inside the period. The peak is made of the whole slots only."""

    def edge(self, key, res):
        window = WIN[key]
        n = window_slots(window)
        now = B + res // 2                                         # the middle of the running slot: half of the edge counts
        first_inside = B - (n - 1) * res + 10                      # the oldest whole slot of the window
        edge_slot = B - n * res + 10                               # the slot before it: its later half is inside the period
        beyond = B - (n + 1) * res + 10                            # older than the period
        archive = Archive()
        archive.add_rain(beyond, 1000.0)
        archive.add_rain(edge_slot, 100.0)
        archive.add_rain(first_inside, 1.0)
        archive.note_rate(beyond, 77.0)
        archive.note_rate(edge_slot, 99.0)
        archive.note_rate(first_inside, 3.0)
        rain, peak = archive.window(window, now)
        self.assertAlmostEqual(rain, 1.0 + 100.0 * 0.5, places=9, msg=key)
        self.assertEqual(peak, 3.0, key)                           # a peak is never taken in proportion: whole slots only

    def test_the_edge_of_every_fine_window(self):
        for key in ("1d", "2d", "7d"):
            self.edge(key, FINE_SLOT)

    def test_the_edge_of_every_coarse_window(self):
        for key in ("14d", "30d", "90d", "180d", "365d"):
            self.edge(key, COARSE_SLOT)

    def test_the_edge_fades_out_linearly_through_a_slot(self):
        window = WIN["1d"]
        archive = Archive()
        archive.add_rain(B - 288 * FINE_SLOT + 10, 10.0)             # in the edge slot of a window that ends in slot B
        for elapsed, expected in ((0, 10.0), (75, 7.5), (150, 5.0), (225, 2.5), (299, 0.0333333333)):
            self.assertAlmostEqual(archive.window(window, B + elapsed)[0], expected, places=6, msg=str(elapsed))
        self.assertEqual(archive.window(window, B + FINE_SLOT)[0], 0.0)      # the next slot: out for good

    def test_the_longest_windows_have_an_edge_too(self):
        # the ring of the longest window of a tier keeps one spare slot for exactly this
        for key, res in (("7d", FINE_SLOT), ("365d", COARSE_SLOT)):
            window = WIN[key]
            archive = Archive()
            archive.add_rain(B - window_slots(window) * res + 10, 8.0)
            self.assertAlmostEqual(archive.window(window, B + res // 4)[0], 6.0, places=9, msg=key)

    def test_rain_that_just_fell_is_in_every_window(self):
        archive = Archive()
        archive.add_rain(B + 100, 2.0)
        for window in WINDOWS:
            self.assertEqual(archive.window(window, B + 200)[0], 2.0, window.key)

    def test_older_rain_leaves_the_short_windows_first(self):
        archive = Archive()
        archive.add_rain(B - 3 * 86400, 1.0)                       # three days ago
        rain = {w.key: archive.window(w, B + 10)[0] for w in WINDOWS}
        self.assertEqual([rain[k] for k in ("1d", "2d", "7d", "14d", "365d")], [0.0, 0.0, 1.0, 1.0, 1.0])

    def test_rain_just_under_a_year_old_is_in_the_yearly_window_and_older_rain_is_not(self):
        archive = Archive()
        archive.add_rain(B - 364 * 86400, 1.0)
        archive.add_rain(B - 366 * 86400, 10.0)
        self.assertEqual(archive.window(WIN["365d"], B + 10)[0], 1.0)

    def test_the_peak_is_a_maximum_not_a_sum(self):
        archive = Archive()
        for t, rate in ((B - 100, 4.0), (B - 5000, 9.5), (B - 2 * 86400, 20.0)):
            archive.note_rate(t, rate)
        peaks = {w.key: archive.window(w, B + 10)[1] for w in WINDOWS}
        self.assertEqual((peaks["1d"], peaks["2d"], peaks["7d"], peaks["365d"]), (9.5, 9.5, 20.0, 20.0))

    def test_peaks_expire_like_rain(self):
        archive = Archive()
        archive.note_rate(B, 30.0)
        self.assertEqual(archive.window(WIN["1d"], B + 86400 + 600)[1], 0.0)
        self.assertEqual(archive.window(WIN["14d"], B + 86400 + 600)[1], 30.0)


class Spreading(unittest.TestCase):
    def test_spread_rain_keeps_its_total_in_both_tiers(self):
        archive = Archive()
        archive.add_spread(B - 7200, B - 600, 6.5)
        self.assertAlmostEqual(archive.window(WIN["1d"], B)[0], 6.5)
        self.assertAlmostEqual(archive.window(WIN["365d"], B)[0], 6.5)

    def test_a_day_spread_in_the_past_reaches_the_long_windows_only_when_it_is_too_old_for_the_short_ones(self):
        archive = Archive()
        archive.add_rain(B, 0.1)                                    # puts the head at "now"
        archive.add_spread(B - 20 * 86400, B - 19 * 86400, 12.0)    # one day, 19 days ago
        self.assertAlmostEqual(archive.window(WIN["7d"], B)[0], 0.1)
        self.assertAlmostEqual(archive.window(WIN["14d"], B)[0], 0.1)
        self.assertAlmostEqual(archive.window(WIN["30d"], B)[0], 12.1)


class Saving(unittest.TestCase):
    def filled(self):
        archive = Archive()
        for i in range(0, 400, 7):
            archive.add_rain(B - i * 3600, 0.3 + (i % 5) * 0.1)
            archive.note_rate(B - i * 3600, 1.0 + (i % 9))
        return archive

    def test_an_archive_survives_a_round_trip(self):
        archive = self.filled()
        again = Archive.from_dict(archive.to_dict())
        for window in WINDOWS:
            a, b = archive.window(window, B + 30), again.window(window, B + 30)
            self.assertAlmostEqual(a[0], b[0], places=3, msg=window.key)
            self.assertAlmostEqual(a[1], b[1], places=2, msg=window.key)

    def test_the_saved_form_is_plain_json_types(self):
        import json
        json.dumps(self.filled().to_dict())

    def test_an_archive_that_is_not_an_object_is_refused(self):
        for junk in (None, [], "x"):
            with self.assertRaises(ValueError):
                Archive.from_dict(junk)

    def test_an_archive_without_its_rings_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            Archive.from_dict({"rain": {}})
        self.assertIn("'peak'", str(ctx.exception))

    def test_a_ring_saved_with_another_resolution_names_itself_in_the_error(self):
        data = Archive().to_dict()
        data["rain"]["fine"]["resolution"] = 60
        with self.assertRaises(ValueError) as ctx:
            Archive.from_dict(data)
        self.assertIn("rain ring 'fine'", str(ctx.exception))

    def test_a_tier_missing_from_the_file_is_an_empty_ring(self):
        data = self.filled().to_dict()
        del data["rain"]["fine"]
        again = Archive.from_dict(data)
        self.assertEqual(again.window(WIN["1d"], B + 30)[0], 0.0)
        self.assertGreater(again.window(WIN["365d"], B + 30)[0], 0.0)


class Computing(unittest.TestCase):
    def test_a_window_is_partial_while_the_data_do_not_reach_back_that_far(self):
        archive = Archive()
        archive.add_rain(B, 1.0)
        stats = compute(archive, B + 60, since=B - 30 * 3600)         # collecting for 30 hours
        self.assertEqual(stats.partial, tuple(w.key for w in WINDOWS[1:]))
        self.assertEqual(stats.rain["1d"], 1.0)

    def test_without_any_data_every_window_is_partial(self):
        stats = compute(Archive(), B, since=None)
        self.assertEqual(len(stats.partial), len(WINDOWS))
        self.assertTrue(all(v == 0.0 for v in stats.rain.values()))

    def test_after_a_year_nothing_is_partial(self):
        stats = compute(Archive(), B, since=B - 366 * 86400)
        self.assertEqual(stats.partial, ())

    def test_compute_names_every_window(self):
        stats = compute(Archive(), B, since=B)
        self.assertEqual(sorted(stats.rain), sorted(w.key for w in WINDOWS))
        self.assertEqual(sorted(stats.peak), sorted(w.key for w in WINDOWS))
        self.assertIsInstance(WINDOWS[0], Window)


if __name__ == "__main__":
    unittest.main()
