# Domoticz-Rainstats

A [Domoticz](https://www.domoticz.com) plugin that turns the rain counter of your weather station into **statistics
over the last 1, 2, 7, 14, 30, 60, 90, 120, 180, 360 and 365 days**, of which you choose the ones you want: how
much it rained, and how hard it rained at most. The numbers are kept in a **round-robin archive** of fixed size that
survives restarts. It replaces the dzVents script *RainStats V2.6* and can take over its data.

* **Rain per window.** One device per window you switch on in `.env`, `Rain 1 d` ... `Rain 365 d`, in mm (eight by
  default).
* **Peak rain rate per window.** One more per window, `Peak Rain Rate 1 d` ... `365 d`, in mm/h (switch them off with
  the setting *Statistics*).
* **You choose the windows.** `RS_LAST_001_DAYS=Y` ... `RS_LAST_365_DAYS=N` in `.env`: a device is only created for a
  window that says `Y`.
* **A history, read once.** `RS_HISTORY_IDX` makes the plugin read the daily rain that Domoticz kept of a rain device
  over the last year, **once**, so the long windows are complete from the first day. It is never read again.
* **Round-robin.** Two rings of fixed size (5-minute slots for a week, 15-minute slots for a year). Nothing grows and
  nothing is trimmed; the oldest slot is overwritten by the newest. Rain is **added** to the slots, never thrown away
  when old data is thinned out.
* **A counter reset never loses or duplicates rain.** A rain counter that starts again at midnight ("rain today"), or
  one that never does ("total"), is handled; the rain that fell right after a reset is counted.
* **The windows slide while it is dry.** They are recomputed every minute, so rain leaves a window on time even when
  no new reading arrives, or the sensor is down.
* **Takes over the data of the old script.** `RS_LEGACY_FILE` imports `rain_stats_data.json` once.
* **No account, no Python packages.** Standard library only, Python 3.7 or newer. Read-only.
* **Quiet.** A problem is logged once and its recovery once; a rain device that cannot be read is asked less and
  less often and never changes the statistics.
* **Modular.** Counter logic, round-robin rings, statistics, state file, API client, settings, device layer and
  polling are separate modules; only `plugin.py` touches Domoticz.

> **Status:** version 0.0.3. Developed against a **simulated** Domoticz (a real HTTP and TLS server inside the test
> suite) and a stub of the plugin API. Since the evening of **2026-11-19** it runs in the author's **Domoticz 2026.4**:
> days of use, not months. The counting logic is checked against a ground-truth simulation of a year of weather; the
> rest of the Domoticz side is checked against the documentation and the source of Domoticz. Read
> [what is verified and what is not](#what-is-verified-and-what-is-not) before you rely on it, and please report
> what you see.

Contents: [Requirements](#requirements) · [Install](#install) · [Check the rain device](#check-the-rain-device) ·
[Settings](#settings) · [Devices](#devices) · [How the numbers are made](#how-the-numbers-are-made) ·
[The one-time history set-up](#the-one-time-history-set-up) · [Replacing the dzVents script](#replacing-the-dzvents-script) · [How it works](#how-it-works) ·
[Privacy and security](#privacy-and-security) · [Troubleshooting](#troubleshooting) · [FAQ](#faq) ·
[Verified](#what-is-verified-and-what-is-not) · [Development](#development) · [Credits](#credits) · [License](#license)

## Requirements

| Where | What |
|---|---|
| Domoticz host | Domoticz with Python plugin support, **Python 3.7+** |
| Rain device | a device in Domoticz of type **Rain** (rain in mm, and normally the rain rate), or a sensor that shows rain in mm |
| Domoticz API | the plugin asks Domoticz itself for the device, over HTTP, normally at `127.0.0.1:8080` (see [why](#how-it-works)). If the API needs a login for local requests, give the plugin a user |

## Install

```sh
cd /srv/domoticz/plugins                  # your Domoticz plugin directory, e.g. ~/domoticz/plugins
git clone https://github.com/janreimen/Domoticz-Rainstats.git
sudo systemctl restart domoticz
```

`plugin.py` and the `rain_stats/` directory must stay together. The directory name does not matter to Domoticz (the
plugin is identified by its `key`, `Rain-Stats`). Use one directory per plugin; do not leave an old copy next to it.
Coming from an early build, which had the key `RainStats`? The hardware has to be moved to the new key once:
[DEPLOY.md, section 7](DEPLOY.md#7-coming-from-the-early-build-key-rainstats).

Or use the bundled [`deploy.sh`](DEPLOY.md), which installs locally or over SSH, checks the syntax of the new files,
makes a backup, restores ownership and restarts Domoticz. It never touches your statistics:

```sh
./deploy.sh --host user@my-domoticz-host
```

Then, if you want other windows than the default eight, edit `.env` ([choose the windows](#choose-the-windows)). Then
*Setup > Hardware*, type **Rain Statistics**, fill in the **Rain device IDX** (*Setup > Devices*, column *Idx*), *Add*.
The devices appear within a few seconds. Full instructions, updates, rollback and removal:
**[DEPLOY.md](DEPLOY.md)**.

## Check the rain device

Before you add the hardware, and whenever the devices show something you do not expect:

```sh
cd /srv/domoticz/plugins/Domoticz-Rainstats
python3 -m rain_stats --find                      # the rain devices of Domoticz and their IDX
python3 -m rain_stats --rain-idx 1161             # what the plugin reads from the device and makes of it
python3 -m rain_stats --rain-idx 1161 --json      # the same as JSON (for a bug report)
python3 -m rain_stats --history 1161              # what the one-time history set-up would read and add (nothing is written)
python3 -m rain_stats --state                     # the statistics in the state file of the plugin
python3 -m rain_stats --legacy /path/to/rain_stats_data.json    # what the import of the old script's file would give
```

It uses the same code as the plugin and **changes nothing**: no state file is written, moved or created. The password
for Domoticz is read from the environment variable `RS_PASSWORD`, never from an argument (the process list shows
arguments). Exit status 0 = worked, 1 = wrong option or unreadable file, 2 = Domoticz cannot be read (the message says
why). Details of what is asked of Domoticz: [docs/DOMOTICZ-API.md](docs/DOMOTICZ-API.md).

## Settings

| Field | Meaning |
|---|---|
| Domoticz address | The machine that runs Domoticz. Default `127.0.0.1`. A host name or an IP address, without `http://` |
| Domoticz port | Default `8080` |
| Domoticz user (optional) | Only when the Domoticz API asks for a login for requests from this machine. Leave empty otherwise. With a user, use `https` (see below): the password is otherwise sent in clear text |
| Domoticz password (optional) | The password of that user. Never written to the log |
| Rain device IDX | The IDX of the rain device (*Setup > Devices*, column *Idx*). Required. A device of type *Rain* gives the counter and the rain rate; a Custom Sensor or another sensor whose value is rain in mm gives the counter only |
| Rain rate device IDX (optional) | The IDX of a separate device whose value is the rain rate in mm/h. Empty = the rate comes from the rain device. When the source has no rate at all, the plugin derives one from the rain between two readings that are at most 30 minutes apart |
| Update Interval (seconds) | Seconds between readings. Default 60, allowed 10 to 3600; other values are corrected and the log says so. A weather station that reports every 5 minutes gains nothing from a shorter interval, except a finer rain rate |
| Rain counter | `Resets at midnight (rain today)` (default): the counter is the rain of today, as Domoticz shows it for a *Rain* device. `Never resets (total)`: a counter that only grows |
| Statistics | `Rain and peak rain rate` (default) or `Rain only` (no peak devices are created or updated; existing ones stay and keep their last value, delete them by hand) |
| Debug | `Off` (default), `Basic` (what happens when), `Verbose` (every reading and what it added), `Debug` (every request and every device update). Secrets never appear in the log, at any level |

**Add the hardware once.** The archive is one file per plugin directory (`rainstats_state.json`), so a second hardware
of this type would share it with the first and the two would overwrite each other's rain. Statistics for a second
weather station are on the [roadmap](ROADMAP.md).

### Technical settings: `.env`

Settings that are rarely changed go into a file named `.env` next to `plugin.py`: copy
[`.env.sample`](.env.sample) and remove the `#` of the lines you want. A variable of the same name in the environment
of the Domoticz service wins over the file; a mistyped name or a bad value is reported in the log, a bad value stops the
plugin with a message that names the key. Changes take effect when the hardware is restarted. `deploy.sh` leaves your
`.env` alone.

| Key | Default | Meaning |
|---|---|---|
| `RS_SCHEME` | http | `http` or `https` for the Domoticz API. For https the certificate is always verified |
| `RS_CA_FILE` | (none) | A certificate authority file (PEM) to trust in addition to the public ones, for https with your own CA |
| `RS_HTTP_TIMEOUT` | 10 | Seconds to wait for an answer of Domoticz (3-60) |
| `RS_STATE_FILE` | `rainstats_state.json` beside `plugin.py` | Where the archive is kept |
| `RS_SAVE_INTERVAL` | 900 | The state file is written at most this often, in seconds (60-86400), and always when the plugin stops. At most this much rain data is lost when Domoticz is killed without a clean stop |
| `RS_REFRESH_INTERVAL` | 300 | An unchanged value is rewritten this often, in seconds (30-86400) |
| `RS_RATE_DIVISOR` | 1 | Divides the rate of the rain device (1-1000). Domoticz answers mm/h, so 1 is right; use 100 only when the rate you see in `python3 -m rain_stats` is 100 times too high |
| `RS_MAX_DELTA` | 100 | More rain than this, in mm (1-1000), between two readings is taken for a sensor error: nothing is counted and the new value becomes the baseline |
| `RS_LEGACY_FILE` | (none) | The data file of the dzVents script, imported once. See [Replacing the dzVents script](#replacing-the-dzvents-script) |
| `RS_HISTORY_IDX` | (none) | The IDX of a rain device whose daily history Domoticz keeps; read once. See [the one-time history set-up](#the-one-time-history-set-up) (1-999999999) |
| `RS_LAST_001_DAYS` ... `RS_LAST_365_DAYS` | Y or N, see below | Which windows get devices. See [Choose the windows](#choose-the-windows) |

### Choose the windows

`.env` has one line per window. `Y` means: the plugin creates the rain device of that window (`Rain 30 d`) and, unless
the setting *Statistics* is *Rain only*, its peak rain rate device, and keeps them up to date. `N` means: it creates
neither.

| Line | Window | Default | Units (rain, peak) |
|---|---|---|---|
| `RS_LAST_001_DAYS` | 1 day (24 h) | Y | 1, 9 |
| `RS_LAST_002_DAYS` | 2 days (48 h) | Y | 2, 10 |
| `RS_LAST_007_DAYS` | 7 days (168 h) | Y | 3, 11 |
| `RS_LAST_014_DAYS` | 14 days | Y | 4, 12 |
| `RS_LAST_030_DAYS` | 30 days | Y | 5, 13 |
| `RS_LAST_060_DAYS` | 60 days | N | 19, 22 |
| `RS_LAST_090_DAYS` | 90 days | Y | 6, 14 |
| `RS_LAST_120_DAYS` | 120 days | N | 20, 23 |
| `RS_LAST_180_DAYS` | 180 days | Y | 7, 15 |
| `RS_LAST_360_DAYS` | 360 days | N | 21, 24 |
| `RS_LAST_365_DAYS` | 365 days | Y | 8, 16 |

Without a `.env`, or without a line, the default of the table is in force, which is the eight windows of the first
version. A value other than `Y` or `N` (`yes`, `no`, `true`, `false`, `1`, `0`, `on` and `off` work too, in any case) stops the plugin with a
message that names the line, and so does a `.env` that switches every window off. The unit numbers are fixed per window,
so switching a window on or off never moves another device. **A device that exists is never deleted**: when you set a
window to `N`, its devices stay in Domoticz, keep their last value and are no longer updated; delete them in *Setup >
Devices*. Set it to `Y` again and they are updated again. Changes take effect when the hardware is restarted.

## Devices

18 devices are created by default (the eight default windows, two devices each, and two status devices); with other
`RS_LAST_..._DAYS` lines there are more or fewer. The full list with types and meaning is in
**[docs/DEVICES.md](docs/DEVICES.md)** (generated from the code).

| Unit | Name | Shows |
|---:|---|---|
| 1-8 | Rain 1 d, 2 d, 7 d, 14 d, 30 d, 90 d, 180 d, 365 d | the rain in the window, in mm (one decimal) |
| 9-16 | Peak Rain Rate 1 d ... 365 d | the highest rain rate seen in the window, in mm/h (two decimals) |
| 17 | Rain Stats Status | On while the rain device can be read, Off while it cannot |
| 18 | Rain Stats Info | the last reading, the rain rate, since when data are collected, and which windows are still partial |
| 19-21 | Rain 60 d, 120 d, 360 d | only with `RS_LAST_060_DAYS=Y` and so on |
| 22-24 | Peak Rain Rate 60 d, 120 d, 360 d | the same, and not with *Rain only* |

All devices are read-only; a command is answered with the real state. They are created *in use*, so nothing has to be
added by hand, and the sensors carry their unit (`mm`, `mm/h`) so that Domoticz draws the right axes. Domoticz names
them `<hardware name> - <device name>`, for example `Rain Statistics - Rain 1 d`. The unit numbers never change, so
scripts and scenes keep working across updates. (The early build named units 1-3 `Rain 24 h`, `Rain 48 h` and
`Rain 168 h`: the same windows. The plugin never renames a device, so those names stay.)

## How the numbers are made

The rain device gives a **counter**. The plugin turns successive readings into *rain that fell*, and adds that rain
to a round-robin archive:

```
reading ──> counter logic ──> rain between two readings ──> spread over the time between them
                                                                  │
                          ┌───────────────────────────────────────┴───────────────┐
                          ▼                                                       ▼
        fine ring: 2016 slots × 5 min = 7 days            coarse ring: 35040 slots × 15 min = 365 days
        answers 1 d, 2 d, 7 d                             answers 14 d, 30 d, 60 d, 90 d, 120 d, 180 d, 360 d, 365 d
```

Each ring comes twice: one holds the **sum of the rain** per slot, one the **highest rain rate** per slot.

* **The rain of a window is the last N hours or days.** It is the slot that is running now, the slots before it that
  make up the window, and the part of the slot before those that still lies inside the period, in proportion to how much
  of it does (the *edge*). That part assumes the rain of a slot fell evenly within it. The rain rings keep one slot more
  than the table above, for the edge.
* **The peak rate of a window is made of whole slots**: the running one plus the ones before it. A peak cannot be taken
  in proportion, so it covers between N-1 and N slots of real time and **never reaches further back than the name
  says**. It is the maximum of its slots, not an average.

* **A counter that resets at midnight.** A drop of the counter shortly after local midnight (or since the day of the
  previous reading) is a reset: the new value is the rain since the reset. The rain of the first minutes of a new day
  is therefore counted. A drop in the middle of the day is a glitch: nothing is counted and the new value becomes the
  baseline.
* **A counter that never resets.** A drop is a restart of the sensor: the new value is the baseline.
* **The first reading.** A *daily* counter has already counted the rain of today, so it is credited, spread from local
  midnight to now. A *total* counter says nothing about the past, so nothing is credited.
* **Rain is spread over the time between two readings**, so a reading after a long gap does not put all the rain
  into one slot. Daylight saving time is handled (the simulation in the tests crosses both changes).
* **Partial windows.** As long as the plugin knows less time than a window is long, the window holds the rain of the
  time that is known, and *Rain Stats Info* says so. After a year no window is partial. With the
  [history set-up](#the-one-time-history-set-up) the rain is known from the start of the history, so the rain windows
  are complete from the first day; the peak rain rates are not (the history has no rates) and say so.

What the counting cannot know, and where it is wrong **by design**:

* **The edge of a window is an estimate.** The slot at the edge of a window (5 minutes for 1 d, 2 d and 7 d,
  15 minutes for 14 d and longer) is counted in proportion to the part that lies inside the period, so a storm fades
  out of a window through one slot instead of dropping out at once. That is exact when the rain of the slot fell evenly,
  and wrong by up to the rain of that one slot when it did not (a downpour in the first minute of a slot leaves the
  window at the start of the slot's time, not at the end). On a simulated year (383 days, 1-minute readings, 1,099
  random moments, 1,510 mm of rain) the windows had **no bias** against an exact rolling "last N hours" sum (the average
  difference was below 0.001 mm for every window) and the largest difference at a single moment was 0.24 mm (24 h),
  0.16 mm (14 d), 1.16 mm (30 d) and 0.35 mm (180 d). The same simulation with the windows made of whole slots, without
  the edge, was up to 2.5 mm (24 h), 20.7 mm (14 d), 29.0 mm (30 d) and 29.5 mm (180 d) too low at a single moment.
  Real rain is more clustered than the simulated rain, so read these figures as "tenths of a millimetre, rarely more",
  not as a guarantee. The peak rate has no edge: it is made of whole slots, up to one slot shorter than its name.
* The rain between the last reading before midnight and midnight itself is invisible to a *daily* counter (it is
  reset before anyone reads it). That is at most one polling interval; and no more often than your weather station
  pushes its data into Domoticz.
* After a gap in which the plugin did not run, a new-day reading that is higher than the last value of the day before
  credits only the increase.
* The peak rate is the highest rate Domoticz showed at a reading. A shower between two readings is not seen.

## The one-time history set-up

A new installation knows nothing about the rain before its first reading, so the long windows start small and grow for
a year. If Domoticz has kept the daily rain of a rain device (every Domoticz does for a device of type *Rain*: it is
the *Last year* graph of the device), the plugin can read those days **once** and complete its statistics with them.

**How to use it, exactly.**

1. Find the IDX of the rain device whose history you want (*Setup > Devices*, column *Idx*, or
   `python3 -m rain_stats --find`). It can be the rain device of the hardware page or another one (for example an older
   sensor that was replaced).
2. Look at what Domoticz would give and what the windows would say, without changing anything:

   ```sh
   python3 -m rain_stats --history 1161
   ```

3. Write the IDX into `.env` (next to `plugin.py`), together with the windows you want. The history is used for every
   window **longer than 1 day** that is `Y`:

   ```
   RS_HISTORY_IDX=1161
   RS_LAST_001_DAYS=Y
   RS_LAST_002_DAYS=Y
   RS_LAST_007_DAYS=Y
   RS_LAST_014_DAYS=Y
   RS_LAST_030_DAYS=Y
   RS_LAST_060_DAYS=N
   RS_LAST_090_DAYS=Y
   RS_LAST_120_DAYS=N
   RS_LAST_180_DAYS=Y
   RS_LAST_360_DAYS=N
   RS_LAST_365_DAYS=Y
   ```

4. Add the hardware (or restart it). Best **before the first start**: the import completes the days *before* the
   plugin's own data, and those begin at the plugin's first reading. (Added later, it fills the days before the data
   the plugin has collected itself, so nothing is lost either.)
5. Check the log (*Setup > Log*): `Reading the daily rain history of IDX 1161 (once)`, then
   `Imported the daily rain history of IDX 1161: N days from DATE to DATE, X mm`, then
   `The rain of the windows longer than 1 day after the import: 2 d ... mm, 7 d ... mm, ...`: the rain of every
   active window above 1 day, as the statistics hold it now.
6. Leave the line in `.env`: it does no harm. The state file says that the history was read, so it is not read again.

**What happens.**

1. The plugin starts as usual and makes its first reading. Only then it knows where its own data begin.
2. It asks Domoticz **one** question, in the background: `type=command&param=graph&sensor=rain&idx=1161&range=year`,
   the daily rain totals from the same date a year ago to today (365 finished days and today). The log says
   `Reading the daily rain history of IDX 1161 (once)`. It does so only when a window longer than 1 day is switched
   on; with only the 1-day window on, nothing is read, the log says so once, and the history is read, once, at the first
   start after a longer window is switched on.
3. Every finished day **before the first midnight of the data the plugin collected itself** is added to the archive.
   Days the plugin knows itself are never touched or doubled. The day the plugin started is its own: the history has
   no hours, so it is not used for that day.
4. The state file is written at once and carries a marker with the IDX, the days and the time. The log says
   `Imported the daily rain history of IDX 1161: N days from DATE to DATE, X mm (...)` and, in the next line, the rain
   of every active window above 1 day (`The rain of the windows longer than 1 day after the import: 2 d 12.3 mm, ...`).
5. **From then on the device is never asked again**: not at the next heartbeat, not after a restart of the hardware or
   of Domoticz, as long as the state file holds the data (the statistics are the values in the devices; the state file is
   where they live). A later start only says once, at Basic level, that `RS_HISTORY_IDX is set, but the history of IDX
   1161 (N days from DATE) is in the state file: it is not read again`. The same holds when Domoticz had finished days
   but the plugin knew every one of them already (it ran for a year, or the old script's file covers them): nothing is
   added, the marker is written all the same (`... was read before: the plugin had its own data for every day, nothing
   was added. It is not read again`). Only a deleted state file (see the FAQ, *Can I reset the statistics?*) brings the
   import back, because then there is nothing left to complete.

**What it can and cannot do.**

* **A day is a day.** The history has one total per day, no hours, and Domoticz states it with one decimal (a sensor
  that counts in tenths of a millimetre loses nothing by that). Each day is spread evenly over its 24 hours, so a window
  adds the whole days it covers exactly, and counts the one day at its edge in proportion to the part that lies
  inside. **That one day is an estimate**: wrong by at most the rain of that day, and by less when the rain was
  spread evenly. For the windows of 2 and 7 days that is a noticeable part of the total, for 90 days and more a small
  one. Every day that passes replaces it by the plugin's own 5-minute data: a window is exact once the plugin has
  collected for as long as the window is long. *Rain Stats Info* says `history from DATE`, and `windows from ... are
  partial` for what is still short of data.
* **The window of 1 day** is not made from the history: only the part of it that lies in the last day before the plugin
  started (the evening of yesterday) is. For a *daily* counter the amount of rain of today comes from the counter itself, exactly.
* **No peak rain rates.** The history has no rates, so the peak devices only know the time the plugin has run.
  *Rain Stats Info* says `peaks from ... are partial`.
* **A year.** Domoticz keeps one year of days. A 365 d window is complete from the first day the history covers; the
  plugin cannot invent more.
* **The counter of the day of the start.** The rain already counted that day by a daily counter is credited at the
  first reading, as always (see *The first reading*).
* **If Domoticz cannot answer**, the log says why once (`Reading the rain history failed: ...`). A network problem
  is tried again at the next reading; a refusal (wrong IDX, a Domoticz that does not know the graph command, an answer that
  makes no sense) is not repeated until the next start. The statistics are not affected either way.
* **If Domoticz has no finished day at all** (a new device, a wrong IDX), nothing is written and the history is *not*
  marked as done, so it is asked again at the next start: a wrong IDX never costs you the one chance.
* **Together with `RS_LEGACY_FILE`** the old script's data are read first, and the history fills only the days before the
  first day of that file. Use the history alone unless you want the hourly data of the script's last 7 days.
* It reads, it never writes to Domoticz. It uses the same address and login as the rain device; the request is limited
  in size like every other.

## Replacing the dzVents script

The script *RainStats V2.6* kept its state in `rain_stats_data.json` and wrote custom sensors. What the plugin does
differently, and why the numbers of the two can differ:

| | dzVents script V2.6 | this plugin |
|---|---|---|
| Rain right after the midnight reset | thrown away (a negative difference counts as 0) | counted |
| Old data | thinned out at every save; the rain of the removed entries was dropped, not added to the ones kept | added to the slot it belongs to; nothing is removed |
| 30 d, 180 d, 365 d | built from the highest counter value of each day | from the same 15-minute slots as 14 d, 90 d |
| Rain rate | not used | peak per window |
| A source that cannot be read | the numbers stop | the windows keep sliding |

**To take the old data over:**

1. Install the plugin ([Install](#install)), but do not add the hardware yet.
2. In `.env`, set the path of the script's file, for example
   `RS_LEGACY_FILE=/srv/domoticz/scripts/dzVents/data/rain_stats_data.json` (the path is the one the script wrote to).
   Check what would be imported: `python3 -m rain_stats --legacy /path/to/rain_stats_data.json`.
3. Add the hardware with the IDX of the same rain device the script read, and *Rain counter* = *Resets at midnight*.
4. The log shows `Imported the data file of RainStats V2.6: N daily totals, M readings of the last days (from ...)`. The
   plugin writes its own state file at once; from now on the import is skipped (the log says so if the line is still in
   `.env`). The script's file is **never changed**.
5. The new devices have **new IDX numbers**. Point your dashboards, notifications and scripts at them, then disable
   the dzVents script. Nothing in Domoticz is deleted for you; the old custom sensors stay until you remove them.

What is imported: the complete part of the script's list (the last full days before its last save, where it had not yet
thinned anything out), and the daily maxima for the days before that, **spread evenly over each day** (the script did
not know when in the day it rained). The counter baseline is taken over, so the rain between the script's last run
and the plugin's first reading is not lost.

## How it works

```
plugin.py               Domoticz glue only: manifest, callbacks, wiring
rain_stats/
    constants.py        the frozen device model, windows, rings, limits
    counter.py          counter readings -> rain that fell (pure logic)
    ring.py             one round-robin ring of fixed size, saved sparsely
    archive.py          the rain and peak rings of both tiers
    stats.py            the windows from the archive
    store.py            the state file: atomic writes, a bad file is moved aside
    legacy.py           the one-time import of the dzVents script's file
    history.py          the one-time import of the daily rain history of a Domoticz rain device
    api.py              Client: the Domoticz JSON API over http(s)
    config.py           Settings: the hardware page and the .env file, validated
    controller.py       polling in a background thread, back-off, log once, saving
    devices.py          DeviceManager: the only module that calls the Domoticz device API
    logger.py           four log levels, secrets masked
    model.py, util.py   small data types and helpers
    cli.py              python3 -m rain_stats
docs/                   DEVICES.md (generated), DOMOTICZ-API.md
tests/                  unit tests, a ground-truth simulation, a fake Domoticz API (HTTP and TLS), a plugin stub
tools/                  gen_device_catalog.py
```

* **Why the JSON API.** A plugin only sees the devices of its own hardware, so it cannot read another hardware's rain
  device through the plugin interface. It asks Domoticz over HTTP, like a browser would:
  `/json.htm?type=command&param=getdevices&rid=<idx>`; once, for the history set-up, also
  `type=command&param=graph&sensor=rain&idx=<idx>&range=year`. The requests go to the address of the hardware settings
  and never through a proxy.
* **Heartbeat 10 s, polling by time.** Domoticz logs a plugin as "not responding" when its heartbeat is longer than
  30 seconds, so the heartbeat stays at 10 s and the plugin reads when the interval has elapsed.
* **A reading never blocks Domoticz.** It runs in a background thread that never touches Domoticz and never logs; the
  plugin thread picks up its result at the next heartbeat (so the first values appear within about ten seconds).
* **The state file** `rainstats_state.json` holds only the slots that have a value, written atomically (a temporary
  file, then a rename) at most every 15 minutes and always when the plugin stops. A file that cannot be used is moved
  aside, never overwritten, and the log says where it went. A file written by an early 0.1.0 build, with 1-hour slots
  in the coarse ring, is upgraded when it is read: the rain of an hour is spread evenly over its four quarters, the
  peak of an hour is kept in each of them, so no rain is lost.
* **Back-off.** After a failed reading the next attempt is delayed (twice the interval, then four times, up to five
  minutes). The first successful reading resets it.
* **Log once.** A problem is an *Error* in the log once, repeats appear only with Debug *Verbose*, and the recovery is
  one line.
* **Never crashes Domoticz.** Every callback is wrapped; an unexpected exception is logged once with its name, details
  at Debug level.

## Privacy and security

Details and the hardening checklist: **[SECURITY.md](SECURITY.md)**. In short:

* The plugin reads one or two devices from your own Domoticz (and, once, the daily history of one rain device) and
  sends nothing anywhere else.
* The password for Domoticz and the header made from it are **never written to the log**, at any level.
* A password is sent only to the address you configured; for anything but this machine use `https`. The plugin warns
  when a password would travel in clear text.
* `RS_SCHEME=https` always verifies the certificate; there is no setting that switches verification off.
* Redirects are refused and the size of every answer is limited, also after gzip decompression.

## Troubleshooting

Set *Debug* to **Basic** first, **Verbose** shows every reading. The messages below are the ones the plugin writes.

| Log message | Cause / fix |
|---|---|
| `Configuration error: The rain device IDX is required: fill in the IDX of your rain device (Setup > Devices, column Idx)` | the *Rain device IDX* field is empty |
| `Configuration error: Update interval must be a number of seconds` | the *Update Interval* field is not a number |
| `Configuration error: RS_SAVE_INTERVAL must be between 60 and 86400` (and the like) | a value in `.env` is out of range or no number; the message names the key |
| `Configuration warning: .env line 3: unknown setting RS_SAVE_INTERVALL ignored` | a mistyped key in `.env`; the default is in force |
| `Reading the rain device failed: Domoticz API (IDX 1161): HTTP 403 Forbidden: Domoticz refuses the request` | the API does not accept requests from this machine without a login: fill in *Domoticz user* and *Domoticz password*, or add the machine to *Setup > Settings > System > Local Networks* |
| `Reading the rain device failed: ... Connection refused (is Domoticz listening on 127.0.0.1:8080?)` | wrong *Domoticz address* or *port*, or the web server of Domoticz listens elsewhere. `python3 -m rain_stats --find` shows the same message |
| `Reading the rain device failed: ... no answer within 10 s` | Domoticz is busy or the address is wrong; raise `RS_HTTP_TIMEOUT` |
| `Reading the rain device failed: ... the answer is not JSON (is 127.0.0.1:8080 a Domoticz address and port?)` | something else answers at that port, or `RS_SCHEME` is wrong |
| `Reading the rain device failed: ... TLS error: the certificate of the server is not trusted` | https with a certificate from your own authority: set `RS_CA_FILE`. There is no setting to switch verification off |
| `Reading the rain device failed: ... Domoticz has no device with this IDX` | the IDX is wrong or the device was deleted: `python3 -m rain_stats --find` lists the rain devices |
| `Reading the rain device failed: ... the device has no rain value` | the device is not of type *Rain* and its value is not rain in mm: choose another device |
| `Reading the rain device failed: ... the device has no rain rate` | the *Rain rate device IDX* points at a device whose value is not a rate in mm/h |
| `The rain device can be read again` | the recovery of any of the above; informational |
| `The rain counter went down from 12.4 to 0.0 mm: the sensor was restarted, its new value is the baseline` | a *total* counter that dropped: nothing was counted. Said once |
| `The daily rain counter went down from 5.2 to 0.1 mm in the middle of the day: ignored, its new value is the baseline` | a *daily* counter that dropped at noon, not at midnight: the sensor was restarted, or *Rain counter* should be *total*. Said once |
| `... mm of rain between two readings is more than the limit of 100 mm (RS_MAX_DELTA): taken for a sensor error, ignored` | a jump that rain cannot make. Raise `RS_MAX_DELTA` only if it was real rain |
| `A rain rate of 3500.0 mm/h is more than 3000 mm/h: taken for a unit or sensor error (see RS_RATE_DIVISOR)` | the rate is not in mm/h; see `RS_RATE_DIVISOR` |
| `Domoticz marks the rain device as not reporting (sensor time-out): its rain rate is taken as 0` | the station stopped sending data; the rate is 0 until it reports again |
| `The state file ... cannot be used (...): it was moved to ..., starting with an empty archive` | the state file was damaged or from a newer version; it is kept under the new name. Copy it back if it can be repaired |
| `Cannot write the state file ...: ... The statistics stay in memory and are lost when the plugin stops` | the plugin directory is not writable for the Domoticz user; fix the permissions or set `RS_STATE_FILE` |
| `Cannot import the data file of RainStats V2.6: ...` | `RS_LEGACY_FILE` is wrong or not the script's file; the archive starts empty. Check with `python3 -m rain_stats --legacy FILE` |
| `Configuration error: RS_LAST_030_DAYS must be Y or N, got 'maybe'` | a window line of `.env` is neither `Y` nor `N`; the message names the line |
| `Configuration error: All the windows are switched off: at least one of the lines RS_LAST_001_DAYS ... RS_LAST_365_DAYS of .env must be Y` | every window is `N`: nothing would be created. Set at least one to `Y` |
| `Reading the rain history failed: ... Is 1161 the IDX of a device of this Domoticz (Setup > Devices, column Idx), and does this Domoticz know the graph command` | `RS_HISTORY_IDX` is wrong, or this Domoticz does not answer the graph command. Not repeated before the next start; check with `python3 -m rain_stats --history IDX` |
| `Reading the rain history failed: ... . It is tried again at the next reading` | Domoticz could not be reached for the history (network problem); it is asked again, once per reading, until it worked |
| `Cannot use the rain history: ...` | Domoticz answered, but the answer cannot be used (the plugin has no data of its own yet, or the rows are nonsense). Not repeated before the next start |
| `RS_HISTORY_IDX is set, but no window longer than 1 day is switched on (RS_LAST_..._DAYS): the history is not needed and not read` | only the 1-day window is `Y`: nothing to complete. Switch a longer window on, restart the hardware: the history is read, once, then |
| `The rain of the windows longer than 1 day after the import: 2 d 12.3 mm, 7 d ...` | informational, once after the import: what every active window above 1 day shows now |
| `Domoticz holds N finished day(s) of rain for IDX 1161 and the plugin has its own data for all of them: there is nothing to add` | informational: the plugin knew every day already; the history is marked as done and not read again |
| `Domoticz holds no finished days of rain for IDX 1161: nothing was added, and the history is asked again at the next start` | the device has no history yet, or the IDX is not a rain device: check it with `python3 -m rain_stats --history IDX` |
| `Reading the rain history did not end within 5 s; it is abandoned` | Domoticz stopped the hardware while the history was still being read; harmless, the next start reads it |
| `Imported the daily rain history of IDX 1161: N days from DATE to DATE, X mm (...)` | the history was read and added; informational, said once. The state file says so from now on |
| `RS_HISTORY_IDX is set, but the history of IDX 1161 (N days from DATE) is in the state file: it is not read again` | informational, Basic level: the history is in the statistics already. Delete the state file to read it again |
| `Domoticz did not create the device ...` | Domoticz refuses new devices while *Setup > Settings > System > Accept new Hardware Devices* is off. Switch it on; the plugin asks again every 5 minutes, no restart needed |
| `Cannot update Domoticz unit N: no such device` | the device was deleted in Domoticz; it is created again at the next reading |
| `A poll of the rain device did not end within 5 s; it is abandoned` | the request was still running while Domoticz stopped the hardware; harmless |
| `Command for unit N ignored: the plugin did not start, see the errors above` | the configuration error above it stopped the plugin |
| `Unexpected error in ...` | a bug: please open an issue with the line and, with Debug on, the lines after it |

## FAQ

**Why does the graph of `Rain 1 d` (`Rain 24 h` in the early build) go up and down?** It is a sliding window, not a running total. It rises when it
rains and falls when the rain of 24 hours ago leaves the window, so a dry day draws a slowly falling line that ends at
0 mm. The rain leaves gradually, over the 5 minutes of the slot it fell in. In the first 24 hours after the very first start there is one more effect: the rain
that the counter had already counted that day is credited at the first reading and spread evenly over the day so far
(the plugin cannot know when it rained), so it also leaves the window evenly, as a small steady fall between showers.

**The windows are smaller than I expected.** A window is partial until the plugin knows that much time;
*Rain Stats Info* names the first partial window. Start with history: [the one-time history set-up](#the-one-time-history-set-up)
reads the daily rain Domoticz kept of a rain device, and [the import of the old script's data](#replacing-the-dzvents-script)
takes over what the dzVents script had.

**The 1 d value is a little different from the sum I get from Domoticz' own graph.** The window is the last 24 hours
with the 5-minute slot at its edge counted in proportion; a sum over a graph is made of its own points, which end at
other times. A difference of a few tenths of a millimetre at the edge is to be expected. See
[How the numbers are made](#how-the-numbers-are-made).

**Why not read the rain device through MQTT?** The plugin reads what Domoticz knows, whatever feeds it (MQTT,
a script, another plugin). That keeps one source of truth. An MQTT source is on the [roadmap](ROADMAP.md).

**Can I have a window that is not in the list, for example 45 days?** The windows and their unit numbers are fixed, so that
scripts keep working; the list is 1, 2, 7, 14, 30, 60, 90, 120, 180, 360 and 365 days, and you choose in `.env` which
of them exist. Another length is a new row in `rain_stats/constants.py` (see [CONTRIBUTING.md](CONTRIBUTING.md)). 168 h *is*
7 days, 24 h is 1 day.

**I switched a window off, and its devices are still there.** The plugin never deletes a device. It stops writing to it;
delete it in *Setup > Devices*.

**Will the history be read again after a restart?** No, as long as the state file exists and holds it. See
[the one-time history set-up](#the-one-time-history-set-up).

**Can I reset the statistics?** Stop the hardware, delete `rainstats_state.json`, start it again. (With `RS_HISTORY_IDX` in
`.env` the history is read again then, because nothing is left of it.)

**What happens at the start of the year?** Nothing special: the rings are round-robin, the oldest slot is overwritten.

## What is verified and what is not

**Verified (by the test suite):**

* the counter logic: daily and total counters, resets at midnight and shortly after, late resets, glitches and sensor
  restarts, implausible jumps, readings that go back in time;
* **the counting as a whole, against a ground truth.** A simulation generates a year of weather (showers, drizzle,
  dry weeks), feeds it through the plugin's counter, archive and windows at 5-minute and at 1-minute intervals, across
  both daylight-saving changes and with restarts, and compares every window with the rain that really fell **in the
  same slots, plus the proportional edge**. Rain sums agree to 1e-6 mm and peak rates exactly; with irregular readings
  the error stays within a stated bound. A mutation check shows that an off-by-one window, a summed peak ring, a wrong
  edge and the midnight flaw of the old script are all caught. The windows are also compared with an exact rolling "last
  N hours" sum: no bias, and never off by more than the rain of the slot at the edge (see *The edge of a window is an
  estimate* above); a test pins that down;
* the rings: wrap-around, slots that are reused, gaps longer than the ring, saving and loading, nonsense in a file;
* the state file: atomic writes, a bad or newer file moved aside and never overwritten, limits;
* the import of the old script's file, including rows that are not usable;
* **the windows you choose.** Which devices exist for every combination of the `.env` lines and the setting
  *Statistics*, that a window switched off is neither created nor written, that a bad value or an empty selection stops
  the plugin with a message that names the line, and that the units never move;
* **the one-time history set-up**, against the fake Domoticz: days before the plugin's own data are added, days it knows
  itself are never doubled, the marker in the state file, **one single request** however often the plugin is restarted,
  retries after a network error and none after a refusal, rows that are not usable, daylight-saving days, a stop while
  the history is being read;
* the API client against a fake Domoticz that is a real HTTP and TLS server (a CA made for the test): the fields of a
  *Rain* device, a custom sensor, a rate device, a login, HTTP errors, dropped connections, garbage, time-outs, an
  untrusted certificate, oversized answers and a gzip bomb, redirects;
* the controller: polling by time, back-off, log once, recovery, a failing source that does not stop the windows,
  saving, a stop during a reading;
* the device layer against a stub of the plugin API: creation, refused creation, the frozen unit model, units,
  writes only on change, refresh, deleted devices, read-only devices;
* `plugin.py` loaded for real against the fake Domoticz: start, readings, stop, commands, a bad configuration,
  an unreachable Domoticz; that **the password never reaches the log**, also at Debug level; that `plugin.py` binds
  none of the names Domoticz injects, imports only the standard library and is the only module that imports
  `Domoticz`; Python 3.7 compatibility (`vermin`);
* `deploy.sh`, including that **an update or a rollback never touches the statistics**.

**Checked against the sources, but not run:**

* **the history request** `type=command&param=graph&sensor=rain&idx=N&range=year`, as read in the source of Domoticz
  (the rows `{"d": "YYYY-MM-DD", "mm": "..."}` and the HTTP 400 for a device it does not know);
* the Domoticz plugin framework as described in its documentation and read in its source: `Used=1`, the unit of a
  Custom Sensor, the type names, `Update()` and what `SuppressTriggers` does and does not write, devices stored as
  `<hardware name> - <name>`, new devices refused while *Accept new Hardware Devices* is off, the `getdevices` command
  and its fields (`Rain` is the rain **today** and `RainRate` is mm/h).

**Seen in a real Domoticz** (the author's Domoticz 2026.4, from 2026-11-19; one screenshot of the log graph of
*Rain 24 h*, now `Rain 1 d`, over a day and a half):

* the hardware starts, the device exists and its value is updated while it rains;
* a *daily* counter passes local midnight without a jump or a drop in the value (the case the old script got wrong);
* the value rises with the rain and falls slowly while it is dry, as a sliding window must (see the [FAQ](#faq)).

**Not verified:**

* **most of Domoticz.** Only that one device has been looked at. Not seen: the Domoticz log, the peak, status and info
  devices, whether `SuppressTriggers` and the unit label behave as read, other Domoticz and Python versions (3.9 to
  3.13 are in the CI matrix and the suite was also run on 3.8; 3.7 is checked statically).
* **the Sainlogic FT310 pipeline.** Not seen: how often your station pushes rain into Domoticz, and whether the
  `Rain` value resets at local midnight exactly as the code assumes. **Run `python3 -m rain_stats --rain-idx <IDX>` on
  your host first** and watch the value over a midnight.
* **long runs.** Weeks of running, the memory use, the state file after a year.
* **the data import against a real file.** It is tested with files built from the format the script writes, not with
  the file of a long-running installation.
* **the history set-up against your Domoticz.** The request is checked against the source of Domoticz and a fake
  server, not against a real answer. **Run `python3 -m rain_stats --history <IDX>` first**: it shows what Domoticz
  answers and what would be added, and writes nothing. Not seen either: whether a *Custom Sensor* has a daily rain history at all; as far as the source of
  Domoticz shows, that table is filled for devices of type *Rain*, so use one of those for `RS_HISTORY_IDX`.

Reports from real installations, also "it works on my setup", are very welcome:
[open an issue](https://github.com/janreimen/Domoticz-Rainstats/issues). The output of
`python3 -m rain_stats --rain-idx <IDX> --json` is the best bug report.

## Development

```sh
git clone https://github.com/janreimen/Domoticz-Rainstats.git
cd Domoticz-Rainstats
make test            # python3 -m unittest discover -s tests
make check           # tests + flake8 + shellcheck + Python 3.7 compatibility (vermin) + generated docs up to date
make package         # dist/Domoticz-Rainstats-<version>.tar.gz
```

Rules, how to add a device and how to prepare a release: [CONTRIBUTING.md](CONTRIBUTING.md) and
[checklist.md](checklist.md). Planned work: [ROADMAP.md](ROADMAP.md). Security notes: [SECURITY.md](SECURITY.md).
Accessibility: [ACCESSIBILITY.md](ACCESSIBILITY.md). Code of conduct: [.github/CODE_OF_CONDUCT.md](.github/CODE_OF_CONDUCT.md).

## Credits

* The idea and the statistics windows: the dzVents script *RainStats V2.6*, which this plugin replaces.
* The Domoticz plugin: Jan Reimen.

## License

[MIT License](LICENSE); see [NOTICE](NOTICE).
