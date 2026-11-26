#!/usr/bin/env python3
"""Cover the bounded coverage summary document that the pull request comment uses."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

BUILDER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_coverage_summary.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_coverage_summary", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def covered_class(name: str, covered: int, total: int) -> str:
    """Return one Cobertura class element with covered of total lines hit."""
    lines = "".join(
        f'<line number="{number}" hits="{1 if number <= covered else 0}"/>'
        for number in range(1, total + 1)
    )
    return f'<class name="{name}" filename="src/{name}"><lines>{lines}</lines></class>'


REPORT = """<?xml version="1.0" ?>
<coverage line-rate="0.5" branch-rate="0.5" lines-covered="3" lines-valid="4"
  branches-covered="1" branches-valid="2" version="7.17.3">
  <packages>
    <package name="src" line-rate="0.5" branch-rate="0.5">
      <classes>
        <class name="a.py" filename="src/a.py" line-rate="0.5" branch-rate="0.5">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
          </lines>
          <branches>
            <branch number="1" type="if" condition-coverage="50% (1/2)"/>
          </branches>
        </class>
        <class name="b.py" filename="src/b.py" line-rate="1.0" branch-rate="1.0">
          <lines>
            <line number="1" hits="3"/>
          </lines>
          <branches/>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


class BuildCoverageSummaryTest(unittest.TestCase):
    """Cover the summary document built from one coverage report."""

    def test_total_uses_lines_and_branches(self) -> None:
        """The total holds the covered lines and branches over the measured ones."""
        summary = builder.read_summary(self.write_report(REPORT))

        self.assertEqual(summary["total"], 66.7)

    def test_files_carry_path_and_percent(self) -> None:
        """Every file record holds its path and its percent."""
        summary = builder.read_summary(self.write_report(REPORT))

        files = summary["files"]
        self.assertEqual(files[0], {"path": "src/a.py", "percent": 50.0})
        self.assertEqual(files[1], {"path": "src/b.py", "percent": 100.0})

    def test_least_covered_file_comes_first(self) -> None:
        """The files sort by the percent and then by the path.

        Input order, path order, and percent order all differ, so only the
        (percent, path) sort yields b, c, a.
        """
        classes = "".join(
            covered_class(name, covered, total)
            for name, covered, total in (("c.py", 1, 10), ("a.py", 1, 2), ("b.py", 1, 10))
        )
        report = (
            '<coverage lines-covered="3" lines-valid="22" branches-covered="0" '
            f'branches-valid="0"><packages><classes>{classes}</classes></packages></coverage>'
        )

        summary = builder.read_summary(self.write_report(report))

        paths = [item["path"] for item in summary["files"]]
        self.assertEqual(paths, ["src/b.py", "src/c.py", "src/a.py"])

    def test_the_source_prefix_appears_once(self) -> None:
        """A path that already holds the prefix keeps one prefix."""
        report = (
            '<coverage lines-covered="1" lines-valid="2" branches-covered="0" '
            'branches-valid="0"><packages><classes>'
            '<class name="a.py" filename="src/infiniteprep/ui/theme.py" line-rate="0.5">'
            '<lines><line number="1" hits="1"/><line number="2" hits="0"/></lines></class>'
            "</classes></packages></coverage>"
        )

        summary = builder.read_summary(self.write_report(report), "src/infiniteprep")

        self.assertEqual(summary["files"][0]["path"], "src/infiniteprep/ui/theme.py")

    def test_source_prefix_joins_relative_path(self) -> None:
        """A path relative to the measured source gains the prefix."""
        report = (
            '<coverage lines-covered="1" lines-valid="2" branches-covered="0" '
            'branches-valid="0"><packages><classes>'
            '<class name="a.py" filename="ui/theme.py" line-rate="0.5">'
            '<lines><line number="1" hits="1"/><line number="2" hits="0"/></lines></class>'
            "</classes></packages></coverage>"
        )

        summary = builder.read_summary(self.write_report(report), "src/infiniteprep")

        self.assertEqual(summary["files"][0]["path"], "src/infiniteprep/ui/theme.py")

    def test_the_file_list_stays_bounded(self) -> None:
        """The document holds no more than the capped number of files."""
        classes = "".join(
            f'<class name="f{index}.py" filename="src/f{index}.py" line-rate="0.5">'
            f'<lines><line number="1" hits="1"/></lines></class>'
            for index in range(30)
        )
        report = (
            '<coverage lines-covered="30" lines-valid="60" branches-covered="0" '
            f'branches-valid="0"><packages><classes>{classes}</classes></packages></coverage>'
        )

        summary = builder.read_summary(self.write_report(report))

        self.assertEqual(len(summary["files"]), builder.MAX_FILES)

    def test_the_summary_writes_json(self) -> None:
        """The command writes the summary document as JSON."""
        with tempfile.TemporaryDirectory() as folder:
            report_path = Path(folder) / "coverage.xml"
            report_path.write_text(REPORT, encoding="utf-8")
            output_path = Path(folder) / "coverage-summary.json"

            code = builder.write_summary(report_path, output_path)

            self.assertEqual(code, 0)
            document = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(document["total"], 66.7)

    def write_report(self, text: str) -> Path:
        """Write one coverage report file and return its path."""
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(_remove_tree, folder)
        path = folder / "coverage.xml"
        path.write_text(text, encoding="utf-8")
        return path


def _remove_tree(folder: Path) -> None:
    """Remove one temporary folder."""
    shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
