# Release checklist

Version numbers are **plain digits** (`0.2.0`, or `0.2.0.1` for a small fix): plugin managers such as PyPluginStore
only recognise plain numeric tags, so no `-alpha`, `-rc` or other suffix, neither in the tag nor in the version.

## 1. Prepare (on the development machine, branch `development`)

- [ ] Decide the version. Bug fix = third segment, new feature = second, a fourth `.N` for a tiny follow-up fix
- [ ] Set it in **all three places** (a test fails when they differ):
  - [ ] `plugin.py`: `version="X.Y.Z"` in the manifest
  - [ ] `rain_stats/__init__.py`: `__version__ = "X.Y.Z"`
  - [ ] `CHANGELOG.md`: top entry `## [X.Y.Z] - YYYY-MM-DD` (rename `Unreleased`), link at the bottom
- [ ] `CHANGELOG.md` lists every user-visible change (Added / Changed / Fixed)
- [ ] `README.md` still true: settings table, `.env` table, device table, troubleshooting messages, "verified / not
      verified", the Domoticz versions it was tried with
- [ ] `ROADMAP.md`: tick what shipped, move what slipped
- [ ] `SECURITY.md` updated if the handling of the password, TLS, the state file or deployment changed
- [ ] New or changed devices or windows: `python3 tools/gen_device_catalog.py` (commit `docs/DEVICES.md`)
- [ ] Changed the counting (`counter.py`, `ring.py`, `archive.py`, `stats.py`): `tests/test_simulation.py` still exact,
      the README section *How the numbers are made* still true
- [ ] **Changed the state file format**: the version number inside it is raised, an older file is still read, and
      `docs` and the tests say so. A state file is the user's data
- [ ] Changed or added a log message that the README troubleshooting table quotes: update the table

## 2. Verify

- [ ] `make check`: tests, flake8, shellcheck, Python 3.7 compatibility, generated docs up to date
- [ ] `make package`, unpack the archive in an empty directory and run `python3 -m unittest discover -s tests` there
      (catches files that are missing from the package)
- [ ] On the Domoticz host: `python3 -m rain_stats --rain-idx <IDX>` reads the rain device (exit status 0) and shows
      what you expect; keep a `--json` copy
- [ ] `./deploy.sh --dry-run`, then `./deploy.sh --host <test host>` on a test Domoticz host
- [ ] In Domoticz: add the hardware, check the log (`Rain Statistics X.Y.Z starting ...`, `Connected to Domoticz ...`,
      no errors), check that the devices of the windows chosen in `.env` exist (18 by default) and `Rain Stats Status` is On
- [ ] Stop Domoticz' web server or use a wrong port: one error line, `Rain Stats Status` goes Off, the windows keep
      sliding; remove the problem: one recovery line, values resume
- [ ] Restart Domoticz: `Loaded the state file ...`, the values are the same as before the restart
- [ ] Over one local midnight: the rain right after the reset is counted (compare with the Domoticz rain graph)
- [ ] With `RS_HISTORY_IDX`: `python3 -m rain_stats --history <IDX>` first, then the log shows `Imported the daily rain
      history ...` once, and after a restart of the hardware the history is **not** read again (Debug level: no
      `graph` request in the log)
- [ ] Switch a window on and off in `.env`: devices are created only for `Y`, none is deleted for `N`, no unit moves
- [ ] Coming from the dzVents script: import its file on a copy, compare the windows with the script's sensors
- [ ] If the plugin directory is now different from before (new module, renamed file): restart Domoticz, not just a
      hardware reload, so Python re-imports everything

## 3. Release (two-branch workflow)

- [ ] On the development copy: commit on `development` (`git commit -am "Release X.Y.Z"`)
- [ ] On the production copy: merge into `master` and push (`git merge development && git push`)
- [ ] Tag `vX.Y.Z` on `master` (digits only) and push the tag; create the GitHub release with the CHANGELOG entry
- [ ] Check that the plugin manager lists the new version

## 4. After the release

- [ ] `./deploy.sh --host <production host>`; keep the previous backup until the new version has run for a day
      (`./deploy.sh --host ... --rollback` restores the code, never the statistics)
- [ ] Answer open issues; add reports from real installations to the verified section of the README
