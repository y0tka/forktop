"""Shared fixtures for pipeline tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def require_stg(case: unittest.TestCase) -> None:  # noqa: F821
    import unittest

    if shutil.which("stg") is None:
        raise unittest.SkipTest("stgit is not installed")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {repo}:\n{result.stderr}{result.stdout}"
        )
    return result


def stg(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    result = subprocess.run(
        ["stg", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"stg {' '.join(args)} failed in {repo}:\n{result.stderr}{result.stdout}"
        )
    return result


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "config", "tag.gpgsign", "false")
    return path


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        write(repo / name, text)
        git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def make_repo_with_patch(
    root: Path,
    patch_name: str = "feature__one",
    file_name: str = "file.txt",
    base_text: str = "base\n",
    patched_text: str = "patched\n",
) -> tuple[Path, str, str]:
    repo = init_repo(root / "work")
    base = commit(repo, {file_name: base_text}, "base")
    git(repo, "tag", "v1.0.0", base)
    git(repo, "checkout", "-q", "-b", "forktop", base)
    stg(repo, "init")
    stg(repo, "new", patch_name, "-m", "Test patch")
    write(repo / file_name, patched_text)
    stg(repo, "refresh")
    patch_commit = git(repo, "rev-parse", f"refs/patches/forktop/{patch_name}").stdout.strip()
    return repo, base, patch_commit


def make_root(tmp: Path) -> Path:
    root = tmp / "root"
    for directory in ("patches", "scripts"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "series").write_text("")
    (root / "upstream-commit").write_text("0" * 40 + "\n")
    (root / "upstream-tag").write_text("v1.0.0\n")
    (root / "deps").mkdir()
    (root / "deps" / "patches.json").write_text("[]\n")
    return root
