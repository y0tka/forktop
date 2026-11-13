from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patchsetlib import config
from patchsetlib.commands import changelog as changelog_command
from patchsetlib.errors import CheckError


class ChangelogTests(unittest.TestCase):
    def test_extract_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text(
                "# Changelog\n\n"
                "## v1.0.0-fork.1 - 2026-01-02\n\n"
                "- First thing.\n"
                "- Second thing.\n\n"
                "## v1.0.0-fork.2 - 2026-01-03\n\n"
                "- Next thing.\n"
            )
            with mock.patch.object(config, "CHANGELOG", path):
                self.assertEqual(
                    changelog_command.extract("v1.0.0-fork.1"),
                    "- First thing.\n- Second thing.\n",
                )
                with self.assertRaises(CheckError):
                    changelog_command.extract("v1.0.0-fork.9")

    def test_section_without_bullet_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text("## v1.0.0-fork.1 - 2026-01-02\n\nNothing here.\n")
            with mock.patch.object(config, "CHANGELOG", path), self.assertRaises(
                CheckError
            ):
                changelog_command.extract("v1.0.0-fork.1")


if __name__ == "__main__":
    unittest.main()
