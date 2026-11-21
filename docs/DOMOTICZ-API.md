# What the plugin asks Domoticz

A Domoticz plugin only sees the devices of its own hardware, so it cannot read the rain device of another hardware
(your weather station) through the plugin interface. The plugin therefore asks Domoticz itself, over HTTP, like a
browser would. This page says exactly what is asked and which fields are used. `python3 -m rain_stats` shows the
same.

## The request

```
GET /json.htm?type=command&param=getdevices&rid=<IDX>
```

| | |
|---|---|
| Address | the *Domoticz address* and *Domoticz port* of the hardware settings, with `RS_SCHEME` (`http` by default) |
| Login | HTTP Basic authentication, only when a user and password are filled in |
| Proxy | never: the request goes straight to the address; `HTTP_PROXY` and friends are ignored |
| Redirects | refused: Domoticz does not redirect its API, so a redirect means a wrong scheme or port |
| Time-out | `RS_HTTP_TIMEOUT` (10 s) |
| Size | an answer larger than 1 MB is refused, also after gzip decompression |
| When | every *Update Interval* (60 s); one request, or two with a separate rate device |
| Thread | in a background thread that never touches Domoticz or the log |

The command line tool `--find` also asks `type=command&param=getdevices&filter=all&used=true&order=Name` once, to list
the devices of type *Rain*.

If Domoticz answers 401 or 403, the API does not accept requests from this machine without a login: give the plugin a
user, or add the machine to *Setup > Settings > System > Local Networks*.

## The fields

The answer is `{"status": "OK", "result": [ {...device...} ]}`. The plugin takes from the device:

| Device `Type` | Counter (mm) | Rate (mm/h) |
|---|---|---|
| `Rain` | `Rain`: the rain **today**, resets at local midnight, resolution 0.1 mm | `RainRate`, divided by `RS_RATE_DIVISOR` (1) |
| anything else | the first number of `Data`, for example `12.3` of a Custom Sensor showing `12.3 mm` | the first number of `Data` of the rate device (*Rain rate device IDX*) |

A *Rain* device gives both. For any other device the rain rate comes from a separate device, or, with none, from the
rain between two readings that are at most 30 minutes apart.

| Other field | Use |
|---|---|
| `Name`, `idx`, `HardwareName` | shown by `--find` |
| `Name`, `LastUpdate`, `Rain`, `RainRate`, `Data` | the raw values of the last reading are logged at Debug level |
| `HaveTimeout` | true when Domoticz marks the device as not reporting (its sensor time-out): the rain rate is taken as 0 until it reports again; the log says so once |

The counter of a *Rain* device is "rain today" because that is what Domoticz keeps: it adds the rain of the day and
starts again at local midnight. That is why the default of *Rain counter* is *Resets at midnight*. A counter that
never resets (a lifetime total) needs *Never resets (total)*.

## The history request (once)

Only with `RS_HISTORY_IDX` in `.env`, and only until it has worked once:

```
GET /json.htm?type=command&param=graph&sensor=rain&idx=<IDX>&range=year
```

The same address, login, proxy and redirect rules, time-out and size limit as above. Domoticz answers
`{"status": "OK", "result": [{"d": "2025-11-21", "mm": "3.2"}, ...]}`: one row per day of the last year, the row of today
last (it is not finished and is never used). The plugin takes `d` (the day) and `mm` (the rain of that day). A row without
a valid date or number, with a negative value or with more than 1000 mm is skipped and counted.

| When | What |
|---|---|
| Asked | once per start, after the first successful reading of the rain device, in a background thread; only while the state file has no record of a history |
| Never again | when the state file records it (the IDX, the number of days, the time): not at the next reading, not after a restart. Only a deleted state file asks again |
| IDX unknown to Domoticz | HTTP 400: the plugin says `Is N the IDX of a device of this Domoticz ...` once and does not ask again before the next start |
| Network problem | tried again at the next reading |

`python3 -m rain_stats --history <IDX>` makes the same request and shows what the plugin would add; it writes nothing.

## What can go wrong, and what the plugin says

| Situation | Message (shortened) |
|---|---|
| The API refuses the request | `HTTP 403 Forbidden: Domoticz refuses the request. Fill in Username and Password ...` |
| Nothing listens at that address | `Connection refused (is Domoticz listening on 127.0.0.1:8080?)` |
| Something else answers | `the answer is not JSON (is 127.0.0.1:8080 a Domoticz address and port?)` |
| The IDX does not exist | `Domoticz has no device with this IDX (see Setup > Devices, column Idx)` |
| The device has no rain | `the device has no rain value. Use a Rain device, or a sensor whose value is the rain in mm` |

Each of these is logged once as an error and its recovery once, see the
[README troubleshooting table](../README.md#troubleshooting).
