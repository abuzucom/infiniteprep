"""Behavioral checks for the bundle size report and its build wiring (D354)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "packaging" / "bundle_size.py"
LIMITS_PATH = ROOT / "packaging" / "bundle_size_limits.json"
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"
REPORT_SCRIPT = "packaging/bundle_size.py"
OPERATING_SYSTEMS = ("windows", "macos", "linux")
PACKAGE_STEPS = (
    "Build Windows portable ZIP",
    "Build Windows installer",
    "Build Linux tarballs",
    "Build Linux AppImage",
    "Build macOS DMG",
)


def _load_script() -> ModuleType:
    """Import packaging/bundle_size.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("bundle_size", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def _make_bundle(root: Path, files: dict[str, int]) -> Path:
    """Create a bundle folder holding files of the given sizes."""
    bundle = root / "InfinitePrep"
    for name, size in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    return bundle


def _write_limits(path: Path, measured: dict[str, int | None]) -> Path:
    """Write a limits file holding the given measured sizes and a null for the rest."""
    document = {
        name: None if measured.get(name) is None else {"measured_bytes": measured[name]}
        for name in OPERATING_SYSTEMS
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class MeasurementTest(unittest.TestCase):
    """The report measures the files of a built bundle."""

    def test_counts_the_regular_files_below_the_bundle(self) -> None:
        """Every file below the bundle root adds its size to the total."""
        with tempfile.TemporaryDirectory() as directory:
            bundle = _make_bundle(Path(directory), {"app.exe": 100, "_internal/lib.dll": 250})
            self.assertEqual(SCRIPT.directory_bytes(bundle), 350)

    def test_skips_a_symbolic_link(self) -> None:
        """A symbolic link adds nothing, so a linked folder counts once."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"_internal/lib.dll": 250})
            try:
                (bundle / "link.dll").symlink_to(bundle / "_internal" / "lib.dll")
            except OSError as error:
                self.skipTest(f"the platform denies symbolic links: {error}")
            self.assertEqual(SCRIPT.directory_bytes(bundle), 250)

    def test_largest_folders_hold_the_recursive_totals(self) -> None:
        """Each folder holds the total size of the files below it."""
        with tempfile.TemporaryDirectory() as directory:
            bundle = _make_bundle(
                Path(directory),
                {"_internal/a.dll": 100, "_internal/deep/b.dll": 50, "Qt/plugins/c.dll": 300},
            )
            folders = SCRIPT.largest_folders(bundle)
            self.assertEqual([folder.name for folder in folders], ["Qt", "_internal"])
            self.assertEqual([folder.bytes for folder in folders], [300, 150])

    def test_largest_folders_stop_at_the_ten_largest(self) -> None:
        """The list holds ten folders even when the bundle holds more."""
        with tempfile.TemporaryDirectory() as directory:
            files = {f"folder{index}/file.dll": index for index in range(12)}
            bundle = _make_bundle(Path(directory), files)
            folders = SCRIPT.largest_folders(bundle)
            self.assertEqual(len(folders), 10)
            self.assertEqual(folders[0].name, "folder11")


class LimitTest(unittest.TestCase):
    """The limit comes from the measured size in the limits file."""

    def test_limit_is_the_measured_size_plus_half(self) -> None:
        """The limit is the measured size plus 50 percent, rounded up."""
        self.assertEqual(SCRIPT.derive_limit(100), 150)
        self.assertEqual(SCRIPT.derive_limit(101), 152)
        self.assertEqual(SCRIPT.derive_limit(0), 0)

    def test_limits_file_names_every_operating_system(self) -> None:
        """The limits file holds a record for each operating system."""
        limits = SCRIPT.load_limits(LIMITS_PATH)
        self.assertEqual(sorted(limits), sorted(OPERATING_SYSTEMS))

    def test_unknown_operating_system_is_rejected(self) -> None:
        """A record for an unknown operating system is an error."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.json"
            document = {"linux": None, "macos": None, "windows": None, "solaris": None}
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                SCRIPT.load_limits(path)

    def test_a_missing_operating_system_is_rejected(self) -> None:
        """A limits file without a record for every operating system is an error."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.json"
            document = {"linux": None, "macos": None}
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                SCRIPT.load_limits(path)

    def test_unknown_field_is_rejected(self) -> None:
        """A record with an unknown field is an error."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.json"
            document = {
                "linux": None,
                "macos": None,
                "windows": {"megabytes": 12},
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                SCRIPT.load_limits(path)

    def test_a_record_without_a_byte_count_is_rejected(self) -> None:
        """A record without a byte count is an error, so no limit is skipped."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.json"
            document = {
                "linux": None,
                "macos": None,
                "windows": {"measured_at": "2026-11-17"},
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                SCRIPT.load_limits(path)

    def test_a_null_record_has_no_measured_size(self) -> None:
        """A record of null means the size was not measured yet."""
        with tempfile.TemporaryDirectory() as directory:
            path = _write_limits(Path(directory) / "limits.json", {"windows": None})
            self.assertIsNone(SCRIPT.load_limits(path)["windows"])

    def test_a_record_may_hold_the_measurement_date(self) -> None:
        """A record may name the day of the measurement."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.json"
            document = {
                "linux": None,
                "macos": None,
                "windows": {"measured_bytes": 100, "measured_at": "2026-11-17"},
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(SCRIPT.load_limits(path)["windows"], 100)

    def test_a_wrong_measurement_date_is_rejected(self) -> None:
        """A measurement date that is not a calendar date is an error."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.json"
            document = {
                "linux": None,
                "macos": None,
                "windows": {"measured_bytes": 100, "measured_at": "2026-13-01"},
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                SCRIPT.load_limits(path)


class ReportTest(unittest.TestCase):
    """The report names the sizes of the bundle and its packages."""

    def test_report_names_the_bundle_and_package_sizes(self) -> None:
        """The report holds the bundle size and the size of every package."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            package = root / "package.zip"
            package.write_bytes(b"x" * 40)
            packages = (SCRIPT.PackageSize("zip", "package.zip", 40),)
            report = SCRIPT.build_report("windows", bundle, bundle, packages, 100)
            self.assertEqual(report.bundle_bytes, 100)
            self.assertEqual(report.limit_bytes, 150)
            self.assertEqual(report.headroom_bytes, 50)
            self.assertFalse(report.over_limit)
            self.assertEqual([package.name for package in report.packages], ["zip"])

    def test_report_marks_a_bundle_past_the_limit(self) -> None:
        """A bundle larger than its limit is marked as over the limit."""
        with tempfile.TemporaryDirectory() as directory:
            bundle = _make_bundle(Path(directory), {"app.exe": 100})
            report = SCRIPT.build_report("windows", bundle, bundle, (), 50)
            self.assertEqual(report.limit_bytes, 75)
            self.assertTrue(report.over_limit)

    def test_json_report_holds_the_sizes(self) -> None:
        """The JSON report holds the bundle size, the limit, and the packages."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            report = SCRIPT.build_report("windows", bundle, bundle, (), 100)
            document = SCRIPT.report_as_json(report)
            self.assertEqual(document["bundle_bytes"], 100)
            self.assertEqual(document["limit_bytes"], 150)
            self.assertFalse(document["over_limit"])
            self.assertTrue(document["limit_recorded"])


class CommandTest(unittest.TestCase):
    """The command reports the sizes and fails past the limit."""

    def _run(self, arguments: list[str]) -> tuple[int, str]:
        """Run the command and return its exit code and its output."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = SCRIPT.main(arguments)
        return status, output.getvalue()

    def test_command_passes_under_the_limit(self) -> None:
        """A bundle below its limit passes and the report names the sizes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            limits = _write_limits(root / "limits.json", {"windows": 100})
            status, output = self._run(
                ["check", "--os", "windows", "--bundle", str(bundle), "--limits", str(limits)]
            )
            self.assertEqual(status, 0)
            self.assertIn("100 bytes", output)

    def test_command_fails_past_the_limit(self) -> None:
        """A bundle above its limit fails and names the limit and the excess."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            limits = _write_limits(root / "limits.json", {"windows": 10})
            status, output = self._run(
                ["check", "--os", "windows", "--bundle", str(bundle), "--limits", str(limits)]
            )
            self.assertEqual(status, 1)
            self.assertIn("over the limit", output)
            self.assertIn("past the limit of 15 bytes", output)

    def test_command_reports_without_a_measured_size(self) -> None:
        """A missing measured size reports the sizes and passes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            limits = _write_limits(root / "limits.json", {"windows": None})
            status, output = self._run(
                ["check", "--os", "windows", "--bundle", str(bundle), "--limits", str(limits)]
            )
            self.assertEqual(status, 0)
            self.assertIn("not recorded yet", output)

    def test_command_writes_the_reports(self) -> None:
        """The command writes a Markdown report and a JSON report."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            limits = _write_limits(root / "limits.json", {"windows": 100})
            reports = root / "reports"
            status, _ = self._run(
                [
                    "check",
                    "--os",
                    "windows",
                    "--bundle",
                    str(bundle),
                    "--limits",
                    str(limits),
                    "--report-dir",
                    str(reports),
                ]
            )
            self.assertEqual(status, 0)
            document = json.loads(
                (reports / "bundle-size-windows.json").read_text(encoding="utf-8")
            )
            self.assertEqual(document["bundle_bytes"], 100)
            self.assertTrue((reports / "bundle-size-windows.md").is_file())

    def test_command_rejects_a_missing_bundle(self) -> None:
        """A bundle folder that does not exist is a usage error."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            limits = _write_limits(root / "limits.json", {"windows": 100})
            with self.assertRaises(SystemExit) as raised:
                self._run(
                    [
                        "check",
                        "--os",
                        "windows",
                        "--bundle",
                        str(root / "absent"),
                        "--limits",
                        str(limits),
                    ]
                )
            self.assertEqual(raised.exception.code, 2)

    def test_command_rejects_a_missing_package(self) -> None:
        """A package file that does not exist is a usage error."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _make_bundle(root, {"app.exe": 100})
            limits = _write_limits(root / "limits.json", {"windows": 100})
            with self.assertRaises(SystemExit) as raised:
                self._run(
                    [
                        "check",
                        "--os",
                        "windows",
                        "--bundle",
                        str(bundle),
                        "--package",
                        "zip=absent.zip",
                        "--limits",
                        str(limits),
                    ]
                )
            self.assertEqual(raised.exception.code, 2)


class WorkflowTest(unittest.TestCase):
    """The bundle job reports the sizes on each operating system."""

    workflow: dict[str, Any]
    steps: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once for every check."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.steps = cls.workflow["jobs"]["bundle"]["steps"]

    def _size_steps(self) -> list[dict[str, Any]]:
        """Return every step that runs the size report."""
        return [step for step in self.steps if REPORT_SCRIPT in step.get("run", "")]

    def test_each_operating_system_reports_its_sizes(self) -> None:
        """One size step names the bundle and the packages of every operating system."""
        steps = self._size_steps()
        self.assertEqual(len(steps), 1)
        run = steps[0]["run"]
        self.assertIn("--bundle", run)
        self.assertIn("--package", run)
        self.assertIn("--report-dir", run)
        systems = re.findall(r"--os ([a-z]+)", run)
        self.assertEqual(sorted(systems), sorted(OPERATING_SYSTEMS))

    def test_the_macos_step_reports_the_folders_of_the_app(self) -> None:
        """The macOS step names the folder that holds the parts of the app."""
        run = next(step["run"] for step in self._size_steps() if "--os macos" in step["run"])
        self.assertIn("--folders-root dist/InfinitePrep.app/Contents", run)

    def test_size_step_runs_after_packaging(self) -> None:
        """Every size step comes after the step that builds the packages."""
        names = [step.get("name", "") for step in self.steps]
        last_package = max(names.index(name) for name in PACKAGE_STEPS)
        for step in self._size_steps():
            self.assertGreater(names.index(step["name"]), last_package)

    def test_the_report_uploads_with_the_other_artifacts(self) -> None:
        """The report uploads as an artifact and keeps it for three days."""
        uploads = [
            step
            for step in self.steps
            if step.get("uses", "").startswith("actions/upload-artifact@")
        ]
        reports = [upload for upload in uploads if "bundle-size" in upload["with"]["name"]]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["with"]["retention-days"], 3)
        self.assertEqual(reports[0]["with"]["if-no-files-found"], "error")


if __name__ == "__main__":
    unittest.main()
