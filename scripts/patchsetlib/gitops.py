"""Git and StGit primitives shared by the commands."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from . import config
from .errors import CheckError, PrereqError
from .log import warn
from .proc import git, stg


def rev_parse(*args: str, cwd: Path | str, check: bool = True) -> str | None:
    result = git("rev-parse", *args, cwd=cwd, check=check)
    if not result.ok:
        return None
    return result.stdout.strip()


def commit_exists(repo: Path, commit: str) -> bool:
    return rev_parse("--verify", f"{commit}^{{commit}}", cwd=repo, check=False) is not None


def head_commit(repo: Path) -> str:
    value = rev_parse("--verify", "HEAD^{commit}", cwd=repo)
    if not value:
        raise CheckError(f"{repo} has no HEAD commit")
    return value


def current_branch(repo: Path) -> str | None:
    value = rev_parse("--abbrev-ref", "HEAD", cwd=repo, check=False)
    return None if value in (None, "HEAD") else value


def local_branch_exists(repo: Path, branch: str) -> bool:
    return (
        git(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            cwd=repo,
            check=False,
        ).returncode
        == 0
    )


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return git(
        "merge-base", "--is-ancestor", ancestor, descendant, cwd=repo, check=False
    ).returncode == 0


def normalize_remote(url: str) -> str:
    value = url.strip().rstrip("/")
    value = value.removesuffix(".git")
    return value


def ensure_upstream_remote(repo: Path) -> None:
    result = git("remote", "get-url", "upstream", cwd=repo, check=False)
    if result.ok:
        current = result.stdout.strip()
        if normalize_remote(current) != normalize_remote(config.UPSTREAM_URL):
            raise CheckError(
                f"remote 'upstream' points to {current}, expected {config.UPSTREAM_URL}"
            )
        return
    existing = git("remote", cwd=repo).stdout.splitlines()
    if "upstream" in {line.strip() for line in existing}:
        raise CheckError("remote 'upstream' exists but has no URL")
    git("remote", "add", "upstream", config.UPSTREAM_URL, cwd=repo)


def remote_url(repo: Path, name: str) -> str | None:
    result = git("remote", "get-url", name, cwd=repo, check=False)
    return result.stdout.strip() if result.ok else None


def tag_commit(repo: Path, tag: str) -> str | None:
    return rev_parse("--verify", f"refs/tags/{tag}^{{commit}}", cwd=repo, check=False)


def fetch_tag(repo: Path, tag: str) -> None:
    existing = tag_commit(repo, tag)
    if existing is not None:
        return
    result = git(
        "fetch",
        "upstream",
        f"refs/tags/{tag}:refs/tags/{tag}",
        cwd=repo,
        check=False,
    )
    if not result.ok:
        raise PrereqError(
            f"could not fetch tag {tag} from {config.UPSTREAM_URL}:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def remote_tag_commit(repo: Path, tag: str) -> str | None:
    """Return the peeled remote tag commit, or None when ls-remote has no tag."""
    result = git(
        "ls-remote",
        "--tags",
        "upstream",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
        cwd=repo,
        check=False,
    )
    if not result.ok:
        raise PrereqError(
            f"could not read tag {tag} from upstream:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            values[parts[1]] = parts[0]
    return values.get(f"refs/tags/{tag}^{{}}") or values.get(f"refs/tags/{tag}")


def status_entries(repo: Path) -> list[tuple[str, str]]:
    """Return (status, path) pairs, including untracked paths."""
    result = git(
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=normal",
        "--ignore-submodules=all",
        cwd=repo,
    )
    fields = result.stdout.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        status = record[:2]
        path = record[3:]
        if not path:
            continue
        entries.append((status, path))
        if "R" in status or "C" in status:
            # The original path is the next NUL-delimited record; stage it too.
            original = fields[index] if index < len(fields) else ""
            index += 1
            if original:
                entries.append((status, original))
    return entries


def dirty_paths(repo: Path) -> list[str]:
    return [path for _status, path in status_entries(repo)]


def require_clean(repo: Path, action: str) -> None:
    paths = dirty_paths(repo)
    if paths:
        raise CheckError(
            f"refusing to {action}: worktree is dirty:\n"
            + "\n".join(f"  {path}" for path in paths)
        )


def submodule_paths(repo: Path) -> list[str]:
    if not (repo / ".gitmodules").is_file():
        return []
    result = git(
        "config",
        "-f",
        ".gitmodules",
        "--get-regexp",
        r"^submodule\..+\.path$",
        cwd=repo,
        check=False,
    )
    if not result.ok:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            paths.append(parts[1].strip())
    return paths


def submodule_status(repo: Path) -> list[tuple[str, str]]:
    result = git("submodule", "status", cwd=repo, check=False)
    if not result.ok:
        return []
    entries: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status = line[0]
        # "<status><sha> <path> (<describe>)"
        rest = line[1:].strip().split()
        if len(rest) >= 2:
            entries.append((status, rest[1]))
    return entries


def stale_submodule_paths(repo: Path) -> set[str]:
    return {path for status, path in submodule_status(repo) if status != " "}


def is_submodule_initialized(repo: Path, submodule: str) -> bool:
    path = repo / submodule
    if not path.is_dir():
        return False
    result = git("rev-parse", "--is-inside-work-tree", cwd=path, check=False)
    return result.ok and result.stdout.strip() == "true"


def deps_patch_path(patch: str) -> Path:
    candidate = Path(patch)
    return candidate if candidate.is_absolute() else config.ROOT / candidate


def dep_is_applied(submodule: Path, patch: Path) -> bool:
    return git(
        "apply", "--reverse", "--check", str(patch), cwd=submodule, check=False
    ).returncode == 0


def apply_deps(repo: Path, deps: Iterable[Mapping[str, str]]) -> int:
    applied = 0
    for entry in deps:
        submodule = entry.get("submodule", "").strip()
        patch_name = entry.get("patch", "").strip()
        if not submodule or not patch_name:
            raise CheckError(f"invalid deps entry: {entry!r}")
        submodule_path = repo / submodule
        if not is_submodule_initialized(repo, submodule):
            raise CheckError(
                f"deps patch {patch_name}: submodule {submodule} is not initialized"
            )
        patch = deps_patch_path(patch_name)
        if not patch.is_file():
            raise CheckError(f"deps patch does not exist: {patch}")
        if dep_is_applied(submodule_path, patch):
            continue
        check = git("apply", "--check", str(patch), cwd=submodule_path, check=False)
        if not check.ok:
            raise CheckError(
                f"deps patch does not apply to {submodule}: {patch}\n"
                f"{check.stderr.strip() or check.stdout.strip()}"
            )
        result = git("apply", str(patch), cwd=submodule_path, check=False)
        if not result.ok:
            raise CheckError(
                f"could not apply deps patch {patch} to {submodule}:\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        applied += 1
    return applied


def reverse_stale_deps(repo: Path, deps: Iterable[Mapping[str, str]]) -> None:
    stale = stale_submodule_paths(repo)
    for entry in reversed(list(deps)):
        submodule = entry.get("submodule", "").strip()
        patch_name = entry.get("patch", "").strip()
        if not submodule or not patch_name or submodule not in stale:
            continue
        if not is_submodule_initialized(repo, submodule):
            continue
        patch = deps_patch_path(patch_name)
        if not patch.is_file():
            raise CheckError(f"deps patch does not exist: {patch}")
        if dep_is_applied(repo / submodule, patch):
            result = git(
                "apply", "--reverse", str(patch), cwd=repo / submodule, check=False
            )
            if not result.ok:
                raise CheckError(
                    f"could not reverse stale deps patch {patch} in {submodule}:\n"
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )


def sync_submodules(repo: Path, deps: Iterable[Mapping[str, str]]) -> bool:
    """Reverse stale deps, update every submodule, and report whether any moved."""
    dep_list = list(deps)
    if not (repo / ".gitmodules").is_file():
        return False
    stale = stale_submodule_paths(repo)
    if not stale:
        return False
    reverse_stale_deps(repo, dep_list)
    result = git("submodule", "update", "--init", "--recursive", cwd=repo, check=False)
    if not result.ok:
        raise CheckError(
            "submodule update failed:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return True


def refs_patches(repo: Path, branch: str) -> dict[str, str]:
    prefix = f"refs/patches/{branch}/"
    result = git(
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        prefix,
        cwd=repo,
        check=False,
    )
    mapping: dict[str, str] = {}
    if not result.ok:
        return mapping
    for line in result.stdout.splitlines():
        ref, _, commit = line.partition(" ")
        if ref.startswith(prefix) and commit:
            mapping[ref[len(prefix) :]] = commit
    return mapping


def stg_series(repo: Path, mode: str) -> list[str]:
    args = ["series", mode]
    result = stg(*args, cwd=repo, check=False)
    if not result.ok:
        raise PrereqError(
            f"could not read the StGit stack:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    names: list[str] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value:
            continue
        # Accept both --no-prefix output and the legacy "+ name" form.
        value = value.split("#", 1)[0].strip()
        if value[:1] in "+>!-*":
            value = value[1:].strip()
        if value:
            names.append(value)
    return names


def stack_exists(repo: Path, branch: str) -> bool:
    return (
        git(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/stacks/{branch}",
            cwd=repo,
            check=False,
        ).returncode
        == 0
    )


def stack_base(repo: Path, branch: str, patches: Sequence[str]) -> str:
    if not patches:
        return head_commit(repo)
    first = patches[0]
    value = rev_parse(
        "--verify", f"refs/patches/{branch}/{first}^", cwd=repo, check=False
    )
    if not value:
        raise CheckError(
            f"could not resolve the base of stack {branch} from patch {first}"
        )
    return value


def init_stg(repo: Path) -> None:
    stg("init", cwd=repo)


def write_worktree(repo: Path, base: str) -> tuple[Path, Path]:
    parent = Path(tempfile.mkdtemp(prefix="forktop-check-"))
    worktree = parent / "wt"
    result = git(
        "worktree", "add", "--detach", str(worktree), base, cwd=repo, check=False
    )
    if not result.ok:
        shutil.rmtree(parent, ignore_errors=True)
        raise CheckError(
            f"could not create a temporary worktree at {base}:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return parent, worktree


def remove_worktree(repo: Path, parent: Path, worktree: Path) -> None:
    git("worktree", "remove", "--force", str(worktree), cwd=repo, check=False)
    shutil.rmtree(parent, ignore_errors=True)


def apply_series(worktree: Path, patch_files: Sequence[Path]) -> str | None:
    """Apply patches in order; return the failing patch name, or None."""
    for patch in patch_files:
        result = git(
            "apply", "--index", str(patch), cwd=worktree, check=False
        )
        if not result.ok:
            return f"{patch.name}: {result.stderr.strip() or result.stdout.strip()}"
    return None


def no_unmerged(worktree: Path) -> bool:
    result = git(
        "diff", "--name-only", "--diff-filter=U", cwd=worktree, check=False
    )
    return result.ok and not result.stdout.strip()


def diff_check(worktree: Path) -> str:
    result = git("diff", "--cached", "--check", cwd=worktree, check=False)
    if result.ok:
        return ""
    return result.stdout.strip() or result.stderr.strip() or "git diff --check failed"


def checkout_detached(repo: Path, commit: str) -> None:
    git("checkout", "--detach", commit, cwd=repo)


def current_head_or_none(repo: Path) -> str | None:
    return rev_parse("--verify", "HEAD^{commit}", cwd=repo, check=False)


def ensure_commit(repo: Path, commit: str, what: str) -> None:
    if not commit_exists(repo, commit):
        raise CheckError(f"{what} does not resolve to a commit in {repo}: {commit}")


def warn_if_docker_missing() -> None:
    if shutil.which("docker") is None:
        warn("docker is not installed; local Linux builds and CI image steps are unavailable")


def fetch_latest_release_tag(repo: Path) -> str:
    import json
    import os
    import urllib.error
    import urllib.request

    url = "https://api.github.com/repos/TDesktop-x64/tdesktop/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "forktop-patchset",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise PrereqError(
            f"could not query the latest 64Gram release ({exc}); "
            "use `rebase <tag>` instead"
        ) from exc
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag:
        raise PrereqError("GitHub release response has no tag_name; use `rebase <tag>`")
    return tag


def require_stg() -> None:
    if shutil.which("stg") is None:
        raise PrereqError(
            "stgit is required for dev setup/export/rebase; install stgit >= 2.0"
        )
