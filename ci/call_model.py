#!/usr/bin/env python3
"""Call the configured provider for the evaluator contract."""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).with_name("model_providers.json")
MAX_INPUT_CHARS = 4_000_000
MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 180
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAY_SECONDS = 2
RETRY_DELAY_JITTER_SECONDS = 2
MAX_RETRY_DELAY_SECONDS = 30
# Code and JSON-escaped text average more than three characters per token
# in common tokenizers. This estimate therefore overcounts.
CHARS_PER_TOKEN_ESTIMATE = 3
# Providers reject a request above the context window with one of these.
CONTEXT_OVERFLOW_STATUS_CODES = {400, 413}
# The workflow reads this exit code as "no configured model can hold the
# review input" and records a NEEDS-HUMAN verdict instead of failing.
CAPACITY_EXIT_CODE = 3
ALLOWED_PROTOCOLS = {"ollama", "openai-compatible", "anthropic", "google"}
ALLOWED_ENDPOINTS = {
    "https://ollama.com/api",
    "https://api.openai.com/v1",
    "https://api.anthropic.com/v1",
    "https://generativelanguage.googleapis.com/v1beta",
}
ENDPOINTS_BY_PROTOCOL = {
    "ollama": "https://ollama.com/api",
    "openai-compatible": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
}


class ProviderError(RuntimeError):
    """Report a provider failure without exposing request or response data."""


class InputTooLargeError(ProviderError):
    """Report review input that exceeds every configured model budget."""


class ContextOverflowError(ProviderError):
    """Report a provider rejection that a larger context window can avoid."""


def _read_text_from_env(name: str) -> str:
    path_text = os.environ.get(name)
    if not path_text:
        raise ProviderError(f"missing {name}")
    path = Path(path_text)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ProviderError(f"cannot read {name}") from error


def _load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProviderError("provider configuration is unreadable") from error
    if not isinstance(config, dict):
        raise ProviderError("provider configuration is not an object")
    return config


def _validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ProviderError("provider endpoint must use HTTPS without credentials")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise ProviderError("provider endpoint contains unsupported URL fields")
    normalized = endpoint.rstrip("/")
    if normalized not in ALLOWED_ENDPOINTS:
        raise ProviderError("provider endpoint is not allowlisted")
    return normalized


def _load_profile(provider_name: str | None = None) -> dict[str, Any]:
    config = _load_config()
    if provider_name is None:
        provider_name = config.get("active_provider")
    providers = config.get("providers")
    if not isinstance(provider_name, str) or not isinstance(providers, dict):
        raise ProviderError("provider configuration has an invalid registry")
    profile = providers.get(provider_name)
    if not isinstance(profile, dict):
        raise ProviderError("active provider is not configured")
    protocol = profile.get("protocol")
    endpoint = profile.get("endpoint")
    model = profile.get("model")
    if protocol not in ALLOWED_PROTOCOLS:
        raise ProviderError("provider protocol is not allowlisted")
    if not isinstance(endpoint, str) or not isinstance(model, str) or not model:
        raise ProviderError("provider profile is incomplete")
    if len(model) > 200 or any(ord(character) < 32 for character in model):
        raise ProviderError("provider model is invalid")
    profile = dict(profile)
    normalized_endpoint = _validate_endpoint(endpoint)
    if normalized_endpoint != ENDPOINTS_BY_PROTOCOL[protocol]:
        raise ProviderError("provider endpoint does not match its protocol")
    profile["endpoint"] = normalized_endpoint
    profile["name"] = provider_name
    return profile


def _input_budget(profile: dict[str, Any]) -> int | None:
    """Return the profile's input token budget, or None when unset."""
    budget = profile.get("max_input_tokens")
    if budget is None:
        return None
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise ProviderError("provider max_input_tokens is invalid")
    return budget


def _fits(profile: dict[str, Any], estimated_tokens: int) -> bool:
    budget = _input_budget(profile)
    return budget is None or estimated_tokens <= budget


def _large_profile() -> dict[str, Any] | None:
    """Return the configured large-input profile, or None when absent."""
    name = _load_config().get("large_input_provider")
    if name is None:
        return None
    if not isinstance(name, str) or not name:
        raise ProviderError("large_input_provider is invalid")
    return _load_profile(name)


def estimate_tokens(system_prompt: str, case_text: str) -> int:
    """Return a conservative token estimate for one review request."""
    characters = len(system_prompt) + len(case_text)
    return -(-characters // CHARS_PER_TOKEN_ESTIMATE)


def select_profile(estimated_tokens: int) -> dict[str, Any]:
    """Return the active profile when the input fits, else the large one.

    A profile without max_input_tokens has no pre-check. Registries written
    before the field existed keep their prior behavior.
    """
    active = _load_profile()
    if _fits(active, estimated_tokens):
        return active
    large = _large_profile()
    if large is not None and _fits(large, estimated_tokens):
        return large
    raise InputTooLargeError(
        f"review input of about {estimated_tokens} tokens exceeds every "
        "configured model budget"
    )


def _headers(protocol: str, api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if protocol in {"ollama", "openai-compatible"}:
        headers["Authorization"] = f"Bearer {api_key}"
    elif protocol == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["x-goog-api-key"] = api_key
    return headers


def _message_payload(system_prompt: str, case_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case_text},
    ]


def _build_request(profile: dict[str, Any], system_prompt: str,
                   case_text: str, api_key: str) -> Request:
    protocol = profile["protocol"]
    model = profile["model"]
    max_tokens = profile.get("max_output_tokens", 8192)
    messages = _message_payload(system_prompt, case_text)
    if protocol == "ollama":
        url = f"{profile['endpoint']}/chat"
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": max_tokens},
        }
    elif protocol == "openai-compatible":
        url = f"{profile['endpoint']}/chat/completions"
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
    elif protocol == "anthropic":
        url = f"{profile['endpoint']}/messages"
        body = {
            "model": model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": case_text}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
    else:
        encoded_model = quote(model, safe="")
        url = f"{profile['endpoint']}/models/{encoded_model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": case_text}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
    serialized = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
    return Request(
        url,
        data=serialized.encode("utf-8"),
        headers=_headers(protocol, api_key),
        method="POST",
    )


