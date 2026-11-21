"""Repository hygiene: versions agree, docs are current, links resolve, the README tells the truth about the code."""
import glob
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools"), os.path.join(ROOT, "tests")]

from rain_stats import __version__  # noqa: E402
from rain_stats import constants as c  # noqa: E402
from rain_stats.config import ENV_SPECS, Settings  # noqa: E402
from rain_stats.util import number_text  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def code_files():
    return [os.path.join(ROOT, "plugin.py")] + sorted(glob.glob(os.path.join(ROOT, "rain_stats", "*.py")))


def source_text():
    """plugin.py and the package, as one string."""
    out = []
    for path in code_files():
        with open(path, encoding="utf-8") as fh:
            out.append(fh.read())
    return "\n".join(out)


def text_files():
    """Every file that people read: documents, scripts, templates, the sample configuration."""
    found = glob.glob(os.path.join(ROOT, "*.md")) + glob.glob(os.path.join(ROOT, "docs", "*.md"))
    found += glob.glob(os.path.join(ROOT, ".github", "**", "*"), recursive=True)
    found += [os.path.join(ROOT, n) for n in ("deploy.sh", "Makefile", "requirements.txt", ".env.sample", "NOTICE",
                                               "LICENSE", ".gitignore")]
    return sorted(p for p in found if os.path.isfile(p))


class VersionTests(unittest.TestCase):
    def test_version_is_plain_numeric(self):
        # plugin managers (PyPluginStore) only recognise plain numeric tags: no -alpha / -rc suffix
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(\.\d+)?$")

    def test_plugin_manifest_matches_package(self):
        match = re.search(r'<plugin\b[^>]*\bversion="([^"]+)"', read("plugin.py"))
        self.assertIsNotNone(match, "no version in the plugin manifest")
        self.assertEqual(match.group(1), __version__)

    def test_plugin_key_never_changes(self):
        # changing the key would turn every existing installation into an unknown hardware type
        match = re.search(r'<plugin\b[^>]*\bkey="([^"]+)"', read("plugin.py"))
        self.assertEqual((match.group(1), c.PLUGIN_KEY), ("Rain-Stats", "Rain-Stats"))

    def test_changelog_top_entry_matches_package(self):
        headings = re.findall(r"^## \[([^\]]+)\]", read("CHANGELOG.md"), re.M)
        released = [h for h in headings if h.lower() != "unreleased"]
        self.assertTrue(released, "CHANGELOG.md has no released version")
        self.assertEqual(released[0], __version__, "newest CHANGELOG entry differs from rain_stats.__version__")

    def test_changelog_links_every_release(self):
        text = read("CHANGELOG.md")
        for version in re.findall(r"^## \[(\d[^\]]*)\]", text, re.M):
            self.assertRegex(text, r"(?m)^\[%s\]: https?://" % re.escape(version))

    def test_changelog_release_link_points_at_this_repository(self):
        self.assertIn("https://github.com/janreimen/Domoticz-Rainstats/releases/tag/v%s" % __version__,
                      read("CHANGELOG.md"))

    def test_the_user_agent_and_the_url_carry_the_version_and_the_repository(self):
        self.assertIn(__version__, c.USER_AGENT)
        self.assertEqual(c.PLUGIN_URL, "https://github.com/janreimen/Domoticz-Rainstats")
        self.assertIn(c.PLUGIN_URL, read("plugin.py"))


