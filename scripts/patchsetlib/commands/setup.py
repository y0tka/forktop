"""Create or reuse the base checkout and its StGit stack."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from ..errors import CheckError, PrereqError
from ..gitops import (
    apply_deps,
    apply_series,
    checkout_detached,
    commit_exists,
    ensure_upstream_remote,
    fetch_tag,
    head_commit,
    init_stg,
    local_branch_exists,
    require_clean,
    require_stg,
    stack_base,
    stack_exists,
    stg_series,
    sync_submodules,
    tag_commit,
)
from ..log import step, success, warn
from ..patches import (
    dep_names,
    load_deps,
    patch_name_from_series,
    read_pin,
    read_series,
    read_tag,
    series_entry_for_patch,
    series_paths,
)
from ..proc import git, stg
from ..state import (
    read_state,
    refuse_pending_rebase,
    state_path,
    write_state,
)


def _series_names() -> tuple[list[str], list[str]]:
    entries = read_series()
    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        name = patch_name_from_series(entry)
        if name in seen:
            raise CheckError(f"duplicate series patch name: {name}")
        seen.add(name)
        if not (config.PATCHES / entry).is_file():
            raise CheckError(f"series references a missing patch: {entry}")
        names.append(name)
    return entries, names


def _is_prefix(prefix: list[str], values: list[str]) -> bool:
    return len(prefix) <= len(values) and prefix == values[: len(prefix)]


def _ensure_worktree(pin: str, tag: str) -> Path:
    worktree = config.worktree_path()
    if not worktree.exists():
        worktree.parent.mkdir(parents=True, exist_ok=True)
        step(f"Cloning {config.UPSTREAM_URL} into {worktree}")
        result = git("clone", config.UPSTREAM_URL, str(worktree), cwd=config.ROOT, check=False)
        if not result.ok:
            raise PrereqError(
                f"could not clone {config.UPSTREAM_URL}:\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
    elif not (worktree / ".git").is_dir():
        raise PrereqError(
            f"{worktree} exists but is not a git checkout; move it aside and rerun"
        )
    else:
        step(f"Reusing existing checkout in {worktree}")

    ensure_upstream_remote(worktree)

    if not commit_exists(worktree, pin) or tag_commit(worktree, tag) is None:
        step(f"Fetching upstream tag {tag}")
        fetch_tag(worktree, tag)

    if not commit_exists(worktree, pin):
        raise CheckError(f"pinned base commit is not present: {pin}")
    resolved = tag_commit(worktree, tag)
    if resolved is None:
        raise CheckError(f"upstream tag is missing after fetch: {tag}")
    if resolved != pin:
        raise CheckError(f"upstream-tag {tag} resolves to {resolved}, expected {pin}")
    return worktree


def _switch_branch(worktree: Path, pin: str) -> None:
    if local_branch_exists(worktree, config.BRANCH):
        step(f"Checking out {config.BRANCH}")
        git("checkout", config.BRANCH, cwd=worktree)
    else:
        step(f"Creating {config.BRANCH} at {pin}")
        git("checkout", "-b", config.BRANCH, pin, cwd=worktree)


def _configure(worktree: Path, deps: list[dict[str, str]]) -> None:
    settings = {
        "stgit.namelength": "120",
        "commit.gpgsign": "false",
        "tag.gpgsign": "false",
        "diff.ignoreSubmodules": "dirty",
        "core.autocrlf": "false",
        "core.untrackedCache": "true",
    }
    for key, value in settings.items():
        git("config", key, value, cwd=worktree)
    for submodule in dep_names(deps):
        git("config", f"submodule.{submodule}.ignore", "dirty", cwd=worktree)


def _import_series(worktree: Path, names: list[str]) -> None:
    for name in names:
        entry = series_entry_for_patch(name)
        patch = config.PATCHES / entry
        step(f"Importing {entry} as {name}")
        stg("import", "-n", name, str(patch), cwd=worktree)


def _verify_base(worktree: Path, pin: str, expected: list[str]) -> None:
    base = stack_base(worktree, config.BRANCH, expected)
    if base != pin:
        raise CheckError(
            f"stack base is {base}, expected pin {pin}; "
            "run setup --force or inspect the stack manually"
        )


def _reconcile_stack(worktree: Path, expected: list[str], pin: str) -> None:
    if not stack_exists(worktree, config.BRANCH):
        step("Initializing stgit")
        init_stg(worktree)

    all_patches = stg_series(worktree, "--all")
    applied = stg_series(worktree, "--applied")

    if all_patches == expected:
        if applied != expected:
            step("Pushing remaining patches")
            stg("push", "-a", cwd=worktree)
    elif _is_prefix(all_patches, expected):
        if applied != all_patches:
            stg("push", "-a", cwd=worktree)
        _import_series(worktree, expected[len(all_patches) :])
    elif not all_patches:
        _import_series(worktree, expected)
    else:
        raise CheckError(
            "existing StGit stack diverges from series; run setup --force"
        )

    applied = stg_series(worktree, "--applied")
    if applied != expected:
        raise CheckError(
            f"stack is {applied!r}, expected {expected!r}; "
            "run setup --force or inspect the stack manually"
        )
    _verify_base(worktree, pin, expected)


def _force_rebuild(worktree: Path, expected: list[str], pin: str) -> None:
    if stack_exists(worktree, config.BRANCH):
        applied = stg_series(worktree, "--applied")
        if applied:
            step(f"Popping {len(applied)} patch(es)")
            stg("pop", "-a", cwd=worktree)
        all_patches = stg_series(worktree, "--all")
        for name in reversed(all_patches):
            step(f"Deleting {name}")
            result = stg("delete", name, cwd=worktree, check=False)
            if not result.ok:
                raise CheckError(
                    f"could not delete patch {name}:\n"
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
    if head_commit(worktree) != pin:
        raise CheckError(
            "branch is not at the pinned base; refusing to reset it. "
            "Inspect the worktree manually."
        )
    if not stack_exists(worktree, config.BRANCH):
        step("Initializing stgit")
        init_stg(worktree)
    _import_series(worktree, expected)


def _write_dev_state(
    worktree: Path, pin: str, tag: str, expected: list[str], deps: list[dict[str, str]]
) -> None:
    write_state(
        worktree,
        {
            "mode": "dev",
            "base": pin,
            "tag": tag,
            "branch": config.BRANCH,
            "patches": expected,
            "deps": dep_names(deps),
        },
    )
    step(f"Wrote state to {state_path(worktree)}")


def setup_dev(force: bool, no_submodules: bool) -> None:
    pin = read_pin()
    tag = read_tag()
    worktree = config.worktree_path()
    if worktree.exists() and (worktree / ".git").is_dir():
        refuse_pending_rebase(worktree, "set up")
    if force and worktree.exists() and (worktree / ".git").is_dir():
        require_clean(worktree, "rebuild the stack")

    worktree = _ensure_worktree(pin, tag)

    if worktree.exists() and (worktree / ".git").is_dir():
        refuse_pending_rebase(worktree, "set up")
    require_clean(worktree, "switch branches")
    _switch_branch(worktree, pin)

    deps = [] if no_submodules else load_deps()
    _configure(worktree, deps)

    _entries, expected = _series_names()
    require_stg()
    if force:
        _force_rebuild(worktree, expected, pin)
    else:
        _reconcile_stack(worktree, expected, pin)

    if no_submodules:
        warn("skipping submodules and deps (--no-submodules)")
        deps = []
    else:
        if deps or (worktree / ".gitmodules").is_file():
            if sync_submodules(worktree, deps):
                step("Submodules synced")
            count = apply_deps(worktree, deps)
            if count:
                step(f"Applied {count} deps patch(es)")

    _write_dev_state(worktree, pin, tag, expected, deps)
    success(f"Base checkout ready at {worktree}")


def _remove_previous_ci(worktree: Path, force: bool) -> bool:
    if not worktree.exists():
        return False
    entries = list(worktree.iterdir()) if worktree.is_dir() else []
    if not entries:
        return False
    state = read_state(worktree)
    default_dev = (config.ROOT / "tdesktop").resolve()
    if (
        force
        and state is not None
        and state.get("mode") == "ci"
        and worktree.resolve() != default_dev
    ):
        step(f"Removing previous CI checkout at {worktree}")
        shutil.rmtree(worktree)
        return False
    raise PrereqError(
        f"--ci needs an absent or empty worktree; {worktree} already exists"
    )


def setup_ci(force: bool, no_submodules: bool) -> None:
    pin = read_pin()
    tag = read_tag()
    worktree = config.worktree_path()
    _remove_previous_ci(worktree, force)
    worktree.mkdir(parents=True, exist_ok=True)

    step(f"Initializing disposable checkout at {worktree}")
    git("init", "-q", cwd=worktree)
    git("remote", "add", "upstream", config.UPSTREAM_URL, cwd=worktree)
    git("config", "core.autocrlf", "false", cwd=worktree)
    step(f"Fetching {tag}")
    result = git(
        "fetch",
        "--depth",
        "1",
        "upstream",
        f"refs/tags/{tag}:refs/tags/{tag}",
        cwd=worktree,
        check=False,
    )
    if not result.ok:
        raise PrereqError(
            f"could not fetch tag {tag}:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    resolved = tag_commit(worktree, tag)
    if resolved != pin:
        raise CheckError(f"upstream-tag {tag} resolves to {resolved}, expected {pin}")
    checkout_detached(worktree, pin)

    entries, _names = _series_names()
    failing = apply_series(worktree, series_paths(entries))
    if failing is not None:
        raise CheckError(f"could not apply series: {failing}")

    deps = [] if no_submodules else load_deps()
    if no_submodules:
        warn("skipping submodules and deps (--no-submodules)")
        deps = []
    else:
        if (worktree / ".gitmodules").is_file():
            step("Updating submodules")
            result = git(
                "submodule", "update", "--init", "--recursive", cwd=worktree, check=False
            )
            if not result.ok:
                raise CheckError(
                    "submodule update failed:\n"
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
        count = apply_deps(worktree, deps)
        if count:
            step(f"Applied {count} deps patch(es)")

    patches = [patch_name_from_series(entry) for entry in entries]
    write_state(
        worktree,
        {
            "mode": "ci",
            "base": pin,
            "tag": tag,
            "branch": "(detached)",
            "patches": patches,
            "deps": dep_names(deps),
        },
    )
    success(f"CI checkout ready at {worktree}")


def run(ci: bool, force: bool, no_submodules: bool) -> None:
    """Entry point for `setup`."""
    # Validate state before creating anything so a pending rebase is never lost.
    worktree = config.worktree_path()
    if ci:
        if worktree.exists() and (worktree / ".git").is_dir():
            state = read_state(worktree)
            if state is not None and state.get("mode") == "dev":
                raise CheckError("refusing --ci on a dev-mode checkout")
        setup_ci(force, no_submodules)
    else:
        if worktree.exists() and (worktree / ".git").is_dir():
            refuse_pending_rebase(worktree, "set up")
        setup_dev(force, no_submodules)
