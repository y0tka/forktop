"""Pre-commit gate for generated files and the pin pair."""

from __future__ import annotations

import os

from .. import config
from ..errors import CheckError
from ..gitops import stack_base, stack_exists, stg_series, tag_commit
from ..patches import read_pin, read_tag
from ..proc import git
from . import export as export_command

RELEVANT = ("patches/", "series", "upstream-commit", "upstream-tag")


def _staged_paths() -> list[str]:
    result = git(
        "diff",
        "--cached",
        "--name-only",
        "-z",
        cwd=config.ROOT,
        check=False,
    )
    return [path for path in result.stdout.split("\0") if path]


def _is_relevant(path: str) -> bool:
    return path in {"series", "upstream-commit", "upstream-tag"} or path.startswith(
        "patches/"
    )


def run() -> None:
    if os.environ.get("FORKTOP_SKIP_CHECK"):
        return
    staged = _staged_paths()
    if any(path.startswith(("tdesktop/", "inugram/")) for path in staged):
        raise CheckError("refusing to commit the base checkout or reference checkout")
    if not any(_is_relevant(path) for path in staged):
        return

    pin_staged = "upstream-commit" in staged
    tag_staged = "upstream-tag" in staged
    if pin_staged != tag_staged:
        raise CheckError("upstream-commit and upstream-tag must be staged together")

    worktree = config.worktree_path()
    if not (worktree / ".git").exists():
        raise CheckError(f"base checkout is missing: {worktree}")

    if pin_staged:
        pin = read_pin()
        tag = read_tag()
        resolved = tag_commit(worktree, tag)
        if resolved != pin:
            raise CheckError(f"{tag} resolves to {resolved}, expected {pin}")
        if not stack_exists(worktree, config.BRANCH):
            raise CheckError("no StGit stack; run `make setup`")
        applied = stg_series(worktree, "--applied")
        base = stack_base(worktree, config.BRANCH, applied)
        if base != pin:
            raise CheckError(f"stack base is {base}, expected pin {pin}")

    export_command.run(check_only=True, allow_dirty=False)
