"""Extract and validate release-note sections."""

from __future__ import annotations

import re

from .. import config
from ..errors import CheckError
from ..log import success
from ..patches import read_text

HEADING_RE = re.compile(r"^##\s+(\S+)\s+-\s+(\d{4}-\d{2}-\d{2})\s*$")
BULLET_RE = re.compile(r"^\s*-\s+\S")


def extract(tag: str) -> str:
    try:
        text = read_text(config.CHANGELOG)
    except FileNotFoundError as exc:
        raise CheckError("CHANGELOG.md is missing") from exc

    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if HEADING_RE.fullmatch(line)]
    found: int | None = None
    for index in matches:
        if HEADING_RE.fullmatch(lines[index]).group(1) == tag:  # type: ignore[union-attr]
            if found is not None:
                raise CheckError(f"CHANGELOG.md has duplicate sections for {tag}")
            found = index
    if found is None:
        raise CheckError(f"CHANGELOG.md has no section for {tag}")
    end = next((index for index in matches if index > found), len(lines))
    body = "\n".join(lines[found + 1 : end]).strip("\n")
    if not any(BULLET_RE.match(line) for line in body.splitlines()):
        raise CheckError(f"CHANGELOG.md section for {tag} has no bullet")
    return body + "\n"


def run(tag: str, check: bool) -> None:
    body = extract(tag)
    if check:
        success(f"CHANGELOG.md has release notes for {tag}")
        return
    print(body, end="")
