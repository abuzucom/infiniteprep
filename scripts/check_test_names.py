#!/usr/bin/env python3
"""Fail on long test names in a commit range or in every test file.

The AGENTS.md Test names rule allows at most five words after `test_`.
This check covers test functions under `tests/` that the range adds or
renames. It reads module and class level functions, the same scope
unittest discovery reads. A function named `test...` without `test_`
fails. Each base name at the same path exempts one head test of that
name. A renamed test file gets no exemption. Exits 1 on any finding or
on any Git, decoding, or parse failure.

The `--all` mode reads every `tests/**/*.py` file in the working tree at
`--repo`. It applies the name rule with no base exemption. It also fails
on narration in comments: a personal pronoun anywhere in the comment, or
a sequencing word (now, first, next, then, finally) that opens it. Pattern
matching covers those forms only. String literals stay out of scope.
"""
import argparse
import ast
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

try:
    from scripts.prose_policy import PERSONAL_PRONOUNS
    from scripts.read_git_state import sanitize_text
    from scripts.trusted_git import run_git
except ModuleNotFoundError:
    from prose_policy import PERSONAL_PRONOUNS
    from read_git_state import sanitize_text
    from trusted_git import run_git

MAX_TEST_NAME_WORDS = 5
TEST_PREFIX = "test_"
# unittest discovery runs every method that starts with this prefix.
DISCOVERY_PREFIX = "test"
TESTS_DIRECTORY = "tests/"
PYTHON_SUFFIX = ".py"
MAX_BLOB_BYTES = 4 * 1024 * 1024
CHANGED_STATUSES = ("A", "M", "R")
TESTS_FOLDER = "tests"
PYTHON_GLOB = "*.py"
# The repository prose policy owns the pronoun list. Longer forms come
# first so that a contraction matches whole.
NARRATION_WORDS = sorted((*PERSONAL_PRONOUNS, "let's", "let me"), key=len, reverse=True)
NARRATION_PRONOUN = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in NARRATION_WORDS) + r")\b",
    re.IGNORECASE,
)
# One --all scan reads at most this many files and bytes. The budget
# bounds CI time and memory for a single run.
MAX_TREE_FILES = 2000
MAX_TREE_BYTES = 64 * 1024 * 1024
# A finding quotes at most this many comment characters to keep one line short.
MAX_COMMENT_EXCERPT_CHARS = 60
NARRATION_OPENER = re.compile(r"^#+\s*(?:now|first|next|then|finally)\b", re.IGNORECASE)


def count_name_words(name: str) -> int:
    """Return the number of `_`-separated words after `test_`."""
    return len([word for word in name[len(TEST_PREFIX):].split("_") if word])


def resolve_commit(repo: str, revision: str) -> str:
    """Return the full commit ID for one revision."""
    result = run_git(
        repo,
        ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        check=True, runner=subprocess.run,
    )
    return result.stdout.strip()


def is_test_path(path: str) -> bool:
    """Return True for a Python file below `tests/` without `..` segments."""
    if ".." in path.split("/"):
        return False
    return path.startswith(TESTS_DIRECTORY) and path.endswith(PYTHON_SUFFIX)


def parse_name_status(stdout: str) -> list[tuple[str, str]]:
    """Return (base path or empty, head path) pairs for changed test files."""
    fields = stdout.split("\0")
    pairs = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index][:1]
        if status == "R":
            old_path, new_path = fields[index + 1], fields[index + 2]
            index += 3
        else:
            old_path = new_path = fields[index + 1]
            index += 2
        if status not in CHANGED_STATUSES or not is_test_path(new_path):
            continue
        pairs.append(("" if status == "A" else old_path, new_path))
    return pairs


def list_changed_tests(repo: str, base: str, head: str) -> list[tuple[str, str]]:
    """Return changed test file pairs between two commits."""
    result = run_git(
        repo,
        ["diff", "--name-status", "-z", "-M", "--no-ext-diff", base, head, "--", "tests"],
        check=True, runner=subprocess.run,
    )
    return parse_name_status(result.stdout)


def read_blob_size(repo: str, commit: str, path: str) -> int:
    """Return one blob's size in bytes without reading its content."""
    result = run_git(repo, ["cat-file", "-s", f"{commit}:{path}"], check=True, runner=subprocess.run)
    return int(result.stdout.strip())


