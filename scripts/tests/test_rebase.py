from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patchsetlib import config, state
from patchsetlib.commands import export as export_command
from patchsetlib.commands import rebase as rebase_command
from patchsetlib.errors import CheckError

from tests.support import (
    commit,
    git,
    init_repo,
    make_root,
    require_stg,
    stg,
    write,
)


class RebaseTests(unittest.TestCase):
    def setUp(self) -> None:
        require_stg(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        self.root = make_root(tmp)
        self.bare = tmp / "upstream.git"
        git(tmp, "init", "--bare", "-q", str(self.bare))
        seed = init_repo(tmp / "seed")
        base = commit(seed, {"file.txt": "base\n", "other.txt": "base\n"}, "base")
        git(seed, "tag", "v1.0.0", base)
        git(seed, "push", "-q", str(self.bare), "HEAD:refs/heads/dev", "--tags")
        self.work = init_repo(tmp / "work")
        # A local clone with a normal upstream remote.
        git(self.work, "remote", "add", "upstream", str(self.bare))
        git(self.work, "fetch", "-q", "upstream", "refs/heads/dev:refs/remotes/upstream/dev", "--tags")
        git(self.work, "checkout", "-q", "-b", "forktop", "v1.0.0")
        stg(self.work, "init")
        self.base = base

        self.patches = self.root / "patches"
        self.series = self.root / "series"
        self.pin = self.root / "upstream-commit"
        self.tag = self.root / "upstream-tag"
        self.env = mock.patch.dict(os.environ, {"FORKTOP_WORKTREE": str(self.work)})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (
            ("PATCHES", self.patches),
            ("SERIES", self.series),
            ("PIN", self.pin),
            ("TAG", self.tag),
            ("ROOT", self.root),
        ):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pin.write_text(f"{base}\n")
        self.tag.write_text("v1.0.0\n")
        state.write_state(
            self.work,
            {
                "mode": "dev",
                "base": base,
                "tag": "v1.0.0",
                "branch": "forktop",
                "patches": ["feature__one"],
                "deps": [],
            },
        )

    def _add_patch(self, file_name: str = "other.txt", text: str = "fork\n") -> None:
        stg(self.work, "new", "feature__one", "-m", "Test patch")
        write(self.work / file_name, text)
        stg(self.work, "refresh")
        export_command.run(check_only=False, allow_dirty=False)

    def _publish_next(self, file_text: str) -> str:
        seed = Path(self.temp.name) / "seed"
        commit(seed, {"file.txt": file_text}, "next")
        next_commit = git(seed, "rev-parse", "HEAD").stdout.strip()
        git(seed, "tag", "v1.1.0", next_commit)
        git(seed, "push", "-q", str(self.bare), "HEAD:refs/heads/dev", "--tags")
        git(self.work, "fetch", "-q", "upstream", "refs/heads/dev:refs/remotes/upstream/dev", "--tags")
        return next_commit

    def test_successful_rebase_updates_pin_after_checks(self) -> None:
        self._add_patch(text="fork\n")
        new = self._publish_next("v2\n")
        rebase_command.rebase(self.work, "v1.1.0", False)
        self.assertEqual(self.pin.read_text().strip(), new)
        self.assertEqual(self.tag.read_text().strip(), "v1.1.0")
        self.assertIsNone(state.read_rebase(self.work))
        self.assertEqual(state.read_state(self.work)["base"], new)

    def test_conflict_leaves_pin_unchanged(self) -> None:
        self._add_patch(file_name="file.txt", text="fork\n")
        new = self._publish_next("upstream\n")
        with self.assertRaises(CheckError):
            rebase_command.rebase(self.work, "v1.1.0", False)
        self.assertEqual(self.pin.read_text().strip(), self.base)
        self.assertEqual(self.tag.read_text().strip(), "v1.0.0")
        pending = state.read_rebase(self.work)
        self.assertEqual(pending.get("phase"), "conflicted")
        self.assertEqual(pending.get("newBase"), new)

    def test_continue_resolves_and_updates_pin(self) -> None:
        self._add_patch(file_name="file.txt", text="fork\n")
        new = self._publish_next("upstream\n")
        with self.assertRaises(CheckError):
            rebase_command.rebase(self.work, "v1.1.0", False)
        write(self.work / "file.txt", "resolved\n")
        rebase_command.continue_rebase(self.work)
        self.assertEqual(self.pin.read_text().strip(), new)
        self.assertEqual(self.tag.read_text().strip(), "v1.1.0")
        self.assertIsNone(state.read_rebase(self.work))


if __name__ == "__main__":
    unittest.main()
