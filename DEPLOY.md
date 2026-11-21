# Deploying Domoticz-Rainstats

The plugin is a plain directory (`plugin.py` + the `rain_stats/` package) that has to sit in Domoticz' `plugins/`
directory. Nothing is compiled and no Python package is installed. This page covers the ways to get it there, how
to verify the result, and how to update, roll back and remove it.

## 0. Prerequisites

| Where | What |
|---|---|
| Domoticz host | Domoticz with Python plugin support, Python 3.7 or newer |
| Rain device | a device of type *Rain* (or a sensor that shows rain in mm) in Domoticz, and its **IDX** (*Setup > Devices*, column *Idx*) |
| Domoticz API | reachable from the Domoticz host, normally `http://127.0.0.1:8080`; a login is needed only if Domoticz asks for one for local requests |

Quick check from the Domoticz host. It reads your rain device with the same code as the plugin and prints what the
plugin would use (it changes nothing):

```sh
cd Domoticz-Rainstats && python3 -m rain_stats --find
cd Domoticz-Rainstats && python3 -m rain_stats --rain-idx 1161
```

Exit status 0 = the device was read, 2 = Domoticz could not be read (the message says why), 1 = a wrong option. If
Domoticz runs in a **container**, run the check inside it. The password for Domoticz, if there is one, goes into the
environment variable `RS_PASSWORD`, not into an argument.

## 1. `deploy.sh` (recommended)

Run it from a checkout (or unpacked archive) of this repository.

```sh
./deploy.sh --dry-run                               # show what would happen on this machine
./deploy.sh                                         # install on this machine
./deploy.sh --host user@my-domoticz-host              # install on another machine over SSH
./deploy.sh --host user@my-domoticz-host --rollback  # back to the previous version
```

Or through make: `make deploy HOST=user@my-domoticz-host`, `make deploy-dry`, `make rollback HOST=...`.

What it does on the target:

1. checks that `python3` is 3.7 or newer
2. **parses the new files before anything is touched**: a copy that does not compile on the target's Python never
   replaces the working installation
3. picks `sudo` only if the plugin directory is not writable for the SSH user (needs **passwordless** sudo then;
   otherwise log in as the Domoticz user or root)
4. backs up the installed copy to `~/domoticz-plugin-backups/Domoticz-Rainstats-<version>-<time>.tar.gz`
   and keeps the newest 3. The backup is deliberately **outside** the plugins directory: a second copy of `plugin.py`
   in there would be loaded by Domoticz as a duplicate plugin. **The state file is not part of the backup**
5. replaces `plugin.py`, `rain_stats/` and the documentation files. Other files you put in that directory, **your
   `.env` and your statistics (`rainstats_state.json`) among them**, are left alone
6. gives the directory back to its previous owner (for example `domoticz:domoticz`)
7. restarts the `domoticz` systemd service

Nothing is changed when a step before the copy fails (for instance when the backup directory is not writable). It
also stops, changing nothing, when another directory below the plugins directory already holds this plugin (the
same plugin key): Domoticz would load both. Update that copy with `--dir`, or remove it. A rollback is not affected.

**Why the statistics are not in the backup:** a rollback brings the *code* back. If the statistics were part of it, a
rollback a week later would turn the archive back a week and the rain of that week would be gone. The statistics are
your data: back up `rainstats_state.json` yourself if you want a copy.

| Option | Default | Meaning |
|---|---|---|
| `--host [USER@]HOST` | this machine | deploy over SSH |
| `--ssh-opts "OPTS"` | | extra `ssh` options, e.g. `"-p 2222 -i ~/.ssh/id_ed25519"` |
| `--dir PATH` | `/srv/domoticz/plugins/Domoticz-Rainstats` | target directory |
| `--dry-run`, `-n` | | report only; the target is not changed |
| `--rollback` | | restore the newest backup |
| `--service NAME` | `domoticz` | systemd service to restart |
| `--restart-cmd CMD` | | run CMD on the target instead (init.d, docker restart, ...) |
| `--no-restart` | | do not restart Domoticz |
| `--backup-dir PATH` | `$HOME/domoticz-plugin-backups` | backup location on the target |
| `--keep N` | `3` | backups to keep |
| `-V`, `--version` | | print the version of this source tree |

Exit status: `0` success, `1` error (nothing was changed if it happened before the copy).

The script opens two SSH connections (upload, then run). Use key authentication, or `ControlMaster auto` in
`~/.ssh/config`, to avoid being asked twice.

## 2. By hand

```sh
cd /srv/domoticz/plugins
sudo cp -r /path/to/Domoticz-Rainstats .                 # plugin.py and rain_stats/ must stay together
sudo chown -R domoticz:domoticz Domoticz-Rainstats       # whatever user runs Domoticz
sudo systemctl restart domoticz
```

