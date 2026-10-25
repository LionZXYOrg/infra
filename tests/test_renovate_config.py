"""Regression tests for Renovate's custom dependency extractors.

These tests validate the repository-specific conventions without accessing
registries or credentials; Renovate still performs the actual updates.
"""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "renovate.json").read_text())


def matches(manager, path):
    """Match a custom manager's repository paths and named capture groups."""
    if not any(
        re.search(pattern[1:-1], str(path))
        for pattern in manager["managerFilePatterns"]
    ):
        return []
    text = (ROOT / path).read_text()
    result = []
    for pattern in manager["matchStrings"]:
        # Renovate uses RE2/JS-style named groups, not Python's syntax.
        python_pattern = pattern.replace("(?<", "(?P<")
        result.extend(match.groupdict() for match in re.finditer(python_pattern, text))
    return result


class RenovateConfigTests(unittest.TestCase):
    def test_requires_human_review_and_no_private_files(self):
        self.assertFalse(CONFIG["automerge"])
        self.assertIn("secrets/**", CONFIG["ignorePaths"])
        self.assertEqual(CONFIG["timezone"], "Europe/London")
        self.assertEqual(CONFIG["prConcurrentLimit"], 0)
        self.assertEqual(CONFIG["prHourlyLimit"], 0)

    def test_all_custom_regexes_compile(self):
        for manager in CONFIG["customManagers"]:
            for pattern in manager["managerFilePatterns"]:
                re.compile(pattern[1:-1])
            for pattern in manager["matchStrings"]:
                re.compile(pattern.replace("(?<", "(?P<"))

    def test_chart_versions_are_discoverable(self):
        samples = {
            "bjw-s app-template Helm chart": "apps/homeassist/helmrelease-homeassist.yaml",
            "Apache CouchDB Helm chart": "apps/obsidian/helmrelease-obsidian.yaml",
            "Vaultwarden Helm chart": "apps/warden/helmrelease-warden.yaml",
            "Immich OCI Helm chart": "apps/immich/helmrelease-immich.yaml",
        }
        for description, path in samples.items():
            with self.subTest(chart=description):
                manager = next(m for m in CONFIG["customManagers"] if m["description"] == description)
                self.assertTrue(matches(manager, Path(path)))
                self.assertTrue(all(m["currentValue"] for m in matches(manager, Path(path))))

    def test_split_tag_and_digest_are_bound_to_same_image(self):
        manager = next(
            m for m in CONFIG["customManagers"]
            if m["description"] == "Docker image tags with separate sha256 digests"
        )
        examples = {
            "apps/immich/helmrelease-immich.yaml": 2,
            "apps/homeassist/helmrelease-homeassist.yaml": 4,
            "apps/birthday-reminder/helmrelease-birthday-reminder.yaml": 2,
        }
        for path, expected in examples.items():
            with self.subTest(path=path):
                image_matches = matches(manager, Path(path))
                self.assertEqual(len(image_matches), expected)
                for image in image_matches:
                    self.assertTrue(image["depName"])
                    self.assertTrue(image["currentValue"])
                    self.assertRegex(image["currentDigest"], r"^sha256:[0-9a-f]{64}$")

    def test_inline_tag_and_digest(self):
        manager = next(
            m for m in CONFIG["customManagers"]
            if m["description"] == "Docker image tags containing sha256 digests"
        )
        for path, expected in [
            ("apps/obsidian/helmrelease-obsidian.yaml", 2),
            ("apps/warden/helmrelease-warden.yaml", 1),
        ]:
            with self.subTest(path=path):
                image_matches = matches(manager, Path(path))
                self.assertEqual(len(image_matches), expected)
                for image in image_matches:
                    self.assertTrue(image["currentValue"])
                    self.assertRegex(image["currentDigest"], r"^sha256:[0-9a-f]{64}$")

    def test_bootstrap_tool_versions_are_discoverable(self):
        managers = [
            manager
            for manager in CONFIG["customManagers"]
            if manager["description"].startswith("Pinned ")
        ]
        self.assertEqual(len(managers), 5)
        for manager in managers:
            with self.subTest(tool=manager["description"]):
                found = matches(manager, Path("bootstrap/versions.env"))
                self.assertEqual(len(found), 1)
                self.assertTrue(found[0]["currentValue"].startswith("v"))

    def test_scopes_do_not_scan_test_fixtures(self):
        for manager in CONFIG["customManagers"]:
            self.assertFalse(
                any(re.search(p[1:-1], "tests/isolated/demo/deployment.yaml")
                    for p in manager["managerFilePatterns"])
            )


if __name__ == "__main__":
    unittest.main()
