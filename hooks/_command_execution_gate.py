"""Classify shell programs and repository workflows for the branch hook.

The branch hook imports this module for every shell command it inspects.
Denials and consent prompts from this module name this file, so a blocked
program points at the command allowlist and not at branch naming.
"""
import functools
import json
import os
import re
import shutil
import stat

# The importing hook puts hooks/ on sys.path and fails closed when these are absent.
import _gate_core as core
import _bash_parser as bash_parser
import _test_runners
import _trust
import _zones

GATE = "_command_execution_gate.py"
INSPECTABLE_PROGRAMS = frozenset({
    "echo", "printf", "pwd", "cat", "head", "tail", "wc", "ls", "dir",
    "get-content", "get-childitem", "get-location", "write-output",
    "touch", "tee", "cp", "mv", "set-content", "add-content", "out-file",
    "copy-item", "move-item", "copy", "move", "type", "true", "false",
})
# The script list sits beside this module, so every adopted copy carries it.
WORKFLOW_SCRIPT_LIST = "workflow-scripts.txt"
WORKFLOW_SCRIPT_LIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), WORKFLOW_SCRIPT_LIST)
# Each adopter may list its own scripts beside the shared list. The file stays out of the
# shared set, and an entry may not repeat a shared entry.
LOCAL_WORKFLOW_SCRIPT_LIST = "workflow-scripts.local.txt"
LOCAL_WORKFLOW_SCRIPT_LIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               LOCAL_WORKFLOW_SCRIPT_LIST)
# The shipped list holds under 3 KiB. The cap bounds the read of a repository-controlled file
# and leaves room for adopter entries.
MAX_WORKFLOW_SCRIPT_LIST_BYTES = 64 * 1024
ALLOW = "allow"
ASK = "ask"
DENY = "deny"
ALLOW_VERDICT = "allow"
CONSENT_READ_VERDICT = "consent-read"
CONSENT_WRITE_VERDICT = "consent-write"
# A trusted-read script allows for a trusted origin owner and asks elsewhere.
TRUSTED_READ_VERDICT = "trusted-read"
SCRIPT_VERDICTS = frozenset({ALLOW_VERDICT, CONSENT_READ_VERDICT, CONSENT_WRITE_VERDICT,
                             TRUSTED_READ_VERDICT})
INSPECTING_SCRIPT_VERDICTS = frozenset({ALLOW_VERDICT, CONSENT_READ_VERDICT, TRUSTED_READ_VERDICT})
REVISION_PLACEHOLDER = "<revision>"
PATH_PLACEHOLDER = "<path>"
PATHS_PLACEHOLDER = "<paths>"
SCRIPT_PLACEHOLDERS = frozenset({REVISION_PLACEHOLDER, PATH_PLACEHOLDER, PATHS_PLACEHOLDER})
LISTED_SCRIPT_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}\.py")
# A leading letter or digit keeps a revision from reading as an option.
REVISION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/~-]{0,199}")
# `*` lets the shell expand a glob. Expansion of a relative pattern stays below the working directory.
WORKFLOW_PATH_PATTERN = re.compile(r"[A-Za-z0-9._@+/*-]{1,512}")
PARENT_DIRECTORY = ".."
SEARCH_FLAGS = frozenset({
    "-n", "--line-number", "-l", "--files-with-matches", "-i", "--ignore-case",
    "-F", "--fixed-strings", "--files", "--hidden", "-g", "--glob", "-e", "--regexp", "--",
})
# The cap bounds token matching for one command part. Listed script shapes hold at most six
# tokens. The room above that serves path lists.
MAX_WORKFLOW_ARGUMENTS = 64
MAKE_TARGETS = ("lint", "test", "check", "sync", "identity")
MAKE_TEST_TARGET = "test"
# pytest reads extra arguments from a file named after a leading @.
ARGUMENT_FILE_PREFIX = "@"
MAKE_ARGUMENTS = ([], ["PYTHON=python"], ["PYTHON=python3"])
PYTHON_PROGRAMS = ("python", "python3", "python.exe", "python3.exe", "py", "py.exe")
VERSIONED_PYTHON_PATTERN = re.compile(r"python3\.[0-9]{1,2}(?:\.exe)?")
# Interpreter options that take no value. A value-taking option such as -c,
# -m, -W, or -X changes which token names the script, so none of those pass.
VALUELESS_INTERPRETER_OPTIONS = frozenset({"-B", "-E", "-I", "-P", "-s", "-S", "-u"})
TRUSTED_GH_SCRIPT = "scripts/trusted_gh.py"
TRUSTED_GIT_SCRIPT = "scripts/trusted_git.py"
# trusted_git.py validates the full argument shape itself and exits 2 otherwise.
TRUSTED_GIT_MINIMUM_ARGUMENTS = {"clone": 3, "fetch": 2}
# trusted_gh.py comment operations. The wrapper validates the target and body
# again before it calls GitHub CLI.
COMMENT_KINDS = frozenset({"issue", "pr"})
THREAD_NUMBER = re.compile(r"[1-9][0-9]{0,9}")
COMMENT_DRAFT_PREFIX = ".comment-drafts/"
COMMENT_BODY_OPTION = "--body-file"
COMMENTS_ARGUMENT_COUNT = 3
COMMENT_ARGUMENT_COUNT = 5
# A comment read fetches one bounded page, so a long thread cannot exhaust memory.
COMMENT_PAGE_OPTION = "--page"
COMMENT_PAGE_NUMBER = re.compile(r"[1-9][0-9]{0,3}")
MAX_COMMENT_PAGE = 1000
COMMENT_PAGE_ARGUMENT_COUNT = 2
# Read-only wrapper `run api` requests. gh api sends GET unless a method,
# field, or input option changes it. A read therefore accepts only GET
# method values, output options, and exactly one REST path.
API_READ_METHOD = "GET"
API_METHOD_OPTIONS = frozenset(("-X", "--method"))
API_READ_VALUE_OPTIONS = frozenset(("--jq", "-q"))
API_READ_FLAGS = frozenset(("--paginate", "--slurp", "-i", "--include", "--silent"))
API_GRAPHQL_PATH = "graphql"
# A read names plain REST path segments and an optional plain query. A
# segment cannot be empty, "." or "..", and no character can percent-encode,
# so no spelling of the path can reach the GraphQL endpoint.
API_PATH_SEGMENT = re.compile(r"\A[A-Za-z0-9_-][A-Za-z0-9._-]*\Z")
API_QUERY = re.compile(r"\A[A-Za-z0-9._~=&,+-]*\Z")
SHELL_OPERATORS = (";", "&", "&&", "|", "||", "(", ")")
OPERATOR_CHARACTERS = frozenset(bash_parser.PUNCTUATION_CHARS)
CHAIN_OPERATOR = "&&"
# Eight parts cover a lint, format, and test chain with room to spare. The cap bounds the
# verdict work for one command.
MAX_CHAIN_PARTS = 8
# shlex ends a word at `#` and Bash does not. A command holding `#` could run text the tokens omit.
COMMENT_CHARACTER = "#"
WORKFLOW_FORBIDDEN_CHARACTERS = "<>%!^\0"
RUFF_PROGRAM = "ruff"
RUFF_MODULE_LAUNCHER = "python -m ruff"
RUFF_FLAGS = {
    "check": frozenset({"--fix", "--no-fix", "--diff", "--statistics", "--show-fixes", "--no-cache",
                        "--quiet", "-q"}),
    "format": frozenset({"--check", "--diff", "--no-cache", "--quiet", "-q"}),
}
# `python -m` imports from the working directory first, so either name replaces Ruff.
RUFF_SHADOW_NAMES = ("ruff.py", "ruff")
PYTEST_PROGRAM = "pytest"
PYTEST_MODULE_LAUNCHER = "python -m pytest"
# Output and stop flags only. Options that load plugins, change the root,
# read another config, or start a debugger stay outside the accepted form.
PYTEST_FLAGS = frozenset({"-q", "-x", "-v", "--tb=short"})
PYTEST_KEYWORD_OPTION = "-k"
PYTEST_KEYWORD_OPERATORS = frozenset({"and", "or", "not"})
PYTEST_KEYWORD_NAME = re.compile(r"[A-Za-z0-9_]{1,128}")
# `python -m` imports from the working directory first, so either name replaces pytest.
PYTEST_SHADOW_NAMES = ("pytest.py", "pytest")
PYTEST_CONSENT = (
    "Repository workflow requires execution consent. "
    "pytest imports repository code, including conftest.py."
)
UNITTEST_VERBOSITY_OPTIONS = ("-v", "-q")
UNITTEST_DIRECTORY_OPTIONS = frozenset({"-s", "--start-directory", "-t", "--top-level-directory"})
UNITTEST_PATTERN_OPTIONS = frozenset({"-p", "--pattern"})
UNITTEST_PATTERN = re.compile(r"[A-Za-z0-9._*][A-Za-z0-9._*-]{0,127}")
OPAQUE_REASON = "Opaque command execution requires an inspectable operation"
CHAIN_HINT = "Run one accepted form per command, or join accepted forms with &&."
RUFF_HINT = ("Accepted Ruff forms: ruff check [--fix|--no-fix|--diff|--statistics|--show-fixes|--no-cache|-q] "
             "[paths], ruff format [--check|--diff|--no-cache|-q] [paths], and the same after python -m ruff. "
             "Paths stay repository-relative. " + CHAIN_HINT)
