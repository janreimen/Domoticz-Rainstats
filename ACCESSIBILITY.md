# Accessibility

## Commitment

Domoticz-Rainstats aims to be usable and understandable by as many people as practical. The project will consider accessibility when updating documentation, issue templates, examples, and any user-facing text. Reports and constructive suggestions about accessibility are welcome.

## Supported environments

This is a Python plugin for Domoticz that shows statistics of the rain (rain and highest rain rate over 24 h to 365 d) as Domoticz devices. Accessibility depends on the surrounding software and environment, including:

- The Domoticz web interface and the browser or client used to access it.
- The operating system, Python runtime, and Domoticz version running the plugin.
- The devices and sensor types available in the installed Domoticz environment.
- The channels you use for anything you build on the plugin's devices (notifications, a text-to-speech script, a dashboard).

The project does not independently provide or certify an accessible user interface. Please consult the documentation of Domoticz and your operating system for their accessibility features and supported environments.

## Information and accessibility

Every value the plugin shows is a **number with its unit** (`mm`, `mm/h`) in a Domoticz sensor, or plain text (*Rain Stats Info*, *Rain Stats Status*). Nothing is shown by colour, sound or position alone. The names of the devices say what they show and for how long (`Rain 24 h`, `Peak Rain Rate 30 d`), so a screen reader that reads a device list gives the full meaning of each entry. A window that does not yet cover its whole length is named in *Rain Stats Info* as text, not only by a lower number.

## Current limitations

- The plugin provides its data through Domoticz devices; it is not a standalone graphical application.
- Accessibility of device labels, charts, tables, controls, and status messages is largely determined by Domoticz and the client used to view it.
- The project has not completed a formal accessibility audit or conformance assessment, so no WCAG conformance level is claimed.
- Problems are reported in the Domoticz log; the clarity and accessibility of those messages may vary by environment. The plugin writes each message as plain text, one problem per line, with a hint about what to check.

## Reporting an accessibility barrier

If you encounter a barrier using the plugin or its documentation, please open an issue:
https://github.com/janreimen/Domoticz-Rainstats/issues

Choose a bug report or feature request template as appropriate and include:

- The page, device, setting, or documentation section involved.
- What you were trying to do and what happened.
- Your Domoticz version, plugin version, operating system, and browser/client where relevant.
- Any workaround that helped, if one is available.

Please do not include passwords or other sensitive information. If the barrier involves private or sensitive details, contact the maintainer through GitHub instead of posting those details publicly.

## Improvements

Accessibility feedback will be considered alongside other project issues and contributions. Improvements may be made as project resources and technical constraints allow.
