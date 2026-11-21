# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are plain numbers
(`MAJOR.MINOR.PATCH`, optionally a fourth `.N` for small fixes) so that release tags work with plugin managers
such as PyPluginStore.

## [0.0.2] - 2026-11-21

The first release of this repository. It replaces the dzVents script *RainStats V2.6*. An earlier build (internally
called 0.1.0, never published) has run in the author's Domoticz 2026.4 since 2026-11-19; this version carries the
changes that came out of that run, see [what is verified and what is not](README.md#what-is-verified-and-what-is-not).

### Added
- **You choose the windows.** One line per window in `.env`, `RS_LAST_001_DAYS=Y` ... `RS_LAST_365_DAYS=N`, for
  1, 2, 7, 14, 24, 30, 60, 90, 120, 180, 360 and 365 days. A window that is switched off gets neither its rain device
  nor its peak rain rate device: the devices are only created when the line says `Y`. The first eight windows of the
  early build (1, 2, 7, 14, 30, 90, 180 and 365 days) are `Y` by default, so an installation without the lines keeps
  what it had. A device that exists is never deleted by the plugin.
- **A one-time history set-up.** `RS_HISTORY_IDX=<IDX of a rain device>` makes the plugin read the daily rain that
  Domoticz keeps of that device for the last year (the *Last year* graph) **once**, and add it to the days before the
  plugin started collecting, so the long windows are complete from the first day. The days the plugin collected itself
  are never overwritten. The state file records that it was done and the device is never asked again, however often
  Domoticz or the hardware is restarted; only a deleted state file brings the import back. The history has days, not
  hours, so it is spread evenly over each day, and it has no rain rates (the peak rates stay partial). The command line
  tool shows what Domoticz would answer: `python3 -m rain_stats --history <IDX>`.
- **`Rain Stats Info` says when a window is partial** and since when the history reaches back.
- **Statistics over 1, 2, 7, 14, 24, 30, 60, 90, 120, 180, 360 and 365 days**, stored round-robin: a fine ring of 2016
  slots of 5 minutes (a week) answers the windows up to 7 days, a coarse ring of 35040 slots of 15 minutes (a year)
  the longer ones. Every reading is added to both. Rain is summed, the rain rate is kept as a maximum, per slot.
- **A window is the last N days, not a count of whole slots.** The rain of a window is its whole slots plus the slot
  at its edge in proportion to the part that lies inside the period (rain is taken to be even within a slot), so rain
  fades out of a window through one slot instead of leaving it in one step. The rain rings keep one slot more for
  this. On a simulated year the windows have no bias against an exact rolling sum, and the largest difference at a
  single moment is 0.24 mm (24 h) to 1.16 mm (30 d), where whole slots alone were up to 2.5 mm (24 h) and 29.5 mm
  (180 d) too low. The peak rate stays made of whole slots: it never reaches further back than the window is long.
- **A state file of an early build is upgraded**: a coarse ring saved with 1-hour slots is converted to 15-minute
  slots when the file is read (the rain of an hour is spread evenly over its quarters, the peak is kept in each of
  them); no rain is lost and the file is not moved aside.
- **Devices:** `Rain 1 d` ... `Rain 365 d` and `Peak Rain Rate 1 d` ... `365 d` (Custom Sensors, mm and mm/h; the peak
  devices are not created with the setting *Statistics* = *Rain only*), `Rain Stats Status` (unit 17) and
  `Rain Stats Info` (unit 18). Created *in use*, with their unit; read-only. The unit numbers are frozen: units 1-16
  are the eight default windows, 19-26 the four that are off by default.
- **A source of any weather station:** the plugin reads a device of Domoticz through the JSON API (counter and rain
  rate of a *Rain* device, or the value of a sensor that shows mm; optionally a separate rate device).
- **Counters that reset at midnight ("rain today") and counters that never do ("total")**, with the handling of
  resets, glitches, sensor restarts, implausible jumps and readings that go back in time.
- **The windows slide with the clock**, also while it is dry and while the source cannot be read.
- **A state file** (`rainstats_state.json`) written atomically, at most every 15 minutes and when the plugin stops;
  a file that cannot be used is moved aside, never overwritten.
- **One-time import of the data file of the dzVents script RainStats V2.6** (`RS_LEGACY_FILE`).
- **`python3 -m rain_stats`** to read the rain device, list the rain devices, read the history, show a state file and
  show what an import would give. It changes nothing.
- Settings in the hardware page and in `.env` (`RS_SCHEME`, `RS_CA_FILE`, `RS_HTTP_TIMEOUT`, `RS_STATE_FILE`,
  `RS_SAVE_INTERVAL`, `RS_REFRESH_INTERVAL`, `RS_RATE_DIVISOR`, `RS_MAX_DELTA`, `RS_LEGACY_FILE`, `RS_HISTORY_IDX`,
  `RS_LAST_..._DAYS`).
- Tests (including a ground-truth simulation of a year of weather and a fake Domoticz on a real HTTP and TLS
  server), `make check`, `deploy.sh`, GitHub workflow, documentation.

### Changed
- **The plugin key is `Rain-Stats`** (it was `RainStats` in the early build) and the repository is
  `Domoticz-Rainstats`. Domoticz stores a hardware by its plugin key, so an installation of the early build has to
  be moved over once: see [DEPLOY.md](DEPLOY.md#7-coming-from-the-early-build-key-rainstats).
- New devices are named `Rain 1 d`, `Rain 2 d`, `Rain 7 d` (the early build called them `Rain 24 h`, `Rain 48 h`,
  `Rain 168 h`): all windows are counted in days. The units and what they show are the same, and the plugin never
  renames a device, so an installation of the early build keeps the names it has.

### Differences from the dzVents script RainStats V2.6
- **Rain right after the midnight reset is counted.** The script took a negative difference of the counter for 0, so the
  rain between the reset and the first reading of the new day was lost.
- **Nothing is thinned out.** The script cut its list down to one entry per hour (up to 30 days) and one per day
  (older) and did not add the rain of the entries it removed to the ones it kept.
- **30 d, 180 d and 365 d come from the same slots as the other windows**, not from the highest counter value of each day.
- **The rain rate is used**: peak per window.
- **A source that cannot be read does not freeze the numbers**, and it is logged once.

[0.0.2]: https://github.com/janreimen/Domoticz-Rainstats/releases/tag/v0.0.2
