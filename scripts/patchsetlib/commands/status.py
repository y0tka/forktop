"""Human-readable recovery summary."""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..errors import CheckError
from ..gitops import (
    current_branch,
    head_commit,
    stack_exists,
    stg_series,
)
from ..log import warn
from ..patches import read_pin, read_tag, read_text
from ..proc import git, stg
from ..state import infer_mode, read_rebase, read_state
from . import export as export_command

MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _conflicted_files(repo: Path) -> list[str]:
    result = git("diff", "--name-only", "--diff-filter=U", cwd=repo, check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _marker_counts(repo: Path, files: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in files:
        path = repo / name
        try:
            text = read_text(path)
        except (OSError, UnicodeError):
            continue
        counts[name] = sum(text.count(marker) for marker in MARKERS)
    return counts


def run() -> None:
    repo = config.worktree_path()
    print(f"worktree  {repo}")
    if not (repo / ".git").exists():
        warn("base checkout is missing; run setup")
        return
    try:
        pin = read_pin()
    except CheckError as exc:
        pin = f"(invalid: {exc})"
    try:
        tag = read_tag()
    except CheckError as exc:
        tag = f"(invalid: {exc})"
    print(f"pin       {pin}")
    print(f"tag       {tag}")
    print(f"branch    {current_branch(repo) or '(detached)'}")
    print(f"head      {head_commit(repo)}")
    state = read_state(repo)
    print(f"mode      {state.get('mode') if state else infer_mode(repo)}")

    if stack_exists(repo, config.BRANCH):
        try:
            applied = stg_series(repo, "--applied")
            all_patches = stg_series(repo, "--all")
            print(f"applied   {len(applied)}")
            print(f"unapplied {len(all_patches) - len(applied)}")
        except CheckError as exc:
            print(f"stack     (unreadable: {exc})")
        top = stg("top", cwd=repo, check=False)
        if top.ok and top.stdout.strip():
            print(f"top       {top.stdout.strip()}")
    else:
        print("stack     (none)")

    pending = read_rebase(repo)
    if pending:
        print()
        print("pending rebase")
        print(f"  phase    {pending.get('phase')}")
        print(f"  oldBase  {pending.get('oldBase')}")
        print(f"  newBase  {pending.get('newBase')}")
        print(f"  tag      {pending.get('tag')}")
        print(f"  started  {pending.get('startedAt')}")
        if pending.get("phase") == "checks":
            print("  run `make finalize` after fixing any reported check")

    conflicts = _conflicted_files(repo)
    if conflicts:
        print()
        print("conflicted files")
        counts = _marker_counts(repo, conflicts)
        for name in conflicts:
            print(f"  {name} ({counts.get(name, 0)} marker lines)")
    elif pending and pending.get("phase") == "conflicted":
        print()
        print("no unmerged paths remain; run `make continue` after resolving markers")

    if state and state.get("mode") == "dev":
        print()
        try:
            export_command.run(check_only=True, allow_dirty=True)
        except CheckError as exc:
            print(f"export drift: {exc}")

    print()
    print("recovery")
    print("  Resolve conflicts in the base checkout, then run `make continue`.")
    print("  The pin/tag files are not updated until checks pass.")
    print("  There is no automatic abort: inspect `stg series`, `git reflog`, and")
    print("  `stg undo` in the base checkout. The pipeline never hard-resets.")