def read_blob(repo: str, commit: str, path: str) -> str:
    """Return one file's text at a commit.

    The size query runs first. A blob over the cap at that query stays
    unread. A post-read check covers a blob that changes between the two
    Git calls.
    """
    blob_size = read_blob_size(repo, commit, path)
    if blob_size > MAX_BLOB_BYTES:
        raise ValueError(f"file {path} holds {blob_size} bytes over the {MAX_BLOB_BYTES} byte cap")
    result = run_git(repo, ["cat-file", "blob", f"{commit}:{path}"], check=True, runner=subprocess.run)
    if len(result.stdout.encode("utf-8")) > MAX_BLOB_BYTES:
        raise ValueError(f"file {path} grew past the {MAX_BLOB_BYTES} byte cap during the read")
    return result.stdout


def parse_test_source(source: str, path: str) -> ast.Module:
    """Parse one test file. Report a syntax error as a ValueError."""
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as error:
        raise ValueError(f"syntax error in {path} at line {error.lineno} ({error.msg})") from error


def collect_test_names(source: str, path: str) -> dict[str, tuple[str, int]]:
    """Parse one test file and map its test names to bare name and line."""
    return collect_tree_names(parse_test_source(source, path))


def collect_tree_names(tree: ast.Module) -> dict[str, tuple[str, int]]:
    """Map each class-qualified test name to its bare name and line.

    Module and class bodies hold test functions. An explicit stack walks
    nested classes without recursion.
    """
    names = {}
    stack = [(tree.body, "")]
    while stack:
        body, prefix = stack.pop()
        for node in body:
            if isinstance(node, ast.ClassDef):
                stack.append((node.body, f"{prefix}{node.name}."))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(DISCOVERY_PREFIX):
                names[f"{prefix}{node.name}"] = (node.name, node.lineno)
    return names


def describe_name_violation(name: str) -> str:
    """Return a finding text for a test name, or an empty string."""
    if not name.startswith(TEST_PREFIX):
        return f"{name} lacks the {TEST_PREFIX} prefix"
    word_count = count_name_words(name)
    if word_count <= MAX_TEST_NAME_WORDS:
        return ""
    return f"{name} has {word_count} words after {TEST_PREFIX} (limit {MAX_TEST_NAME_WORDS})"


def find_file_violations(repo: str, base: str, head: str, pair: tuple[str, str]) -> list[str]:
    """Return findings for new nonconforming test names in one changed file.

    The base comparison counts bare names. Each base occurrence exempts one
    head test of that name, in line order. A test moved to another class
    keeps its exemption. A copy into a second class fails. A renamed file
    gets no exemption.
    """
    base_path, head_path = pair
    head_names = collect_test_names(read_blob(repo, head, head_path), head_path)
    base_name_counts = Counter()
    if base_path == head_path:
        base_entries = collect_test_names(read_blob(repo, base, base_path), base_path)
        base_name_counts = Counter(name for name, _line in base_entries.values())
    findings = []
    for name, line in sorted(head_names.values(), key=lambda entry: entry[1]):
        if base_name_counts[name] > 0:
            base_name_counts[name] -= 1
            continue
        violation = describe_name_violation(name)
        if violation:
            findings.append(f"{head_path}:{line}: {violation}")
    return findings


def collect_string_spans(tree: ast.AST) -> dict[int, list[tuple[int, float]]]:
    """Map each line number to the byte ranges that string literals cover.

    The parser reports UTF-8 byte columns. A `#` inside one of these
    ranges belongs to a string, not to a comment.
    """
    spans = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, (str, bytes)):
            continue
        for line_number in range(node.lineno, node.end_lineno + 1):
            start = node.col_offset if line_number == node.lineno else 0
            end = node.end_col_offset if line_number == node.end_lineno else float("inf")
            spans.setdefault(line_number, []).append((start, end))
    return spans


def find_line_comment(line: bytes, ranges: list[tuple[int, float]]) -> str:
    """Return the comment text on one line, or an empty string."""
    position = line.find(b"#")
    while position != -1:
        if not any(start <= position < end for start, end in ranges):
            return line[position:].decode("utf-8")
        position = line.find(b"#", position + 1)
    return ""


def find_comment_violations(source: str, tree: ast.Module, path: str) -> list[str]:
    """Return findings for narration in the comments of one parsed file."""
    spans = collect_string_spans(tree)
    findings = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        comment = find_line_comment(line.encode("utf-8"), spans.get(line_number, []))
        if not comment or comment.startswith("#!"):
            continue
        if NARRATION_OPENER.match(comment) or NARRATION_PRONOUN.search(comment):
            findings.append(f"{path}:{line_number}: comment narration ({comment.strip()[:MAX_COMMENT_EXCERPT_CHARS]})")
    return findings


