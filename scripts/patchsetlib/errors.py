"""Pipeline errors mapped to stable process exit codes."""

from __future__ import annotations


class PatchsetError(Exception):
    """A user-facing failure that is not a validation result."""

    exit_code = 1


class CheckError(PatchsetError):
    """A validation gate failed."""

    exit_code = 1


class PrereqError(PatchsetError):
    """A prerequisite (tool, checkout, usage) is missing."""

    exit_code = 2
