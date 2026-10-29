"""Consent-gated Ollama detection, official installer execution, and model pull service."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Final

OLLAMA_TAGS_URL: Final[str] = "http://127.0.0.1:11434/api/tags"


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
            with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                return [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
        except Exception:
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