def _read_response(response: Any) -> dict[str, Any]:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError) as error:
            raise ProviderError("provider response has an invalid length") from error
        if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
            raise ProviderError("provider response exceeds the configured limit")
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("provider response exceeds the configured limit")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProviderError("provider response is not valid JSON") from error
    if not isinstance(result, dict):
        raise ProviderError("provider response is not an object")
    return result


def _extract_text(protocol: str, result: dict[str, Any]) -> str:
    if protocol == "ollama":
        message = result.get("message")
        text = message.get("content") if isinstance(message, dict) else None
    elif protocol == "openai-compatible":
        choices = result.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
    elif protocol == "anthropic":
        blocks = result.get("content")
        first = blocks[0] if isinstance(blocks, list) and blocks else None
        text = first.get("text") if isinstance(first, dict) else None
    else:
        candidates = result.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else None
        content = first.get("content") if isinstance(first, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            parts = []
        text_parts = [
            part["text"]
            for part in parts
            if isinstance(part, dict)
            and not part.get("thought")
            and isinstance(part.get("text"), str)
        ]
        text = "".join(text_parts) if text_parts else None
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("provider response has no text content")
    return text


def _retry_delay_seconds(retry_after: str | None) -> float:
    """Return a bounded delay before the single retry.

    Honors a provider Retry-After header when present. Retry-After can also
    carry an HTTP date; float() rejects that form and falls back to the
    jittered default rather than parsing a date string.
    """
    if retry_after is not None:
        try:
            parsed = float(retry_after)
        except ValueError:
            parsed = None
        if parsed is not None and parsed >= 0:
            return min(parsed, MAX_RETRY_DELAY_SECONDS)
    return min(
        RETRY_DELAY_SECONDS + random.uniform(0, RETRY_DELAY_JITTER_SECONDS),
        MAX_RETRY_DELAY_SECONDS,
    )


def _request_once(request: Request) -> tuple[dict[str, Any], int | None, str | None]:
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return _read_response(response), None, None
    except HTTPError as error:
        if error.code in RETRYABLE_STATUS_CODES:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            return {}, error.code, retry_after
        message = f"provider request failed with HTTP {error.code}"
        if error.code in CONTEXT_OVERFLOW_STATUS_CODES:
            raise ContextOverflowError(message) from error
        raise ProviderError(message) from error
    except URLError as error:
        raise ProviderError("provider network request failed") from error
    except (OSError, ValueError) as error:
        raise ProviderError("provider response could not be read") from error


def _send(profile: dict[str, Any], system_prompt: str, case_text: str,
          api_key: str) -> str:
    """Return one profile's response, retrying once on a transient status."""
    request = _build_request(profile, system_prompt, case_text, api_key)
    for attempt in range(2):
        result, retry_status, retry_after = _request_once(request)
        if retry_status is None:
            return _extract_text(profile["protocol"], result)
        if attempt == 0:
            time.sleep(_retry_delay_seconds(retry_after))
    raise ProviderError("provider request failed after one retry")


def _send_large(profile: dict[str, Any], system_prompt: str, case_text: str,
                api_key: str) -> str:
    """Return the large profile's response or report a capacity failure.

    No larger model exists. A context rejection here means the review
    exceeds every configured model and needs a recorded NEEDS-HUMAN.
    """
    try:
        return _send(profile, system_prompt, case_text, api_key)
    except ContextOverflowError as error:
        raise InputTooLargeError(
            "review input exceeds the large-input model context"
        ) from error


def call_model(system_prompt: str, mode: str, case_text: str) -> str:
    """Return the configured provider response for one evaluator request."""
    del mode
    if len(system_prompt) + len(case_text) > MAX_INPUT_CHARS:
        raise InputTooLargeError("review input exceeds the configured limit")
    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        raise ProviderError("MODEL_API_KEY is not configured")
    estimated_tokens = estimate_tokens(system_prompt, case_text)
    profile = select_profile(estimated_tokens)
    large = _large_profile()
    if large is not None and large["name"] == profile["name"]:
        return _send_large(profile, system_prompt, case_text, api_key)
    try:
        return _send(profile, system_prompt, case_text, api_key)
    except ContextOverflowError as error:
        # The character estimate can undercount a dense tokenizer. A context
        # rejection gets one attempt on the large-input model. Without one,
        # the review exceeds every configured model.
        if large is None or not _fits(large, estimated_tokens):
            raise InputTooLargeError(
                "review input exceeds every configured model context"
            ) from error
    return _send_large(large, system_prompt, case_text, api_key)


def main() -> int:
    """Run the adapter command used by the workflow."""
    try:
        response = call_model(
            _read_text_from_env("AUDIT_PROMPT_FILE"),
            "PR",
            _read_text_from_env("CASE_TEXT_FILE"),
        )
    except InputTooLargeError as error:
        print(f"model call skipped: {error}", file=sys.stderr)
        return CAPACITY_EXIT_CODE
    except ProviderError as error:
        print(f"model call failed: {error}", file=sys.stderr)
        return 1
    print(response, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
