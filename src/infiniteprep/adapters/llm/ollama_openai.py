"""Optional Ollama / OpenAI-compatible closed-world reasoning adapter (Section 7.D)."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.adapters.llm.validator import ClosedWorldValidator
from infiniteprep.config.settings import validate_endpoint_url

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_MODEL: Final[str] = "qwen2.5:3b"
RECOMMENDED_MODELS: Final[tuple[str, ...]] = ("qwen2.5:3b", "qwen2.5:7b", "llama3.2:3b")
MAX_LLM_RESPONSE_BYTES: Final[int] = 262_144

SYSTEM_PROMPT: Final[str] = (
    "You are a strict closed-world DJ music metadata arbitrator. "
    "You may ONLY select a candidate ID and value present in the provided allowed_candidates. "
    "Never invent artists, titles, remixers, labels, catalog numbers, BPM, or keys. "
    "Respond strictly in JSON with keys: selected_candidate_id, proposed_value, rationale."
)


class OllamaOpenAIReasoningAdapter:
    """Queries a local Ollama or OpenAI-compatible endpoint with ClosedWorldValidator."""

    adapter_name: str = "ollama_openai"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = DEFAULT_OLLAMA_MODEL,
        api_key: str | None = None,
        validator: ClosedWorldValidator | None = None,
        http_post_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        """Initialize OllamaOpenAIReasoningAdapter with SSRF-checked base_url."""
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.validator = validator or ClosedWorldValidator()
        self.http_post_json = http_post_json or self._default_http_post_json

    def is_available(self) -> tuple[bool, str | None]:
        """Validate endpoint URL against SSRF rules before allowing requests."""
        return validate_endpoint_url(self.base_url)

    def arbitrate(self, evidence_packet: dict[str, Any]) -> dict[str, Any]:
        """Send evidence_packet to LLM and validate response against allowed_candidates."""
        ready, reason = self.is_available()
        if not ready:
            return {
                "selected_candidate_id": None,
                "proposed_value": None,
                "rationale": reason or "LLM endpoint unavailable.",
            }
        allowed = evidence_packet.get("allowed_candidates", [])
        if not allowed:
            return {
                "selected_candidate_id": None,
                "proposed_value": None,
                "rationale": "No allowed candidates supplied.",
            }

        body = {
            "model": self.model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(evidence_packet, ensure_ascii=True)},
            ],
        }
        try:
            raw_resp = self.http_post_json(f"{self.base_url}/chat/completions", body)
            parsed = self._extract_json_message(raw_resp)
        except Exception as exc:
            logger.warning("LLM arbitration request failed: %s", exc)
            return {
                "selected_candidate_id": None,
                "proposed_value": None,
                "rationale": f"LLM request failed ({exc}); falling back to deterministic scoring.",
            }

        is_valid, err_msg = self.validator.validate_response(parsed, allowed)
        if not is_valid:
            return {
                "selected_candidate_id": None,
                "proposed_value": None,
                "rationale": err_msg or "Discarded hallucinated LLM output.",
            }
        return {
            "selected_candidate_id": parsed.get("selected_candidate_id"),
            "proposed_value": parsed.get("proposed_value"),
            "rationale": str(parsed.get("rationale", "Validated LLM arbitration.")),
        }

    def _extract_json_message(self, raw_resp: dict[str, Any]) -> dict[str, Any]:
        """Extract and parse the JSON content string from OpenAI-compatible choices."""
        choices = raw_resp.get("choices") or [{}]
        msg = choices[0].get("message") or {}
        content = str(msg.get("content", "{}"))
        if len(content) > MAX_LLM_RESPONSE_BYTES:
            raise ValueError("LLM response content exceeded maximum permitted size.")
        return dict(json.loads(content))

    def _default_http_post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send HTTP(S) POST request to validated Ollama or OpenAI endpoint."""
        ok, reason = validate_endpoint_url(url, resolve_dns=True)
        if not ok:
            raise ValueError(reason or "Blocked endpoint URL.")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_bytes = resp.read(MAX_LLM_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_LLM_RESPONSE_BYTES:
                raise ValueError("LLM HTTP response exceeded maximum byte size limit.")
            return dict(json.loads(raw_bytes.decode("utf-8")))
