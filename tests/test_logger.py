"""The logger: when it touches Domoticz, what it hides, which levels reach the log."""
import unittest

import domoticz_stub as dz
import helpers  # noqa: F401  (paths)
from rain_stats.logger import BASIC, DEBUG, OFF, Logger, MASKED


class Setup(unittest.TestCase):
    def setUp(self):
        dz.reset()

    def calls(self, kind="debugging"):
        return [entry for entry in dz.logs if entry[0] == kind]

    def test_a_new_logger_does_not_call_domoticz(self):
        Logger(dz)
        self.assertEqual(dz.logs, [])                    # plugin.py makes one at import, before Domoticz allows any call

    def test_a_logger_made_with_a_level_tells_domoticz(self):
        Logger(dz, DEBUG)
        self.assertEqual(self.calls(), [("debugging", 1)])

    def test_the_level_is_told_once_and_only_when_it_changes(self):
        log = Logger(dz)
        log.set_level(OFF)
        log.set_level(OFF)
        self.assertEqual(self.calls(), [("debugging", 0)])       # the first time: Domoticz is told, even for Off
        log.set_level(BASIC)
        log.set_level(DEBUG)                                      # still on: nothing to tell
        self.assertEqual(self.calls(), [("debugging", 0), ("debugging", 1)])
        log.set_level(OFF)
        self.assertEqual(self.calls(), [("debugging", 0), ("debugging", 1), ("debugging", 0)])

    def test_a_level_that_is_not_a_number_means_off(self):
        log = Logger(dz)
        log.set_level("loud")
        self.assertEqual(log.level, OFF)

    def test_a_refusal_of_domoticz_is_an_error_and_not_a_crash(self):
        class Refusing:
            @staticmethod
            def Debugging(flag):
                raise RuntimeError("nope")

            @staticmethod
            def Error(text):
                dz.logs.append(("error", text))

        Logger(Refusing, BASIC)
        self.assertTrue(any("could not configure Domoticz debugging: nope" in t for _, t in dz.logs))


class Output(unittest.TestCase):
    def setUp(self):
        dz.reset()

    def test_levels_decide_what_reaches_the_log(self):
        log = Logger(dz, BASIC)
        log.Basic("a")
        log.Verbose("b")
        log.Debug("c")
        log.Log("d")
        log.Error("e")
        self.assertEqual([t for _, t in dz.logs if _ in ("debug", "log", "error")], ["[BASIC] a", "d", "e"])

    def test_a_secret_never_reaches_the_log(self):
        log = Logger(dz, DEBUG)
        log.add_secret("hunter2hunter2")
        for write in (log.Basic, log.Verbose, log.Debug, log.Log, log.Error):
            write("password is hunter2hunter2")
        texts = [t for kind, t in dz.logs if kind in ("debug", "log", "error")]
        self.assertEqual(len(texts), 5)
        self.assertTrue(all("hunter2" not in t and MASKED in t for t in texts))

    def test_a_short_secret_is_not_registered(self):
        log = Logger(dz, DEBUG)
        log.add_secret("ab")
        log.Log("about")
        self.assertEqual([t for kind, t in dz.logs if kind == "log"], ["about"])


if __name__ == "__main__":
    unittest.main()
