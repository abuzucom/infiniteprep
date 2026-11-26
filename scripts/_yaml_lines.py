#!/usr/bin/env python3
"""Load YAML with source lines for the workflow and container checkers.

LineLoader registers its mapping constructor on a SafeLoader subclass. The
global yaml.SafeLoader stays unmodified, so the line numbers one checker
reports never depend on which other checker imported first.
"""
from typing import Callable, Iterator

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.tokens import ScalarToken

MERGE_KEY_TAG = "tag:yaml.org,2002:merge"


class MarkedMapping(dict):
    """Store the source line of a YAML mapping and of each key."""

    def __init__(self, line: int) -> None:
        super().__init__()
        self.line = line
        self.key_lines: dict[object, int] = {}


class LineLoader(yaml.SafeLoader):
    """Safe loader that marks mappings and rejects duplicate keys."""


def _reject_duplicate_keys(loader: LineLoader, node: MappingNode) -> None:
    """Raise ConstructorError for a duplicate or unhashable explicit key."""
    explicit_keys = set()
    for key_node, _value_node in node.value:
        if key_node.tag == MERGE_KEY_TAG:
            continue
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in explicit_keys
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable key", key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        explicit_keys.add(key)


def _construct_marked_mapping(
    loader: LineLoader, node: MappingNode
) -> Iterator[MarkedMapping]:
    """Construct a marked mapping. Yield first so aliases can refer to it."""
    mapping = MarkedMapping(node.start_mark.line)
    yield mapping
    _reject_duplicate_keys(loader, node)
    loader.flatten_mapping(node)
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        mapping[key] = loader.construct_object(value_node, deep=True)
        mapping.key_lines[key] = key_node.start_mark.line


LineLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_marked_mapping)


def load_documents(text: str) -> list:
    """Return every YAML document in `text` with marked mappings."""
    return list(yaml.load_all(text, Loader=LineLoader))


def mapping_line_numbers(lines: list[str], mapping: dict) -> set[int]:
    """Return line numbers directly associated with a marked mapping."""
    start = getattr(mapping, "line", 0)
    indent = len(lines[start]) - len(lines[start].lstrip(" "))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        next_indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if lines[index].strip() and next_indent <= indent:
            end = index
            break
    associated = set(range(start, end))
    index = start - 1
    while index >= 0 and lines[index].strip().startswith("#"):
        comment_indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if comment_indent != indent:
            break
        associated.add(index)
        index -= 1
    return associated


def exception_comment_lines(text: str, is_exception: Callable[[str], bool]) -> set[int]:
    """Return lines that `is_exception` accepts, outside block scalar content."""
    scalar_lines = set()
    for token in yaml.scan(text):
        if isinstance(token, ScalarToken) and (
            token.style in ("|", ">")
            or token.end_mark.line > token.start_mark.line
        ):
            scalar_lines.update(
                range(token.start_mark.line + 1, token.end_mark.line))
    return {
        number
        for number, line in enumerate(text.splitlines())
        if number not in scalar_lines and is_exception(line)
    }


def walk_mappings(documents: list[object]) -> Iterator[dict]:
    """Yield every mapping once, without following alias cycles twice."""
    stack = list(reversed(documents))
    visited: set[int] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
        if isinstance(value, dict):
            yield value
            stack.extend(reversed(list(value.values())))
        elif isinstance(value, list):
            stack.extend(reversed(value))
