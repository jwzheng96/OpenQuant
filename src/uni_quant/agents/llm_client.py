"""LLM client abstraction — DeepSeek primary, Qwen / Claude / GLM pluggable.

DeepSeek's API is OpenAI-compatible:
  POST https://api.deepseek.com/v1/chat/completions
  Authorization: Bearer {token}

We use httpx (already a dep) instead of pulling openai SDK to keep deps lean.
Auto-retry with exponential backoff on transient failures (429 / 5xx / timeout).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from uni_quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class LLMResponse:
    text: str
    raw: dict[str, Any]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    model: str = ""


class LLMClient(Protocol):
    """Minimal LLM interface used by agents."""

    name: str
    model: str

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------- #
# DeepSeek (OpenAI-compatible)                                                 #
# ---------------------------------------------------------------------------- #


class DeepSeekClient:
    """DeepSeek API client. Uses the OpenAI Chat Completions schema.

    Model names tested:
      - deepseek-chat (V3, general purpose, fast)
      - deepseek-reasoner (R1, slow but stronger reasoning)
      - deepseek-v4-flash (newest, if your account supports it)
    """

    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ):
        # Load config as base, then explicit args override
        cfg_api_key = None
        cfg_model = None
        cfg_base_url = None
        cfg_timeout = None
        try:
            from uni_quant.utils import load_settings
            cfg = load_settings().data_sources.deepseek
            cfg_api_key = cfg.api_key or None
            cfg_model = cfg.model or None
            cfg_base_url = cfg.base_url or None
            cfg_timeout = cfg.timeout
        except Exception:
            pass

        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or cfg_api_key
        if not self.api_key or self.api_key.startswith("REPLACE_"):
            raise RuntimeError(
                "DEEPSEEK_API_KEY not found. Set env var or add to "
                "configs/data_sources.yaml under `deepseek.api_key`"
            )
        self.model = model or cfg_model or "deepseek-chat"
        self.base_url = (base_url or cfg_base_url or "https://api.deepseek.com/v1").rstrip("/")
        self.timeout = timeout or cfg_timeout or 60.0
        self._client = httpx.Client(timeout=self.timeout)
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._n_calls = 0

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError)),
    )
    def _post(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        r = self._client.post(url, headers=headers, json=payload)
        if r.status_code == 429:
            log.warning("DeepSeek rate-limited; backing off")
            raise httpx.HTTPStatusError("rate limited", request=r.request, response=r)
        if r.status_code >= 500:
            raise httpx.HTTPStatusError(f"server {r.status_code}", request=r.request, response=r)
        if r.status_code >= 400:
            # 4xx other than 429 — usually permission/quota/bad request, don't retry
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise RuntimeError(f"DeepSeek API error {r.status_code}: {detail}")
        return r.json()

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        t0 = time.time()
        raw = self._post(payload)
        elapsed = time.time() - t0

        choice = (raw.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content", "")
        finish = choice.get("finish_reason", "")
        usage = raw.get("usage") or {}
        pt = int(usage.get("prompt_tokens", 0))
        ct = int(usage.get("completion_tokens", 0))
        tt = int(usage.get("total_tokens", pt + ct))

        self._total_prompt_tokens += pt
        self._total_completion_tokens += ct
        self._n_calls += 1

        log.debug(f"DeepSeek {self.model} {elapsed:.1f}s pt={pt} ct={ct}")
        return LLMResponse(
            text=text, raw=raw,
            prompt_tokens=pt, completion_tokens=ct, total_tokens=tt,
            finish_reason=finish, model=self.model,
        )

    def stats(self) -> dict[str, int]:
        return {
            "n_calls": self._n_calls,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
        }

    def close(self):
        self._client.close()


# ---------------------------------------------------------------------------- #
# Factory                                                                      #
# ---------------------------------------------------------------------------- #


def build_llm_client(provider: str = "deepseek", model: str | None = None, **kwargs) -> LLMClient:
    """Resolve provider name → concrete client instance."""
    if provider == "deepseek":
        return DeepSeekClient(model=model or "deepseek-chat", **kwargs)
    # Stubs for future providers — easy to add Claude / Qwen / GLM later
    raise ValueError(f"unsupported LLM provider: {provider}. Current options: deepseek")
