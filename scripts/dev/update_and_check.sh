#!/usr/bin/env bash
# Update main, refresh the virtual environment, run every local check, and launch the app.
#
# Usage: scripts/dev/update_and_check.sh [--no-pull] [--no-launch] [--checks-only] [--dry-run]
#   --no-pull      Skip the branch guard and git pull. Test the current checkout as it is.
#   --no-launch    Do not start infiniteprep after the checks pass.
#   --checks-only  Skip creating .venv and installing packages.
#   --dry-run      Run the guards, print the planned steps, and exit without changing anything.
#
# Exit codes: 0 when every check passes, 1 when a check fails, 2 when a guard stops the run.
# The script never stashes, resets, or deletes anything.
set -u

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repo_root" || exit 2

no_pull=0
no_launch=0
checks_only=0
dry_run=0
for arg in "$@"; do
    case "$arg" in
        --no-pull) no_pull=1 ;;
        --no-launch) no_launch=1 ;;
        --checks-only) checks_only=1 ;;
        --dry-run) dry_run=1 ;;
        *)
            echo "Unknown option: $arg. Use --no-pull, --no-launch, --checks-only, or --dry-run."
            exit 2
            ;;
    esac
done

if [ -n "$(git status --porcelain)" ]; then
    echo "The working tree has uncommitted changes. Commit or stash them, then run again."
    exit 2
fi
branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$no_pull" -eq 0 ] && [ "$branch" != "main" ]; then
    echo "The checkout is on '$branch', not main. Switch to main, or pass --no-pull to test it."
    exit 2
fi

if [ "$dry_run" -eq 1 ]; then
    [ "$no_pull" -eq 0 ] && echo "Would run: git pull --ff-only"
    [ "$checks_only" -eq 0 ] && echo "Would install: -e .[dev] and requirements-checkers.txt"
    echo "Would run checks: ruff-check ruff-format pytest policy-tests prose-lint sync-check"
    [ "$no_launch" -eq 0 ] && echo "Would launch: infiniteprep"
    exit 0
fi

mkdir -p logs
log_file="logs/update_and_check-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$log_file") 2>&1
echo "Logging to $log_file"

if [ "$no_pull" -eq 0 ]; then
    if ! git pull --ff-only; then
        echo "git pull could not fast-forward main. Resolve it by hand, then run again."
        exit 2
    fi
fi

venv_python=".venv/bin/python"
if [ "$checks_only" -eq 0 ]; then
    [ -x "$venv_python" ] || python3 -m venv .venv || exit 2
    "$venv_python" -m pip install --upgrade pip || exit 2
    "$venv_python" -m pip install -e ".[dev]" || exit 2
    "$venv_python" -m pip install --requirement requirements-checkers.txt || exit 2
fi

# The prose files and lint scripts mirror the lint target in the Makefile.
prose_files=(AGENTS.md README.md CHANGELOG.md docs/project-orientation.md docs/template-drift.md
    plan/HANDOFF.md.example SECURITY.md CONTRIBUTING.md)
names=()
results=()
run_check() {
    local name="$1"
    shift
    echo "=== $name ==="
    if "$@"; then results+=("PASS"); else results+=("FAIL"); fi
    names+=("$name")
}
run_check ruff-check "$venv_python" -m ruff check src tests
run_check ruff-format "$venv_python" -m ruff format --check src tests
run_check pytest "$venv_python" -m pytest
run_check policy-tests "$venv_python" scripts/run_tests.py
run_check prose-lint "$venv_python" scripts/lint_style.py
run_check prose-advisory "$venv_python" scripts/check_hedging.py "${prose_files[@]}"
run_check conflict-markers "$venv_python" scripts/check_conflict_markers.py
run_check sync-check "$venv_python" scripts/sync.py --check

failed=0
echo "=== Summary ==="
for i in "${!names[@]}"; do
    printf '%-18s %s\n' "${names[$i]}" "${results[$i]}"
    [ "${results[$i]}" = "PASS" ] || failed=1
done
if [ "$failed" -ne 0 ]; then
    echo "At least one check failed. See $log_file, then send the failing section."
    exit 1
fi
if [ "$no_launch" -eq 0 ]; then
    .venv/bin/infiniteprep
fi
exit 0