class FileTests(unittest.TestCase):
    REQUIRED = ["LICENSE", "NOTICE", "README.md", "CHANGELOG.md", "ROADMAP.md", "SECURITY.md", "CONTRIBUTING.md",
                "DEPLOY.md", "ACCESSIBILITY.md", "checklist.md", "Makefile", "deploy.sh", "requirements.txt",
                ".gitignore", ".env.sample", ".github/workflows/tests.yml", ".github/CODE_OF_CONDUCT.md",
                ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/config.yml",
                ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/feature_request.yml",
                "docs/DEVICES.md", "docs/DOMOTICZ-API.md", "tools/gen_device_catalog.py"]

    def test_required_files_exist(self):
        missing = [p for p in self.REQUIRED if not os.path.exists(os.path.join(ROOT, p))]
        self.assertEqual(missing, [])

    def test_no_real_env_file_or_state_file_is_in_the_repository(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".env")))
        self.assertFalse(glob.glob(os.path.join(ROOT, "rainstats_state.json*")),
                         "a state file in the source tree would be packaged and deployed by hand")
        self.assertRegex(read(".gitignore"), r"(?m)^\.env$")
        self.assertRegex(read(".gitignore"), r"(?m)^!\.env\.sample$")
        self.assertRegex(read(".gitignore"), r"(?m)^rainstats_state\.json\*$")

    @unittest.skipIf(os.name != "posix", "POSIX permissions")
    def test_scripts_are_executable(self):
        for path in ("deploy.sh", "tools/gen_device_catalog.py"):
            self.assertTrue(os.access(os.path.join(ROOT, path), os.X_OK), path + " is not executable")

    def test_markdown_links_resolve(self):
        docs = [os.path.relpath(p, ROOT) for p in text_files() if p.endswith(".md")]
        broken = []
        for doc in docs:
            base = os.path.dirname(os.path.join(ROOT, doc))
            for target in re.findall(r"\]\(([^)\s]+)\)", read(doc)):
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                path = target.split("#", 1)[0]
                if path and not os.path.exists(os.path.normpath(os.path.join(base, path))):
                    broken.append("%s -> %s" % (doc, target))
        self.assertEqual(broken, [])

    def test_anchors_that_documents_use_exist(self):
        def slug(heading):
            heading = re.sub(r"[`*]", "", heading).strip().lower()
            heading = re.sub(r"[^\w\- ]", "", heading)
            return heading.replace(" ", "-")

        def anchors(doc):
            return {slug(h) for h in re.findall(r"^#{1,6} (.+)$", read(doc), re.M)}

        missing = []
        for path in text_files():
            if not path.endswith(".md"):
                continue
            doc = os.path.relpath(path, ROOT)
            base = os.path.dirname(doc)
            for target, anchor in re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", read(doc)):
                if target.startswith(("http:", "https:")):
                    continue
                other = os.path.normpath(os.path.join(base, target)) if target else doc
                if anchor not in anchors(other):
                    missing.append("%s -> %s#%s" % (doc, other, anchor))
        self.assertEqual(missing, [])

    def test_the_docs_use_the_real_directory_name(self):
        text = read("README.md") + read("DEPLOY.md")
        self.assertIn("git clone https://github.com/janreimen/Domoticz-Rainstats.git", text)

    def test_nothing_is_left_over_from_the_plugins_this_one_was_modelled_on(self):
        pattern = re.compile(r"pi-?hole|synosnmp|synology|snmp|openaq|myskoda|voyo|xorfor|meteoalarm|lu[-_ ]?alert|"
                             r"cap-lu|data\.public\.lu", re.I)
        found = []
        for path in text_files() + glob.glob(os.path.join(ROOT, "tests", "*.py")) + code_files():
            name = os.path.relpath(path, ROOT)
            if name == "tests/test_repo.py":                    # this file holds the pattern
                continue
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if pattern.search(line):
                        found.append("%s:%d" % (name, number))
        self.assertEqual(found, [])

    def test_the_documents_name_no_assistant_or_tool_that_wrote_them(self):
        pattern = re.compile(r"claude|anthropic|chatgpt|openai|copilot|co-authored|generated with|\bAI\b|\bLLM\b", re.I)
        found = []
        for path in text_files() + code_files():
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if pattern.search(line):
                        found.append("%s:%d: %s" % (os.path.relpath(path, ROOT), number, line.strip()[:80]))
        self.assertEqual(found, [])

    def test_no_host_or_account_of_the_author_is_in_the_repository(self):
        # the documents and examples use generic names; the author's machines, paths and user names stay out of the package
        pattern = re.compile(r"rpi4|rpi[0-9]|jan@|@rpi|/home/(?!user)|/root/|tmp/claude|live_ext|/mnt/", re.I)
        for name in ("README.md", "DEPLOY.md", "CHANGELOG.md", "CONTRIBUTING.md", "checklist.md", "ROADMAP.md", "SECURITY.md",
                     "Makefile", "deploy.sh", ".env.sample", "docs/DOMOTICZ-API.md", "docs/DEVICES.md", "plugin.py"):
            self.assertIsNone(pattern.search(read(*name.split("/"))), name)

    def test_the_package_makes_no_use_of_the_working_directory(self):
        # Domoticz starts plugins from its own directory: every file the plugin touches is found from __file__ or is
        # an explicit setting
        text = source_text()
        self.assertNotIn("os.getcwd", text)
        self.assertNotIn("os.chdir", text)


