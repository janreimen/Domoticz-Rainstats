"""The one-time import of the data file of the dzVents script RainStats V2.6."""
import os
import shutil
import tempfile
import unittest

import helpers
from helpers import local, write_legacy
from rain_stats import legacy
from rain_stats.legacy import LegacyError, describe, import_legacy
from rain_stats.stats import compute
from rain_stats.store import State

NOW = local("2026-11-18 06:00")


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rainstats-legacy-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "rain_stats_data.json")

    def imported(self, **kw):
        write_legacy(self.path, **kw)
        state = State()
        return state, import_legacy(self.path, state, NOW)

    def stats(self, state):
        return compute(state.archive, NOW, state.since)

    # -- what is used ---------------------------------------------------------------------------------------
    def test_the_complete_part_of_the_list_and_the_older_daily_totals_are_used(self):
        state, result = self.imported(
            last_save="2026-11-18 05:59", last_value=("2026-11-18 05:59", 2.0),
            values=[("2026-11-18 03:00", 1.5), ("2026-11-18 05:59", 0.5), ("2026-11-17 12:00", 4.0),
                    ("2026-11-05 12:00", 9.9)],                           # thinned part of the script's list: not trusted
            maxima={"2026-11-10": 6.0, "2026-11-17": 4.0, "2026-11-18": 2.0})
        self.assertEqual((result["days"], result["entries"], result["skipped"]), (1, 3, 0))
        self.assertEqual(result["complete_from"], local("2026-11-12 00:00"))
        stats = self.stats(state)
        self.assertAlmostEqual(stats.rain["1d"], 6.0, places=6)         # 4.0 + 1.5 + 0.5
        self.assertAlmostEqual(stats.rain["30d"], 12.0, places=6)       # + the 6.0 mm day, spread over its 24 hours
        self.assertEqual(state.since, local("2026-11-10 00:00"))

    def test_the_baseline_of_the_script_is_taken_over(self):
        state, _ = self.imported(last_value=("2026-11-18 05:59", 2.0))
        self.assertEqual((state.counter_ts, state.counter_value), (local("2026-11-18 05:59"), 2.0))

    def test_a_daily_total_is_spread_over_its_day_and_keeps_its_total(self):
        state, _ = self.imported(values=[], maxima={"2026-11-01": 8.0}, last_save="2026-11-18 05:59")
        self.assertAlmostEqual(self.stats(state).rain["30d"], 8.0, places=6)
        self.assertAlmostEqual(self.stats(state).rain["14d"], 0.0, places=6)

    def test_the_day_that_is_not_over_and_the_days_covered_by_the_list_are_not_added_twice(self):
        state, result = self.imported(
            values=[("2026-11-17 12:00", 4.0)], maxima={"2026-11-17": 4.0, "2026-11-18": 2.0},
            last_save="2026-11-18 05:59")
        self.assertEqual(result["days"], 0)
        self.assertAlmostEqual(self.stats(state).rain["365d"], 4.0, places=6)

    def test_a_missing_last_save_is_taken_from_the_newest_entry(self):
        write_legacy(self.path, values=[("2026-11-17 12:00", 4.0)], last_save="")
        data = helpers.load_json(self.path)
        del data["lastSave"]
        helpers.dump_json(self.path, data)
        state = State()
        result = import_legacy(self.path, state, NOW)
        self.assertEqual(result["last_save"], local("2026-11-17 12:00"))
        self.assertEqual(result["entries"], 1)

    def test_zero_entries_mark_the_start_but_add_no_rain(self):
        state, result = self.imported(values=[("2026-11-16 08:00", 0.0), ("2026-11-17 08:00", 0.0)])
        self.assertEqual(result["entries"], 2)
        self.assertEqual(state.since, local("2026-11-16 08:00"))
        self.assertEqual(self.stats(state).rain["365d"], 0.0)

    # -- what is not -----------------------------------------------------------------------------------------
    def test_unusable_rows_are_skipped_and_counted(self):
        write_legacy(self.path, values=[("2026-11-17 12:00", 4.0)], maxima={"2026-11-01": -3.0, "garbage": 1.0})
        data = helpers.load_json(self.path)
        data["allValues"] += [{"date": "yesterday", "value": 1.0}, {"date": "2026-11-17 13:00", "value": -2.0},
                              {"date": "2026-11-17 14:00", "value": "heavy"}, "not a dict",
                              {"date": "2030-01-01 00:00", "value": 1.0}]
        helpers.dump_json(self.path, data)
        state = State()
        result = import_legacy(self.path, state, NOW)
        self.assertEqual(result["skipped"], 5 + 2)
        self.assertAlmostEqual(compute(state.archive, NOW, state.since).rain["365d"], 4.0, places=6)

    def test_the_counter_baseline_is_ignored_when_it_is_nonsense(self):
        write_legacy(self.path, values=[("2026-11-17 12:00", 4.0)])
        data = helpers.load_json(self.path)
        data["lastValue"] = {"date": "never", "value": -1}
        helpers.dump_json(self.path, data)
        state = State()
        import_legacy(self.path, state, NOW)
        self.assertEqual((state.counter_ts, state.counter_value), (None, None))

    def test_a_file_without_data_gives_an_empty_start(self):
        state, result = self.imported(values=[], maxima={})
        self.assertIsNone(result["since"])
        self.assertIsNone(state.since)
        self.assertIn("holds no usable data", describe(result))

    # -- files that are not the script's ---------------------------------------------------------------------
    def refused(self, text, fragment):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        with self.assertRaises(LegacyError) as caught:
            import_legacy(self.path, State(), NOW)
        self.assertIn(fragment, str(caught.exception))

    def test_things_that_are_not_the_data_file(self):
        self.refused("{nope", "is not valid JSON")
        self.refused("[1, 2]", "not a data file of RainStats V2.6")
        self.refused('{"lastValue": {}}', "no 'allValues' list")
        self.refused('{"allValues": {}}', "no 'allValues' list")

    def test_a_file_that_is_not_there(self):
        with self.assertRaises(LegacyError) as caught:
            import_legacy(os.path.join(self.tmp, "nope.json"), State(), NOW)
        self.assertIn("cannot read", str(caught.exception))

    def test_a_file_that_is_too_large(self):
        write_legacy(self.path)
        saved = legacy.MAX_LEGACY_BYTES
        legacy.MAX_LEGACY_BYTES = 10
        try:
            with self.assertRaises(LegacyError) as caught:
                import_legacy(self.path, State(), NOW)
        finally:
            legacy.MAX_LEGACY_BYTES = saved
        self.assertIn("larger than 10 bytes", str(caught.exception))

    def test_the_script_s_file_is_never_changed(self):
        write_legacy(self.path, values=[("2026-11-17 12:00", 4.0)])
        before = helpers.read_path(self.path, binary=True)
        import_legacy(self.path, State(), NOW)
        self.assertEqual(helpers.read_path(self.path, binary=True), before)

    def test_the_description_says_what_was_imported(self):
        _, result = self.imported(values=[("2026-11-17 12:00", 4.0)], maxima={"2026-11-01": 8.0})
        text = describe(result)
        self.assertIn("1 daily totals", text)
        self.assertIn("1 readings of the last days (from 2026-11-01)", text)


if __name__ == "__main__":
    unittest.main()
