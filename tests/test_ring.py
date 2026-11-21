"""The round-robin buffer: slots, wrap-around, expiry, spreading, saving. Includes a randomised check against a model."""
import random
import unittest

import helpers  # noqa: F401  (paths, time zone)
from rain_stats.ring import MAX, SUM, Ring


def model_window(truth, head, slots, now_slot, count):
    """What a ring must answer, computed from a plain dict slot -> value."""
    if head is None or count < 1:
        return 0.0
    high = min(now_slot, head)
    low = max(now_slot - count + 1, head - slots + 1)
    return sum(truth.get(s, 0.0) for s in range(low, high + 1))


class RingBasics(unittest.TestCase):
    def test_an_empty_ring_answers_zero(self):
        ring = Ring(12, 300)
        self.assertEqual(ring.window(10_000, 12), 0.0)
        self.assertIsNone(ring.head)

    def test_the_slot_of_the_moment_is_part_of_the_window(self):
        ring = Ring(12, 300)
        ring.add(3000 + 10, 1.5)                       # slot 10
        self.assertEqual(ring.window(3000 + 290, 1), 1.5)     # the same slot, window of one slot
        self.assertEqual(ring.window(3300 + 5, 1), 0.0)       # the next slot is a window of one slot: the rain is out
        self.assertEqual(ring.window(3300 + 5, 2), 1.5)

    def test_a_window_covers_the_current_slot_and_the_ones_before(self):
        ring = Ring(100, 60)
        for slot in range(10, 20):
            ring.add(slot * 60, 1.0)
        now = 19 * 60 + 30                              # inside slot 19
        self.assertEqual(ring.window(now, 1), 1.0)
        self.assertEqual(ring.window(now, 5), 5.0)
        self.assertEqual(ring.window(now, 10), 10.0)
        self.assertEqual(ring.window(now, 50), 10.0)

    def test_values_in_one_slot_are_summed_in_a_sum_ring(self):
        ring = Ring(10, 60)
        ring.add(600, 0.2)
        ring.add(630, 0.3)
        self.assertAlmostEqual(ring.window(660, 2), 0.5)

    def test_a_max_ring_keeps_the_largest_value_of_a_slot(self):
        ring = Ring(10, 60, MAX)
        for value in (1.0, 7.5, 3.0):
            ring.add(600, value)
        ring.add(660, 2.0)
        self.assertEqual(ring.window(660, 2), 7.5)
        self.assertEqual(ring.window(660, 1), 2.0)

    def test_zero_negative_and_nan_values_change_nothing(self):
        ring = Ring(10, 60)
        for value in (0, 0.0, -1.0, float("nan")):
            self.assertTrue(ring.add(600, value))
        self.assertIsNone(ring.head)
        self.assertEqual(ring.window(600, 10), 0.0)

    def test_the_constructor_refuses_nonsense(self):
        for args in ((0, 60), (10, 0), (10, 60, "avg")):
            with self.assertRaises(ValueError):
                Ring(*args)


