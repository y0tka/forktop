"""Validation gates for the exported patch series."""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..errors import CheckError
from ..gitops import (
    apply_series,
    commit_exists,
    dep_is_applied,
    deps_patch_path,
    diff_check,
    head_commit,
    is_submodule_initialized,
    no_unmerged,
    remove_worktree,
    rev_parse,
    stack_base,
    stg_series,
    submodule_paths,
    tag_commit,
    write_worktree,
)
from ..lint import analyze, format_ranges
from ..log import step, success, warn
from ..patches import (
    forbidden_path_reasons,
    is_binary_diff,
    load_deps,
    patch_body,
    patch_changed_files,
    patch_name_from_series,
    patch_subject,
    read_pin,
    read_series,
    read_tag,
    read_text,
    series_paths,
)
from ..proc import git
from ..state import infer_mode, read_rebase, read_state
from . import export as export_command


def _resolve_base(repo: Path, pin: str, tag: str, base_arg: str | None) -> tuple[str, str]:
    if base_arg is None:
        if not commit_exists(repo, pin):
            raise CheckError(f"pinned base commit is absent: {pin}")
        resolved = tag_commit(repo, tag)
        if resolved is None:
            raise CheckError(f"upstream tag is not available locally: {tag}")
        if resolved != pin:
            raise CheckError(f"{tag} resolves to {resolved}, expected {pin}")
        return pin, tag

    pending = read_rebase(repo)
    if pending is None:
        raise CheckError("check --base requires an in-flight rebase")
    if pending.get("phase") != "checks":
        raise CheckError("check --base is only valid after the rebase completes")
    reported = str(pending.get("newBase", ""))
    resolved = rev_parse("--verify", f"{base_arg}^{{commit}}", cwd=repo, check=False)
    if not resolved:
        raise CheckError(f"--base does not resolve to a commit: {base_arg}")
    if resolved != reported:
        raise CheckError(f"--base {resolved} does not match rebase target {reported}")
    rebase_tag = str(pending.get("tag", ""))
    if not rebase_tag:
        raise CheckError("rebase state has no tag")
    resolved_tag = tag_commit(repo, rebase_tag)
    if resolved_tag != resolved:
        raise CheckError(f"{rebase_tag} resolves to {resolved_tag}, expected {resolved}")
    return resolved, rebase_tag


def _gate_mode(repo: Path, base: str) -> str:
    mode = infer_mode(repo)
    if mode == "dev":
        all_patches = stg_series(repo, "--all")
        applied = stg_series(repo, "--applied")
        if applied != all_patches:
            raise CheckError("the StGit stack has unapplied patches")
        actual_base = stack_base(repo, config.BRANCH, applied)
        if actual_base != base:
            raise CheckError(f"stack base is {actual_base}, expected {base}")
        state = read_state(repo)
        if state is not None and state.get("mode") != "dev":
            raise CheckError("state file does not say dev mode")
        return mode

    state = read_state(repo)
    if state is None:
        raise CheckError("missing CI state; run setup --ci")
    if state.get("mode") != "ci":
        raise CheckError("state file does not say ci mode")
    actual_head = head_commit(repo)
    if actual_head != base:
        raise CheckError(f"CI checkout HEAD is {actual_head}, expected {base}")
    return mode


def _gate_series(repo: Path, mode: str) -> tuple[list[str], list[str]]:
    entries = read_series()
    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        name = patch_name_from_series(entry)
        if entry in seen:
            raise CheckError(f"duplicate series entry: {entry}")
        seen.add(entry)
        if not (config.PATCHES / entry).is_file():
            raise CheckError(f"series references a missing patch: {entry}")
        names.append(name)

    expected = {entry for entry in entries}
    existing = {
        path.relative_to(config.PATCHES).as_posix()
        for path in config.PATCHES.rglob("*.patch")
    }
    orphans = sorted(existing - expected)
    if orphans:
        raise CheckError(
            "patches not listed in series:\n" + "\n".join(f"  {entry}" for entry in orphans)
        )

    if mode == "dev":
        applied = stg_series(repo, "--applied")
        if applied != names:
            raise CheckError(f"stack order {applied!r} does not match series {names!r}")
    else:
        state = read_state(repo) or {}
        if state.get("patches") != names:
            raise CheckError("CI state does not list every series patch")
    return entries, names


def _gate_clean_apply(repo: Path, base: str, entries: list[str]) -> None:
    step(f"Applying {len(entries)} patch(es) to pristine {base[:12]}")
    parent, worktree = write_worktree(repo, base)
    failing = apply_series(worktree, series_paths(entries))
    if failing is not None:
        raise CheckError(
            f"series does not apply cleanly; temporary worktree left at {worktree}\n"
            f"  {failing}"
        )
    whitespace = diff_check(worktree)
    if whitespace:
        raise CheckError(
            f"applied series has whitespace errors; temporary worktree left at "
            f"{worktree}\n{whitespace}"
        )
    if not no_unmerged(worktree):
        raise CheckError(
            f"applied series has unmerged paths; temporary worktree left at {worktree}"
        )
    remove_worktree(repo, parent, worktree)


