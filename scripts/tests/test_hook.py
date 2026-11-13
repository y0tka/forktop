from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patchsetlib import config, state
from patchsetlib.commands import export as export_command
from patchsetlib.commands import hook_pre_commit
from patchsetlib.errors import CheckError

from tests.support import (
    git,
    init_repo,
    make_repo_with_patch,
    make_root,
    require_stg,
)


class PreCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        require_stg(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        self.root = make_root(tmp)
        init_repo(self.root)
        self.repo, self.base, _ = make_repo_with_patch(tmp)
        self.patches = self.root / "patches"
        self.series = self.root / "series"
        self.env = mock.patch.dict(os.environ, {"FORKTOP_WORKTREE": str(self.repo)})
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
        export_command.run(check_only=False, allow_dirty=False)
        git(self.root, "add", "--", "patches", "series")

    def test_drift_blocks_and_export_allows(self) -> None:
        patch = self.patches / "feature" / "one.patch"
        patch.write_bytes(patch.read_bytes() + b"\n# drift\n")
        git(self.root, "add", "--", "patches/feature/one.patch")
        with self.assertRaises(CheckError):
            hook_pre_commit.run()

        export_command.run(check_only=False, allow_dirty=False)
        git(self.root, "add", "--", "patches/feature/one.patch")
        hook_pre_commit.run()

    def test_skip_environment_bypasses(self) -> None:
        with mock.patch.dict(os.environ, {"FORKTOP_SKIP_CHECK": "1"}):
            hook_pre_commit.run()


if __name__ == "__main__":
    unittest.main()
