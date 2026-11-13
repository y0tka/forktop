from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patchsetlib.lint import analyze, format_ranges


def header(subject: str, body: str) -> str:
    return (
        f"From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
        f"Subject: {subject}\n\n---\n{body}"
    )


A_ADDS_ONE = header(
    "A adds one",
    "diff --git a/x.txt b/x.txt\n"
    "--- a/x.txt\n"
    "+++ b/x.txt\n"
    "@@ -1 +1,2 @@\n"
    " base\n"
    "+added\n",
)

B_REVERTS_ONE = header(
    "B reverts one",
    "diff --git a/x.txt b/x.txt\n"
    "--- a/x.txt\n"
    "+++ b/x.txt\n"
    "@@ -2 +1,0 @@\n"
    "-added\n",
)

A_ADDS_TWO = header(
    "A adds two",
    "diff --git a/y.txt b/y.txt\n"
    "--- a/y.txt\n"
    "+++ b/y.txt\n"
    "@@ -1 +1,3 @@\n"
    " base\n"
    "+one\n"
    "+two\n",
)

B_OVERWRITES_ONE = header(
    "B overwrites one",
    "diff --git a/y.txt b/y.txt\n"
    "--- a/y.txt\n"
    "+++ b/y.txt\n"
    "@@ -2 +1,0 @@\n"
    "-one\n",
)


class LintTests(unittest.TestCase):
    def test_full_reversion(self) -> None:
        result = analyze(
            ["feature__a", "feature__b"],
            {"feature__a": A_ADDS_ONE, "feature__b": B_REVERTS_ONE},
        )
        self.assertEqual(len(result.reversions), 1)
        self.assertEqual(result.reversions[0].earlier, "feature__a")
        self.assertEqual(result.reversions[0].later, "feature__b")
        self.assertFalse(result.overwrites)

    def test_partial_overwrite(self) -> None:
        result = analyze(
            ["feature__a", "feature__b"],
            {"feature__a": A_ADDS_TWO, "feature__b": B_OVERWRITES_ONE},
        )
        self.assertFalse(result.reversions)
        self.assertEqual(len(result.overwrites), 1)
        self.assertEqual(result.overwrites[0].locations["y.txt"], {2})
        self.assertEqual(format_ranges({2}), "2")

    def test_ignore_suppresses_only_an_exact_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ignore = Path(tmp) / "lint-ignore.txt"
            ignore.write_text("feature__b\tfeature__a\treason\n")
            result = analyze(
                ["feature__a", "feature__b"],
                {"feature__a": A_ADDS_ONE, "feature__b": B_REVERTS_ONE},
                ignore,
            )
        self.assertFalse(result.has_findings)
        # A different pair is still reported.
        other = analyze(
            ["feature__a", "feature__z", "feature__b"],
            {"feature__a": A_ADDS_ONE, "feature__z": "", "feature__b": B_REVERTS_ONE},
            Path("/does/not/exist"),
        )
        self.assertTrue(other.has_findings)


if __name__ == "__main__":
    unittest.main()
