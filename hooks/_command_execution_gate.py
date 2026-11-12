"""Classify shell programs and repository workflows for the branch hook.

The branch hook imports this module for every shell command it inspects.
Denials and consent prompts from this module name this file, so a blocked
program points at the command allowlist rather than at branch naming.
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
# The shipped list holds under 3 KiB. The cap bounds the read of a repository-controlled file
# and leaves room for adopter entries.
MAX_WORKFLOW_SCRIPT_LIST_BYTES = 64 * 1024
ALLOW = "allow"
ASK = "ask"
DENY = "deny"
ALLOW_VERDICT = "allow"
CONSENT_READ_VERDICT = "consent-read"
CONSENT_WRITE_VERDICT = "consent-write"
SCRIPT_VERDICTS = frozenset({ALLOW_VERDICT, CONSENT_READ_VERDICT, CONSENT_WRITE_VERDICT})
INSPECTING_SCRIPT_VERDICTS = frozenset({ALLOW_VERDICT, CONSENT_READ_VERDICT})
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
# unterminated string, which stops the scan before any retry at later quotes.
JSON_TOKEN_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]|"')
NPM_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
UNTRUSTED_SCRIPTS_LABEL = "npm runs these package.json scripts (untrusted repository data)"
UNSHOWN_CODE_NOTE = "Dependency lifecycle scripts and node_modules binaries also run and are not shown."


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
        hint = _accepted_forms_hint(program, executable[1:])
        return f"{OPAQUE_REASON}. {hint}" if hint else OPAQUE_REASON
    return ""


def _accepted_forms_hint(program: str, arguments: list) -> str:
    """Return the accepted forms for a known tool, or "" for another program."""
    if program == RUFF_PROGRAM:
        return RUFF_HINT
    if not _python_program(program):
        return ""
    if arguments[:MODULE_LAUNCH_ARGUMENTS] == ["-m", RUFF_PROGRAM]:
        return RUFF_HINT
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


@functools.lru_cache(maxsize=1)
def _load_workflow_scripts() -> tuple:
    """Return the parsed script list, or raise ValueError naming the repair."""
    try:
        with open(WORKFLOW_SCRIPT_LIST_PATH, "rb") as handle:
            content = handle.read(MAX_WORKFLOW_SCRIPT_LIST_BYTES + 1)
        if len(content) > MAX_WORKFLOW_SCRIPT_LIST_BYTES:
            raise ValueError("the file exceeds 64 KiB")
        return _parse_workflow_scripts(content.decode("ascii"))
    except OSError as error:
        detail = _os_error_text(error)
    except UnicodeDecodeError:
        detail = "the file holds non-ASCII bytes"
    except ValueError as error:
        detail = str(error)
    raise ValueError(f"hooks/{WORKFLOW_SCRIPT_LIST} cannot be used ({detail}). "
                     "Restore the file from the policy source, then retry.")


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
    """Accept discovery options whose directories stay inside the repository."""
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


def _ruff_lookup_error(launcher: str, project_dir: str) -> str:
    """Deny a Ruff launch that repository content can replace."""
    if launcher == RUFF_PROGRAM:
        relative = _repository_program_path(RUFF_PROGRAM, project_dir)
        if not relative:
            return ""
        return (f"PATH resolves ruff to `{relative}` inside the repository. "
                "Remove that directory from PATH, then retry.")
    shadow = next((name for name in RUFF_SHADOW_NAMES
                   if os.path.lexists(os.path.join(project_dir, name))), "")
    if not shadow:
        return ""
    return (f"`{shadow}` at the repository root shadows the ruff module under python -m. "
            "Run the ruff executable instead, then retry.")


def _ruff_writes(subcommand: str, flags: list) -> bool:
    """Return whether one accepted Ruff invocation rewrites files."""
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
    error = _ruff_lookup_error(launcher, project_dir)
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


def _python_verdict(tokens: list, project_dir: str) -> tuple:
    """Return (decision, reason, read-only) for one Python interpreter invocation."""
    if tokens[1:MODULE_LAUNCH_END] == ["-m", "unittest"]:
        accepted = _unittest_accepted(tokens[MODULE_LAUNCH_END:], project_dir)
        return (ALLOW if accepted else ""), "", accepted
    if tokens[1:MODULE_LAUNCH_END] == ["-m", RUFF_PROGRAM]:
        return _ruff_verdict(tokens[MODULE_LAUNCH_END:], project_dir, RUFF_MODULE_LAUNCHER)
    script, arguments = _python_script_call(tokens, project_dir)
    if not script:
        return "", "", False
    if script in (TRUSTED_GH_SCRIPT, TRUSTED_GIT_SCRIPT):
        accepted = _trusted_wrapper_accepted(script, arguments, project_dir)
        return (ASK if accepted else ""), "", False
    verdict, error = _script_verdict(script, arguments)
    if error:
        return DENY, error, False
    if verdict == ALLOW_VERDICT:
        return ALLOW, "", True
    return (ASK if verdict else ""), "", verdict in INSPECTING_SCRIPT_VERDICTS


def _python_workflow(tokens: list, project_dir: str) -> bool:
    """Recognize bounded repository scripts, unittest, and Ruff module invocations."""
    return _python_verdict(tokens, project_dir)[0] in (ALLOW, ASK)


def _literal_part(tokens: list) -> bool:
    """Return whether tokens form one invocation without operators or expansion."""
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
    """Return the tokens of one literal invocation without wrappers or redirection."""
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
                      f"Add a regular {name} at the repository root, then retry.")
    if not stat.S_ISREG(details.st_mode):
        return None, (f"{name} is not a regular file. Replace the symlink or special file "
                      "with a regular file, then retry.")
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
                              "Retry once the file is stable.")
            content = handle.read(MAX_PACKAGE_MANIFEST_BYTES + 1)
    except OSError as error:
        return None, (f"{NPM_MANIFEST} cannot be read ({_os_error_text(error)}). "
                      f"Add a regular {NPM_MANIFEST} at the repository root, then retry.")
    if len(content) > MAX_PACKAGE_MANIFEST_BYTES:
        return None, f"{NPM_MANIFEST} exceeds 1 MiB. Reduce the manifest size, then retry."
    return content, ""


def _manifest_depth_error(text: str) -> str:
    """Bound JSON nesting iteratively so json.loads never recurses past the limit."""
    depth = 0
    for match in JSON_TOKEN_PATTERN.finditer(text):
        token = match.group()
        if token == '"':
            return (f"{NPM_MANIFEST} is not valid JSON (unterminated string). "
                    "Fix the JSON syntax, then retry.")
        if token[0] == '"':
            continue
        depth = max(0, depth + (1 if token in "[{" else -1))
        if depth > MAX_PACKAGE_MANIFEST_DEPTH:
            return (f"{NPM_MANIFEST} nests deeper than {MAX_PACKAGE_MANIFEST_DEPTH} levels. "
                    "Flatten the manifest, then retry.")
    return ""


def _parse_package_manifest(content: bytes) -> tuple:
    """Return (manifest, "") for UTF-8 JSON within the depth limit, or (None, reason)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        return None, (f"{NPM_MANIFEST} is not valid UTF-8 (byte offset {error.start}). "
                      "Re-save it as UTF-8, then retry.")
    error_text = _manifest_depth_error(text)
    if error_text:
        return None, error_text
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as error:
        return None, (f"{NPM_MANIFEST} is not valid JSON (lineno {error.lineno}, colno {error.colno}). "
                      "Fix the JSON syntax, then retry.")
    except ValueError as error:
        # Integers past the interpreter's digit limit raise plain ValueError.
        return None, (f"{NPM_MANIFEST} holds a value the gate cannot parse ({type(error).__name__}). "
                      "Shorten the value, then retry.")


