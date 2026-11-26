#!/usr/bin/env python3
"""Parse commit message trailers for the attribution and banned-agent checkers.

Both checkers read the same terminal trailer paragraph. One parser keeps
their view of a commit identical.
"""
import re

TRAILER_PATTERN = re.compile(
    r"^(?P<key>[A-Za-z0-9]+(?:[- ][A-Za-z0-9]+)*):[ \t]*(?P<value>.*)$"
)
# Callers match a stripped value. The anchors reject anything after the email.
COAUTHOR_PATTERN = re.compile(r"\A(?P<name>[^<>]+?)\s*(?:<(?P<email>[^<>]+)>)?\Z")


def terminal_paragraph(body: str) -> list[str]:
    """Return the lines of the last non-blank paragraph of a message."""
    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    paragraph = []
    for line in reversed(lines):
        if not line.strip():
            break
        paragraph.append(line)
    paragraph.reverse()
    return paragraph


def terminal_trailers(body: str) -> list[tuple[str, str]]:
    """Return key/value pairs from the terminal trailer paragraph.

    An indented line continues the previous trailer value.
    """
    trailers = []
    for line in terminal_paragraph(body):
        if line.startswith((" ", "\t")):
            if trailers:
                key, value = trailers[-1]
                trailers[-1] = (key, f"{value}\n{line.lstrip()}")
            continue
        match = TRAILER_PATTERN.fullmatch(line)
        if match:
            trailers.append((match.group("key"), match.group("value")))
    return trailers


def parse_coauthor(value: str):
    """Return the name and email match for one trailer value, or None."""
    return COAUTHOR_PATTERN.fullmatch(value.strip())