class RingWrapAround(unittest.TestCase):
    def test_the_oldest_slot_is_overwritten_by_the_newest(self):
        ring = Ring(4, 60)
        for slot in range(1, 5):
            ring.add(slot * 60, float(slot))           # 1 2 3 4
        self.assertEqual(ring.window(4 * 60, 4), 10.0)
        ring.add(5 * 60, 5.0)                           # slot 1 falls out
        self.assertEqual(ring.window(5 * 60, 4), 14.0)
        self.assertEqual(ring.window(5 * 60, 10), 14.0)    # a window longer than the ring is the ring

    def test_quiet_time_expires_old_rain_without_any_write(self):
        ring = Ring(4, 60)
        ring.add(60, 2.0)
        self.assertEqual(ring.window(60 * 4, 4), 2.0)       # slot 4 is the newest of the window: 1..4
        self.assertEqual(ring.window(60 * 5, 4), 0.0)       # the window slid on: 2..5
        self.assertEqual(ring.window(60 * 1000, 4), 0.0)

    def test_a_write_after_a_long_quiet_time_clears_everything_older(self):
        ring = Ring(4, 60)
        ring.add(60, 2.0)
        ring.add(60 * 1000, 1.0)
        self.assertEqual(ring.window(60 * 1000, 4), 1.0)

    def test_skipped_slots_are_cleared_not_reused(self):
        ring = Ring(4, 60)
        ring.add(60, 9.0)                               # slot 1, index 1
        ring.add(5 * 60, 1.0)                           # slot 5 reuses index 1: the 9.0 must be gone
        self.assertEqual(ring.window(5 * 60, 4), 1.0)

    def test_a_moment_before_the_head_still_inside_the_ring_is_written_into_the_past(self):
        ring = Ring(10, 60)
        ring.add(10 * 60, 1.0)
        self.assertTrue(ring.add(5 * 60, 2.0))
        self.assertEqual(ring.window(10 * 60, 10), 3.0)
        self.assertEqual(ring.window(10 * 60, 3), 1.0)

    def test_a_moment_older_than_the_ring_is_refused(self):
        ring = Ring(10, 60)
        ring.add(100 * 60, 1.0)
        self.assertFalse(ring.add(90 * 60, 5.0))            # slot 90 == head - slots: gone
        self.assertTrue(ring.add(91 * 60, 5.0))             # slot 91 is the oldest the ring still holds
        self.assertEqual(ring.window(100 * 60, 10), 6.0)

    def test_a_clock_that_runs_behind_the_head_ends_the_window_at_now(self):
        ring = Ring(10, 60)
        ring.add(98 * 60, 1.0)
        ring.add(100 * 60, 4.0)
        self.assertEqual(ring.window(99 * 60, 10), 1.0)         # the slot of the head is still in the future of "now"
        self.assertEqual(ring.window(100 * 60, 10), 5.0)        # and counts again when the clock has caught up
        self.assertEqual(ring.window(1 * 60, 10), 0.0)          # a clock far in the past sees nothing, and nothing breaks


class RingSpread(unittest.TestCase):
    def test_rain_is_spread_in_proportion_to_the_time_in_each_slot(self):
        ring = Ring(10, 60)
        ring.spread(60 + 30, 3 * 60 + 30, 4.0)              # 30 s + 60 s + 30 s of 120 s
        self.assertAlmostEqual(ring.window(60, 1), 1.0)
        self.assertAlmostEqual(ring.window(120, 1), 2.0)
        self.assertAlmostEqual(ring.window(180, 1), 1.0)
        self.assertAlmostEqual(ring.window(180, 10), 4.0)

    def test_spreading_keeps_the_total(self):
        ring = Ring(2000, 60)
        ring.spread(1234, 98765, 7.7)
        self.assertAlmostEqual(ring.window(98765, 2000), 7.7)

    def test_an_empty_or_backwards_span_lands_in_one_slot(self):
        ring = Ring(10, 60)
        ring.spread(100, 100, 1.0)
        ring.spread(500, 400, 1.0)
        self.assertAlmostEqual(ring.window(100, 1), 1.0)
        self.assertAlmostEqual(ring.window(500, 1), 1.0)

    def test_a_span_that_ends_exactly_on_a_slot_edge_does_not_touch_the_next_slot(self):
        ring = Ring(10, 300)
        ring.spread(3 * 300, 4 * 300, 2.0)
        self.assertAlmostEqual(ring.window(3 * 300, 1), 2.0)
        self.assertEqual(ring.data[4 % 10], 0.0)

    def test_the_part_older_than_the_ring_is_dropped(self):
        ring = Ring(4, 60)
        ring.add(100 * 60, 1.0)
        ring.spread(60, 100 * 60 + 60, 10.0)                # almost all of it is far older than 4 slots
        self.assertAlmostEqual(ring.window(100 * 60, 4), 1.0 + 10.0 * 4 / 100)     # 4 of the 100 slots of the span are kept

    def test_a_span_of_decades_costs_no_more_than_one_ring(self):
        ring = Ring(8760, 3600)
        ring.spread(3600.0, 1_800_000_000.0, 50.0)                  # a clock that was wrong, then jumped to 2027
        self.assertAlmostEqual(ring.window(1_800_000_000.0, 8760), 50.0 * 8760 * 3600 / (1_800_000_000.0 - 3600.0), places=3)

    def test_a_max_ring_ignores_spread(self):
        ring = Ring(10, 60, MAX)
        ring.spread(0, 300, 5.0)
        self.assertEqual(ring.window(300, 10), 0.0)