UNITTEST_HINT = ("Accepted unittest forms: python -m unittest [-v|-q] tests.<module>, and "
                 "python -m unittest discover [-v|-q] [-s <directory>] [-t <directory>] [-p <pattern>]. "
                 + CHAIN_HINT)
PYTEST_HINT = ("Accepted pytest forms: pytest [-q|-x|-v|--tb=short] [-k <expression>] <paths>, "
               "and the same after python -m pytest. A -k expression holds names joined by and, or, "
               "and not. Paths stay repository-relative. " + CHAIN_HINT)
SCRIPT_HINT = (f"hooks/{WORKFLOW_SCRIPT_LIST} lists the accepted repository script forms. " + CHAIN_HINT)
# Prefix wrappers that only adjust the environment or skip shell lookup.
TRANSPARENT_WRAPPERS = ("env", "command", "exec")
WORKFLOW_CONSENT = "Repository workflow requires execution consent"
NPM_MANIFEST = "package.json"
NPM_CONFIG = ".npmrc"
NPM_RUN_SCRIPTS = frozenset({"lint", "typecheck", "build"})
NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json")
# Root package scripts that npm ci runs around the dependency install.
NPM_CI_LIFECYCLE = ("preinstall", "install", "postinstall", "prepublish",
                    "preprepare", "prepare", "postprepare")
MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
# Real manifests nest under 10 levels. The bound keeps json.loads recursion shallow.
MAX_PACKAGE_MANIFEST_DEPTH = 32
MAX_TEST_PATH_COMPONENTS = 32
# `-m <module>` takes two arguments after the interpreter token.
MODULE_LAUNCH_ARGUMENTS = 2
MODULE_LAUNCH_END = 1 + MODULE_LAUNCH_ARGUMENTS
# A script list line holds a verdict and a script before its argument pattern.
SCRIPT_LINE_REQUIRED_FIELDS = 2
# Arguments start after the program token and its subcommand or target.
SUBCOMMAND_ARGUMENTS_INDEX = 2
NPM_TEST_TOKEN_COUNT = 2
NPM_TEST_FILES_INDEX = 3
NPM_RUN_TOKEN_COUNT = 3
# npm joins test arguments into a shell string, so only inert characters pass.
TEST_PATH_PATTERN = re.compile(r"[A-Za-z0-9._@+/-]+")
# One alternation per match keeps the scan linear. A lone quote marks an
# unterminated string. That match stops the scan before any retry at later quotes.
JSON_TOKEN_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]|"')
NPM_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
UNTRUSTED_SCRIPTS_LABEL = "npm runs these package.json scripts (untrusted repository data)"
UNSHOWN_CODE_NOTE = "Dependency lifecycle scripts and node_modules binaries also run and are not shown."
# pnpm reads .npmrc. A yarn rc file can set yarnPath, which runs a repository file as yarn.
JS_RUNNER_LOCKFILES = {"pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}
JS_RUNNER_CONFIGS = {"pnpm": (".npmrc",), "yarn": (".yarnrc", ".yarnrc.yml")}
JS_TEST_SCRIPT = "test"
JS_TEST_LIFECYCLE = ("pretest", "test", "posttest")
DELETE_ZONE_SCRIPT = "scripts/delete_zone_path.py"
ZONE_SESSION_OPTION = "--session"
# The session option, its value, and at least one operand.
ZONE_HELPER_MINIMUM_ARGUMENTS = 3
ZONE_DELETE_REASON = ("Repository workflow requires execution consent. The zone delete helper "
                      "deletes inside this session's own zone folder.")


def segment_program_reason(segment: list) -> str:
    """Return why one command segment runs an uninspectable program, or ""."""
    executable, _assignments, complete = bash_parser.strip_prefixes(segment)
    if not complete:
        return "Command wrapper could not be inspected"
    if not executable:
        return ""
    program = core.normalize_windows_command_name(executable[0])
    if executable[0].casefold() not in (program, program + ".exe"):
        return "A script or executable path cannot claim an inspectable program name"
    # Prefix options can change cwd or remove inherited configuration.
    prefix_count = len(segment) - len(executable)
    if any(token in bash_parser.WRAPPERS and token not in TRANSPARENT_WRAPPERS
           for token in segment[:prefix_count]):
        return "Command wrapper has opaque input or execution context"
    if any(token.startswith("-") for token in segment[:prefix_count]):
        return "Command wrapper changes unresolved execution settings"
    if program != "git" and program not in INSPECTABLE_PROGRAMS:
        reason = f"{OPAQUE_REASON} (rejected: {core.sanitize(program)})"
        hint = _accepted_forms_hint(program, executable[1:])
        return f"{reason}. {hint}" if hint else reason
    return ""


def _accepted_forms_hint(program: str, arguments: list) -> str:
    """Return the accepted forms for a known tool, or "" for another program."""
    if program == RUFF_PROGRAM:
        return RUFF_HINT
    if program == PYTEST_PROGRAM:
        return PYTEST_HINT
    if not _python_program(program):
        return ""
    if arguments[:MODULE_LAUNCH_ARGUMENTS] == ["-m", RUFF_PROGRAM]:
        return RUFF_HINT
    if arguments[:MODULE_LAUNCH_ARGUMENTS] == ["-m", PYTEST_PROGRAM]:
        return PYTEST_HINT
    if arguments[:MODULE_LAUNCH_ARGUMENTS] == ["-m", "unittest"]:
        return UNITTEST_HINT
    if any(argument.endswith(".py") for argument in arguments):
        return SCRIPT_HINT
    return ""


def arguments_reason(executable: list) -> str:
    """Return why non-Git arguments depend on shell expansion, or ""."""
    if any(core.is_ambiguous(token) for token in executable):
        return "Command arguments contain unresolved expansion"
    return ""


def _python_program(name: str) -> bool:
    """Return whether a program name selects a Python interpreter."""
    return name in PYTHON_PROGRAMS or bool(VERSIONED_PYTHON_PATTERN.fullmatch(name))


def _python_script_call(tokens: list, project_dir: str) -> tuple:
    """Return (repository-relative script, arguments), or ("", ()) for no script."""
    index = 1
    while index < len(tokens) and tokens[index] in VALUELESS_INTERPRETER_OPTIONS:
        index += 1
    if index >= len(tokens):
        return "", ()
    path = core.resolved_under(project_dir, tokens[index])
    if path is None or not os.path.isfile(path):
        return "", ()
    relative = os.path.relpath(path, os.path.realpath(project_dir)).replace(os.sep, "/")
    return relative, tuple(tokens[index + 1:])


def _trusted_git_workflow(arguments: tuple) -> bool:
    """Recognize the fixed trusted Git clone and fetch operations."""
    minimum = TRUSTED_GIT_MINIMUM_ARGUMENTS.get(arguments[0] if arguments else "")
    return minimum is not None and len(arguments) >= minimum


def _parse_script_line(fields: list, line_number: int) -> tuple:
    """Return (script, argument pattern, verdict) for one list line, or raise ValueError."""
    if len(fields) < SCRIPT_LINE_REQUIRED_FIELDS or fields[0] not in SCRIPT_VERDICTS:
        raise ValueError(f"line {line_number} needs a verdict and a script")
    script = fields[1]
    pattern = tuple(fields[SCRIPT_LINE_REQUIRED_FIELDS:])
    if not LISTED_SCRIPT_PATTERN.fullmatch(script) or PARENT_DIRECTORY in script.split("/"):
        raise ValueError(f"line {line_number} names no repository-relative Python script")
    for position, field in enumerate(pattern, 1):
        if field.startswith("<") and field not in SCRIPT_PLACEHOLDERS:
            raise ValueError(f"line {line_number} uses an unknown placeholder")
        if field == PATHS_PLACEHOLDER and position != len(pattern):
            raise ValueError(f"line {line_number} puts {PATHS_PLACEHOLDER} before the last position")
    return script, pattern, fields[0]


def _parse_workflow_scripts(text: str) -> tuple:
    """Return every (script, argument pattern, verdict) entry, or raise ValueError."""
    entries = []
    for line_number, line in enumerate(text.splitlines(), 1):
        fields = line.split()
        if fields and not fields[0].startswith("#"):
            entries.append(_parse_script_line(fields, line_number))
    return tuple(entries)


def _read_script_list(path: str) -> tuple:
    """Return the parsed entries of one script list file, or raise ValueError or OSError."""
    with open(path, "rb") as handle:
        content = handle.read(MAX_WORKFLOW_SCRIPT_LIST_BYTES + 1)
    if len(content) > MAX_WORKFLOW_SCRIPT_LIST_BYTES:
        raise ValueError("the file exceeds 64 KiB")
    try:
        return _parse_workflow_scripts(content.decode("ascii"))
    except UnicodeDecodeError as error:
        raise ValueError("the file holds non-ASCII bytes") from error


def _load_shared_scripts() -> tuple:
    """Return the shared list entries, or raise ValueError naming the repair."""
    try:
        return _read_script_list(WORKFLOW_SCRIPT_LIST_PATH)
    except OSError as error:
        detail = _os_error_text(error)
    except ValueError as error:
        detail = str(error)
    raise ValueError(f"hooks/{WORKFLOW_SCRIPT_LIST} cannot be used ({detail}). "
                     "Restore the file from the policy source.")


def _load_local_scripts(shared: tuple) -> tuple:
    """Return adopter entries from the local list, or () when the file is absent."""
    try:
        entries = _read_script_list(LOCAL_WORKFLOW_SCRIPT_LIST_PATH)
    except FileNotFoundError:
        return ()
    except OSError as error:
        detail = _os_error_text(error)
    except ValueError as error:
        detail = str(error)
    else:
        shared_shapes = {(script, pattern) for script, pattern, _verdict in shared}
        duplicate = next((script for script, pattern, _verdict in entries
                          if (script, pattern) in shared_shapes), "")
        if not duplicate:
            return entries
        detail = f"it repeats the shared entry for {duplicate}"
    raise ValueError(f"hooks/{LOCAL_WORKFLOW_SCRIPT_LIST} cannot be used ({detail}). "
                     "Fix or remove the adopter entries.")


@functools.lru_cache(maxsize=1)
def _load_workflow_scripts() -> tuple:
    """Return the shared and adopter script entries, or raise ValueError naming the repair."""
    shared = _load_shared_scripts()
    return shared + _load_local_scripts(shared)


def _literal_script_arguments(verdicts: frozenset) -> dict:
    """Return {script: argument tuples} for listed shapes without placeholders."""
    try:
        entries = _load_workflow_scripts()
    except ValueError:
        # An unusable list leaves the tables empty. Every script call then denies with the cause.
        return {}
    table = {}
    for script, pattern, verdict in entries:
        if verdict in verdicts and not SCRIPT_PLACEHOLDERS.intersection(pattern):
            table.setdefault(script, []).append(pattern)
    return {script: tuple(shapes) for script, shapes in table.items()}


def _is_workflow_path(argument: str) -> bool:
    """Return whether an argument is an inert repository-relative path."""
    return (bool(WORKFLOW_PATH_PATTERN.fullmatch(argument))
            and not argument.startswith(("-", "/"))
            and PARENT_DIRECTORY not in argument.split("/"))


def _is_revision(argument: str) -> bool:
    """Return whether an argument names one Git revision and no range."""
    return bool(REVISION_PATTERN.fullmatch(argument)) and PARENT_DIRECTORY not in argument


def _argument_matches(field: str, argument: str) -> bool:
    """Match one argument against a literal or a single-value placeholder."""
    if field == REVISION_PLACEHOLDER:
        return _is_revision(argument)
    if field == PATH_PLACEHOLDER:
        return _is_workflow_path(argument)
    return field == argument


def _pattern_matches(pattern: tuple, arguments: tuple) -> bool:
    """Match script arguments against one listed shape."""
    open_ended = pattern[-1:] == (PATHS_PLACEHOLDER,)
    fixed = pattern[:-1] if open_ended else pattern
    if len(arguments) < len(fixed) or (not open_ended and len(arguments) != len(fixed)):
        return False
    return (all(_argument_matches(field, argument) for field, argument in zip(fixed, arguments))
            and all(_is_workflow_path(argument) for argument in arguments[len(fixed):]))


def _script_verdict(script: str, arguments: tuple) -> tuple:
    """Return (list verdict, "") for a listed shape, ("", "") otherwise, or ("", load error)."""
    try:
        entries = _load_workflow_scripts()
    except ValueError as error:
        return "", str(error)
    for listed, pattern, verdict in entries:
        if listed == script and _pattern_matches(pattern, arguments):
            return verdict, ""
    return "", ""


def _unittest_modules_accepted(arguments: list) -> bool:
    """Accept dotted module names below the tests package."""
    modules = [token for token in arguments if token not in UNITTEST_VERBOSITY_OPTIONS]
    return bool(modules) and all(
        token.startswith("tests.") and all(part.isidentifier() for part in token.split("."))
        for token in modules)


def _unittest_discover_accepted(arguments: list, project_dir: str) -> bool:
    """Accept discovery options that name directories inside the repository."""
    index = 0
    while index < len(arguments):
        option = arguments[index]
        index += 1
        if option in UNITTEST_VERBOSITY_OPTIONS:
            continue
        if index >= len(arguments):
            return False
        value = arguments[index]
        index += 1
        if option in UNITTEST_DIRECTORY_OPTIONS:
            accepted = (_is_workflow_path(value) and "*" not in value
                        and core.resolved_under(project_dir, value) is not None)
        else:
            accepted = option in UNITTEST_PATTERN_OPTIONS and bool(UNITTEST_PATTERN.fullmatch(value))
        if not accepted:
            return False
    return True


def _unittest_accepted(arguments: list, project_dir: str) -> bool:
    """Recognize unittest module runs and bounded discovery."""
    if arguments[:1] == ["discover"]:
        return _unittest_discover_accepted(arguments[1:], project_dir)
    return _unittest_modules_accepted(arguments)


def _program_lookup_error(program: str, shadow_names: tuple, launcher: str,
                          project_dir: str) -> str:
    """Deny a tool launch that repository content can replace."""
    if launcher == program:
        relative = _repository_program_path(program, project_dir)
        if not relative:
            return ""
        return (f"PATH resolves {program} to `{relative}` inside the repository. "
                "Remove that directory from PATH.")
    shadow = next((name for name in shadow_names
                   if os.path.lexists(os.path.join(project_dir, name))), "")
    if not shadow:
        return ""
    return (f"`{shadow}` at the repository root shadows the {program} module under python -m. "
            f"Run the {program} executable instead.")


def _pytest_keyword_accepted(expression: str) -> bool:
    """Return whether a -k expression holds only names joined by and, or, and not."""
    words = expression.split()
    if not words or all(word in PYTEST_KEYWORD_OPERATORS for word in words):
        return False
    return all(word in PYTEST_KEYWORD_OPERATORS or PYTEST_KEYWORD_NAME.fullmatch(word)
               for word in words)


def _trusted_project(project_dir: str) -> bool:
    """Return whether hooks/trusted-owners.txt names the origin owner of the project."""
    return _trust.owner_trusted(core._origin_owner(project_dir))


def _pytest_arguments_accepted(arguments: list) -> bool:
    """Return whether pytest arguments use only fixed flags, -k, and inert paths."""
    # An argument file could carry any option past the flag list.
    if any(argument.startswith(ARGUMENT_FILE_PREFIX) for argument in arguments):
        return False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == PYTEST_KEYWORD_OPTION:
            index += 1
            if index >= len(arguments) or not _pytest_keyword_accepted(arguments[index]):
                return False
        elif argument not in PYTEST_FLAGS and not _is_workflow_path(argument):
            return False
        index += 1
    return True


def _pytest_verdict(arguments: list, project_dir: str, launcher: str) -> tuple:
    """Return (decision, reason, read-only) for one accepted pytest command."""
    if not _pytest_arguments_accepted(arguments):
        return "", "", False
    error = _program_lookup_error(PYTEST_PROGRAM, PYTEST_SHADOW_NAMES, launcher, project_dir)
    if error:
        return DENY, error, False
    if _trusted_project(project_dir):
        return ALLOW, "", True
    return ASK, PYTEST_CONSENT, True


def _ruff_writes(subcommand: str, flags: list) -> bool:
    """Return whether one accepted Ruff command rewrites files."""
    if "--diff" in flags:
        return False
    if subcommand == "check":
        return "--fix" in flags
    return "--check" not in flags


def _ruff_verdict(arguments: list, project_dir: str, launcher: str) -> tuple:
    """Return (decision, reason, read-only) for ruff check and ruff format."""
    if not arguments or arguments[0] not in RUFF_FLAGS:
        return "", "", False
    subcommand = arguments[0]
    flags = [argument for argument in arguments[1:] if argument.startswith("-")]
    paths = [argument for argument in arguments[1:] if not argument.startswith("-")]
    if (any(flag not in RUFF_FLAGS[subcommand] for flag in flags)
            or not all(_is_workflow_path(path) for path in paths)):
        return "", "", False
    error = _program_lookup_error(RUFF_PROGRAM, RUFF_SHADOW_NAMES, launcher, project_dir)
    if error:
        return DENY, error, False
    if _ruff_writes(subcommand, flags):
        return ASK, f"{WORKFLOW_CONSENT}. ruff {subcommand} rewrites files in the working tree.", False
    return ALLOW, "", True


def _trusted_wrapper_accepted(script: str, arguments: tuple, project_dir: str) -> bool:
    """Recognize the trusted GitHub and Git wrapper operations."""
    if script == TRUSTED_GIT_SCRIPT:
        return _trusted_git_workflow(arguments)
    if arguments[:1] != ("run",) or len(arguments) <= 1:
        return False
    decision, _reason = core.forge_verdict("gh", list(arguments[1:]), project_dir)
    return decision != DENY


def _comment_page_accepted(extra: tuple) -> bool:
    """Return whether comment read arguments hold no page or one bounded page."""
    if not extra:
        return True
    return (len(extra) == COMMENT_PAGE_ARGUMENT_COUNT and extra[0] == COMMENT_PAGE_OPTION
            and bool(COMMENT_PAGE_NUMBER.fullmatch(extra[1]))
            and int(extra[1]) <= MAX_COMMENT_PAGE)


def _trusted_comment_verdict(arguments: tuple) -> tuple:
    """Return (decision, reason, read-only) for trusted_gh.py comment operations.

    A comment read is a GET on this repository, so it passes on every branch
    state. A comment post changes hosted state, so it asks.
    """
    operation = arguments[0]
    if (len(arguments) < COMMENTS_ARGUMENT_COUNT or arguments[1] not in COMMENT_KINDS
            or not THREAD_NUMBER.fullmatch(arguments[2])):
        return "", "", False
    if operation == "comments":
        return (ALLOW, "", True) if _comment_page_accepted(
            arguments[COMMENTS_ARGUMENT_COUNT:]) else ("", "", False)
    body = arguments[COMMENTS_ARGUMENT_COUNT + 1] if len(arguments) == COMMENT_ARGUMENT_COUNT else ""
    if (arguments[COMMENTS_ARGUMENT_COUNT:COMMENTS_ARGUMENT_COUNT + 1] != (COMMENT_BODY_OPTION,)
            or not body.startswith(COMMENT_DRAFT_PREFIX) or not _is_workflow_path(body)):
        return "", "", False
    return ASK, "Posting a GitHub comment requires active-human consent.", False


def _api_read_path(arguments: tuple) -> str:
    """Return the single REST path of a read-only `api` request, or ""."""
    paths = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in API_METHOD_OPTIONS or token in API_READ_VALUE_OPTIONS:
            if index + 1 >= len(arguments):
                return ""
            if token in API_METHOD_OPTIONS and arguments[index + 1] != API_READ_METHOD:
                return ""
            index += 2
            continue
        if token.startswith("-") and token not in API_READ_FLAGS:
            return ""
        if token not in API_READ_FLAGS:
            paths.append(token)
        index += 1
    return paths[0] if len(paths) == 1 else ""


def _api_read_path_safe(path: str) -> bool:
    """Return whether a REST path holds plain segments, a plain query, and no GraphQL target."""
    route, _separator, query = path.partition("?")
    segments = route.removeprefix("/").split("/")
    if not all(API_PATH_SEGMENT.fullmatch(segment) for segment in segments):
        return False
    if segments[0].casefold() == API_GRAPHQL_PATH:
        return False
    return bool(API_QUERY.fullmatch(query))


def _trusted_api_read_accepted(arguments: tuple, project_dir: str) -> bool:
    """Return whether a wrapper call is one read-only `run api` GET request.

    Body, header, and hostname options fall outside the read allowlist, so
    any such option leaves the call at its existing verdict.
    """
    if arguments[:2] != ("run", "api"):
        return False
    decision, _reason = core.forge_verdict("gh", list(arguments[1:]), project_dir)
    if decision == DENY:
        return False
    path = _api_read_path(arguments[2:])
    return bool(path) and _api_read_path_safe(path)


def _python_verdict(tokens: list, project_dir: str, session_id: str = "") -> tuple:
    """Return (decision, reason, read-only) for one Python interpreter command."""
    if tokens[1:MODULE_LAUNCH_END] == ["-m", "unittest"]:
        accepted = _unittest_accepted(tokens[MODULE_LAUNCH_END:], project_dir)
        return (ALLOW if accepted else ""), "", accepted
    if tokens[1:MODULE_LAUNCH_END] == ["-m", RUFF_PROGRAM]:
        return _ruff_verdict(tokens[MODULE_LAUNCH_END:], project_dir, RUFF_MODULE_LAUNCHER)
    if tokens[1:MODULE_LAUNCH_END] == ["-m", PYTEST_PROGRAM]:
        return _pytest_verdict(tokens[MODULE_LAUNCH_END:], project_dir, PYTEST_MODULE_LAUNCHER)
    script, arguments = _python_script_call(tokens, project_dir)
    if not script:
        return "", "", False
    if script == DELETE_ZONE_SCRIPT:
        return _zone_helper_verdict(arguments, project_dir, session_id)
    if script == TRUSTED_GH_SCRIPT and arguments[:1] in (("comments",), ("comment",)):
        return _trusted_comment_verdict(arguments)
    if script == TRUSTED_GH_SCRIPT and _trusted_api_read_accepted(arguments, project_dir):
        return ALLOW, "", True
    if script in (TRUSTED_GH_SCRIPT, TRUSTED_GIT_SCRIPT):
        accepted = _trusted_wrapper_accepted(script, arguments, project_dir)
        return (ASK if accepted else ""), "", False
    verdict, error = _script_verdict(script, arguments)
    if error:
        return DENY, error, False
    if verdict == ALLOW_VERDICT or (verdict == TRUSTED_READ_VERDICT and _trusted_project(project_dir)):
        return ALLOW, "", True
    return (ASK if verdict else ""), "", verdict in INSPECTING_SCRIPT_VERDICTS


def _python_workflow(tokens: list, project_dir: str) -> bool:
    """Recognize bounded repository scripts, unittest, and Ruff module commands."""
    return _python_verdict(tokens, project_dir)[0] in (ALLOW, ASK)


def _literal_part(tokens: list) -> bool:
    """Return whether tokens form one command without operators or expansion."""
    if not 1 < len(tokens) <= MAX_WORKFLOW_ARGUMENTS:
        return False
    return not any(
        core.is_ambiguous(token) or (token and OPERATOR_CHARACTERS.issuperset(token))
        or any(character in token for character in WORKFLOW_FORBIDDEN_CHARACTERS)
        for token in tokens)


def _command_parts(command: str) -> list:
    """Return the token list of each && chain part, or [] for any other syntax."""
    if ("\n" in command or "\r" in command or COMMENT_CHARACTER in command
            or len(command) > bash_parser.MAX_COMMAND_CHARACTERS):
        return []
    tokens, complete = bash_parser._tokenize_line(command)
    if not complete:
        return []
    parts = [[]]
    for token in tokens:
        if token == CHAIN_OPERATOR:
            parts.append([])
        else:
            parts[-1].append(token)
    if len(parts) > MAX_CHAIN_PARTS or not all(_literal_part(part) for part in parts):
        return []
    return parts


def _literal_tokens(command: str) -> list:
    """Return the tokens of one literal command without wrappers or redirection."""
    parts = _command_parts(command)
    return parts[0] if len(parts) == 1 else []


def _os_error_text(error: OSError) -> str:
    """Name an OS failure without echoing the absolute path it carries."""
    return f"{type(error).__name__}: {core.sanitize(error.strerror or 'no detail')}"


def _regular_file_details(project_dir: str, name: str) -> tuple:
    """Return (lstat result, "") for one regular root file, or (None, reason)."""
    try:
        details = os.lstat(os.path.join(project_dir, name))
    except OSError as error:
        return None, (f"{name} cannot be read ({_os_error_text(error)}). "
                      f"Add a regular {name} at the repository root.")
    if not stat.S_ISREG(details.st_mode):
        return None, (f"{name} is not a regular file. Replace the symlink or special file "
                      "with a regular file.")
    return details, ""


def _read_package_manifest(project_dir: str) -> tuple:
    """Return (bytes, "") for a bounded regular package.json, or (None, reason)."""
    details, error = _regular_file_details(project_dir, NPM_MANIFEST)
    if error:
        return None, error
    try:
        descriptor = os.open(os.path.join(project_dir, NPM_MANIFEST), NPM_READ_FLAGS)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            # A swap between lstat and open could substitute a symlink target or a FIFO.
            if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
                return None, (f"{NPM_MANIFEST} changed while the gate read it. "
                              "Keep the file unchanged while the gate reads it.")
            content = handle.read(MAX_PACKAGE_MANIFEST_BYTES + 1)
    except OSError as error:
        return None, (f"{NPM_MANIFEST} cannot be read ({_os_error_text(error)}). "
                      f"Add a regular {NPM_MANIFEST} at the repository root.")
    if len(content) > MAX_PACKAGE_MANIFEST_BYTES:
        return None, f"{NPM_MANIFEST} exceeds 1 MiB. Reduce the manifest size."
    return content, ""


def _manifest_depth_error(text: str) -> str:
    """Bound JSON nesting iteratively so json.loads never recurses past the limit."""
    depth = 0
    for match in JSON_TOKEN_PATTERN.finditer(text):
        token = match.group()
        if token == '"':
            return (f"{NPM_MANIFEST} is not valid JSON (unterminated string). "
                    "Fix the JSON syntax.")
        if token[0] == '"':
            continue
        depth = max(0, depth + (1 if token in "[{" else -1))
        if depth > MAX_PACKAGE_MANIFEST_DEPTH:
            return (f"{NPM_MANIFEST} nests deeper than {MAX_PACKAGE_MANIFEST_DEPTH} levels. "
                    "Flatten the manifest.")
    return ""


def _parse_package_manifest(content: bytes) -> tuple:
    """Return (manifest, "") for UTF-8 JSON within the depth limit, or (None, reason)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        return None, (f"{NPM_MANIFEST} is not valid UTF-8 (byte offset {error.start}). "
                      "Re-save it as UTF-8.")
    error_text = _manifest_depth_error(text)
    if error_text:
        return None, error_text
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as error:
        return None, (f"{NPM_MANIFEST} is not valid JSON (lineno {error.lineno}, colno {error.colno}). "
                      "Fix the JSON syntax.")
    except ValueError as error:
        # Integers past the interpreter's digit limit raise plain ValueError.
        return None, (f"{NPM_MANIFEST} holds a value the gate cannot parse ({type(error).__name__}). "
                      "Shorten the value.")


def _package_scripts(project_dir: str) -> tuple:
    """Return (scripts, "") from package.json, or (None, reason)."""
    content, error = _read_package_manifest(project_dir)
    if error:
        return None, error
    manifest, error = _parse_package_manifest(content)
    if error:
        return None, error
    if not isinstance(manifest, dict):
        return None, f"{NPM_MANIFEST} root is not a JSON object. Make the root an object."
    scripts = manifest.get("scripts", {})
    if not isinstance(scripts, dict):
        return None, (f"{NPM_MANIFEST} `scripts` is not a JSON object. "
                      "Make `scripts` an object.")
    invalid = next((name for name, body in scripts.items() if not isinstance(body, str)), None)
    if invalid is not None:
        return None, (f"Script `{core.sanitize(invalid)}` in {NPM_MANIFEST} is not a string. "
                      "Make every script a string.")
    return scripts, ""


def _script_display_error(name: str, body: str) -> str:
    """Deny a script the consent prompt cannot show in full and in plain text."""
    if len(body) > core.MAX_REASON_VALUE:
        return (f"Script `{name}` exceeds {core.MAX_REASON_VALUE} characters, so the consent prompt "
                "cannot show it in full. Move the logic into a file the script calls.")
    if not all(" " <= character <= "~" for character in body):
        return (f"Script `{name}` contains characters outside printable ASCII. "
                "Remove them.")
    return ""


def _repository_program_path(program: str, project_dir: str) -> str:
    """Return the repository-relative path PATH resolves a program to, or ""."""
    found = shutil.which(program)
    if found is None:
        return ""
    location = os.path.abspath(found)
    norm_location = os.path.normcase(location)
    for base in {os.path.abspath(project_dir), os.path.realpath(project_dir)}:
        norm_base = os.path.normcase(base)
        # commonpath raises ValueError across Windows drives; a drive mismatch is never containment.
        if (os.path.splitdrive(norm_location)[0] == os.path.splitdrive(norm_base)[0]
                and os.path.commonpath([norm_location, norm_base]) == norm_base):
            return core.sanitize(os.path.relpath(location, base))
    return ""


def _npm_lookup_error(project_dir: str) -> str:
    """Deny an npm executable that PATH resolves to inside the repository."""
    if shutil.which("npm") is None:
        return "npm is not on PATH. Install Node.js and npm."
    relative = _repository_program_path("npm", project_dir)
    if relative:
        return (f"PATH resolves npm to `{relative}` inside the repository. "
                "Remove that directory from PATH.")
    return ""


def _workflow_consent_text(program: str, project_dir: str) -> str:
    """Name a program resolved from the repository. Repository writers control it."""
    relative = _repository_program_path(program, project_dir)
    if not relative:
        return WORKFLOW_CONSENT
    return (f"{WORKFLOW_CONSENT}. PATH resolves {core.sanitize(program)} to `{relative}` inside "
            "the repository (untrusted repository code).")


def _test_path_components(argument: str) -> tuple:
    """Return (components, "") for one inert relative path, or ((), reason)."""
    label = core.sanitize(argument)
    if argument.startswith("-"):
        return (), f"`{label}`: npm options stay denied. Remove the option."
    if not TEST_PATH_PATTERN.fullmatch(argument):
        return (), (f"`{label}` uses characters outside A-Z a-z 0-9 . _ @ + / -. "
                    "Rename the file or pass another path.")
    parts = tuple(part for part in argument.split("/") if part not in ("", "."))
    if argument.startswith("/") or ".." in parts:
        return (), (f"`{label}` must be a repository-relative path without `..`. "
                    "Pass a path below the repository root.")
    if not parts:
        return (), f"`{label}` does not name a test file. Pass a repository-relative file path."
    if len(parts) > MAX_TEST_PATH_COMPONENTS:
        return (), (f"`{label}` has more than {MAX_TEST_PATH_COMPONENTS} path components. "
                    "Pass a shorter path.")
    return parts, ""


def _test_file_error(base: str, parts: tuple, verified_directories: set) -> str:
    """Walk one path with lstat so no component can be a symlink or leave base."""
    label = core.sanitize("/".join(parts))
    for index in range(1, len(parts) + 1):
        prefix = parts[:index]
        leaf = index == len(parts)
        if not leaf and prefix in verified_directories:
            continue
        try:
            details = os.lstat(os.path.join(base, *prefix))
        except OSError as error:
            return f"`{label}` cannot be read ({_os_error_text(error)}). Check the path."
        expected_type = stat.S_ISREG if leaf else stat.S_ISDIR
        if not expected_type(details.st_mode):
            return (f"`{label}` passes through a symlink or is not a regular file. "
                    "Pass the real file path.")
        verified_directories.add(prefix)
    return ""


def _npm_test_files_error(arguments: list, project_dir: str) -> str:
    """Check every test file argument with string rules first, then the filesystem."""
    if not arguments:
        return "npm test -- needs at least one test file. Name a test file after --."
    checked_paths = []
    for argument in dict.fromkeys(arguments):
        parts, error = _test_path_components(argument)
        if error:
            return error
        checked_paths.append(parts)
    base = os.path.realpath(project_dir)
    verified_directories = set()
    for parts in checked_paths:
        error = _test_file_error(base, parts, verified_directories)
        if error:
            return error
    return ""


def _npm_shape(tokens: list):
    """Return (main script, executed script names, test files) for an accepted form, else None."""
    if tokens == ["npm", "ci"]:
        return "", NPM_CI_LIFECYCLE, None
    bare_test = len(tokens) == NPM_TEST_TOKEN_COUNT
    if tokens[1] == "test" and (bare_test or tokens[SUBCOMMAND_ARGUMENTS_INDEX] == "--"):
        return "test", ("pretest", "test", "posttest"), None if bare_test else tokens[NPM_TEST_FILES_INDEX:]
    if len(tokens) == NPM_RUN_TOKEN_COUNT and tokens[1] == "run" and tokens[-1] in NPM_RUN_SCRIPTS:
        name = tokens[-1]
        return name, (f"pre{name}", name, f"post{name}"), None
    return None


def _npm_requirement_error(main: str, scripts: dict, test_files, project_dir: str) -> str:
    """Require the lockfile or script the repository defines, plus valid test files."""
    if not main:
        if any(_regular_file_details(project_dir, name)[0] is not None for name in NPM_LOCKFILES):
            return ""
        return ("npm ci requires package-lock.json or npm-shrinkwrap.json as a regular file. "
                "Commit a lockfile.")
    if main not in scripts:
        return (f"{NPM_MANIFEST} defines no `{main}` script, so the repository does not require "
                "this workflow. Add the script or skip the command.")
    if test_files is None:
        return ""
    return _npm_test_files_error(test_files, project_dir)


def _npm_decision(tokens: list, project_dir: str) -> tuple:
    """Route an accepted npm form to consent with every script it runs, or deny with a cause."""
    shape = _npm_shape(tokens)
    if shape is None:
        return "", ""
    main, lifecycle, test_files = shape
    if os.path.lexists(os.path.join(project_dir, NPM_CONFIG)):
        return "deny", (f"Project {NPM_CONFIG} can change the shell, node options, and registry "
                        f"npm uses. Remove {NPM_CONFIG}.")
    error = _npm_lookup_error(project_dir)
    if error:
        return "deny", error
    scripts, error = _package_scripts(project_dir)
    if error:
        return "deny", error
    error = _npm_requirement_error(main, scripts, test_files, project_dir)
    if error:
        return "deny", error
    executed = [name for name in lifecycle if name in scripts]
    for name in executed:
        error = _script_display_error(name, scripts[name])
        if error:
            return "deny", error
    if main == MAKE_TEST_TARGET and _trusted_project(project_dir):
        return "allow", ""
    listed = "; ".join(f'{name}="{core.sanitize(scripts[name])}"' for name in executed) or "none"
    return "ask", f"{WORKFLOW_CONSENT}. {UNTRUSTED_SCRIPTS_LABEL}: {listed}. {UNSHOWN_CODE_NOTE}"


def _search_stays_in_repository(arguments: list) -> bool:
    """Return whether no search operand can name a path outside the repository."""
    return not any(argument.startswith(("/", "~")) or PARENT_DIRECTORY in argument.split("/")
                   for argument in arguments)


def _search_verdict(tokens: list) -> tuple:
    """Return (decision, reason, read-only) for one rg search."""
    if not all(not token.startswith("-") or token in SEARCH_FLAGS for token in tokens[1:]):
        return "", "", False
    # A pattern and a path look alike, so any operand that can leave the repository asks.
    return (ALLOW if _search_stays_in_repository(tokens[1:]) else ASK), "", True


def _js_runner_error(program: str, project_dir: str) -> str:
    """Return the first deny reason for `pnpm test` or `yarn test`, or ""."""
    config = next((name for name in JS_RUNNER_CONFIGS[program]
                   if os.path.lexists(os.path.join(project_dir, name))), "")
    if config:
        return (f"Project {config} can change how {program} runs. Remove {config}.")
    if shutil.which(program) is None:
        return f"{program} is not on PATH. Install {program}."
    relative = _repository_program_path(program, project_dir)
    if relative:
        return (f"PATH resolves {program} to `{relative}` inside the repository. "
                "Remove that directory from PATH.")
    lockfile = JS_RUNNER_LOCKFILES[program]
    if _regular_file_details(project_dir, lockfile)[0] is None:
        return f"{program} test requires {lockfile} as a regular file. Commit a lockfile."
    return ""


def _js_test_decision(program: str, project_dir: str) -> tuple:
    """Allow `pnpm test` or `yarn test` for a trusted owner, else ask with every script it runs."""
    error = _js_runner_error(program, project_dir)
    if error:
        return DENY, error
    scripts, error = _package_scripts(project_dir)
    if error:
        return DENY, error
    if JS_TEST_SCRIPT not in scripts:
        return DENY, (f"{NPM_MANIFEST} defines no `test` script, so the repository does not "
                      "require this workflow. Add the script or skip the command.")
    executed = [name for name in JS_TEST_LIFECYCLE if name in scripts]
    for name in executed:
        error = _script_display_error(name, scripts[name])
        if error:
            return DENY, error
    if _trusted_project(project_dir):
        return ALLOW, ""
    listed = "; ".join(f'{name}="{core.sanitize(scripts[name])}"' for name in executed)
    return ASK, (f"{WORKFLOW_CONSENT}. {program} runs these {NPM_MANIFEST} scripts (untrusted "
                 f"repository data): {listed}. {UNSHOWN_CODE_NOTE}")


def _compiled_test_verdict(tokens: list, project_dir: str) -> tuple:
    """Return (decision, reason, read-only) for an accepted go or cargo test run."""
    program = tokens[0]
    if not _test_runners.compiled_test_shape(program, tokens[1:], project_dir, _is_workflow_path):
        return "", "", False
    error = _program_lookup_error(program, (), program, project_dir)
    if error:
        return DENY, error, False
    # Both runners write build caches, so neither counts as read-only.
    return (ALLOW if _trusted_project(project_dir) else ASK), "", False


def _zone_helper_verdict(arguments: tuple, project_dir: str, session_id: str) -> tuple:
    """Return (decision, reason, read-only) for the zone delete helper.

    The helper asks only for this session's own id and operands that pass
    the zone check. Any other call stays unrecognized. The opaque denial
    then applies.
    """
    if len(arguments) < ZONE_HELPER_MINIMUM_ARGUMENTS or arguments[0] != ZONE_SESSION_OPTION:
        return "", "", False
    if arguments[1] != session_id or not _zones.valid_session_id(session_id):
        return "", "", False
    if not _zones.zone_delete_allowed(list(arguments[2:]), project_dir, session_id):
        return "", "", False
    return ASK, ZONE_DELETE_REASON, False


def _program_verdict(tokens: list, project_dir: str, session_id: str = "") -> tuple:
    """Return (decision, reason, read-only) for one literal command by program."""
    program = tokens[0]
    if program == "npm":
        decision, reason = _npm_decision(tokens, project_dir)
        return decision, reason, False
    if program in JS_RUNNER_LOCKFILES:
        if tokens[1:] != [JS_TEST_SCRIPT]:
            return "", "", False
        decision, reason = _js_test_decision(program, project_dir)
        return decision, reason, False
    if program in _test_runners.COMPILED_RUNNERS:
        return _compiled_test_verdict(tokens, project_dir)
    if program == RUFF_PROGRAM:
        return _ruff_verdict(tokens[1:], project_dir, RUFF_PROGRAM)
    if program == PYTEST_PROGRAM:
        return _pytest_verdict(tokens[1:], project_dir, PYTEST_PROGRAM)
    if _python_program(program):
        return _python_verdict(tokens, project_dir, session_id)
    if program == "make":
        matched = tokens[1] in MAKE_TARGETS and tokens[SUBCOMMAND_ARGUMENTS_INDEX:] in MAKE_ARGUMENTS
        if matched and tokens[1] == MAKE_TEST_TARGET and _trusted_project(project_dir):
            return ALLOW, "", False
        return (ASK if matched else ""), "", False
    if program == "rg":
        return _search_verdict(tokens)
    return "", "", False


def _part_verdict(tokens: list, project_dir: str, session_id: str = "") -> tuple:
    """Return (decision, reason, read-only) with consent text and PATH disclosure applied."""
    decision, reason, read_only = _program_verdict(tokens, project_dir, session_id)
    # Repository writers control a program that PATH resolves inside the repository.
    if decision == ALLOW and _repository_program_path(tokens[0], project_dir):
        decision = ASK
    if decision == ASK and not reason:
        reason = _workflow_consent_text(tokens[0], project_dir)
    return decision, reason, read_only


def _workflow_verdict(command: str, project_dir: str, session_id: str = "") -> tuple:
    """Return (decision, reason, read-only) for one command or an && chain."""
    parts = _command_parts(command)
    verdicts = [_part_verdict(part, project_dir, session_id) for part in parts]
    decisions = [verdict[0] for verdict in verdicts]
    if not verdicts:
        return "", "", False
    if DENY in decisions:
        return verdicts[decisions.index(DENY)]
    if "" in decisions:
        return "", "", False
    read_only = all(part_read_only for _decision, _reason, part_read_only in verdicts)
    if ASK in decisions:
        reasons = dict.fromkeys(verdict[1] for verdict in verdicts if verdict[0] == ASK)
        return ASK, " ".join(reasons), read_only
    return ALLOW, "", read_only


def workflow_decision(command: str, project_dir: str, session_id: str = "") -> tuple:
    """Return ("allow", ""), ("ask", prompt), ("deny", reason), or ("", "").

    The command holds one literal workflow or an && chain of them. A chain
    takes the strictest decision of its parts. `session_id` names the
    caller's zone folders. Without it no delete is recognized.
    """
    decision, reason, _read_only = _workflow_verdict(command, project_dir, session_id)
    return decision, reason


def workflow_needs_consent(command: str, project_dir: str) -> bool:
    """Return whether a recognized workflow routes to consent."""
    return workflow_decision(command, project_dir)[0] == ASK


def read_only_workflow(command: str, project_dir: str) -> bool:
    """Return whether a recognized workflow only inspects the repository."""
    # npm always executes repository code, so skip the manifest read entirely.
    if any(part[0] == "npm" for part in _command_parts(command)):
        return False
    decision, _reason, read_only = _workflow_verdict(command, project_dir)
    return decision in (ALLOW, ASK) and read_only


# Literal shapes stay importable under these names for existing callers.
WORKFLOW_SCRIPT_ARGUMENTS = _literal_script_arguments(SCRIPT_VERDICTS)
READ_ONLY_WORKFLOW_ARGUMENTS = _literal_script_arguments(INSPECTING_SCRIPT_VERDICTS)