def _package_scripts(project_dir: str) -> tuple:
    """Return (scripts, "") from package.json, or (None, reason)."""
    content, error = _read_package_manifest(project_dir)
    if error:
        return None, error
    manifest, error = _parse_package_manifest(content)
    if error:
        return None, error
    if not isinstance(manifest, dict):
        return None, f"{NPM_MANIFEST} root is not a JSON object. Make the root an object, then retry."
    scripts = manifest.get("scripts", {})
    if not isinstance(scripts, dict):
        return None, (f"{NPM_MANIFEST} `scripts` is not a JSON object. "
                      "Make `scripts` an object, then retry.")
    invalid = next((name for name, body in scripts.items() if not isinstance(body, str)), None)
    if invalid is not None:
        return None, (f"Script `{core.sanitize(invalid)}` in {NPM_MANIFEST} is not a string. "
                      "Make every script a string, then retry.")
    return scripts, ""


def _script_display_error(name: str, body: str) -> str:
    """Deny a script the consent prompt cannot show in full and in plain text."""
    if len(body) > core.MAX_REASON_VALUE:
        return (f"Script `{name}` exceeds {core.MAX_REASON_VALUE} characters, so the consent prompt "
                "cannot show it in full. Move the logic into a file the script calls, then retry.")
    if not all(" " <= character <= "~" for character in body):
        return (f"Script `{name}` contains characters outside printable ASCII. "
                "Remove them, then retry.")
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
        return "npm is not on PATH. Install Node.js and npm, then retry."
    relative = _repository_program_path("npm", project_dir)
    if relative:
        return (f"PATH resolves npm to `{relative}` inside the repository. "
                "Remove that directory from PATH, then retry.")
    return ""