class RingSaving(unittest.TestCase):
    def filled(self):
        ring = Ring(50, 300)
        for slot, value in ((10, 0.3), (11, 1.25), (30, 4.0), (59, 0.1)):
            ring.add(slot * 300, value)
        return ring

    def test_a_ring_survives_a_round_trip_with_its_windows(self):
        ring = self.filled()
        again = Ring.from_dict(ring.to_dict(4), 50, 300, SUM)
        self.assertEqual(again.head, ring.head)
        for count in (1, 5, 30, 50):
            self.assertAlmostEqual(again.window(59 * 300, count), ring.window(59 * 300, count))

    def test_only_slots_with_a_value_are_saved(self):
        data = self.filled().to_dict(4)
        self.assertEqual(sorted(int(k) for k in data["values"]), [10, 11, 30, 59])     # head 59 of 50 slots: 10..59
        self.assertEqual(data["head"], 59)
        self.assertEqual(data["resolution"], 300)

    def test_a_ring_can_be_loaded_into_a_longer_ring(self):
        data = self.filled().to_dict(4)
        bigger = Ring.from_dict(data, 500, 300, SUM)
        self.assertAlmostEqual(bigger.window(59 * 300, 500), 0.3 + 1.25 + 4.0 + 0.1)

    def test_saved_with_a_resolution_that_cannot_be_converted_is_refused(self):
        saved = self.filled().to_dict(4)                           # 300 second slots
        for slots, resolution in ((50, 600), (50, 200), (50, 7)):  # longer than the saved ones, not a divisor, not a divisor
            with self.assertRaises(ValueError) as ctx:
                Ring.from_dict(saved, slots, resolution, SUM)
            self.assertIn("300 second slots", str(ctx.exception))
        with self.assertRaises(ValueError):                        # a divisor, but 300 times as many slots as it should be
            Ring.from_dict(saved, 50, 1, SUM)
        with self.assertRaises(ValueError):
            Ring.from_dict(dict(saved, resolution="soon"), 50, 300, SUM)

    # -- a ring saved with longer slots (the hours of version 0.1.0 become quarters of an hour) ----------------
    def hourly(self, mode=SUM):
        ring = Ring(48, 3600, mode)
        for hour, value in ((100, 4.0), (101, 0.8), (130, 2.0), (147, 6.0)):
            ring.add(hour * 3600 + 5, value)
        return ring

    def test_a_sum_ring_with_longer_slots_is_spread_over_the_new_slots_and_loses_nothing(self):
        old = self.hourly()
        new = Ring.from_dict(old.to_dict(4), 192, 900, SUM)
        self.assertEqual(new.resolution, 900)
        self.assertEqual(new.head, 147 * 4 + 3)                    # the last quarter of the newest hour
        self.assertAlmostEqual(new.window(new.head * 900, 192), 4.0 + 0.8 + 2.0 + 6.0, places=9)
        # an hour of 2.0 mm is four quarters of 0.5
        for quarter in range(4):
            self.assertAlmostEqual(new.data[(130 * 4 + quarter) % 192], 0.5, places=9)
        self.assertAlmostEqual(sum(new.data), 12.8, places=9)
        self.assertEqual(sorted(v for v in new.data if v), [0.2] * 4 + [0.5] * 4 + [1.0] * 4 + [1.5] * 4)

    def test_a_max_ring_with_longer_slots_keeps_the_peak_in_every_new_slot(self):
        old = Ring(48, 3600, MAX)
        old.add(130 * 3600 + 5, 14.5)
        new = Ring.from_dict(old.to_dict(2), 192, 900, MAX)
        self.assertEqual([v for v in new.data if v], [14.5] * 4)
        self.assertEqual(new.window((130 * 4 + 2) * 900, 1), 14.5)

    def test_a_converted_ring_goes_on_working(self):
        new = Ring.from_dict(self.hourly().to_dict(4), 192, 900, SUM)
        new.add((147 * 4 + 3) * 900 + 60, 1.0)                     # rain now, in the last quarter that was written
        new.add((148 * 4) * 900 + 60, 2.0)                         # and in the next hour: the ring moves on
        self.assertEqual(new.head, 148 * 4)
        # all of it, except the first quarter of hour 100 (1.0 mm): by hour 148 it is older than the 192 quarters of the ring
        self.assertAlmostEqual(new.window((148 * 4) * 900, 192), 4.0 - 1.0 + 0.8 + 2.0 + 6.0 + 1.0 + 2.0, places=9)

    def test_a_converted_ring_drops_what_does_not_fit_and_says_nothing_wrong(self):
        new = Ring.from_dict(self.hourly().to_dict(4), 8, 900, SUM)   # a ring of only 8 quarters = the last 2 hours
        self.assertAlmostEqual(sum(new.data), 6.0, places=9)           # only the hour of 147 (and its 4 quarters) remain

    def test_a_file_that_is_no_ring_is_refused(self):
        for junk in (None, [], "x", 5):
            with self.assertRaises(ValueError):
                Ring.from_dict(junk, 50, 300, SUM)

    def test_bad_values_are_skipped_and_counted(self):
        data = {"resolution": 300, "slots": 50, "head": 100,
                "values": {"100": 1.0, "abc": 2.0, "99": "x", "98": -1.0, "97": float("nan"), "50": 3.0, "101": 4.0, "96": 0.5}}
        ring = Ring.from_dict(data, 50, 300, SUM)
        self.assertEqual(ring.skipped, 6)                    # abc, "x", -1, nan, slot 50 (too old), slot 101 (newer than head)
        self.assertAlmostEqual(ring.window(100 * 300, 50), 1.5)

    def test_a_ring_without_a_head_is_empty(self):
        ring = Ring.from_dict({"resolution": 300, "slots": 50, "head": None, "values": {}}, 50, 300, SUM)
        self.assertIsNone(ring.head)


