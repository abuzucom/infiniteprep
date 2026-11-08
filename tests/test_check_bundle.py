"""Behavioral checks for the bundle content check and its build wiring (D83a4a)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "packaging" / "check_bundle.py"
ALLOWLIST_DIR = ROOT / "packaging" / "bundle-allowlist"
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"
NOTICES = ("LICENSE", "NOTICE", "ICU-LICENSE.txt")
ICU_LICENSE_URL = "https://raw.githubusercontent.com/unicode-org/icu/release-73-2/icu4c/LICENSE"
ICU_LICENSE_SHA256 = "f3005e195ff74d8812cc1f182a1c446fab678d70a10e3dada497585befee5416"
PACKAGING_STEPS = ("Build Windows portable ZIP", "Build Linux tarballs", "Sign macOS app")


def _load_checker() -> ModuleType:
    """Import packaging/check_bundle.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("check_bundle", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


def _make_bundle(root: Path, files: tuple[str, ...]) -> Path:
    """Create a bundle folder holding empty files at the given relative paths."""
    bundle = root / "InfinitePrep"
    for name in files:
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return bundle


class PatternTest(unittest.TestCase):
    """Allowlist patterns follow the documented wildcard rules."""

    def test_single_star_stays_within_one_path_part(self) -> None:
        """'*' matches a file name but never a folder separator."""
        pattern = CHECKER.compile_pattern("_internal/*.pyd")
        self.assertTrue(pattern.fullmatch("_internal/_ssl.pyd"))
        self.assertFalse(pattern.fullmatch("_internal/PySide6/QtCharts.pyd"))

    def test_double_star_spans_any_number_of_folders(self) -> None:
        """'**/' matches no folder, one folder, or several."""
        pattern = CHECKER.compile_pattern("_internal/scipy/**/*.pyd")
        self.assertTrue(pattern.fullmatch("_internal/scipy/_cyutility.pyd"))
        self.assertTrue(pattern.fullmatch("_internal/scipy/linalg/_fblas.pyd"))
        self.assertTrue(pattern.fullmatch("_internal/scipy/sparse/linalg/_dsolve/_superlu.pyd"))
        self.assertFalse(pattern.fullmatch("_internal/scipy/linalg/_fblas.dll"))

    def test_trailing_double_star_matches_everything_below(self) -> None:
        """A folder pattern ending in '**' matches every file below that folder."""
        pattern = CHECKER.compile_pattern("_internal/keyring-*.dist-info/**")
        self.assertTrue(pattern.fullmatch("_internal/keyring-25.7.0.dist-info/licenses/LICENSE"))
        self.assertFalse(pattern.fullmatch("_internal/numpy-2.5.4.dist-info/METADATA"))

    def test_question_mark_and_literal_dots(self) -> None:
        """'?' matches one character, and a dot matches only a dot."""
        pattern = CHECKER.compile_pattern("_internal/libbz2.so.?")
        self.assertTrue(pattern.fullmatch("_internal/libbz2.so.1"))
        self.assertFalse(pattern.fullmatch("_internal/libbz2.so.10"))
        self.assertFalse(pattern.fullmatch("_internal/libbz2xso.1"))

    def test_allowlist_reader_skips_blanks_and_comments(self) -> None:
        """Comment lines and blank lines add no pattern."""
        patterns = CHECKER.read_allowlist(["# comment", "", "  LICENSE  ", "NOTICE"])
        self.assertEqual(len(patterns), 2)
        self.assertTrue(patterns[0].fullmatch("LICENSE"))


class DenylistTest(unittest.TestCase):
    """Qt tools, GPL-3-only Qt modules, and GNU Readline are always denied."""

    def test_qt_tools_are_denied_on_every_os(self) -> None:
        """Tool executables and macOS tool app bundles fail the check."""
        for name in (
            "_internal/PySide6/designer.exe",
            "_internal/PySide6/lupdate",
            "_internal/PySide6/Qt/libexec/rcc",
            "_internal/PySide6/Designer.app/Contents/MacOS/Designer",
            "_internal/PySide6/qsb.exe",
        ):
            with self.subTest(name=name):
                self.assertTrue(CHECKER.is_denied(name))

    def test_gpl3_module_families_are_denied_on_every_os(self) -> None:
        """Each GPL-3-only Qt module family fails under Linux, macOS, and Windows names."""
        for name in (
            "_internal/libQt6Charts.so.6",
            "_internal/PySide6/Qt6DataVisualization.dll",
            "_internal/PySide6/Qt/lib/QtGraphs.framework/Versions/A/QtGraphs",
            "_internal/PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so",
            "_internal/PySide6/Qt6Quick3DPhysics.dll",
            "_internal/libQt6Lottie.so.6",
            "_internal/PySide6/QtQuickTimeline.abi3.so",
            "_internal/PySide6/Qt6NetworkAuth.dll",
            "_internal/libQt6HttpServer.so.6",
        ):
            with self.subTest(name=name):
                self.assertTrue(CHECKER.is_denied(name))

    def test_readline_is_denied(self) -> None:
        """The GNU Readline library and Python's readline module fail the check."""
        self.assertTrue(CHECKER.is_denied("_internal/libreadline.so.8"))
        self.assertTrue(
            CHECKER.is_denied("_internal/python3.12/lib-dynload/readline.cpython-312-darwin.so")
        )

    def test_files_the_app_uses_are_not_denied(self) -> None:
        """Quick, QML, Svg, the touch plugin, and the SVG icon engine stay allowed."""
        for name in (
            "_internal/libQt6Quick.so.6",
            "_internal/PySide6/Qt/lib/QtQml.framework/Versions/A/QtQml",
            "_internal/PySide6/Qt6Svg.dll",
            "_internal/PySide6/plugins/generic/qtuiotouchplugin.dll",
            "_internal/PySide6/plugins/iconengines/qsvgicon.dll",
            "_internal/PySide6/translations/qtbase_de.qm",
            "InfinitePrep.exe",
        ):
            with self.subTest(name=name):
                self.assertFalse(CHECKER.is_denied(name))


class CheckFilesTest(unittest.TestCase):
    """check_files reports unlisted files, denied files, and missing notices."""

    def test_listed_bundle_with_notices_passes(self) -> None:
        """Every file matches a pattern, and all three notices exist."""
        patterns = CHECKER.read_allowlist(["LICENSE", "NOTICE", "ICU-LICENSE.txt", "app.exe"])

        report = CHECKER.check_files([*NOTICES, "app.exe"], patterns)

        self.assertTrue(report.passed)

    def test_unlisted_file_fails(self) -> None:
        """A file with no matching pattern shows up by its relative path."""
        patterns = CHECKER.read_allowlist(["LICENSE", "NOTICE", "ICU-LICENSE.txt"])

        report = CHECKER.check_files([*NOTICES, "_internal/libnew.so.1"], patterns)

        self.assertFalse(report.passed)
        self.assertEqual(report.unlisted, ["_internal/libnew.so.1"])

    def test_denied_file_fails_even_when_a_pattern_matches_it(self) -> None:
        """The denylist wins over a pattern that allows every file."""
        patterns = CHECKER.read_allowlist(["**"])

        report = CHECKER.check_files([*NOTICES, "_internal/PySide6/Qt6Charts.dll"], patterns)

        self.assertEqual(report.unlisted, [])
        self.assertEqual(report.denied, ["_internal/PySide6/Qt6Charts.dll"])
        self.assertFalse(report.passed)

    def test_missing_notices_fail_in_a_fixed_order(self) -> None:
        """Each notice missing from the root is named, and a nested copy does not count."""
        patterns = CHECKER.read_allowlist(["**"])

        report = CHECKER.check_files(["LICENSE", "_internal/NOTICE"], patterns)

        self.assertEqual(report.missing_notices, ["NOTICE", "ICU-LICENSE.txt"])


class BundleFolderTest(unittest.TestCase):
    """check_bundle and main read a real folder and a real allowlist."""

    def test_real_linux_allowlist_names_only_the_unknown_library(self) -> None:
        """A folder of listed Linux files plus one unknown library fails on that library."""
        files = (
            *NOTICES,
            "InfinitePrep",
            "_internal/base_library.zip",
            "_internal/libQt6Core.so.6",
            "_internal/scipy/linalg/_fblas.cpython-312-x86_64-linux-gnu.so",
            "_internal/PySide6/Qt/translations/qtbase_de.qm",
            "_internal/libunknown.so.3",
        )
        with tempfile.TemporaryDirectory() as temp:
            bundle = _make_bundle(Path(temp), files)

            report = CHECKER.check_bundle(bundle, "linux")

        self.assertEqual(report.unlisted, ["_internal/libunknown.so.3"])
        self.assertEqual(report.denied, [])
        self.assertEqual(report.missing_notices, [])

    def test_main_returns_one_and_names_each_problem(self) -> None:
        """main prints each problem and returns 1 for a failing Windows bundle."""
        files = ("LICENSE", "InfinitePrep.exe", "_internal/PySide6/designer.exe")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp:
            bundle = _make_bundle(Path(temp), files)
            with contextlib.redirect_stdout(output):
                exit_code = CHECKER.main([str(bundle), "windows"])

        text = output.getvalue()
        self.assertEqual(exit_code, CHECKER.CHECK_FAILED)
        self.assertIn("denied file: _internal/PySide6/designer.exe", text)
        self.assertIn("not in the allowlist: _internal/PySide6/designer.exe", text)
        self.assertIn("missing notice at the bundle root: ICU-LICENSE.txt", text)

    def test_main_returns_zero_for_a_passing_macos_bundle(self) -> None:
        """A macOS bundle of listed files and all notices passes."""
        files = (*NOTICES, "InfinitePrep", "_internal/QtCore", "_internal/Python")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp:
            bundle = _make_bundle(Path(temp), files)
            with contextlib.redirect_stdout(output):
                exit_code = CHECKER.main([str(bundle), "macos"])

        self.assertEqual(exit_code, 0)
        self.assertIn("macos bundle passed", output.getvalue())

    def test_every_allowlist_lists_the_notices_and_no_denied_name(self) -> None:
        """Each OS allowlist allows the three notices, and no plain entry is a denied name."""
        for operating_system in CHECKER.OPERATING_SYSTEMS:
            with self.subTest(operating_system=operating_system):
                lines = (ALLOWLIST_DIR / f"{operating_system}.txt").read_text(encoding="utf-8")
                patterns = CHECKER.read_allowlist(lines.splitlines())
                for notice in NOTICES:
                    self.assertTrue(any(p.fullmatch(notice) for p in patterns), notice)
                entries = [line.strip() for line in lines.splitlines()]
                plain = [entry for entry in entries if entry and not entry.startswith("#")]
                self.assertEqual([entry for entry in plain if CHECKER.is_denied(entry)], [])


class WorkflowWiringTest(unittest.TestCase):
    """build-packages fetches the ICU license and checks every bundle before packaging."""

    steps: list[dict[str, Any]]
    names: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.steps = workflow["jobs"]["bundle"]["steps"]
        cls.names = [step.get("name", "") for step in cls.steps]

    def _step(self, name: str) -> dict[str, Any]:
        """Return the one step with this name."""
        [step] = [step for step in self.steps if step.get("name") == name]
        return step

    def test_icu_license_is_pinned_and_checked(self) -> None:
        """The ICU license comes from the release-73-2 tag and must match its SHA-256."""
        step = self._step("Fetch pinned ICU license")
        self.assertEqual(step["env"]["ICU_LICENSE_URL"], ICU_LICENSE_URL)
        self.assertEqual(step["env"]["ICU_LICENSE_SHA256"], ICU_LICENSE_SHA256)
        self.assertIn("hashlib.sha256", step["run"])
        self.assertNotIn("if", step)

    def test_notices_include_the_icu_license(self) -> None:
        """The bundle gets the ICU license with LICENSE and NOTICE, after the fetch."""
        notices = self._step("Add license notices to bundle")["run"]
        self.assertIn('"$RUNNER_TEMP/icu/ICU-LICENSE.txt"', notices)
        fetch = self.names.index("Fetch pinned ICU license")
        self.assertLess(fetch, self.names.index("Add license notices to bundle"))

    def test_check_runs_on_every_os_before_any_upload_or_package(self) -> None:
        """The check follows the notices and comes before every upload and package step."""
        step = self._step("Check bundle contents")
        self.assertNotIn("if", step)
        self.assertIn('python packaging/check_bundle.py dist/InfinitePrep "$os_name"', step["run"])
        self.assertIn("tr '[:upper:]' '[:lower:]'", step["run"])
        check = self.names.index("Check bundle contents")
        self.assertGreater(check, self.names.index("Add license notices to bundle"))
        for index, step_entry in enumerate(self.steps):
            if step_entry.get("uses", "").startswith("actions/upload-artifact@"):
                self.assertLess(check, index, step_entry.get("name"))
        for name in PACKAGING_STEPS:
            self.assertLess(check, self.names.index(name), name)

    def test_every_package_carries_the_icu_license(self) -> None:
        """The macOS app, the DMG, the Linux tarballs, and the installed app hold the file."""
        for name in ("Sign macOS app", "Build macOS DMG"):
            self.assertIn('"$RUNNER_TEMP/icu/ICU-LICENSE.txt"', self._step(name)["run"], name)
        for name in ("Test macOS DMG", "Test Linux packages", "Test Windows installer lifecycle"):
            self.assertIn("ICU-LICENSE.txt", self._step(name)["run"], name)


if __name__ == "__main__":
    unittest.main()
