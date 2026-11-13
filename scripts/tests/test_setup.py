from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patchsetlib import config
from patchsetlib.commands import setup as setup_command
from patchsetlib.patches import generate_stable_patch

from tests.support import git, make_repo_with_patch, make_root, require_stg, stg


class SetupTests(unittest.TestCase):
    def test_force_reimports_the_exported_series(self) -> None:
        require_stg(self)
        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            root = make_root(tmp)
            repo, base, patch_commit = make_repo_with_patch(tmp)
            git(repo, "remote", "add", "upstream", config.UPSTREAM_URL)
            patch_dir = root / "patches" / "feature"
            patch_dir.mkdir(parents=True, exist_ok=True)
            (patch_dir / "one.patch").write_text(
                generate_stable_patch(repo, patch_commit)
            )
            (root / "series").write_text("feature/one.patch\n")
            (root / "upstream-commit").write_text(f"{base}\n")
            (root / "upstream-tag").write_text("v1.0.0\n")
            env = mock.patch.dict(os.environ, {"FORKTOP_WORKTREE": str(repo)})
            env.start()
            self.addCleanup(env.stop)
            for name, value in (
                ("ROOT", root),
                ("PATCHES", root / "patches"),
                ("SERIES", root / "series"),
                ("PIN", root / "upstream-commit"),
                ("TAG", root / "upstream-tag"),
            ):
                patcher = mock.patch.object(config, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
            setup_command.run(ci=False, force=True, no_submodules=True)
            series = stg(repo, "series", "--all").stdout.strip()
            self.assertEqual(series, "> feature__one")
            self.assertEqual(
                git(repo, "rev-parse", "refs/patches/forktop/feature__one^")
                .stdout.strip(),
                base,
            )


if __name__ == "__main__":
    unittest.main()
