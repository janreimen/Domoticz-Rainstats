# Security

## Scope

The plugin only **reads**: it asks your own Domoticz for one or two devices (the rain counter and, optionally, the
rain rate) over HTTP or HTTPS, and once for the daily rain history of one device (`RS_HISTORY_IDX`), and shows
statistics as Domoticz devices. It has no account of its own, it sends nothing
about you to anyone, and it cannot switch or change anything in Domoticz except its own devices. It runs inside
Domoticz as the Domoticz user, starts no processes and uses only the Python standard library. The one thing it writes
is its state file, `rainstats_state.json`, beside `plugin.py`.

## Things to know

| Topic | Details | What to do |
|---|---|---|
| **The Domoticz password** | If the Domoticz API asks for a login, the plugin sends it as HTTP Basic authentication. The password and the header made from it are registered with the logger and **replaced by `<masked>` in every message at every level**; they are never part of an error text | Use a user that may only read devices. If you post a log in an issue, look it over anyway |
| **Clear text on the network** | With `http` and a user, the password travels unencrypted to the Domoticz address. For this machine (`127.0.0.1`, `::1`) that never leaves the host. For another machine the plugin logs a warning | Use `RS_SCHEME=https` for anything but this machine |
| **Certificates are always verified** | For https the certificate of Domoticz is checked against the system store (and `RS_CA_FILE` when you give your own authority). There is no setting that switches verification off | Do not work around a TLS error: fix the certificate or set `RS_CA_FILE` |
| **No proxy, no redirect** | The request goes straight to the address of the hardware settings, never through a proxy from the environment. A redirect is refused (it would send a password somewhere else) and only its target host is named in the error | Nothing to do |
| **Answers are not trusted** | An answer is limited to 1 MB (16 MB for the device list of the command line tool), **also after gzip decompression**, so a small compressed answer cannot fill the memory. Values that are not numbers, are negative or are not finite are ignored | Nothing to do |
| **The state file** | It holds rain amounts and times, nothing else: no address, no password. It is written to a temporary file and renamed, so a crash never leaves half a file; a file that cannot be read or is from a newer version is moved aside, never overwritten, and a state file larger than 32 MB is not read | Back it up like any data. Make the plugin directory writable only for the Domoticz user and the user that deploys |
| **The history set-up** | With `RS_HISTORY_IDX` the plugin asks the same Domoticz **once** for the daily rain totals of a device (`type=command&param=graph&sensor=rain&idx=N&range=year`): the same address, login, proxy and redirect rules, the same size limit (1 MB, also after gzip decompression). Rows that are not numbers, are negative or are above 1000 mm a day are skipped. The state file records that it was done, and the device is not asked again | Nothing to do. Use an IDX of your own Domoticz |
| **The import of the old script's file** | Read once, never changed, limited in size; rows that are not usable are skipped and counted | Point `RS_LEGACY_FILE` only at a file you trust |
| **The `.env` file** | It holds technical settings, no secret. A variable of the same name in the environment wins | Keep it writable only for the Domoticz user |
| **`deploy.sh`** | Runs commands on the target through SSH (`bash -s`), uses `sudo -n` only if the plugin directory is not writable, and never changes anything outside the plugin directory, the backup directory and (restart) the Domoticz service. It leaves your statistics and your `.env` alone | Review the script before running it; use SSH keys |

## Hardening checklist

1. Prefer a Domoticz user that only reads, or add the host to *Setup > Settings > System > Local Networks* and use no
   password at all for `127.0.0.1`.
2. Use https for a Domoticz that is not on the same machine.
3. Keep the plugin directory writable only for the user that runs Domoticz and the user that deploys.
4. Run `python3 -m rain_stats --find` once after changing the Domoticz address, port or login.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's private vulnerability reporting
(*Security* tab of the repository > *Report a vulnerability*). Include the plugin version, what you observed and, if
possible, how to reproduce it. Do not include passwords or other private data.