class RingAgainstAModel(unittest.TestCase):
    """Random writes, in order and not, with gaps, against a dict that keeps every slot forever."""

    def run_model(self, seed, mode):
        rnd = random.Random(seed)
        slots, resolution = rnd.choice((3, 7, 24, 61)), 60
        ring = Ring(slots, resolution, mode)
        truth, head = {}, None
        moment = 10_000_000
        for _ in range(600):
            kind = rnd.random()
            if kind < 0.55:
                moment += rnd.choice((0, 10, 60, 61, 119, 300))              # forward
            elif kind < 0.75:
                moment -= rnd.choice((0, 30, 60, 120, 600))                  # back in time
            elif kind < 0.80:
                moment += rnd.choice((slots * resolution, slots * resolution * 3))     # a long quiet time
            value = round(rnd.choice((0.0, 0.1, 0.3, 1.0, 2.5)) + rnd.random(), 3) if rnd.random() < 0.6 else 0.0
            slot = int(moment // resolution)
            accepted = ring.add(moment, value)
            if value > 0:
                self.assertEqual(accepted, head is None or slot > head - slots)
                if accepted:
                    head = slot if head is None else max(head, slot)
                    old = truth.get(slot, 0.0)
                    truth[slot] = old + value if mode == SUM else max(old, value)
            for count in (1, 2, slots - 1, slots, slots + 5):
                now = moment + rnd.choice((0, 59, 60, 61, 600))
                got = ring.window(now, count)
                now_slot = int(now // resolution)
                if head is None or count < 1:
                    want = 0.0
                else:
                    high, low = min(now_slot, head), max(now_slot - count + 1, head - slots + 1)
                    values = [truth.get(s, 0.0) for s in range(low, high + 1)]
                    want = (sum(values) if mode == SUM else max(values)) if values else 0.0
                self.assertAlmostEqual(got, want, places=9, msg="seed %d count %d" % (seed, count))

    def test_a_sum_ring_agrees_with_the_model(self):
        for seed in range(40):
            self.run_model(seed, SUM)

    def test_a_max_ring_agrees_with_the_model(self):
        for seed in range(40):
            self.run_model(1000 + seed, MAX)

    def test_saving_and_loading_never_changes_an_answer(self):
        rnd = random.Random(5)
        ring = Ring(100, 60)
        moment = 5_000_000
        for _ in range(300):
            moment += rnd.choice((0, 60, 120, 900))
            ring.add(moment, round(rnd.random() * 3, 3))
        again = Ring.from_dict(ring.to_dict(6), 100, 60, SUM)
        for count in (1, 10, 50, 100):
            for offset in (0, 60, 3000):
                self.assertAlmostEqual(again.window(moment + offset, count), ring.window(moment + offset, count), places=5)


if __name__ == "__main__":
    unittest.main()
