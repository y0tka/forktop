"""Patch naming, series files, stable export text, and diff parsing."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from . import config
from .errors import CheckError
from .proc import run_bytes

DIFF_HEADER_RE = re.compile(r"^diff --git ", re.MULTILINE)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+( \d+)?$", re.MULTILINE)
SUBJECT_RE = re.compile(r"^Subject:(.*(?:\n[ \t]+.*)*)", re.MULTILINE)


def parse_patch_name(name: str) -> tuple[str, str, str]:
    value = name.strip()
    if not config.PATCH_NAME_RE.fullmatch(value):
        raise CheckError(
            f"invalid patch name {value!r}; expected "
            "<bugfix|feature|debloat|hooks|misc>__<kebab-name>"
        )
    group, short = value.split("__", 1)
    return group, short, f"{group}/{short}.patch"


def patch_name_from_series(entry: str) -> str:
    value = entry.strip().replace("\\", "/")
    match = config.PATCH_FILE_RE.fullmatch(value)
    if not match:
        raise CheckError(f"invalid series entry: {entry!r}")
    return f"{match.group(1)}__{match.group(2)}"


def series_entry_for_patch(name: str) -> str:
    return parse_patch_name(name)[2]


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", "surrogateescape")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_pin() -> str:
    try:
        value = read_text(config.PIN).strip()
    except FileNotFoundError as exc:
        raise CheckError(f"missing {config.PIN.name}") from exc
    if not config.COMMIT_RE.fullmatch(value):
        raise CheckError(
            f"{config.PIN.name} must contain one 40-character lowercase hex commit"
        )
    return value


def read_tag() -> str:
    try:
        value = read_text(config.TAG).strip()
    except FileNotFoundError as exc:
        raise CheckError(f"missing {config.TAG.name}") from exc
    if not config.TAG_RE.fullmatch(value):
        raise CheckError(f"{config.TAG.name} must contain one release tag starting with v")
    return value


def read_series(path: Path | None = None) -> list[str]:
    file = path or config.SERIES
    try:
        raw = read_text(file)
    except FileNotFoundError as exc:
        raise CheckError(f"missing {file.name}") from exc
    return [line.strip() for line in raw.splitlines() if line.strip()]


def write_series(entries: Sequence[str], path: Path | None = None) -> None:
    file = path or config.SERIES
    text = "" if not entries else "".join(f"{entry}\n" for entry in entries)
    atomic_write_bytes(file, text.encode("utf-8"))


def list_patch_files() -> list[Path]:
    if not config.PATCHES.is_dir():
        return []
    return sorted(config.PATCHES.rglob("*.patch"))


def relative_patch_path(path: Path) -> str:
    return path.relative_to(config.PATCHES).as_posix()


def series_paths(entries: Sequence[str]) -> list[Path]:
    return [config.PATCHES / entry for entry in entries]


def normalize_patch_text(text: str) -> str:
    text = INDEX_RE.sub(
        lambda match: "index 0000000..0000000" + (match.group(1) or ""), text
    )

    def flatten(match: re.Match[str]) -> str:
        value = match.group(1).replace("\n", " ")
        value = re.sub(r"[ \t]+", " ", value).strip()
        return f"Subject: {value}" if value else "Subject:"

    return SUBJECT_RE.sub(flatten, text, count=1)


def generate_stable_patch(repo: Path, commit: str) -> str:
    completed = run_bytes(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.quotepath=false",
            "format-patch",
            "--stdout",
            "--ignore-submodules=none",
            "--zero-commit",
            "--no-signature",
            "--subject-prefix=",
            "-1",
            commit,
        ],
        cwd=repo,
        check=True,
    )
    return normalize_patch_text(completed.stdout.decode("utf-8", "surrogateescape"))


def patch_has_diff(text: str) -> bool:
    return "\ndiff --git " in text


def patch_subject(text: str) -> str:
    match = SUBJECT_RE.search(text)
    return match.group(1).strip() if match else ""


def patch_body(text: str) -> str:
    lines = text.splitlines()
    subject_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Subject:")),
        None,
    )
    if subject_index is None:
        return ""
    index = subject_index + 1
    while index < len(lines) and (lines[index].startswith(" ") or lines[index].startswith("\t")):
        index += 1
    if index < len(lines) and not lines[index].strip():
        index += 1
    body: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line == "---":
            break
        body.append(line)
        index += 1
    return "\n".join(body)


def diff_blocks(text: str) -> list[str]:
    matches = list(DIFF_HEADER_RE.finditer(text))
    blocks: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append(text[start:end])
    return blocks


def _git_unquote(value: str) -> str:
    value = value.strip()
    if not (len(value) >= 2 and value[0] == '"' and value[-1] == '"'):
        return value
    inner = value[1:-1]
    output = bytearray()
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            output.extend(char.encode("utf-8", "surrogateescape"))
            index += 1
            continue
        index += 1
        if index >= len(inner):
            break
        escaped = inner[index]
        index += 1
        mapping = {
            "n": b"\n",
            "t": b"\t",
            "r": b"\r",
            '"': b'"',
            "\\": b"\\",
            "a": b"\a",
            "b": b"\b",
            "f": b"\f",
            "v": b"\v",
        }
        if escaped in mapping:
            output.extend(mapping[escaped])
        elif escaped in "01234567":
            digits = escaped
            while index < len(inner) and len(digits) < 3 and inner[index] in "01234567":
                digits += inner[index]
                index += 1
            output.append(int(digits, 8))
        else:
            output.extend(escaped.encode("utf-8", "surrogateescape"))
    return output.decode("utf-8", "surrogateescape")


def normalize_diff_path(raw: str) -> str | None:
    value = raw.split("\t", 1)[0].strip()
    if not value or value == "/dev/null":
        return None
    value = _git_unquote(value)
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def block_changed_file(block: str) -> str | None:
    lines = block.splitlines()
    old_path: str | None = None
    new_path: str | None = None
    for line in lines:
        if line.startswith("@@"):
            break
        if line.startswith("--- "):
            old_path = normalize_diff_path(line[4:])
        elif line.startswith("+++ "):
            new_path = normalize_diff_path(line[4:])
    return new_path or old_path


def patch_changed_files(text: str) -> list[str]:
    files: list[str] = []
    for block in diff_blocks(text):
        old_path: str | None = None
        new_path: str | None = None
        for line in block.splitlines():
            if line.startswith("@@"):
                break
            if line.startswith("--- "):
                old_path = normalize_diff_path(line[4:])
            elif line.startswith("+++ "):
                new_path = normalize_diff_path(line[4:])
        if new_path is None and old_path is None:
            header = block.splitlines()[0]
            tokens = re.findall(r'"(?:\\.|[^"])*"|\S+', header)
            if len(tokens) >= 3:
                old_path = normalize_diff_path(tokens[1])
                new_path = normalize_diff_path(tokens[2])
        for changed in (new_path, old_path):
            if changed and changed not in files:
                files.append(changed)
    return files


def is_binary_diff(text: str) -> bool:
    return "GIT binary patch" in text or "Binary files " in text


def forbidden_path_reasons(
    files: Iterable[str], group: str, submodules: Sequence[str]
) -> list[str]:
    reasons: list[str] = []
    for file in files:
        if file == ".gitmodules":
            reasons.append(f"{file}: submodule metadata is forbidden")
            continue
        for submodule in submodules:
            if file == submodule or file.startswith(f"{submodule}/"):
                reasons.append(f"{file}: submodule paths are forbidden")
                break
        if file in config.VERSION_PATHS:
            reasons.append(f"{file}: version identity comes from base/release tags")
            continue
        if any(
            file == generated or file.startswith(f"{generated}/")
            for generated in config.GENERATED_ROOT_PATHS
        ):
            reasons.append(f"{file}: generated patchset files are forbidden")
            continue
        if file == config.CMAKELISTS and group != "misc":
            reasons.append(
                f"{file}: only misc patches may edit the top-level CMakeLists"
            )
    return reasons


def validate_patch_for_export(
    text: str, patch_name: str, submodules: Sequence[str]
) -> None:
    group, _short, _entry = parse_patch_name(patch_name)
    if not patch_has_diff(text):
        raise CheckError(f"patch {patch_name} is empty")
    if not patch_subject(text):
        raise CheckError(f"patch {patch_name} has no Subject")
    reasons = forbidden_path_reasons(patch_changed_files(text), group, submodules)
    if reasons:
        raise CheckError(
            f"patch {patch_name} changes forbidden paths:\n"
            + "\n".join(f"  {reason}" for reason in reasons)
        )


def load_deps(path: Path | None = None) -> list[dict[str, str]]:
    file = path or config.DEPS
    try:
        raw = read_text(file)
    except FileNotFoundError:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckError(f"{file} is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise CheckError(f"{file} must contain a JSON array")
    entries: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise CheckError(f"{file} entries must be objects")
        submodule = item.get("submodule")
        patch = item.get("patch")
        if not isinstance(submodule, str) or not isinstance(patch, str):
            raise CheckError(f"{file} entries need string 'submodule' and 'patch'")
        for value in (submodule, patch):
            if not value or any(char.isspace() for char in value):
                raise CheckError(
                    f"{file} paths must be non-empty and contain no whitespace: {value!r}"
                )
            if Path(value).is_absolute():
                raise CheckError(f"{file} paths must be repository-relative: {value!r}")
        entries.append({"submodule": submodule, "patch": patch})
    return entries


def dep_names(deps: Sequence[dict[str, str]]) -> list[str]:
    return [entry["submodule"] for entry in deps]
