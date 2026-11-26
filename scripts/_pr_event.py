#!/usr/bin/env python3
"""Load bounded pull request data from a GitHub event file.

The PR prose checker, the external reference checker, and the agent prose
gate read the same event. One loader keeps their limits and checks equal.
"""
import json
from pathlib import Path

MAX_EVENT_BYTES = 1024 * 1024
MAX_TITLE_LENGTH = 4096
MAX_BODY_LENGTH = 1024 * 1024
DEPENDABOT_LOGIN = "dependabot[bot]"


def load_event_payload(path: Path) -> dict:
    """Return the event object after a size check."""
    if path.stat().st_size > MAX_EVENT_BYTES:
        raise ValueError("event payload exceeds the size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("event payload must contain an object")
    return payload


def pull_request_data(payload: dict) -> dict:
    """Return the pull_request object of an event payload."""
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("event payload lacks pull request data")
    return pull_request


def repository_owner_login(payload: dict) -> str:
    """Return the repository owner login of an event payload."""
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise ValueError("event payload lacks repository data")
    owner = repository.get("owner")
    if not isinstance(owner, dict) or not isinstance(owner.get("login"), str):
        raise ValueError("event payload lacks a repository owner login")
    return owner["login"]


def pull_request_prose(pull_request: dict) -> tuple[str, str, str]:
    """Return the bounded title, body, and author login of a pull request."""
    title = pull_request.get("title")
    body = pull_request.get("body")
    author = pull_request.get("user")
    if not isinstance(title, str):
        raise ValueError("pull request title must contain text")
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise ValueError("pull request body must contain text or null")
    if not isinstance(author, dict) or not isinstance(author.get("login"), str):
        raise ValueError("pull request author must contain a login")
    if len(title) > MAX_TITLE_LENGTH or len(body) > MAX_BODY_LENGTH:
        raise ValueError("pull request prose exceeds the size limit")
    return title, body, author["login"]


def load_pull_request(path: Path) -> tuple[str, str, str]:
    """Load bounded pull request prose and author identity from event JSON."""
    return pull_request_prose(pull_request_data(load_event_payload(path)))
