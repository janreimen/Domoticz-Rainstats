# Pull Request

## Summary

<!-- Briefly explain what this pull request changes. -->

## Related issue

<!-- Link the issue, for example: Closes #12 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Rain counting or round-robin storage change (counter rules, rings, windows, state file)
- [ ] Refactoring or code quality improvement
- [ ] Documentation update
- [ ] Other

## Changes made

<!-- Describe the important changes and their impact. -->

-
-
-

## Testing performed

- [ ] `make check` passes (tests, flake8, Python 3.7 compatibility, device catalogue).
- [ ] Tests added or updated for the change.
- [ ] Plugin startup tested in Domoticz, where possible.
- [ ] Existing devices (units 1-18) still update correctly, where applicable; no unit number changed or reused.
- [ ] A state file written by the previous version still loads (or the state version was raised and the old one is moved aside, never overwritten).
- [ ] Error handling and logging reviewed.
- [ ] `CHANGELOG.md` updated under `Unreleased`; documentation updated, where necessary.

### Test environment

- **Plugin version / branch:**
- **Domoticz version:**
- **Operating system / Python:**
- **Tested against:** real rain device / fake Domoticz only

### Test results

<!-- Describe what you tested and the results. If testing was not possible, explain why. -->

## Compatibility and limitations

<!-- Describe differences between rain sensors, migration considerations, or untested functionality. -->

## Security and privacy

- [ ] No passwords, API tokens or other secrets are included.
- [ ] No private host names, IP addresses or state files are included.
- [ ] Logs, screenshots, and test data have been reviewed and redacted.

## Checklist

- [ ] This pull request targets the `development` branch.
- [ ] I followed the rules in `CONTRIBUTING.md` (only `plugin.py` imports `Domoticz`, only `devices.py` calls the device API, no third-party packages, device model unchanged).
- [ ] My changes are focused and do not contain unrelated modifications.
- [ ] I have not included generated files or temporary development artifacts unnecessarily.
- [ ] I have documented any tests I could not perform.

## Additional notes

<!-- Anything else the maintainer should know before reviewing this pull request? -->
