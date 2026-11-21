# Contributing

Bug reports, reports from real installations (also "it works on my setup...") and pull requests are welcome. The most
valuable contribution right now is **a report from a real Domoticz**: run `python3 -m rain_stats --rain-idx <IDX>
--json` on the Domoticz host, install the plugin, set *Debug* to *Basic*, and open an issue with the Domoticz version
and what the log shows. A rain counter that behaves in a way the tests do not cover (a reset at another time, a
value that jumps) is worth more than any description: it becomes a test.

## Set up

```sh
git clone https://github.com/janreimen/Domoticz-Rainstats.git
cd Domoticz-Rainstats
make test            # python3 -m unittest discover -s tests
make check           # tests + flake8 (pyflakes checks) + shellcheck + Python 3.7 compatibility (vermin) + generated docs
```

The tests need only Python 3 (and the `openssl` command for the TLS tests, which skip themselves without it). They
talk to their own fake Domoticz on `127.0.0.1`; never point them at a real installation.

## Rules

- **No third-party Python packages**, and nothing that needs a newer Python than 3.7 (`make compat`).
- **Only `plugin.py` imports `Domoticz`**; only `devices.py` calls the Domoticz device API (through the object it is
  given). `api.py`, `counter.py`, `ring.py`, `archive.py`, `stats.py`, `store.py`, `legacy.py`, `history.py`, `controller.py`
  and `config.py` never see Domoticz and are tested without it.
- **`plugin.py` must not bind the names Domoticz injects** (`Parameters`, `Devices`, `Images`, `Settings`): the
  injected globals would silently shadow them. A test enforces this. Import modules under another name
  (`from rain_stats import config as rs_config`). `Devices` is replaced by Domoticz before every callback, so the
  device manager reads it through a function (`lambda: Devices`), never through a reference taken at start.
- **The reading runs in a background thread that never touches Domoticz and never logs.** Whatever it has to say
  travels in its result; the plugin thread writes the log, the archive and the devices.
- **Never log secrets.** The password and the header made from it are registered with the logger, which masks them in
  every message. New code must not build log lines that bypass the logger. `tests/test_plugin.py` checks the log at
  Debug level.
- **The device model is frozen.** Units 1 - 8 (rain), 9 - 16 (peak rate), 17 - 18 (status, info), 19 - 22 (rain) and
  23 - 26 (peak rate) are what an installation in the field uses; changing them breaks upgrades. A new device gets a
  new unit and a line in `constants.py`. The plugin key (`Rain-Stats`) is frozen too: Domoticz stores a hardware by it. The windows and the rings are frozen for the same reason; if a ring has to change, an existing
  state file must be converted when it is read (see `Ring.from_dict`), never discarded.
- **Rain is added, never dropped.** No code path may throw away rain that was measured: a rule that leaves rain out
  (a glitch, an implausible jump) needs a test, a log line and a line in the README.
- **Counting changes need the ground truth.** A change in `counter.py`, `ring.py`, `archive.py` or `stats.py` must keep
  `tests/test_simulation.py` exact. If a change makes it fail, the change is wrong, not the simulation.
- **The state file is the user's data.** It is written atomically, never overwritten when it cannot be read, and never
  touched by `deploy.sh`.
- **A problem is logged once, and its recovery once.** Use `Controller._report` / `_recovered`; repeats go to
  Verbose.
- **A failed reading never changes the statistics.** The windows keep sliding with the clock.
- **Domoticz callbacks must return quickly** and must not raise: wrap new callback code like the existing ones.
- The plugin only reads. It does not send anything anywhere.

## Adding a device

1. Add a `Device` row to `FIXED_DEVICES` (or a window to `WINDOWS`, which adds a rain and a peak device and the line
   `RS_LAST_NNN_DAYS` in `.env`) in `rain_stats/constants.py` (the next free unit; never reuse one) and write its value
   in `DeviceManager.show`. A window also needs its line in `.env.sample` and in the README table *Choose the windows*.
2. Add the meaning to `tools/gen_device_catalog.py` and run `python3 tools/gen_device_catalog.py` to regenerate
   `docs/DEVICES.md` (a test fails if you forget).
3. Update the frozen list in `tests/test_devices.py` and add tests for creation and update.
4. Update the README device table and `CHANGELOG.md`.

## Changing the counting

The rules live in `rain_stats/counter.py` (from readings to rain) and `rain_stats/archive.py` (from rain to slots).
Add a case to `tests/test_counter.py`, and if it concerns time, a scenario to `tests/test_simulation.py`. Describe the
rule in the README section *How the numbers are made*.

## Pull requests

- One topic per pull request; keep it small enough to review. Target the `development` branch. `master` only receives
  merges from `development` at a release and always holds the last released version.
- Add or update tests; `make check` must pass.
- Update `CHANGELOG.md` under an `Unreleased` heading. The maintainer sets the version (see `checklist.md`).
- Describe what you tested and with which Domoticz version, and whether against a real rain device or only the fake.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
