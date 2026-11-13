"""Cross-patch lint command."""

from __future__ import annotations

from .. import config
from ..errors import CheckError
from ..lint import analyze, format_ranges
from ..log import success, warn
from ..patches import patch_name_from_series, read_series, read_text


def run(check: bool) -> None:
    entries = read_series()
    names = [patch_name_from_series(entry) for entry in entries]
    texts = {
        name: read_text(config.PATCHES / entry) for name, entry in zip(names, entries)
    }
    result = analyze(names, texts, config.LINT_IGNORE)

    if result.reversions:
        print("Full reversions:")
        for item in result.reversions:
            print(
                f"  {item.later} reverts {item.earlier} "
                f"({item.lines} changed lines)"
            )
            for file in item.files:
                print(f"    {file}")
    if result.overwrites:
        if result.reversions:
            print()
        print("Earlier patch lines overwritten:")
        for item in result.overwrites:
            count = sum(len(lines) for lines in item.locations.values())
            plural = "line" if count == 1 else "lines"
            print(f"  {item.later} overwrites {count} {plural} from {item.earlier}")
            for file, lines in sorted(item.locations.items()):
                print(f"    {file}:{format_ranges(lines)}")

    if result.has_findings:
        if check:
            raise CheckError("patch overwrite lint failed")
        warn("patch overwrite findings (advisory; use --check to fail)")
        return
    success(f"No patch overwrites found across {len(names)} patches")
