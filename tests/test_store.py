"""The state file: atomic writes, a file that cannot be used is moved aside and never overwritten."""
import json
import os
import shutil
import tempfile
import unittest

import helpers
from helpers import local
from rain_stats import store
from rain_stats.constants import WINDOWS
from rain_stats.stats import compute
from rain_stats.store import State, StateError

NOW = local("2026-11-18 08:00")


def filled_state():
    state = State()
    state.archive.add_rain(NOW - 3600, 2.5)
    state.archive.add_spread(NOW - 7200, NOW - 3600, 1.2)
    state.archive.note_rate(NOW - 1800, 14.0)
    state.since = NOW - 86400
    state.counter_ts, state.counter_value = NOW - 60, 2.5
    return state


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rainstats-store-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "state.json")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def aside(self):
        return sorted(n for n in os.listdir(self.tmp) if ".corrupt-" in n)

    # -- round trip -----------------------------------------------------------------------------------------
    def test_no_file_is_no_state(self):
        self.assertIsNone(store.load(self.path, NOW))

    def test_a_state_survives_saving_and_loading(self):
        state = filled_state()
        store.save(self.path, state, NOW)
        loaded = store.load(self.path, NOW)
        self.assertEqual((loaded.since, loaded.counter_ts, loaded.counter_value), (state.since, state.counter_ts, 2.5))
        before, after = compute(state.archive, NOW, state.since), compute(loaded.archive, NOW, loaded.since)
        for window in WINDOWS:
            self.assertAlmostEqual(after.rain[window.key], before.rain[window.key], places=3)
            self.assertAlmostEqual(after.peak[window.key], before.peak[window.key], places=2)
        self.assertGreater(after.rain["1d"], 3.5)
        self.assertEqual(after.peak["1d"], 14.0)

    def test_an_empty_state_round_trips(self):
        store.save(self.path, State(), NOW)
        loaded = store.load(self.path, NOW)
        self.assertEqual((loaded.since, loaded.counter_ts, loaded.counter_value), (None, None, None))

    def test_the_file_is_plain_json_with_a_version_and_the_save_time(self):
        store.save(self.path, filled_state(), NOW)
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["v"], 1)
        self.assertEqual(data["saved"], "2026-11-18 08:00:00")
        self.assertEqual(set(data["archive"]), {"rain", "peak"})
        self.assertEqual(set(data["archive"]["rain"]), {"fine", "coarse"})

    def test_the_file_holds_only_what_has_a_value(self):
        state = State()
        state.archive.add_rain(NOW, 1.0)
        store.save(self.path, state, NOW)
        self.assertLess(os.path.getsize(self.path), 1500)

    def test_saving_creates_the_folder_and_leaves_no_temporary_file(self):
        path = os.path.join(self.tmp, "sub", "deeper", "state.json")
        store.save(path, State(), NOW)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.listdir(os.path.dirname(path)), ["state.json"])

    def test_a_save_that_fails_leaves_the_old_file_and_no_temporary_file(self):
        store.save(self.path, filled_state(), NOW)
        old = helpers.read_path(self.path)
        blocked = State()

        class Boom(State):
            def to_dict(self, now):
                raise OSError("disk full")

        with self.assertRaises(OSError):
            store.save(self.path, Boom(), NOW)
        self.assertEqual(helpers.read_path(self.path), old)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])
        del blocked

    def test_a_path_below_a_file_cannot_be_written(self):
        open(os.path.join(self.tmp, "blocker"), "w").close()
        with self.assertRaises(OSError):
            store.save(os.path.join(self.tmp, "blocker", "state.json"), State(), NOW)

    # -- a file that cannot be used --------------------------------------------------------------------------
    def test_garbage_is_moved_aside_and_said(self):
        self.write("{not json")
        with self.assertRaises(StateError) as caught:
            store.load(self.path, NOW)
        self.assertIn("moved to", str(caught.exception))
        self.assertIn("starting with an empty archive", str(caught.exception))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(len(self.aside()), 1)
        self.assertEqual(helpers.read_path(os.path.join(self.tmp, self.aside()[0])), "{not json")

    def test_two_bad_files_in_the_same_second_do_not_overwrite_each_other(self):
        for text in ("{one", "{two"):
            self.write(text)
            with self.assertRaises(StateError):
                store.load(self.path, NOW)
        self.assertEqual(len(self.aside()), 2)

    def test_reading_only_never_moves_anything(self):
        self.write("{not json")
        with self.assertRaises(StateError) as caught:
            store.load(self.path, NOW, move=False)
        self.assertNotIn("moved", str(caught.exception))
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(self.aside(), [])

    def test_a_file_that_cannot_be_moved_is_said_too(self):
        self.write("{not json")
        original = store.os.replace

        def refuse(a, b):
            raise OSError("read-only")
        store.os.replace = refuse
        try:
            with self.assertRaises(StateError) as caught:
                store.load(self.path, NOW)
        finally:
            store.os.replace = original
        self.assertIn("could not be moved aside", str(caught.exception))

    def test_a_file_of_a_newer_version_is_moved_aside_never_overwritten(self):
        self.write(json.dumps({"v": 2, "archive": {}}))
        with self.assertRaises(StateError) as caught:
            store.load(self.path, NOW)
        self.assertIn("newer version of the plugin", str(caught.exception))
        self.assertEqual(len(self.aside()), 1)

    def test_a_file_that_is_too_large_is_refused(self):
        self.write(json.dumps({"v": 1, "padding": "x" * 5000}))
        saved = store.MAX_STATE_BYTES
        store.MAX_STATE_BYTES = 1000
        try:
            with self.assertRaises(StateError) as caught:
                store.load(self.path, NOW)
        finally:
            store.MAX_STATE_BYTES = saved
        self.assertIn("larger than 1000 bytes", str(caught.exception))

    def test_every_kind_of_nonsense_is_a_state_error_not_a_crash(self):
        good = filled_state().to_dict(NOW)
        cases = {
            "not an object": [1, 2],
            "no version": {"archive": good["archive"]},
            "a boolean version": dict(good, v=True),
            "a text version": dict(good, v="1"),
            "no archive": {"v": 1},
            "an archive that is a list": dict(good, archive=[]),
            "no rain rings": dict(good, archive={"peak": good["archive"]["peak"]}),
            "since is text": dict(good, since="soon"),
            "counter is a list": dict(good, counter=[1]),
            "counter without a value": dict(good, counter={"ts": 1.0}),
            "counter below zero": dict(good, counter={"ts": 1.0, "value": -1}),
            "counter time is nan": dict(good, counter={"ts": float("nan"), "value": 1}),
            "a ring with another resolution": dict(good, archive=dict(good["archive"], rain={
                "fine": dict(good["archive"]["rain"]["fine"], resolution=60)})),
        }
        for name, data in cases.items():
            self.write(json.dumps(data))
            with self.assertRaises(StateError, msg=name):
                store.load(self.path, NOW)
            for leftover in self.aside():
                os.remove(os.path.join(self.tmp, leftover))

    def test_bad_values_inside_a_good_ring_are_skipped_not_fatal(self):
        data = filled_state().to_dict(NOW)
        values = data["archive"]["rain"]["fine"]["values"]
        values["not-a-slot"] = 1.0
        values[str(10 ** 12)] = 1.0            # far in the future of the head
        values["5"] = "heavy"
        self.write(json.dumps(data))
        loaded = store.load(self.path, NOW)
        self.assertEqual(loaded.archive.rain["fine"].skipped, 3)
        self.assertGreater(compute(loaded.archive, NOW, loaded.since).rain["1d"], 3.5)


    # -- the marker of the imported history -------------------------------------------------------------------
    def test_the_marker_of_the_history_survives_saving_and_loading(self):
        from rain_stats.store import HistoryMark
        state = filled_state()
        state.history = HistoryMark(1161, NOW - 366 * 86400, NOW - 86400, 340, NOW)
        store.save(self.path, state, NOW)
        self.assertEqual(helpers.load_json(self.path)["history"], {"idx": 1161, "first": NOW - 366 * 86400,
                                                                   "last": NOW - 86400, "days": 340, "at": NOW})
        self.assertEqual(store.load(self.path, NOW).history, state.history)

    def test_a_file_without_the_marker_has_no_history(self):
        store.save(self.path, filled_state(), NOW)
        self.assertNotIn("history", helpers.load_json(self.path))
        self.assertIsNone(store.load(self.path, NOW).history)

    def test_a_marker_that_makes_no_sense_makes_the_file_unusable_and_it_is_moved_aside(self):
        good = {"idx": 1161, "first": 1.0, "last": 2.0, "days": 3, "at": 4.0}
        for name, bad in {"a list": [1], "no idx": dict(good, idx=None), "idx 0": dict(good, idx=0),
                          "days below 0": dict(good, days=-1), "text for first": dict(good, first="then"),
                          "last before first": dict(good, last=0.5), "no time": dict(good, at=None)}.items():
            data = filled_state().to_dict(NOW)
            data["history"] = bad
            self.write(json.dumps(data))
            with self.assertRaises(StateError, msg=name):
                store.load(self.path, NOW)
            for leftover in self.aside():
                os.remove(os.path.join(self.tmp, leftover))

    # -- a file of an early 0.1.0 build: the coarse ring had 1-hour slots -----------------------------------
    def hourly_file(self):
        """The state of filled_state(), with the coarse rings in the layout of the early build: 8760 slots of one hour."""
        data = filled_state().to_dict(NOW)
        hour = NOW // 3600 - 2                                      # an hour that is over
        data["archive"]["rain"]["coarse"] = {"resolution": 3600, "slots": 8760, "head": hour + 1,
                                             "values": {str(hour - 30 * 24): 2.0, str(hour): 3.0, str(hour + 1): 1.0}}
        data["archive"]["peak"]["coarse"] = {"resolution": 3600, "slots": 8760, "head": hour + 1,
                                             "values": {str(hour): 12.0, str(hour + 1): 4.0}}
        return data

    def test_a_file_with_hourly_coarse_slots_is_upgraded_not_moved_aside(self):
        self.write(json.dumps(self.hourly_file()))
        loaded = store.load(self.path, NOW)
        self.assertEqual(self.aside(), [])
        coarse, fine = loaded.archive.rain["coarse"], loaded.archive.rain["fine"]
        self.assertEqual((coarse.resolution, coarse.slots), (900, 35040 + 1))
        self.assertEqual((fine.resolution, fine.slots), (300, 2016 + 1))
        # nothing is lost: the rain of the file is all there (the 30-day-old hour has left the 14 d window, not the 90 d)
        stats = compute(loaded.archive, NOW, loaded.since)
        self.assertAlmostEqual(stats.rain["14d"], 4.0, places=6)
        self.assertAlmostEqual(stats.rain["90d"], 6.0, places=6)
        self.assertAlmostEqual(stats.rain["365d"], 6.0, places=6)
        self.assertEqual(stats.peak["14d"], 12.0)
        self.assertEqual(stats.peak["1d"], 14.0)                   # the fine ring was untouched

    def test_an_upgraded_file_is_saved_in_the_new_layout_and_loads_again(self):
        self.write(json.dumps(self.hourly_file()))
        store.save(self.path, store.load(self.path, NOW), NOW)
        with open(self.path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["archive"]["rain"]["coarse"]["resolution"], 900)
        self.assertEqual(saved["archive"]["peak"]["coarse"]["resolution"], 900)
        again = store.load(self.path, NOW)
        self.assertAlmostEqual(compute(again.archive, NOW, again.since).rain["90d"], 6.0, places=6)
        self.assertEqual(self.aside(), [])

    def test_a_coarse_ring_with_slots_that_cannot_be_converted_is_still_moved_aside(self):
        data = self.hourly_file()
        data["archive"]["rain"]["coarse"]["resolution"] = 1000      # not a multiple of 15 minutes
        self.write(json.dumps(data))
        with self.assertRaises(StateError):
            store.load(self.path, NOW)
        self.assertEqual(len(self.aside()), 1)


if __name__ == "__main__":
    unittest.main()
