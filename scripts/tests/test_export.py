from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patchsetlib import config, state
from patchsetlib.commands import export as export_command
from patchsetlib.errors import CheckError

from tests.support import (
    make_repo_with_patch,
    make_root,
    require_stg,
    stg,
    write,
)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        require_stg(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        self.root = make_root(tmp)
        self.repo, self.base, _patch_commit = make_repo_with_patch(
            tmp, patch_name="feature__one", patched_text="patched\n"
        )
        self.patches = self.root / "patches"
        self.series = self.root / "series"
        self.env = mock.patch.dict(
            os.environ, {"FORKTOP_WORKTREE": str(self.repo)}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (
            ("PATCHES", self.patches),
            ("SERIES", self.series),
            ("ROOT", self.root),
        ):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        state.write_state(
            self.repo,
            {
                "mode": "dev",
                "base": self.base,
                "tag": "v1.0.0",
                "branch": "forktop",
                "patches": ["feature__one"],
                "deps": [],
            },
        )

    def test_export_writes_patch_and_series(self) -> None:
        export_command.run(check_only=False, allow_dirty=False)
        self.assertEqual(self.series.read_text(), "feature/one.patch\n")
        self.assertTrue((self.patches / "feature" / "one.patch").is_file())
        export_command.run(check_only=True, allow_dirty=False)

    def test_unapplied_stack_is_refused(self) -> None:
        stg(self.repo, "pop", "-a")
        with self.assertRaises(CheckError):
            export_command.run(check_only=False, allow_dirty=False)

    def test_dirty_worktree_is_refused(self) -> None:
        write(self.repo / "untracked.txt", "dirty\n")
        with self.assertRaises(CheckError):
            export_command.run(check_only=False, allow_dirty=False)

    def test_stale_files_survive_a_failed_new_write(self) -> None:
        stg(self.repo, "new", "feature__two", "-m", "Second patch")
        write(self.repo / "second.txt", "second\n")
        from tests.support import git

        git(self.repo, "add", "--", "second.txt")
        stg(self.repo, "refresh")
        export_command.run(check_only=False, allow_dirty=False)
        one = self.patches / "feature" / "one.patch"
        two = self.patches / "feature" / "two.patch"
        self.assertTrue(one.is_file())
        self.assertTrue(two.is_file())
        one.unlink()
        two.unlink()
        stale = self.patches / "feature" / "stale.patch"
        write(stale, "stale\n")
        with mock.patch.object(
            export_command, "atomic_write_bytes", side_effect=OSError("boom")
        ), self.assertRaises(OSError):
            export_command.run(check_only=False, allow_dirty=False)
        self.assertTrue(stale.is_file())
        self.assertEqual(self.series.read_text(), "feature/one.patch\nfeature/two.patch\n")


if __name__ == "__main__":
    unittest.main()
