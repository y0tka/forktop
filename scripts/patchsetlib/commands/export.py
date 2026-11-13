"""Regenerate patches/ and series from the applied StGit stack."""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..errors import CheckError
from ..gitops import (
    refs_patches,
    require_clean,
    stack_exists,
    stg_series,
    submodule_paths,
)
from ..log import step, success, warn
from ..patches import (
    atomic_write_bytes,
    generate_stable_patch,
    list_patch_files,
    parse_patch_name,
    read_series,
    relative_patch_path,
    series_entry_for_patch,
    validate_patch_for_export,
    write_series,
)
from ..state import read_rebase, require_dev_mode


def _guard(repo: Path) -> None:
    pending = read_rebase(repo)
    if pending is not None and pending.get("phase") != "checks":
        raise CheckError(
            "export is disabled while a rebase is conflicted; finish it first"
        )


def _current_series() -> list[str]:
    try:
        return read_series()
    except CheckError:
        return []


def _compare(
    expected: dict[str, bytes],
    entries: list[str],
) -> list[str]:
    problems: list[str] = []
    existing = {relative_patch_path(path) for path in list_patch_files()}
    for entry in entries:
        path = config.PATCHES / entry
        if not path.is_file():
            problems.append(f"missing {entry}")
            continue
        actual = path.read_bytes()
        if actual != expected[str(path.resolve())]:
            problems.append(f"drift {entry}")
    for entry in sorted(existing - set(entries)):
        problems.append(f"orphan {entry}")
    current = _current_series()
    if not config.SERIES.is_file():
        problems.append("missing series")
    elif current != entries:
        if set(current) == set(entries):
            problems.append("series order mismatch")
        else:
            problems.append(
                f"series mismatch (on disk {current!r}, expected {entries!r})"
            )
    return problems


def run(check_only: bool, allow_dirty: bool) -> None:
    repo = config.worktree_path()
    require_dev_mode(repo)
    _guard(repo)

    if not stack_exists(repo, config.BRANCH):
        raise CheckError("no StGit stack; run `make setup`")
    all_patches = stg_series(repo, "--all")
    applied = stg_series(repo, "--applied")
    if applied != all_patches:
        raise CheckError(
            "refusing to export while patches are unapplied; push the stack first"
        )
    if not allow_dirty:
        require_clean(repo, "export")

    submodules = submodule_paths(repo)
    refs = refs_patches(repo, config.BRANCH)
    expected: dict[Path, bytes] = {}
    entries: list[str] = []
    for name in applied:
        _group, _short, entry = parse_patch_name(name)
        commit = refs.get(name)
        if not commit:
            raise CheckError(f"no commit for applied patch {name}")
        text = generate_stable_patch(repo, commit)
        validate_patch_for_export(text, name, submodules)
        target = config.PATCHES / entry
        if target.resolve() in {path.resolve() for path in expected}:
            raise CheckError(f"duplicate export target for {name}: {entry}")
        expected[target] = text.encode("utf-8", "surrogateescape")
        entries.append(series_entry_for_patch(name))

    keyed = {str(path.resolve()): data for path, data in expected.items()}

    if check_only:
        problems = _compare(keyed, entries)
        if problems:
            for problem in problems:
                warn(problem)
            raise CheckError("export is not up to date; run `make export`")
        success(f"Export is up to date ({len(entries)} patches)")
        return

    changed = 0
    for path, data in expected.items():
        previous = path.read_bytes() if path.is_file() else None
        if previous == data:
            continue
        atomic_write_bytes(path, data)
        step(f"Exported {relative_patch_path(path)}")
        changed += 1

    expected_paths = {path.resolve() for path in expected}
    for path in list_patch_files():
        if path.resolve() not in expected_paths:
            step(f"Removing stale {relative_patch_path(path)}")
            path.unlink()

    write_series(entries)
    step(f"Wrote {config.SERIES.name}")
    success(f"Exported {len(entries)} patches ({changed} changed)")