def _workflow_consent_text(program: str, project_dir: str) -> str:
    """Name a repository-resolved program, which repository writers control."""
    relative = _repository_program_path(program, project_dir)
    if not relative:
        return WORKFLOW_CONSENT
    return (f"{WORKFLOW_CONSENT}. PATH resolves {core.sanitize(program)} to `{relative}` inside "
            "the repository (untrusted repository code).")


def _test_path_components(argument: str) -> tuple:
    """Return (components, "") for one inert relative path, or ((), reason)."""
    label = core.sanitize(argument)
    if argument.startswith("-"):
        return (), f"`{label}`: npm options stay denied. Remove the option, then retry."
    if not TEST_PATH_PATTERN.fullmatch(argument):
        return (), (f"`{label}` uses characters outside A-Z a-z 0-9 . _ @ + / -. "
                    "Rename the file or pass another path, then retry.")
    parts = tuple(part for part in argument.split("/") if part not in ("", "."))
    if argument.startswith("/") or ".." in parts:
        return (), (f"`{label}` must be a repository-relative path without `..`. "
                    "Pass a path below the repository root, then retry.")
    if not parts:
        return (), f"`{label}` does not name a test file. Pass a repository-relative file path, then retry."
    if len(parts) > MAX_TEST_PATH_COMPONENTS:
        return (), (f"`{label}` has more than {MAX_TEST_PATH_COMPONENTS} path components. "
                    "Pass a shorter path, then retry.")
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
            return f"`{label}` cannot be read ({_os_error_text(error)}). Check the path, then retry."
        expected_type = stat.S_ISREG if leaf else stat.S_ISDIR
        if not expected_type(details.st_mode):
            return (f"`{label}` passes through a symlink or is not a regular file. "
                    "Pass the real file path, then retry.")
        verified_directories.add(prefix)
    return ""


def _npm_test_files_error(arguments: list, project_dir: str) -> str:
    """Check every test file argument with string rules first, then the filesystem."""
    if not arguments:
        return "npm test -- needs at least one test file. Name a test file after --, then retry."
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
                "Commit a lockfile, then retry.")
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
                        f"npm uses. Remove {NPM_CONFIG}, then retry.")
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


def _program_verdict(tokens: list, project_dir: str) -> tuple:
    """Return (decision, reason, read-only) for one literal invocation by program."""
    program = tokens[0]
    if program == "npm":
        decision, reason = _npm_decision(tokens, project_dir)
        return decision, reason, False
    if program == RUFF_PROGRAM:
        return _ruff_verdict(tokens[1:], project_dir, RUFF_PROGRAM)
    if _python_program(program):
        return _python_verdict(tokens, project_dir)
    if program == "make":
        matched = tokens[1] in MAKE_TARGETS and tokens[SUBCOMMAND_ARGUMENTS_INDEX:] in MAKE_ARGUMENTS
        return (ASK if matched else ""), "", False
    if program == "rg":
        return _search_verdict(tokens)
    return "", "", False


def _part_verdict(tokens: list, project_dir: str) -> tuple:
    """Return (decision, reason, read-only) with consent text and PATH disclosure applied."""
    decision, reason, read_only = _program_verdict(tokens, project_dir)
    # Repository writers control a program that PATH resolves inside the repository.
    if decision == ALLOW and _repository_program_path(tokens[0], project_dir):
        decision = ASK
    if decision == ASK and not reason:
        reason = _workflow_consent_text(tokens[0], project_dir)
    return decision, reason, read_only


def _workflow_verdict(command: str, project_dir: str) -> tuple:
    """Return (decision, reason, read-only) for one invocation or an && chain."""
    parts = _command_parts(command)
    verdicts = [_part_verdict(part, project_dir) for part in parts]
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


def workflow_decision(command: str, project_dir: str) -> tuple:
    """Return ("allow", ""), ("ask", prompt), ("deny", reason), or ("", "").

    The command holds one literal workflow or an && chain of them. A chain
    takes the strictest decision of its parts.
    """
    decision, reason, _read_only = _workflow_verdict(command, project_dir)
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
