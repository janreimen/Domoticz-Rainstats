"""Domoticz-Rainstats: rolling rain statistics (1 d to 365 d) for a Domoticz rain sensor, kept in a round-robin store.

Package layout (each module has exactly one job):

    config.py       plugin settings (parsed and validated from the Domoticz ``Parameters`` and the plugin-local .env)
    constants.py    device model (unit numbers, types), the round-robin layout and the windows, defaults, timings
    logger.py       four-level logger; the only place that decides what reaches the Domoticz log
    util.py         small helpers
    model.py        plain data objects handed between the layers (Reading, Change, Stats, RainDevice)
    ring.py         the round-robin buffer: a fixed number of time slots that is reused in a circle
    archive.py      the rain ring (sum) and the peak-rate ring (maximum) of each tier; windows
    counter.py      from successive readings of the rain counter to the rain that fell
    stats.py        from the archive to one value per window
    store.py        the state file: archive, counter baseline, start of collecting (atomic writes)
    legacy.py       one-time import of the data file of the dzVents script RainStats V2.6
    history.py      one-time import of the daily rain history that Domoticz keeps of a rain device
    api.py          Domoticz JSON API client (standard library only, no Domoticz module)
    devices.py      the ONLY module that talks to the Domoticz device API
    controller.py   poll scheduling, error policy, saving (no Domoticz import)
    cli.py          ``python3 -m rain_stats``: read the sensor, find the rain device, show a state file - without Domoticz

``plugin.py`` is thin glue and the only module that imports ``Domoticz``.
"""

__version__ = "0.0.3"
