"""Consent-gated Ollama detection, official installer execution, and model pull service."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Final

OLLAMA_TAGS_URL: Final[str] = "http://127.0.0.1:11434/api/tags"
# A model reference is [host/][namespace/]model:tag, e.g. hf.co/user/model:q4.
# Each slash-separated segment and the tag must start with a letter or digit.
# The CLI parses an argument as an option only when it starts with a hyphen.
# The leading character rule also excludes empty, "." and ".." segments.
# Each segment allows 64 characters. Both bounds exceed published model names.
MODEL_REFERENCE_PART_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_MODEL_NAME_SEGMENTS: Final[int] = 3


def is_safe_model_reference(model_name: str) -> bool:
    """Return True when model_name is a name:tag reference with safe path segments."""
    name, separator, tag = model_name.partition(":")
    if not separator:
        return False
    segments = name.split("/")
    if len(segments) > MAX_MODEL_NAME_SEGMENTS:
        return False
    return all(MODEL_REFERENCE_PART_RE.fullmatch(part) for part in [*segments, tag])


class OllamaBootstrapService:
    """Detects local Ollama installation and performs consent-gated installation/model pull."""

    def __init__(
        self,
        runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Initialize OllamaBootstrapService with optional subprocess test runner."""
        self.runner = runner

    def is_ollama_installed(self) -> bool:
        """Return True if the ollama CLI binary is present on PATH."""
        return shutil.which("ollama") is not None

    def list_installed_models(self) -> list[str]:
        """Query local Ollama daemon /api/tags and return installed model names."""
        req = urllib.request.Request(OLLAMA_TAGS_URL)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
        except (OSError, ValueError):
            return []

    def install_ollama(self, *, user_consented: bool) -> str:
        """Install Ollama using official OS package manager only when user_consented is True."""
        if not user_consented:
            raise PermissionError(
                "Explicit user consent is required before installing Ollama; "
                "pass user_consented=True after user approval."
            )
        sys_name = platform.system().lower()
        if sys_name.startswith("win"):
            cmd = [
                "winget",
                "install",
                "--id",
                "Ollama.Ollama",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ]
        elif sys_name == "darwin":
            cmd = ["brew", "install", "--cask", "ollama"]
        else:
            return "On Linux, install Ollama from https://ollama.com/download"
        completed = self._exec_array(cmd)
        return completed.stdout

    def pull_model(self, model_name: str, *, user_consented: bool) -> str:
        """Pull an Ollama model (e.g., qwen2.5:3b) only when user_consented is True."""
        if not is_safe_model_reference(model_name):
            raise ValueError(
                f"Invalid Ollama model name {model_name!r}. Use [host/][namespace/]model:tag. "
                "Start each part with a letter or digit and keep it within 64 characters."
            )
        if not user_consented:
            raise PermissionError(
                f"Explicit user consent is required before pulling Ollama model '{model_name}'."
            )
        completed = self._exec_array(["ollama", "pull", model_name])
        return completed.stdout

    def _exec_array(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Execute command array safely with shell=False."""
        if self.runner is not None:
            return self.runner(cmd)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
            shell=False,
        )

    def resolve_ollama_path(self) -> Path | None:
        """Return resolved Path to ollama executable if installed."""
        found = shutil.which("ollama")
        return Path(found) if found else None
