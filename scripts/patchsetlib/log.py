"""Readable, consistently prefixed console output."""

from __future__ import annotations

import sys


def step(message: str) -> None:
    print(f"==> {message}")


def success(message: str) -> None:
    print(f"ok {message}")


def warn(message: str) -> None:
    print(f"warn {message}")


def error(message: str) -> None:
    print(f"error {message}", file=sys.stderr)
