"""Argument shapes for the go and cargo test runners.

A compiled test runner builds and runs repository code. The command gate
accepts only the fixed shapes below, and only in a repository that holds
the runner's manifest as a regular file at its root. The gate then applies
the trusted owner rule and the PATH lookup check.

This module uses only the standard library. It imports no gate module, so
no import cycle can form.
"""
import os
import re
import stat
from typing import Callable, NamedTuple

TEST_SUBCOMMAND = "test"
# A test name holds letters, digits, and underscores. go also accepts a leading ^ anchor.
TEST_NAME_PATTERN = re.compile(r"\^?[A-Za-z0-9_]{1,128}")


class CompiledRunner(NamedTuple):
    """One compiled test runner and the argument shape the gate accepts."""

    manifest: str
    flags: frozenset
    filter_option: str
    accepts_packages: bool


COMPILED_RUNNERS = {
    "go": CompiledRunner("go.mod", frozenset({"-v", "-count=1"}), "-run", True),
    "cargo": CompiledRunner("Cargo.toml", frozenset({"-q", "--quiet", "--no-fail-fast"}), "", False),
}


def _manifest_present(project_dir: str, manifest: str) -> bool:
    """Return whether the manifest is a regular file, not a link, at the repository root."""
    try:
        details = os.lstat(os.path.join(project_dir, manifest))
    except OSError:
        return False
    return stat.S_ISREG(details.st_mode)


def _arguments_accepted(runner: CompiledRunner, arguments: list, path_ok: Callable) -> bool:
    """Return whether test arguments use only the runner's flags, one filter, and packages."""
    filters = 0
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in runner.flags:
            index += 1
            continue
        if runner.filter_option and argument == runner.filter_option:
            index += 1
            if index >= len(arguments) or not TEST_NAME_PATTERN.fullmatch(arguments[index]):
                return False
            filters += 1
        elif not runner.filter_option and TEST_NAME_PATTERN.fullmatch(argument):
            filters += 1
        elif not (runner.accepts_packages and path_ok(argument)):
            return False
        index += 1
    return filters <= 1


def compiled_test_shape(program: str, arguments: list, project_dir: str,
                        path_ok: Callable) -> bool:
    """Return whether `program test ...` is an accepted compiled test run.

    `arguments` holds the tokens after the program. `path_ok` decides
    whether a token is an inert repository-relative package path.
    """
    runner = COMPILED_RUNNERS.get(program)
    if runner is None or not arguments or arguments[0] != TEST_SUBCOMMAND:
        return False
    return (_manifest_present(project_dir, runner.manifest)
            and _arguments_accepted(runner, arguments[1:], path_ok))
