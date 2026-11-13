"""Subprocess wrappers with deterministic, non-interactive environments."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import PrereqError


@dataclass(frozen=True)
class Result:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def describe_failure(self) -> str:
        command = shlex.join(self.args)
        detail = self.stderr.strip() or self.stdout.strip()
        return f"{command} failed" + (f":\n{detail}" if detail else "")


def _environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "LC_ALL": "C",
        }
    )
    if extra:
        env.update(extra)
    return env


def _execute(
    args: Sequence[str],
    *,
    cwd: Path | None,
    check: bool,
    extra_env: Mapping[str, str] | None,
    text: bool,
) -> subprocess.CompletedProcess:
    command = list(args)
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=_environment(extra_env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=text,
            encoding="utf-8" if text else None,
            errors="surrogateescape" if text else None,
        )
    except FileNotFoundError as exc:
        raise PrereqError(f"required executable not found: {command[0]}") from exc
    except OSError as exc:
        raise PrereqError(f"could not run {command[0]}: {exc}") from exc
    if check and result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "surrogateescape")
        stdout = result.stdout if text else result.stdout.decode("utf-8", "surrogateescape")
        detail = (stderr or "").strip() or (stdout or "").strip()
        raise PrereqError(
            f"{shlex.join(command)} failed" + (f":\n{detail}" if detail else "")
        )
    return result


def run(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
    extra_env: Mapping[str, str] | None = None,
) -> Result:
    completed = _execute(
        args,
        cwd=Path(cwd) if cwd is not None else None,
        check=check,
        extra_env=extra_env,
        text=True,
    )
    return Result(tuple(args), completed.returncode, completed.stdout, completed.stderr)


def run_bytes(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return _execute(
        args,
        cwd=Path(cwd) if cwd is not None else None,
        check=check,
        extra_env=extra_env,
        text=False,
    )


def git(
    *args: str,
    cwd: Path | str | None = None,
    check: bool = True,
) -> Result:
    return run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        check=check,
    )


def stg(*args: str, cwd: Path | str | None = None, check: bool = True) -> Result:
    return run(["stg", *args], cwd=cwd, check=check)