def _gate_metadata(entries: list[str]) -> None:
    for entry in entries:
        text = read_text(config.PATCHES / entry)
        subject = patch_subject(text)
        if not subject:
            raise CheckError(f"{entry}: empty Subject")
        if "[PATCH" in subject:
            raise CheckError(f"{entry}: subject contains a [PATCH] prefix")
        for line in patch_body(text).splitlines():
            lowered = line.strip().lower()
            if any(lowered.startswith(marker) for marker in config.FORBIDDEN_TRAILERS):
                raise CheckError(f"{entry}: forbidden trailer: {line.strip()}")
        if is_binary_diff(text):
            warn(f"{entry}: binary diff; review cannot diff it as text")
        if any(
            file == "Telegram/SourceFiles/stdafx.h"
            for file in patch_changed_files(text)
        ):
            warn(f"{entry}: changes PCH stdafx.h; expect a rebuild")


def _gate_forbidden(repo: Path, entries: list[str]) -> None:
    submodules = submodule_paths(repo)
    problems: list[str] = []
    for entry in entries:
        group = patch_name_from_series(entry).split("__", 1)[0]
        text = read_text(config.PATCHES / entry)
        reasons = forbidden_path_reasons(
            patch_changed_files(text), group, submodules
        )
        problems.extend(f"{entry}: {reason}" for reason in reasons)
    if problems:
        raise CheckError(
            "forbidden paths in series:\n"
            + "\n".join(f"  {problem}" for problem in problems)
        )


def _gate_lint(entries: list[str]) -> None:
    names = [patch_name_from_series(entry) for entry in entries]
    texts = {name: read_text(config.PATCHES / entry) for name, entry in zip(names, entries)}
    result = analyze(names, texts, config.LINT_IGNORE)
    if result.reversions:
        print("Full reversions:")
        for item in result.reversions:
            print(
                f"  {item.later} reverts {item.earlier} "
                f"({item.lines} changed lines)"
            )
            for file in item.files:
                print(f"    {file}")
    if result.overwrites:
        if result.reversions:
            print()
        print("Earlier patch lines overwritten:")
        for item in result.overwrites:
            count = sum(len(lines) for lines in item.locations.values())
            plural = "line" if count == 1 else "lines"
            print(f"  {item.later} overwrites {count} {plural} from {item.earlier}")
            for file, lines in sorted(item.locations.items()):
                print(f"    {file}:{format_ranges(lines)}")
    if result.has_findings:
        raise CheckError("patch overwrite lint failed")
    success("No patch overwrites found")


def _gate_deps(repo: Path, deps: list[dict[str, str]]) -> None:
    if not deps:
        return
    initialized = [
        is_submodule_initialized(repo, entry["submodule"]) for entry in deps
    ]
    if not any(initialized):
        warn("skipping deps check: submodules are not initialized")
        return
    for entry in deps:
        if not is_submodule_initialized(repo, entry["submodule"]):
            raise CheckError(f"submodule is not initialized: {entry['submodule']}")
        patch = deps_patch_path(entry["patch"])
        if not patch.is_file():
            raise CheckError(f"deps patch does not exist: {patch}")
        submodule = repo / entry["submodule"]
        if dep_is_applied(submodule, patch):
            continue
        check = git("apply", "--check", str(patch), cwd=submodule, check=False)
        if not check.ok:
            raise CheckError(
                f"deps patch does not apply to {entry['submodule']}: {patch}\n"
                f"{check.stderr.strip() or check.stdout.strip()}"
            )
    success(f"{len(deps)} deps patch(es) verified")


def _gate_drop_one(repo: Path, base: str, entries: list[str]) -> None:
    if not entries:
        warn("no patches to drop")
        return
    for index, entry in enumerate(entries):
        parent, worktree = write_worktree(repo, base)
        remaining = [
            path
            for position, path in enumerate(series_paths(entries))
            if position != index
        ]
        failing = apply_series(worktree, remaining)
        if failing is None:
            print(f"  without {entry}: ok")
        else:
            print(f"  without {entry}: breaks {failing}")
        remove_worktree(repo, parent, worktree)


def run(drop_one: bool, base_arg: str | None) -> None:
    repo = config.worktree_path()
    pin = read_pin()
    tag = read_tag()
    base, effective_tag = _resolve_base(repo, pin, tag, base_arg)
    step(f"Base {effective_tag} at {base[:12]}")

    mode = _gate_mode(repo, base)
    entries, _names = _gate_series(repo, mode)
    step(f"Series has {len(entries)} patch(es)")
    if mode == "dev":
        export_command.run(check_only=True, allow_dirty=False)
    else:
        step("Skipping export freshness in CI mode")
    _gate_clean_apply(repo, base, entries)
    _gate_metadata(entries)
    _gate_forbidden(repo, entries)
    _gate_lint(entries)
    _gate_deps(repo, load_deps())

    if drop_one:
        step("Drop-one audit (advisory)")
        _gate_drop_one(repo, base, entries)

    success(f"All checks passed for {len(entries)} patch(es) on {effective_tag}")
