"""Repository-wide configuration for the patchset pipeline."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_URL = "https://github.com/TDesktop-x64/tdesktop"
UPSTREAM_BRANCH = "dev"  # diagnostics only; never a pin
BRANCH = "forktop"

WORKTREE = Path(os.environ.get("FORKTOP_WORKTREE", str(ROOT / "tdesktop"))).expanduser()

PATCHES = ROOT / "patches"
SERIES = ROOT / "series"
PIN = ROOT / "upstream-commit"
TAG = ROOT / "upstream-tag"
DEPS = ROOT / "deps" / "patches.json"
LINT_IGNORE = ROOT / "scripts" / "lint-ignore.txt"
CHANGELOG = ROOT / "CHANGELOG.md"

PROJECT = "forktop"
GROUPS = ("bugfix", "feature", "debloat", "hooks", "misc")
PATCH_NAME_RE = re.compile(r"^(bugfix|feature|debloat|hooks|misc)__[a-z0-9][a-z0-9-]*$")
PATCH_FILE_RE = re.compile(r"^(bugfix|feature|debloat|hooks|misc)/([a-z0-9][a-z0-9-]*)\.patch$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^v[0-9][0-9A-Za-z._-]*$")
BUILD_HOOK_PATCH = "misc__build-fork"

VERSION_PATHS = frozenset(
    {
        "Telegram/build/version",
        "Telegram/SourceFiles/core/version.h",
    }
)
GENERATED_ROOT_PATHS = ("patches", "series", "upstream-commit", "upstream-tag")
CMAKELISTS = "Telegram/CMakeLists.txt"

FORBIDDEN_TRAILERS = (
    "task:",
    "autotask:",
    "co-authored-by:",
    "assisted-by:",
    "generated-by:",
)


def worktree_path() -> Path:
    """Return the configured base checkout path (resolved at call time)."""
    return Path(os.environ.get("FORKTOP_WORKTREE", str(ROOT / "tdesktop"))).expanduser()