Or with git, which makes updates a `git pull`:

```sh
cd /srv/domoticz/plugins
git clone https://github.com/janreimen/Domoticz-Rainstats.git
# later:
cd Domoticz-Rainstats && git pull && sudo systemctl restart domoticz
```

The directory name does not matter to Domoticz; the plugin is identified by the `key` in the manifest (`Rain-Stats`).
Use one directory per plugin: do not leave an old copy, a `.bak` folder or an unpacked archive next to it (Domoticz
loads every `plugin.py` it finds one level below `plugins/`).

## 3. Add the hardware

1. *Setup > Hardware*, type **Rain Statistics**, name it (for example `Rain Statistics`).
2. Fill in the **Rain device IDX**. Leave the address (`127.0.0.1`) and the port (`8080`) unless your Domoticz listens
   elsewhere. Choose *Rain counter*: *Resets at midnight (rain today)* for a Domoticz *Rain* device.
3. Choose the windows: in `.env` (copy `.env.sample`) every window has a line `RS_LAST_001_DAYS=Y` ...
   `RS_LAST_365_DAYS=N`; only the windows set to `Y` get devices. Without a `.env` the eight windows 1, 2, 7, 14, 30,
   90, 180 and 365 days are created.
4. Want the long windows filled from the first day? Set `RS_HISTORY_IDX` in `.env` to the IDX of a rain device whose
   daily history Domoticz has kept (see the [README](README.md#the-one-time-history-set-up)). It is read once.
5. Coming from the dzVents script? Set `RS_LEGACY_FILE` in `.env` **before** you add the hardware, see the
   [README](README.md#replacing-the-dzvents-script).
6. *Add*. The log shows `Rain Statistics X.Y.Z starting ...`, the line `Windows: ...` and, after the first reading,
   `Connected to Domoticz ...`; the devices of the chosen windows are created (18 devices, units 1-18, by default).

All fields are explained in the [README](README.md#settings). Technical settings go into a `.env` file next to
`plugin.py` (copy `.env.sample`); they take effect when the hardware is restarted.

**No devices appear?** Domoticz only accepts new devices from plugins while *Setup > Settings > System > Accept new
Hardware Devices* is on. The plugin says so once in the log (`Domoticz did not create the device ...`) and asks again
every five minutes: switch the setting on and the devices appear without a restart.

## 4. Verify

In the Domoticz log (*Setup > Log*), with *Debug* at *Basic* for the first run:

* `Rain Statistics X.Y.Z starting (Domoticz at http://127.0.0.1:8080, rain IDX 1161, update every 60 s, ...)`: the
  plugin started with the settings you expect
* `No usable state file yet (...): starting with an empty archive` on the first start, or `Loaded the state file ...`
  afterwards, or `Imported the data file of RainStats V2.6: ...` with the import
* `Windows: 1 d, 2 d, ...`: the windows that get devices, as the `RS_LAST_..._DAYS` lines of `.env` say
* `Connected to Domoticz at ...: the rain counter reads N mm`: the first reading worked (within about ten seconds of
  the start, at the next heartbeat)
* with `RS_HISTORY_IDX`: `Reading the daily rain history of IDX N (once)` and, a few seconds later, `Imported the
  daily rain history of IDX N: ...`. On every later start the state file says it was done and nothing is read
* the devices (*Setup > Devices*) have values, `Rain Stats Status` is On, *Rain Stats Info* says `OK ...`

If something is wrong, set *Debug* to *Verbose*, reload the hardware and read the log; the
[README troubleshooting table](README.md#troubleshooting) explains the messages. Check the text before you post it.

## 5. Update, roll back, remove

* **Update:** run `deploy.sh` again (or `git pull`) and restart Domoticz. Devices are matched by their unit, so
  existing devices, their names, history, scenes and schedules are kept. Your `.env` and your statistics stay.
* **Roll back:** `./deploy.sh [--host ...] --rollback`, or unpack the newest archive from the backup directory
  over the plugin directory. Your statistics stay as they are.
* **Remove:** delete the hardware in *Setup > Hardware* (this removes its devices), delete the plugin directory
  (and with it `rainstats_state.json`, if you do not want it), restart Domoticz.

## 6. Troubleshooting a deployment

| Symptom | Cause / fix |
|---|---|
| Hardware type "Rain Statistics" is not in the list | Domoticz was not restarted, the directory is not directly below `plugins/`, or `plugin.py` is not readable by the Domoticz user. See the Domoticz log at start-up |
| `ModuleNotFoundError: No module named 'rain_stats'` | the `rain_stats/` directory is missing or not next to `plugin.py` (copied only `plugin.py`) |
| Hardware added, but no devices | *Setup > Settings > System > Accept new Hardware Devices* is off (see the log line `Domoticz did not create the device ...`). Switch it on, wait up to five minutes |
| Two hardware types / duplicate plugin errors | a second copy of the plugin sits in `plugins/` (backup folder, old version): remove it |
| The hardware of the early build (key `RainStats`) shows as unknown | the key changed to `Rain-Stats`: see [section 7](#7-coming-from-the-early-build-key-rainstats) |
| A window was set to `N` but its devices are still there | the plugin never deletes a device: it stops updating it. Delete it in *Setup > Devices* |
| `RS_HISTORY_IDX` is set, but nothing is read | the state file says the history was read already (the log says so once). Only a deleted state file reads it again |
| The old behaviour is still there after an update | Domoticz keeps the old modules in memory: restart Domoticz, a hardware reload is not enough when a module was added or renamed |
| A change in `.env` has no effect | the file is read when the hardware starts: restart the hardware. A variable of the same name in the Domoticz service's environment wins over the file |
| The statistics start from zero after an update | the state file was not where the plugin looks for it (`RS_STATE_FILE`, or a new plugin directory): copy `rainstats_state.json` over and restart |
| `deploy.sh`: `not writable ... sudo needs a password` | log in as the Domoticz user or root, or allow passwordless sudo for the SSH user |
| `deploy.sh`: `the new files do not parse` | the target's Python is older than the code needs; the installation was not touched |

## 7. Coming from the early build (key `RainStats`)

An early build of this plugin (before the repository was called `Domoticz-Rainstats`) used the plugin key `RainStats`.
Domoticz stores a hardware together with the key of its plugin type, so the hardware of that build does not belong to
this plugin until its key is changed. Two ways; the first keeps everything (hardware, devices, their IDX, graphs,
scenes, your statistics).

**A. Change the key in the Domoticz database (recommended).** You need the `sqlite3` command and a stopped Domoticz.

1. Note where your old copy of the plugin is (for example `/srv/domoticz/plugins/Domoticz-RainStats`) and where the
   Domoticz database is (`domoticz.db`, in the Domoticz directory or `/var/lib/domoticz`).
2. Copy your statistics somewhere safe: `cp rainstats_state.json ~/rainstats_state.json.backup` (in the old copy's
   directory). Copy your `.env` too, if you have one.
3. Stop Domoticz: `sudo systemctl stop domoticz`.
4. Copy the database: `cp /path/to/domoticz.db /path/to/domoticz.db.before-rain-stats`.
5. Change the key. Check first that exactly one hardware is affected:

   ```sh
   sqlite3 /path/to/domoticz.db "SELECT ID, Name, Extra FROM Hardware WHERE Extra = 'RainStats';"
   sqlite3 /path/to/domoticz.db "UPDATE Hardware SET Extra = 'Rain-Stats' WHERE Extra = 'RainStats';"
   ```

6. Install this version **over the old directory**, so that the state file and `.env` stay where the plugin looks for
   them: `./deploy.sh --dir /srv/domoticz/plugins/Domoticz-RainStats --host user@my-domoticz-host` (leave out `--host`
   on the Domoticz machine). `deploy.sh` refuses to install next to an old copy, because Domoticz would load both;
   `--dir` names the copy to update. The directory keeps its old name, which does not matter to Domoticz.
   Or, to have the new directory name: unpack the new version into `plugins/Domoticz-Rainstats`, copy
   `rainstats_state.json` and `.env` into it, and **remove the old directory** (move it out of `plugins/`).
7. Add the new lines to `.env` (compare with `.env.sample`): `RS_LAST_..._DAYS` for the windows you want and, if you
   like, `RS_HISTORY_IDX`. Without the lines you get the eight windows you had.
8. Start Domoticz: `sudo systemctl start domoticz`. The log shows `Rain Statistics 0.0.2 starting ...`,
   `Loaded the state file ...` and the `Windows: ...` line. The devices keep their IDX and their names; windows you
   newly switched on appear as new devices. A state file that the early build wrote with 1-hour slots is converted to
   15-minute slots when it is read; the rain is kept.

**B. Add the hardware again.** Delete nothing: add a new hardware of type *Rain Statistics* (it is now found under the
new key) after the steps 2 and 6 above. The new hardware creates **new devices with new IDX numbers**: your old
devices stay as they are but are not updated, and dashboards, scenes and scripts have to be pointed at the new ones. The
state file is read, so the statistics continue. Delete the old hardware when you no longer need its devices (this
removes them, with their graphs).
