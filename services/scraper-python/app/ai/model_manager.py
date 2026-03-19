from __future__ import annotations

import threading
from typing import Any

import httpx

from .config import AI_MAX_NEW_TOKENS, AI_MODEL_ID, AI_OLLAMA_BASE_URL, AI_TOP_P, AI_TEMPERATURE


class ModelUnavailableError(RuntimeError):
    pass


class OllamaModelManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._load_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "modelId": AI_MODEL_ID,
            "provider": "ollama",
            "baseUrl": AI_OLLAMA_BASE_URL,
            "loaded": self._loaded,
            "available": self._load_error is None,
            "error": self._load_error,
        }

    def ensure_loaded(self) -> None:
        if self._load_error:
            raise ModelUnavailableError(self._load_error)
        with self._lock:
            if self._load_error:
                raise ModelUnavailableError(self._load_error)
            try:
                response = httpx.get(f"{AI_OLLAMA_BASE_URL}/api/tags", timeout=10.0)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover - depends on local env
                self._load_error = f"Failed to reach Ollama: {exc}"
                raise ModelUnavailableError(self._load_error) from exc
            models = {str(item.get("name", "")).strip() for item in payload.get("models", [])}
            if AI_MODEL_ID not in models:
                self._load_error = f"Ollama model '{AI_MODEL_ID}' is not installed."
                raise ModelUnavailableError(self._load_error)

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        self.ensure_loaded()
        try:
            response = httpx.post(
                f"{AI_OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": AI_MODEL_ID,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": AI_TEMPERATURE,
                        "top_p": AI_TOP_P,
                        "num_predict": AI_MAX_NEW_TOKENS,
                    },
                },
                timeout=None,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # pragma: no cover - depends on local env
            raise ModelUnavailableError(f"Ollama generate failed: {exc}") from exc
        content = str(payload.get("response", "")).strip()
        if not content:
            raise ModelUnavailableError("Ollama returned an empty response.")
        self._loaded = True
        return content


model_manager = OllamaModelManager()
