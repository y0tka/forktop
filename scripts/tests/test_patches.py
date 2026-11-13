from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patchsetlib import config
from patchsetlib.commands import export as export_command
from patchsetlib.errors import CheckError
from patchsetlib.gitops import apply_deps, apply_series, dep_is_applied
from patchsetlib.patches import (
    forbidden_path_reasons,
    generate_stable_patch,
    parse_patch_name,
    patch_name_from_series,
    validate_patch_for_export,
)

from tests.support import commit, init_repo, write


class NameTests(unittest.TestCase):
    def test_patch_name_roundtrip(self) -> None:
        group, short, entry = parse_patch_name("feature__my-change")
        self.assertEqual((group, short), ("feature", "my-change"))
        self.assertEqual(entry, "feature/my-change.patch")
        self.assertEqual(patch_name_from_series(entry), "feature__my-change")

    def test_invalid_names_are_rejected(self) -> None:
        for value in ("feature__Bad", "visual__x", "feature_x", "feature__", "feature__x/y"):
            with self.subTest(value=value), self.assertRaises(CheckError):
                parse_patch_name(value)
        with self.assertRaises(CheckError):
            patch_name_from_series("feature/x.patch/extra")


class StablePatchTests(unittest.TestCase):
    def test_index_and_folded_subject_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo")
            subject = (
                "A deliberately long subject that git will fold across more than "
                "one header line because it is far beyond the usual width"
            )
            base = commit(repo, {"file.txt": "base\n"}, "base")
            commit(repo, {"file.txt": "next\n"}, subject)
            first = generate_stable_patch(repo, "HEAD")
            second = generate_stable_patch(repo, "HEAD")
            self.assertEqual(first, second)
            self.assertIn("index 0000000..0000000", first)
            self.assertEqual(len(re.findall(r"^Subject:", first, re.MULTILINE)), 1)
            self.assertNotRegex(first.split("---", 1)[0], r"Subject:[^\n]*\n[ \t]+")
            self.assertNotIn(base, first)  # not a whole-history diff

    def test_empty_patch_is_rejected(self) -> None:
        with self.assertRaises(CheckError):
            validate_patch_for_export("Subject: Empty\n\n---\n", "feature__empty", [])


class ForbiddenPathTests(unittest.TestCase):
    def test_submodule_version_and_cmake_paths(self) -> None:
        reasons = forbidden_path_reasons(
            [
                "Telegram/lib_ui/foo.cpp",
                "Telegram/build/version",
                "Telegram/CMakeLists.txt",
                "series",
            ],
            "feature",
            ["Telegram/lib_ui"],
        )
        joined = "\n".join(reasons)
        self.assertIn("submodule", joined)
        self.assertIn("version identity", joined)
        self.assertIn("only misc patches", joined)
        self.assertIn("generated patchset", joined)

    def test_misc_may_edit_cmake(self) -> None:
        self.assertEqual(
            forbidden_path_reasons(["Telegram/CMakeLists.txt"], "misc", []),
            [],
        )


class ApplySeriesTests(unittest.TestCase):
    def test_apply_succeeds_and_reports_the_failing_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo")
            commit(repo, {"a.txt": "base\n"}, "base")
            good = Path(tmp) / "good.patch"
            good.write_text(
                "diff --git a/b.txt b/b.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/b.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello\n"
            )
            bad = Path(tmp) / "bad.patch"
            bad.write_text(
                "diff --git a/missing.txt b/missing.txt\n"
                "--- a/missing.txt\n"
                "+++ b/missing.txt\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
            self.assertIsNone(apply_series(repo, [good]))
            self.assertEqual((repo / "b.txt").read_text(), "hello\n")
            failure = apply_series(repo, [bad])
            self.assertIsNotNone(failure)
            self.assertIn("bad.patch", failure or "")


class DepsTests(unittest.TestCase):
    def test_apply_is_idempotent_and_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo")
            sub = init_repo(repo / "sub")
            commit(sub, {"x.txt": "old\n"}, "sub base")
            patch = Path(tmp) / "sub.patch"
            patch.write_text(
                "diff --git a/x.txt b/x.txt\n"
                "--- a/x.txt\n"
                "+++ b/x.txt\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
            entry = {"submodule": "sub", "patch": str(patch)}
            self.assertEqual(apply_deps(repo, [entry]), 1)
            self.assertTrue(dep_is_applied(sub, patch))
            self.assertEqual(apply_deps(repo, [entry]), 0)
            self.assertEqual((sub / "x.txt").read_text(), "new\n")


class SeriesCompareTests(unittest.TestCase):
    def test_order_and_orphans_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patches = root / "patches"
            patches.mkdir()
            write(patches / "feature" / "a.patch", "a")
            write(patches / "feature" / "b.patch", "b")
            write(patches / "feature" / "orphan.patch", "c")
            series = root / "series"
            series.write_text("feature/b.patch\nfeature/a.patch\n")
            expected = {
                str((patches / "feature" / "a.patch").resolve()): b"a",
                str((patches / "feature" / "b.patch").resolve()): b"b",
            }
            with mock.patch.object(config, "PATCHES", patches), mock.patch.object(
                config, "SERIES", series
            ):
                problems = export_command._compare(
                    expected, ["feature/a.patch", "feature/b.patch"]
                )
            self.assertIn("orphan feature/orphan.patch", problems)
            self.assertIn("series order mismatch", problems)


if __name__ == "__main__":
    unittest.main()
