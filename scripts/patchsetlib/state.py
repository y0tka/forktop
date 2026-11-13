"""Persistent setup/rebase state kept inside the base repository's git dir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .errors import CheckError
from .gitops import stack_exists
from .patches import atomic_write_bytes, read_text
from .proc import git


def git_path(repo: Path, name: str) -> Path:
    result = git(
        "rev-parse", "--path-format=absolute", "--git-path", name, cwd=repo, check=False
    )
    if result.ok and result.stdout.strip():
        return Path(result.stdout.strip())
    return repo / ".git" / name


def state_path(repo: Path) -> Path:
    return git_path(repo, f"{config.PROJECT}/state.json")


def rebase_path(repo: Path) -> Path:
    return git_path(repo, f"{config.PROJECT}/rebase.json")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise CheckError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CheckError(f"{path} must contain a JSON object")
    return payload


def read_state(repo: Path) -> dict[str, Any] | None:
    return _read_json(state_path(repo))


def write_state(repo: Path, state: dict[str, Any]) -> None:
    data = json.dumps(state, indent=2, sort_keys=True) + "\n"
    atomic_write_bytes(state_path(repo), data.encode("utf-8"))


def read_rebase(repo: Path) -> dict[str, Any] | None:
    return _read_json(rebase_path(repo))


def write_rebase(repo: Path, state: dict[str, Any]) -> None:
    data = json.dumps(state, indent=2, sort_keys=True) + "\n"
    atomic_write_bytes(rebase_path(repo), data.encode("utf-8"))


def clear_rebase(repo: Path) -> None:
    try:
        rebase_path(repo).unlink()
    except FileNotFoundError:
        pass


def infer_mode(repo: Path) -> str:
    state = read_state(repo)
    if state is not None:
        mode = state.get("mode")
        if mode in {"dev", "ci"}:
            return str(mode)
        raise CheckError(f"state file has invalid mode: {mode!r}")
    if stack_exists(repo, config.BRANCH):
        return "dev"
    return "ci"


def require_dev_mode(repo: Path) -> dict[str, Any]:
    mode = infer_mode(repo)
    if mode != "dev":
        raise CheckError("this command requires dev mode; run `make setup`")
    state = read_state(repo)
    if state is None:
        raise CheckError("missing dev state; run `make setup`")
    return state


def pending_rebase(repo: Path) -> dict[str, Any] | None:
    return read_rebase(repo)


def refuse_pending_rebase(repo: Path, action: str) -> None:
    pending = read_rebase(repo)
    if pending is not None:
        raise CheckError(
            f"refusing to {action}: a rebase is pending "
            f"(phase {pending.get('phase')!r}); run `python3 scripts/patchset.py status`"
        )
