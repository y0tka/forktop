"""Print tool and checkout prerequisites."""

from __future__ import annotations

import re
import shutil
import sys

from .. import config
from ..errors import CheckError
from ..gitops import tag_commit
from ..log import error, success, warn
from ..patches import read_pin, read_tag
from ..proc import git, run

VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _version(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(text)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def run_doctor() -> None:
    ok = True
    if sys.version_info >= (3, 10):  # noqa: UP036
        success(f"python {sys.version.split()[0]}")
    else:
        error("python 3.10+ is required")
        ok = False

    git_result = git("--version", cwd=config.ROOT, check=False)
    git_version = _version(git_result.stdout) if git_result.ok else None
    if git_version and git_version >= (2, 36, 0):
        success(f"git {git_result.stdout.strip()}")
    else:
        error("git 2.36+ is required")
        ok = False

    if shutil.which("stg") is None:
        warn("stgit is not installed; dev setup/export/rebase are unavailable")
    else:
        stg_result = run(["stg", "--version"], cwd=config.ROOT, check=False)
        stg_version = _version(stg_result.stdout + stg_result.stderr)
        if stg_version and stg_version >= (2, 0, 0):
            success(f"stgit {'.'.join(str(part) for part in stg_version)}")
        else:
            warn("stgit 2.0+ is recommended; install a current release")

    if shutil.which("docker") is None:
        warn("docker is not installed; local Linux builds and image steps are unavailable")
    else:
        success("docker")

    worktree = config.worktree_path()
    if (worktree / ".git").exists():
        success(f"base checkout {worktree}")
        try:
            pin = read_pin()
            tag = read_tag()
        except CheckError as exc:
            warn(str(exc))
        else:
            resolved = tag_commit(worktree, tag)
            if resolved == pin:
                success(f"pin {pin[:12]} matches {tag}")
            else:
                warn(f"pin {pin[:12]} does not match {tag} ({resolved})")
    else:
        warn(f"base checkout is absent: {worktree}")

    if not ok:
        raise CheckError("doctor found missing prerequisites")
