"""Move the StGit stack to a newer 64Gram release tag."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .. import config
from ..errors import CheckError, PatchsetError, PrereqError
from ..gitops import (
    apply_deps,
    fetch_latest_release_tag,
    fetch_tag,
    is_ancestor,
    remote_tag_commit,
    require_clean,
    stack_base,
    stack_exists,
    stg_series,
    sync_submodules,
    tag_commit,
)
from ..log import step, success, warn
from ..patches import (
    atomic_write_bytes,
    load_deps,
    read_pin,
    read_tag,
)
from ..proc import git, stg
from ..state import (
    clear_rebase,
    read_rebase,
    read_state,
    require_dev_mode,
    write_rebase,
    write_state,
)
from . import check as check_command
from . import export as export_command

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _resolve_target(repo: Path, target: str) -> tuple[str, str]:
    if target == "latest":
        target = fetch_latest_release_tag(repo)
        step(f"Latest 64Gram release is {target}")
    if target in {"dev", "master", "origin/dev", "upstream/dev"}:
        raise PrereqError("base policy is release tags only; dev tips are not allowed")
    if SHA_RE.fullmatch(target):
        raise PrereqError("base policy is release tags only; raw commits are not allowed")
    if not config.TAG_RE.fullmatch(target):
        raise PrereqError(f"not a release tag: {target}")

    local = tag_commit(repo, target)
    if local is None:
        step(f"Fetching tag {target}")
        fetch_tag(repo, target)
        local = tag_commit(repo, target)
    if local is None:
        raise CheckError(f"tag {target} is not available locally")

    remote = remote_tag_commit(repo, target)
    if remote is None:
        raise CheckError(f"tag {target} is not present in the upstream repository")
    if remote != local:
        raise CheckError(
            f"local tag {target} is {local}, upstream has {remote}; "
            "refusing to rebase onto a stale tag"
        )
    if not re.fullmatch(r"[0-9a-f]{40}", local):
        raise CheckError(f"tag {target} did not resolve to a commit: {local}")
    return target, local


def _write_phase(repo: Path, state: dict[str, object], phase: str) -> dict[str, object]:
    next_state = dict(state)
    next_state["phase"] = phase
    write_rebase(repo, next_state)
    return next_state


def _conflict_details(repo: Path) -> None:
    top = stg("top", cwd=repo, check=False)
    if top.ok and top.stdout.strip():
        warn(f"current patch: {top.stdout.strip()}")
    conflicts = git(
        "diff", "--name-only", "--diff-filter=U", cwd=repo, check=False
    )
    paths = [line.strip() for line in conflicts.stdout.splitlines() if line.strip()]
    if paths:
        print("conflicted paths:")
        for path in paths:
            print(f"  {path}")
    print("resolve conflicts in the base checkout, then run `make continue`")
    print("the pin/tag files are unchanged; there is no automatic abort")


def _finish(repo: Path, state: dict[str, object]) -> None:
    new_base = str(state.get("newBase", ""))
    new_tag = str(state.get("tag", ""))
    if not new_base or not new_tag:
        raise CheckError("rebase state is missing newBase/tag")
    try:
        deps = load_deps()
        if sync_submodules(repo, deps):
            step("Submodules synced")
        count = apply_deps(repo, deps)
        if count:
            step(f"Applied {count} deps patch(es)")
        export_command.run(check_only=False, allow_dirty=False)
        check_command.run(drop_one=False, base_arg=new_base)
    except PatchsetError as exc:
        raise CheckError(
            "rebase rewrote the stack but did not update the pin/tag.\n"
            f"fix the failure above and run `make finalize`.\n{exc}"
        ) from exc

    atomic_write_bytes(config.PIN, f"{new_base}\n".encode())
    atomic_write_bytes(config.TAG, f"{new_tag}\n".encode())
    current_state = read_state(repo)
    if current_state is not None:
        current_state["base"] = new_base
        current_state["tag"] = new_tag
        write_state(repo, current_state)
    clear_rebase(repo)
    success(f"Rebased onto {new_tag} ({new_base[:12]})")
    print(f"suggested commit message: pin: base on 64Gram {new_tag}")


def _fail_step(repo: Path, result) -> None:
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.stdout.strip():
        print(result.stdout.strip())
    _conflict_details(repo)
    raise CheckError("rebase paused")


def rebase(repo: Path, target: str, allow_non_descendant: bool) -> None:
    require_dev_mode(repo)
    if read_rebase(repo) is not None:
        raise CheckError("a rebase is already in progress; run `make status`")
    require_clean(repo, "rebase")

    if not stack_exists(repo, config.BRANCH):
        raise CheckError("no StGit stack; run `make setup`")
    all_patches = stg_series(repo, "--all")
    applied = stg_series(repo, "--applied")
    if applied != all_patches:
        raise CheckError("push every patch before rebasing")
    old_base = read_pin()
    old_tag = read_tag()
    resolved_old = tag_commit(repo, old_tag)
    if resolved_old != old_base:
        raise CheckError(f"{old_tag} resolves to {resolved_old}, expected {old_base}")
    actual_base = stack_base(repo, config.BRANCH, applied)
    if actual_base != old_base:
        raise CheckError(f"stack base is {actual_base}, expected {old_base}")

    new_tag, new_base = _resolve_target(repo, target)
    if new_base == old_base:
        print(f"already on {new_tag}")
        return
    if not allow_non_descendant and not is_ancestor(repo, old_base, new_base):
        raise CheckError(
            f"{new_tag} is not a descendant of the current base {old_tag}; "
            "refusing to move backwards. Use --allow-non-descendant only for a "
            "deliberate recovery."
        )

    state: dict[str, object] = {
        "oldBase": old_base,
        "newBase": new_base,
        "tag": new_tag,
        "startedAt": datetime.now(UTC).isoformat(),
        "phase": "conflicted",
    }
    write_rebase(repo, state)
    step(f"Rebasing {len(applied)} patch(es) onto {new_tag}")
    result = stg("rebase", new_base, cwd=repo, check=False)
    if not result.ok:
        _fail_step(repo, result)

    applied_after = stg_series(repo, "--applied")
    all_after = stg_series(repo, "--all")
    if applied_after != all_after:
        pushed = stg("push", "-a", cwd=repo, check=False)
        if not pushed.ok:
            _fail_step(repo, pushed)

    state = _write_phase(repo, state, "checks")
    _finish(repo, state)


def _marker_files(repo: Path) -> list[str]:
    from ..gitops import status_entries

    marked: list[str] = []
    for _status, path in status_entries(repo):
        file = repo / path
        if not file.is_file():
            continue
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", "surrogateescape")
        for marker in ("<<<<<<<", "=======", ">>>>>>>"):
            if any(line.startswith(marker) for line in text.splitlines()):
                marked.append(path)
                break
    return marked


def continue_rebase(repo: Path) -> None:
    state = read_rebase(repo)
    if state is None:
        raise CheckError("no rebase is in progress")
    if state.get("phase") != "conflicted":
        raise CheckError(
            "rebase is already past the conflict phase; run `make finalize`"
        )
    marked = _marker_files(repo)
    if marked:
        raise CheckError(
            "conflict markers remain:\n" + "\n".join(f"  {path}" for path in marked)
        )

    from ..gitops import status_entries

    paths = [
        path for status, path in status_entries(repo) if not status.startswith("??")
    ]
    if paths:
        staged = git("add", "-A", "--", *paths, cwd=repo, check=False)
        if not staged.ok:
            raise CheckError(
                "could not stage resolved paths:\n"
                f"{staged.stderr.strip() or staged.stdout.strip()}"
            )
    unmerged = git(
        "diff", "--name-only", "--diff-filter=U", cwd=repo, check=False
    )
    if unmerged.stdout.strip():
        raise CheckError("resolve every unmerged path before `make continue`")
    refreshed = stg("refresh", cwd=repo, check=False)
    if not refreshed.ok:
        raise CheckError(
            "stg refresh failed:\n"
            f"{refreshed.stderr.strip() or refreshed.stdout.strip()}"
        )
    applied_after = stg_series(repo, "--applied")
    all_after = stg_series(repo, "--all")
    if applied_after != all_after:
        pushed = stg("push", "-a", cwd=repo, check=False)
        if not pushed.ok:
            _fail_step(repo, pushed)

    state = _write_phase(repo, state, "checks")
    _finish(repo, state)


def finalize(repo: Path) -> None:
    state = read_rebase(repo)
    if state is None:
        raise CheckError("no rebase is in progress")
    if state.get("phase") != "checks":
        raise CheckError("rebase is not ready to finalize; resolve conflicts first")
    _finish(repo, state)