def read_tree_file(file_path: Path, display_path: str) -> str:
    """Return one working tree file's text. Reject files over the size cap."""
    file_size = file_path.stat().st_size
    if file_size > MAX_BLOB_BYTES:
        raise ValueError(f"file {display_path} holds {file_size} bytes over the {MAX_BLOB_BYTES} byte cap")
    return file_path.read_text(encoding="utf-8")


def resolve_tests_root(repo: str) -> Path:
    """Return the resolved tests folder of a repository tree.

    A missing repository, a missing tests folder, a symbolic link as the
    tests folder, or a tests folder outside the repository fails.
    """
    repo_root = Path(repo)
    if not repo_root.is_dir():
        raise ValueError(f"repository {repo} does not exist")
    tests_link = repo_root / TESTS_FOLDER
    if tests_link.is_symlink():
        raise ValueError(f"repository {repo} has a tests folder that is a symbolic link")
    tests_root = tests_link.resolve()
    if not tests_root.is_dir():
        raise ValueError(f"repository {repo} holds no tests folder")
    if not tests_root.is_relative_to(repo_root.resolve()):
        raise ValueError(f"repository {repo} has a tests folder outside the repository")
    return tests_root


def list_tree_files(tests_root: Path) -> list[Path]:
    """Return the regular test files below a tests folder within budget.

    Symbolic links and files that resolve outside the tests folder stay
    unlisted. The file count and the summed size must stay within
    MAX_TREE_FILES and MAX_TREE_BYTES before any file is read.
    """
    files = []
    total_bytes = 0
    for file_path in sorted(tests_root.rglob(PYTHON_GLOB)):
        if file_path.is_symlink() or not file_path.is_file():
            continue
        if not file_path.resolve().is_relative_to(tests_root):
            continue
        files.append(file_path)
        if len(files) > MAX_TREE_FILES:
            raise ValueError(f"tests folder holds more than the {MAX_TREE_FILES} file limit")
        total_bytes += file_path.stat().st_size
        if total_bytes > MAX_TREE_BYTES:
            raise ValueError(f"tests folder holds more than the {MAX_TREE_BYTES} byte limit")
    return files


def find_tree_violations(repo: str) -> list[str]:
    """Return name and comment findings for every test file in a tree."""
    tests_root = resolve_tests_root(repo)
    findings = []
    for file_path in list_tree_files(tests_root):
        display_path = f"{TESTS_FOLDER}/{file_path.relative_to(tests_root).as_posix()}"
        source = read_tree_file(file_path, display_path)
        tree = parse_test_source(source, display_path)
        names = collect_tree_names(tree)
        for name, line in sorted(names.values(), key=lambda entry: entry[1]):
            violation = describe_name_violation(name)
            if violation:
                findings.append(f"{display_path}:{line}: {violation}")
        findings.extend(find_comment_violations(source, tree, display_path))
    return findings


def check_tree(repo: str) -> int:
    """Print findings for every test file. Return 1 on any finding, else 0."""
    findings = find_tree_violations(repo)
    for finding in findings:
        print(sanitize_text(finding))
    if not findings:
        return 0
    print(
        f"Rename each listed test to {TEST_PREFIX} plus at most {MAX_TEST_NAME_WORDS} words."
        " Rewrite each listed comment to state what the code does.",
        file=sys.stderr,
    )
    return 1


def check(base: str, head: str, repo: str) -> int:
    """Print findings for base..head. Return 1 on any finding, else 0."""
    base_commit = resolve_commit(repo, base)
    head_commit = resolve_commit(repo, head)
    findings = []
    for pair in list_changed_tests(repo, base_commit, head_commit):
        findings.extend(find_file_violations(repo, base_commit, head_commit, pair))
    for finding in findings:
        print(sanitize_text(finding))
    if not findings:
        return 0
    print(f"Rename each listed test to {TEST_PREFIX} plus at most {MAX_TEST_NAME_WORDS} words.", file=sys.stderr)
    return 1


def main() -> int:
    """Parse arguments and run the range or tree check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="base revision (exclusive)")
    parser.add_argument("--head", help="head revision (inclusive)")
    parser.add_argument("--all", action="store_true", help="check every test file in the working tree")
    parser.add_argument("--repo", default=os.getcwd(), help="repository to inspect (default: cwd)")
    args = parser.parse_args()
    if args.all and (args.base or args.head):
        parser.error("--all takes no --base or --head")
    if not args.all and not (args.base and args.head):
        parser.error("pass --base and --head, or pass --all")
    try:
        if args.all:
            return check_tree(args.repo)
        return check(args.base, args.head, args.repo)
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError, IndexError) as error:
        print(sanitize_text(f"Test name check failed ({error}). Fix the cause and rerun."), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
