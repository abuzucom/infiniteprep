# Update main, refresh the virtual environment, run every local check, and launch the app.
#
# Usage: scripts\dev\update_and_check.ps1 [-NoPull] [-NoLaunch] [-ChecksOnly] [-DryRun]
#   -NoPull      Skip the branch guard and git pull. Test the current checkout as it is.
#   -NoLaunch    Do not start infiniteprep after the checks pass.
#   -ChecksOnly  Skip creating .venv and installing packages.
#   -DryRun      Run the guards, print the planned steps, and exit without changing anything.
#
# Exit codes: 0 when every check passes, 1 when a check fails, 2 when a guard stops the run.
# The script never stashes, resets, or deletes anything.
param(
    [switch]$NoPull,
    [switch]$NoLaunch,
    [switch]$ChecksOnly,
    [switch]$DryRun
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

if (git status --porcelain) {
    Write-Output "The working tree has uncommitted changes. Commit or stash them, then run again."
    exit 2
}
$branch = (git rev-parse --abbrev-ref HEAD)
if (-not $NoPull -and $branch -ne "main") {
    Write-Output "The checkout is on '$branch', not main. Switch to main, or pass -NoPull to test it."
    exit 2
}

if ($DryRun) {
    if (-not $NoPull) { Write-Output "Would run: git pull --ff-only" }
    if (-not $ChecksOnly) { Write-Output "Would install: -e .[dev] and requirements-checkers.txt" }
    Write-Output ("Would run checks: ruff-check ruff-format pytest policy-tests prose-lint " +
        "prose-advisory conflict-markers sync-check")
    if (-not $NoLaunch) { Write-Output "Would launch: infiniteprep" }
    exit 0
}

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$logFile = Join-Path "logs" "update_and_check-$stamp.log"
Start-Transcript -Path $logFile -Append | Out-Null

function Stop-Run([int]$code) {
    Stop-Transcript | Out-Null
    exit $code
}

if (-not $NoPull) {
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Write-Output "git pull could not fast-forward main. Resolve it by hand, then run again."
        Stop-Run 2
    }
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not $ChecksOnly) {
    if (-not (Test-Path $venvPython)) {
        # The py launcher picks the newest Python 3 on Windows. Plain python is the fallback.
        if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv }
        else { python -m venv .venv }
        if ($LASTEXITCODE -ne 0) { Stop-Run 2 }
    }
    $installs = @(
        @("-m", "pip", "install", "--upgrade", "pip"),
        @("-m", "pip", "install", "-e", ".[dev]"),
        @("-m", "pip", "install", "--requirement", "requirements-checkers.txt")
    )
    foreach ($install in $installs) {
        & $venvPython @install
        if ($LASTEXITCODE -ne 0) { Stop-Run 2 }
    }
}

# The prose files and lint scripts mirror the lint target in the Makefile.
$proseFiles = @("AGENTS.md", "README.md", "CHANGELOG.md", "docs/project-orientation.md",
    "docs/template-drift.md", "plan/HANDOFF.md.example", "SECURITY.md", "CONTRIBUTING.md")
$checks = [ordered]@{
    "ruff-check"       = @("-m", "ruff", "check", "src", "tests")
    "ruff-format"      = @("-m", "ruff", "format", "--check", "src", "tests")
    "pytest"           = @("-m", "pytest")
    "policy-tests"     = @("scripts/run_tests.py")
    "prose-lint"       = @("scripts/lint_style.py")
    "prose-advisory"   = @("scripts/check_hedging.py") + $proseFiles
    "conflict-markers" = @("scripts/check_conflict_markers.py")
    "sync-check"       = @("scripts/sync.py", "--check")
}
$results = [ordered]@{}
foreach ($name in $checks.Keys) {
    Write-Output "=== $name ==="
    $arguments = $checks[$name]
    & $venvPython @arguments
    $results[$name] = if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }
}

Write-Output "=== Summary ==="
$failed = $false
foreach ($name in $results.Keys) {
    Write-Output ("{0,-18} {1}" -f $name, $results[$name])
    if ($results[$name] -ne "PASS") { $failed = $true }
}
if ($failed) {
    Write-Output "At least one check failed. See $logFile, then send the failing section."
    Stop-Run 1
}
if (-not $NoLaunch) {
    Stop-Transcript | Out-Null
    & (Join-Path $repoRoot ".venv\Scripts\infiniteprep.exe")
    exit 0
}
Stop-Run 0
