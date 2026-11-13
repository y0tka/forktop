"""Cross-patch reversion and overwrite analysis."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .patches import block_changed_file, diff_blocks, is_binary_diff, read_text

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class ChangedLine:
    file: str
    line: int
    content: str


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_count: int
    changes: list[tuple[str, int | None]] = field(default_factory=list)


@dataclass
class Delta:
    patch: str
    added: list[ChangedLine] = field(default_factory=list)
    deleted: list[ChangedLine] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class Reversion:
    earlier: str
    later: str
    files: list[str]
    lines: int


@dataclass
class Overwrite:
    earlier: str
    later: str
    locations: dict[str, set[int]]


@dataclass
class LintResult:
    reversions: list[Reversion]
    overwrites: list[Overwrite]

    @property
    def has_findings(self) -> bool:
        return bool(self.reversions or self.overwrites)


def parse_patch_files(text: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for block in diff_blocks(text):
        if is_binary_diff(block):
            continue
        file = block_changed_file(block)
        if file:
            files.append((file, block))
    return files


def parse_diff(file: str, diff: str) -> tuple[list[ChangedLine], list[ChangedLine], list[Hunk]]:
    added: list[ChangedLine] = []
    deleted: list[ChangedLine] = []
    hunks: list[Hunk] = []
    old_line = 0
    new_line = 0
    in_hunk = False
    current: Hunk | None = None

    for line in diff.splitlines():
        match = HUNK_RE.match(line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(3))
            current = Hunk(
                old_start=old_line,
                old_count=1 if match.group(2) is None else int(match.group(2)),
                new_count=1 if match.group(4) is None else int(match.group(4)),
            )
            hunks.append(current)
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\"):
            continue
        if line.startswith("-"):
            current.changes.append(("delete", old_line))
            deleted.append(ChangedLine(file, old_line, line[1:]))
            old_line += 1
        elif line.startswith("+"):
            current.changes.append(("add", None))
            added.append(ChangedLine(file, new_line, line[1:]))
            new_line += 1
        elif line.startswith(" "):
            current.changes.append(("context", None))
            old_line += 1
            new_line += 1
    return added, deleted, hunks


def count_lines(lines: Iterable[ChangedLine]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for line in lines:
        key = (line.file, line.content)
        counts[key] = counts.get(key, 0) + 1
    return counts


def net_counts(delta: Delta) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
    added = count_lines(delta.added)
    deleted = count_lines(delta.deleted)
    for key in set(added) & set(deleted):
        shared = min(added[key], deleted[key])
        added[key] -= shared
        deleted[key] -= shared
        if added[key] == 0:
            del added[key]
        if deleted[key] == 0:
            del deleted[key]
    return added, deleted


def contains_counts(
    actual: dict[tuple[str, str], int], expected: dict[tuple[str, str], int]
) -> bool:
    return all(actual.get(key, 0) >= count for key, count in expected.items())


def format_ranges(lines: Iterable[int]) -> str:
    values = sorted(set(lines))
    ranges: list[tuple[int, int]] = []
    for line in values:
        if ranges and line == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], line)
        else:
            ranges.append((line, line))
    return ",".join(
        f"{start}" if start == end else f"{start}-{end}" for start, end in ranges
    )


def read_ignore(path: Path | None) -> set[tuple[str, str]]:
    if path is None or not path.is_file():
        return set()
    ignored: set[tuple[str, str]] = set()
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) < 2:
            continue
        ignored.add((parts[0].strip(), parts[1].strip()))
    return ignored


def analyze(
    entries: Sequence[str],
    texts: dict[str, str],
    ignore_path: Path | None = None,
) -> LintResult:
    patches = [entry for entry in entries]
    deltas: list[Delta] = []
    overwrites: dict[tuple[str, str], Overwrite] = {}
    file_owners: dict[str, list[str | None]] = {}

    for patch in patches:
        content = texts[patch]
        delta = Delta(patch=patch)
        for file, diff in parse_patch_files(content):
            added, deleted, hunks = parse_diff(file, diff)
            delta.added.extend(added)
            delta.deleted.extend(deleted)
            delta.hunks.extend(hunks)

            owners = file_owners.setdefault(file, [])
            offset = 0
            for hunk in hunks:
                index = (
                    hunk.old_start + offset
                    if hunk.old_count == 0
                    else max(0, hunk.old_start - 1 + offset)
                )
                for kind, old_line in hunk.changes:
                    if kind == "context":
                        while len(owners) <= index:
                            owners.append(None)
                        index += 1
                    elif kind == "add":
                        while len(owners) < index:
                            owners.append(None)
                        owners.insert(index, patch)
                        index += 1
                    else:
                        while len(owners) <= index:
                            owners.append(None)
                        owner = owners.pop(index)
                        if owner and owner != patch:
                            key = (owner, patch)
                            overwrite = overwrites.setdefault(
                                key, Overwrite(owner, patch, {})
                            )
                            overwrite.locations.setdefault(file, set()).add(
                                old_line if old_line is not None else hunk.old_start
                            )
                offset += hunk.new_count - hunk.old_count
        deltas.append(delta)

    net = [net_counts(delta) for delta in deltas]
    reversions: list[Reversion] = []
    reversion_pairs: set[tuple[str, str]] = set()
    for later_index in range(1, len(deltas)):
        later_deleted, later_added = net[later_index][1], net[later_index][0]
        for earlier_index in range(later_index):
            earlier_added, earlier_deleted = net[earlier_index]
            changed = sum(earlier_added.values()) + sum(earlier_deleted.values())
            if changed == 0:
                continue
            if not contains_counts(later_deleted, earlier_added):
                continue
            if not contains_counts(later_added, earlier_deleted):
                continue
            earlier = deltas[earlier_index]
            later = deltas[later_index]
            files = sorted(
                {item.file for item in earlier.added + earlier.deleted}
            )
            reversions.append(Reversion(earlier.patch, later.patch, files, changed))
            reversion_pairs.add((earlier.patch, later.patch))

    ignored = read_ignore(ignore_path)
    reversions = [
        item
        for item in reversions
        if (item.later, item.earlier) not in ignored
    ]

    partial = [
        item for key, item in overwrites.items() if key not in reversion_pairs
    ]
    partial = [item for item in partial if (item.later, item.earlier) not in ignored]
    order = {patch: index for index, patch in enumerate(patches)}
    partial.sort(key=lambda item: (order.get(item.later, 0), order.get(item.earlier, 0)))
    return LintResult(reversions, partial)