class DocumentationTests(unittest.TestCase):
    def test_troubleshooting_messages_exist_in_the_code_and_in_the_readme(self):
        # the README quotes log messages: if one is reworded in the code, the table must follow. A fragment is
        # always inside one string literal of the code, so that a source search finds it
        fragments = [
            "is required: fill in the IDX of your rain device", "Update interval must be a number of seconds",
            "must be between", "unknown setting", "Domoticz refuses the request", "is Domoticz listening on",
            "no answer within", "the answer is not JSON", "TLS error: the certificate of the server is not trusted",
            "Domoticz has no device with this IDX", "the device has no rain value", "the device has no rain rate",
            "Reading the rain device failed", "The rain device can be read again",
            "the sensor was restarted,", "in the middle of the day",
            "is more than the limit of", "(RS_MAX_DELTA)", "sensor error (see RS_RATE_DIVISOR)",
            "Domoticz marks the rain device as not reporting (sensor time-out)", "starting with an empty archive",
            "Cannot write the state file", "The statistics stay in memory and are lost when the ",
            "Cannot import the data file of RainStats V2.6", "Domoticz did not create the device",
            "Accept new Hardware Devices", "Cannot update Domoticz unit", "did not end within",
            "the plugin did not start, see the errors above", "Unexpected error in"]
        code, readme = source_text(), read("README.md")
        for fragment in fragments:
            self.assertIn(fragment, code, "no longer in the code: " + fragment)
        for fragment in fragments:
            if fragment == "The statistics stay in memory and are lost when the ":
                fragment = "The statistics stay in memory and are lost when the plugin stops"
            self.assertIn(fragment, readme, "not in the README troubleshooting table: " + fragment)

    def test_the_readme_quotes_the_http_message_as_the_code_builds_it(self):
        self.assertIn('"%s: HTTP %d %s: Domoticz refuses the request.', source_text())
        self.assertIn("HTTP 403 Forbidden: Domoticz refuses the request", read("README.md"))
        self.assertIn("Domoticz API (IDX %d)", source_text())

    def test_deploy_guide_quotes_the_start_up_messages_the_code_writes(self):
        code, deploy = source_text(), read("DEPLOY.md")
        for fragment in ("starting (Domoticz at", "Connected to Domoticz at", "No usable state file yet", "Loaded the state file",
                         "Imported the data file of RainStats V2.6", "Domoticz did not create the device"):
            self.assertIn(fragment, code, "no longer in the code: " + fragment)
            self.assertIn(fragment, deploy, "not in DEPLOY.md: " + fragment)

    def test_readme_names_the_hardware_type_of_the_manifest(self):
        name = re.search(r'<plugin\b[^>]*\bname="([^"]+)"', read("plugin.py")).group(1)
        self.assertEqual(name, c.PLUGIN_NAME)
        self.assertIn(name, read("README.md"))
        self.assertIn(name, read("DEPLOY.md"))

    def test_the_readme_lists_every_hardware_field_of_the_manifest(self):
        readme = read("README.md")
        for label in re.findall(r'<param\b[^>]*\blabel="([^"]+)"', read("plugin.py")):
            first = re.split(r"[:(]", label)[0].strip()
            self.assertIn(first, readme, "hardware field not in the README: " + label)

    def test_the_readme_lists_every_env_key_with_its_default_and_range(self):
        readme = read("README.md")
        defaults = Settings()
        shown = {"RS_HTTP_TIMEOUT": defaults.http_timeout, "RS_SAVE_INTERVAL": defaults.save_interval,
                 "RS_REFRESH_INTERVAL": defaults.refresh_interval, "RS_RATE_DIVISOR": defaults.rate_divisor,
                 "RS_MAX_DELTA": defaults.max_delta, "RS_SCHEME": defaults.scheme}
        for key, spec in ENV_SPECS.items():
            self.assertRegex(readme, r"\| `%s` \|" % key)
            if key in shown:
                row = re.search(r"\| `%s` \| ([^|]+) \|[^\n]*" % key, readme).group(0)
                self.assertIn("| %s |" % (number_text(shown[key], 0) if isinstance(shown[key], (int, float)) else shown[key]),
                              row, key)
                if spec.kind in ("int", "float"):
                    self.assertIn("(%s-%s)" % (number_text(spec.low, 0), number_text(spec.high, 0)), row, key)

    def test_the_sample_env_file_documents_the_defaults_and_nothing_else(self):
        # every commented example line is a valid value, and the default of the sample is the default of the code
        text = read(".env.sample")
        keys = re.findall(r"(?m)^# (RS_[A-Z_]+)=(.*)$", text)
        self.assertEqual(sorted(k for k, _ in keys), sorted(ENV_SPECS))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, ".env")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join("%s=%s" % pair for pair in keys))
            saved = {k: os.environ.pop(k) for k in list(os.environ) if k in ENV_SPECS}
            try:
                from_sample = Settings.from_parameters({"Mode1": "1"}, env_path=path)
                plain = Settings.from_parameters({"Mode1": "1"}, env_path=os.path.join(folder, "none"))
            finally:
                os.environ.update(saved)
        for attribute in ("scheme", "ca_file", "http_timeout", "save_interval", "refresh_interval", "rate_divisor",
                          "max_delta"):
            self.assertEqual(getattr(from_sample, attribute), getattr(plain, attribute), attribute)
        self.assertEqual(from_sample.warnings, [])
        # the one example that has no default: the history is off unless a device is named; the sample names one
        self.assertIsNone(plain.history_idx)
        self.assertEqual(from_sample.history_idx, 1161)
        # the window lines are active in the sample and say what the code does without a .env
        self.assertEqual(from_sample.windows, plain.windows)
        for window in c.WINDOWS:
            self.assertRegex(text, r"(?m)^%s=%s$" % (window.env_key, "Y" if window.default else "N"))

    def test_readme_device_table_matches_the_code(self):
        readme = read("README.md")
        self.assertIn("| 1-8 | Rain 1 d, 2 d, 7 d, 14 d, 30 d, 90 d, 180 d, 365 d |", readme)
        self.assertIn("| 9-16 | Peak Rain Rate 1 d ... 365 d |", readme)
        self.assertIn("| 19-22 | Rain 24 d, 60 d, 120 d, 360 d |", readme)
        self.assertIn("| 23-26 | Peak Rain Rate 24 d, 60 d, 120 d, 360 d |", readme)
        self.assertIn("| %d | Rain Stats Status |" % c.UNIT_STATUS, readme)
        self.assertIn("| %d | Rain Stats Info |" % c.UNIT_INFO, readme)
        # the units of the table are the units of the code
        default = [w for w in c.WINDOWS if w.default]
        self.assertEqual(sorted(w.rain_unit for w in default), list(range(1, 9)))
        self.assertEqual(sorted(w.peak_unit for w in default), list(range(9, 17)))
        self.assertEqual(sorted(w.rain_unit for w in c.WINDOWS if not w.default), list(range(19, 23)))
        self.assertEqual(sorted(w.peak_unit for w in c.WINDOWS if not w.default), list(range(23, 27)))
        self.assertEqual(len(c.ALL_DEVICES), 26)
        self.assertEqual(len(c.RAIN_DEVICES) + len(c.PEAK_DEVICES) + len(c.FIXED_DEVICES), 26)
        self.assertEqual(2 * len(default) + len(c.FIXED_DEVICES), 18)
        self.assertIn("18 devices are created by default", readme)

    def test_the_readme_names_every_window_and_the_rings_as_the_code_has_them(self):
        readme = read("README.md")
        self.assertIn("1, 2, 7, 14, 24, 30, 60, 90, 120, 180, 360 and 365 days", readme)
        self.assertEqual([w.days for w in c.WINDOWS], [1, 2, 7, 14, 24, 30, 60, 90, 120, 180, 360, 365])
        self.assertEqual([w.label for w in c.WINDOWS], ["1 d", "2 d", "7 d", "14 d", "24 d", "30 d", "60 d", "90 d",
                                                        "120 d", "180 d", "360 d", "365 d"])
        self.assertEqual([w.days for w in c.WINDOWS if w.default], [1, 2, 7, 14, 30, 90, 180, 365])
        for window in c.WINDOWS:
            # one row of "Choose the windows" per window: the .env line, the default and the two units
            row = re.search(r"\| `%s` \| [^|]+ \| (Y|N) \| (\d+), (\d+) \|" % window.env_key, readme)
            self.assertIsNotNone(row, window.env_key)
            self.assertEqual((row.group(1) == "Y", int(row.group(2)), int(row.group(3))),
                             (window.default, window.rain_unit, window.peak_unit), window.env_key)
        fine, coarse = c.TIERS
        self.assertEqual((fine.resolution, fine.slots, coarse.resolution, coarse.slots), (300, 2016, 900, 35040))
        self.assertIn("2016 slots × 5 min = 7 days", readme)
        self.assertIn("35040 slots × 15 min = 365 days", readme)
        self.assertIn("coarse ring: 35040 slots", readme)
        self.assertIn("answers 1 d, 2 d, 7 d ", readme)
        self.assertIn("answers 14 d, 24 d, 30 d, 60 d, 90 d, 120 d, 180 d, 360 d, 365 d", readme)
        self.assertIn("allowed %d to %d" % (c.MIN_INTERVAL, c.MAX_INTERVAL), readme)
        self.assertIn("Default %d," % c.DEFAULT_INTERVAL, readme)

    def test_the_readme_describes_the_history_set_up_as_the_code_does_it(self):
        readme, code = read("README.md"), source_text()
        self.assertIn("## The one-time history set-up", readme)
        self.assertIn("type=command&param=graph&sensor=rain&idx=%d&range=year", code)
        self.assertIn("python3 -m rain_stats --history", readme)
        self.assertRegex(readme, r"\| `RS_HISTORY_IDX` \| \(none\) \|[^\n]*\(1-999999999\)")
        self.assertEqual(ENV_SPECS["RS_HISTORY_IDX"].low, 1)
        self.assertEqual(ENV_SPECS["RS_HISTORY_IDX"].high, 999999999)
        for fragment in ("Reading the daily rain history of IDX", "Imported the daily rain history of IDX",
                         "Reading the rain history failed", "Cannot use the rain history",
                         "Reading the rain history did not end within", "It is tried again at the next reading",
                         "is in the state file: ", "it is not read again",
                         "does this Domoticz know the graph command",
                         "must be Y or N, got", "All the windows are switched off: at least one of the lines"):
            self.assertIn(fragment, code, "no longer in the code: " + fragment)
            self.assertIn(fragment, readme, "not in the README: " + fragment)

    def test_the_counting_limits_the_documents_state_are_the_ones_of_the_code(self):
        readme, sample = read("README.md"), read(".env.sample")
        self.assertIn("at most 30 minutes apart", readme)
        self.assertEqual(c.MAX_RATE_GAP, 30 * 60)
        self.assertIn("more than %d mm/h" % c.MAX_PLAUSIBLE_RATE, readme)
        self.assertIn("RS_MAX_DELTA=%s" % number_text(c.DEFAULT_MAX_DELTA, 0), sample)
        self.assertIn("RS_SAVE_INTERVAL=%d" % c.DEFAULT_SAVE_INTERVAL, sample)
        self.assertIn("RS_REFRESH_INTERVAL=%d" % c.REFRESH_SECONDS, sample)

    def test_the_documents_say_what_was_seen_in_a_real_domoticz_and_what_was_not(self):
        # the most important part of the documentation: the line between what was run and what was only tested
        readme = read("README.md")
        self.assertIn("Domoticz 2026.4", readme)
        self.assertRegex(readme, r"days of use, not months")
        self.assertIn("**Seen in a real Domoticz**", readme)
        self.assertIn("**Not verified:**", readme)
        self.assertIn("What is verified and what is not", readme)
        self.assertNotIn("never run in a real Domoticz", readme)       # no longer true: do not let it come back
        self.assertIn("2026.4", read("CHANGELOG.md"))

    def test_the_documents_agree_that_deploy_and_rollback_leave_the_statistics_alone(self):
        for name in ("README.md", "DEPLOY.md", "checklist.md"):
            self.assertRegex(read(name), r"(?i)statistics", name)
        self.assertIn("rainstats_state.json", read("DEPLOY.md"))
        self.assertIn("rainstats_state.json*", read("deploy.sh"))


class GeneratedDocsTests(unittest.TestCase):
    def test_device_catalogue_is_up_to_date(self):
        import gen_device_catalog
        self.assertEqual(read("docs", "DEVICES.md"), gen_device_catalog.render(),
                         "docs/DEVICES.md is stale: run python3 tools/gen_device_catalog.py")

    def test_the_catalogue_lists_every_device_and_every_window(self):
        text = read("docs", "DEVICES.md")
        for spec in c.ALL_DEVICES:
            self.assertIn("| %d | %s |" % (spec.unit, spec.name), text)
        for window in c.WINDOWS:
            self.assertIn("| %s |" % window.label, text)


if __name__ == "__main__":
    unittest.main()
