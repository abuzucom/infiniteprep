"""Classify shell programs and repository workflows for the branch hook.

The branch hook imports this module for every shell command it inspects.
Denials and consent prompts from this module name this file, so a blocked
program points at the command allowlist rather than at branch naming.
"""
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
WORKFLOW_SCRIPT_ARGUMENTS = {
    "scripts/run_tests.py": ((),),
    "scripts/read_git_state.py": tuple((mode,) for mode in ("branch", "status", "remote", "revision", "all")),
    "scripts/sync.py": ((), ("--check",), ("--check-shared",), ("--write-shared",), ("--print-adoptable",)),
    "scripts/check_action_pins.py": ((),),
    "scripts/check_gate_adoption.py": ((),),
}
READ_ONLY_WORKFLOW_ARGUMENTS = {
    "scripts/run_tests.py": ((),),
    "scripts/read_git_state.py": WORKFLOW_SCRIPT_ARGUMENTS["scripts/read_git_state.py"],
    "scripts/sync.py": (("--check",), ("--check-shared",), ("--print-adoptable",)),
    "scripts/check_action_pins.py": ((),),
    "scripts/check_gate_adoption.py": ((),),
}
SEARCH_FLAGS = frozenset({
    "-n", "--line-number", "-l", "--files-with-matches", "-i", "--ignore-case",
    "-F", "--fixed-strings", "--files", "--hidden", "-g", "--glob", "-e", "--regexp", "--",
})
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
WORKFLOW_FORBIDDEN_CHARACTERS = "<>%!^\0"
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
        return "Opaque command execution requires an inspectable operation"
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


def _python_workflow(tokens: list, project_dir: str) -> bool:
    """Recognize bounded repository scripts and unittest module invocations."""
    if tokens[1:3] == ["-m", "unittest"]:
        modules = [token for token in tokens[3:] if token not in ("-v", "-q")]
        return bool(modules) and all(
            token.startswith("tests.") and all(part.isidentifier() for part in token.split("."))
            for token in modules)
    script, arguments = _python_script_call(tokens, project_dir)
    if script == TRUSTED_GH_SCRIPT and arguments[:1] == ("run",):
        if len(arguments) <= 1:
            return False
        decision, _reason = core.forge_verdict("gh", list(arguments[1:]), project_dir)
        return decision != "deny"
    if script == TRUSTED_GIT_SCRIPT:
        return _trusted_git_workflow(arguments)
    return arguments in WORKFLOW_SCRIPT_ARGUMENTS.get(script, ())


def _literal_tokens(command: str) -> list:
    """Return the tokens of one literal invocation without wrappers or redirection."""
    if "\n" in command or "\r" in command or len(command) > bash_parser.MAX_COMMAND_CHARACTERS:
        return []
    tokens, complete = bash_parser._tokenize_line(command)
    if not complete or not 1 < len(tokens) <= MAX_WORKFLOW_ARGUMENTS:
        return []
    if any(core.is_ambiguous(token) or token in SHELL_OPERATORS
           or any(character in token for character in WORKFLOW_FORBIDDEN_CHARACTERS)
           for token in tokens):
        return []
    return tokens


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
    if tokens[1] == "test" and (len(tokens) == 2 or tokens[2] == "--"):
        return "test", ("pretest", "test", "posttest"), tokens[3:] if len(tokens) > 2 else None
    if len(tokens) == 3 and tokens[1] == "run" and tokens[2] in NPM_RUN_SCRIPTS:
        name = tokens[2]
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


def workflow_decision(command: str, project_dir: str) -> tuple:
    """Return ("ask", prompt), ("deny", reason), or ("", "") for one literal workflow."""
    tokens = _literal_tokens(command)
    if not tokens:
        return "", ""
    if tokens[0] == "npm":
        return _npm_decision(tokens, project_dir)
    if _python_program(tokens[0]):
        matched = _python_workflow(tokens, project_dir)
    elif tokens[0] == "make":
        matched = tokens[1] in MAKE_TARGETS and tokens[2:] in MAKE_ARGUMENTS
    elif tokens[0] == "rg":
        matched = all(not token.startswith("-") or token in SEARCH_FLAGS for token in tokens[1:])
    else:
        matched = False
    if not matched:
        return "", ""
    return "ask", _workflow_consent_text(tokens[0], project_dir)


def workflow_needs_consent(command: str, project_dir: str) -> bool:
    """Limit workflow consent to one literal invocation without wrappers or redirection."""
    return workflow_decision(command, project_dir)[0] == "ask"


def read_only_workflow(command: str, project_dir: str) -> bool:
    """Return whether a consent-routed workflow only inspects the repository."""
    # npm always executes repository code, so skip the manifest read entirely.
    if _literal_tokens(command)[:1] == ["npm"]:
        return False
    if not workflow_needs_consent(command, project_dir):
        return False
    tokens = _literal_tokens(command)
    if tokens[0] == "rg" or tokens[1:3] == ["-m", "unittest"]:
        return True
    if not _python_program(tokens[0]):
        return False
    script, arguments = _python_script_call(tokens, project_dir)
    return arguments in READ_ONLY_WORKFLOW_ARGUMENTS.get(script, ())
