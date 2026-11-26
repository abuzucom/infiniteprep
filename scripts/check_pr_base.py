#!/usr/bin/env python3
"""Fail when a pull request base is outside the stable branch set.

Workflow branch filters are static and cannot read hooks/primary-branch.txt.
The workflows run on every base. The gate integrity job runs this script from
the trusted base checkout. The hook code in that checkout defines the stable
set, so a pull request cannot change the rule it is checked against.
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

CORE_MODULE = "_gate_core"
HOOKS_DIRECTORY = "hooks"
FAILURE_EXIT_CODE = 1


def load_gate_core(root: Path):
    """Import hooks/_gate_core.py from root by path."""
    hooks = root / HOOKS_DIRECTORY
    path = hooks / f"{CORE_MODULE}.py"
    if not path.is_file():
        raise FileNotFoundError(f"{path.relative_to(root).as_posix()} is missing")
    # The core module imports sibling hook modules by bare name.
    sys.path.insert(0, str(hooks))
    spec = importlib.util.spec_from_file_location(CORE_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_base(root: Path, base: str) -> str:
    """Return an empty string for a stable base, else the denial reason."""
    core = load_gate_core(root)
    decision, reason = core.pull_request_base_verdict(["pr", "create", "--base", base], str(root))
    return reason if decision == "deny" else ""


def main() -> int:
    """Check one pull request base and report the result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="pull request base branch")
    parser.add_argument("--root", default=os.getcwd(), help="trusted repository root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        reason = check_base(root, args.base)
    except (OSError, ImportError) as error:
        print(f"error: the stable branch set cannot load ({error}). Restore hooks/ "
              "in the base branch and rerun the job.", file=sys.stderr)
        return FAILURE_EXIT_CODE
    if reason:
        print(f"error: {reason}", file=sys.stderr)
        return FAILURE_EXIT_CODE
    print(f"pull request base `{args.base}` is a stable branch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
