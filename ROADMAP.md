# Roadmap

Planned work, roughly in order. Nothing here is a promise; it is what would make the plugin better.

## First: check against reality

The plugin was developed without a running Domoticz (see [what is verified](README.md#what-is-verified-and-what-is-not)).
Before anything else:

- [ ] Run `python3 -m rain_stats --find` and `--rain-idx <IDX>` on the Domoticz host against the real rain device;
      keep the `--json` output
- [ ] Watch the `Rain` value of the weather station over one local midnight: does it reset at 00:00 as the *daily*
      counter assumes, and how often does the station push a new value into Domoticz?
- [x] Add the plugin as hardware (done 2026-11-19 in Domoticz 2026.4: the devices exist, `Rain 24 h` (now `Rain 1 d`)
      updates and passes midnight cleanly)
- [ ] Look at the log (`Rain Statistics 0.0.3 starting ...`, `Windows: ...`, `Connected to Domoticz ...`, no errors) and
      at all 18 devices, the peak, status and info ones included
- [ ] Run `python3 -m rain_stats --history <IDX>` against the real rain device and compare its days with the *Last
      year* graph of Domoticz; then set `RS_HISTORY_IDX`, check the log line `Imported the daily rain history ...`,
      restart the hardware and check that the history is **not** read again
- [ ] Switch a window on that is off by default (`RS_LAST_060_DAYS=Y`): its two devices appear, no other unit moves
- [ ] Import the data file of the dzVents script (`RS_LEGACY_FILE`) and compare the windows with the script's sensors
      for a week; explain every difference
- [ ] Run it for a month; look at the memory use, the size of the state file and the log
- [x] Add the Domoticz versions it was tried with to the README (2026.4)
- [ ] Save a real `--json` output as a test fixture

## 0.2

- [ ] A **rain rate from the counter** with a window tied to the station's reporting interval, if the station does not
      deliver a rate that is good enough
- [ ] Optional: *this month* and *this year* (calendar windows) next to the sliding ones
- [ ] Optional: the time of the highest peak rate, in *Rain Stats Info*
- [ ] Optional: days without rain (a counter of dry days in a row)

## Later

- [ ] **More than one weather station**: a state file per hardware (named after the hardware ID), so that two
      instances do not share one archive. Today the plugin supports one hardware
- [ ] **An MQTT source** (the weathercloud2mqtt topics) as an alternative to the Domoticz JSON API, for installations
      where the rain device is not in Domoticz or the API is not reachable
- [ ] Listing the plugin in a plugin manager
- [ ] A dzVents example for a notification when the 1 d rain passes a threshold, tried on a real installation
- [ ] Hours in the history: the 5-minute and the hourly graph of Domoticz (`range=day`, `range=month`) could place the
      rain of the last weeks better than an even spread over the day

## Not planned

- Sending anything anywhere: the plugin only reads.
- Changing a window or its unit: the 12 windows and their unit numbers are frozen, so scripts and scenes keep working.
  A new length is a new row, with new units.
- Python packages: the plugin stays standard-library only.
