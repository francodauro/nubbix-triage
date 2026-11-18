"""Proveedor Google Gemini (gratuito): gemini-3.5-flash-lite por defecto.

Gemini no figura en los ejemplos de código de Henry (que usan OpenAI y Anthropic),
pero M1L1 lo menciona en la tabla "Familias de modelos y sus APIs" y en el cierre
("saber que Gemini funciona bajo la misma lógica"), y la consigna del PI pide
">=2 proveedores configurables (por ejemplo OpenAI y Anthropic)".
Ver docs/DECISIONES.md, decisión D-03.

Como en M1L1 con Anthropic, Google no expone un tokenizer local: los tokens se leen
del ``usage_metadata`` de la respuesta, después de la llamada.
"""

from __future__ import annotations

import re
import time
from typing import Any

from google import genai
from google.genai import errors, types

from .base import (
    AuthError,
    BadRequestError,
    EmptyResponseError,
    LLMProvider,
    LLMResponse,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    ServerError,
    TokenCallback,
)


def _find_retry_delay(obj: Any) -> float | None:
    """Busca ``retryDelay`` (ej. "34s") dentro del JSON de error de Google (google.rpc.RetryInfo)."""
    if isinstance(obj, dict):
        value = obj.get("retryDelay")
        if isinstance(value, str):
            match = re.match(r"^\s*([\d.]+)s\s*$", value)
            if match:
                return float(match.group(1))
        for child in obj.values():
            found = _find_retry_delay(child)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for child in obj:
            found = _find_retry_delay(child)
            if found is not None:
                return found
    return None


def _retry_after(exc: errors.APIError) -> float | None:
    """Prioriza el header Retry-After; si no está, usa el retryDelay del cuerpo del error."""
    try:
        header = exc.response.headers.get("retry-after") if exc.response is not None else None
        if header is not None:
            return float(header)
    except (AttributeError, ValueError):
        pass
    return _find_retry_delay(getattr(exc, "details", None))


def _usage(meta: Any) -> tuple[int, int, int]:
    """Devuelve (input, output, total). Los tokens de "pensamiento" se cuentan como salida."""
    if meta is None:
        return 0, 0, 0
    prompt = meta.prompt_token_count or 0
    output = (meta.candidates_token_count or 0) + (getattr(meta, "thoughts_token_count", None) or 0)
    total = meta.total_token_count or prompt + output
    return prompt, output, total


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, max_tokens: int, temperature: float, timeout_s: float):
        super().__init__(model, max_tokens, temperature, timeout_s)
        if not api_key:
            raise AuthError(self.name, "Falta GEMINI_API_KEY en el .env")
        # timeout en milisegundos. Sin retry_options: los reintentos los maneja src/retry.py.
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    def complete(self, prompt: str, system: str, *, on_token: TokenCallback | None = None) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        start = time.perf_counter()
        emitted = False
        ttft: float | None = None
        try:
            if on_token is None:
                resp = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
                text = resp.text or ""
                meta = resp.usage_metadata
                finish = _finish_reason(resp)
            else:
                parts: list[str] = []
                meta, finish = None, None
                for chunk in self._client.models.generate_content_stream(model=self.model, contents=prompt, config=config):
                    piece = chunk.text or ""
                    if piece:
                        if ttft is None:
                            ttft = time.perf_counter() - start  # TTFT: primer fragmento
                        parts.append(piece)
                        on_token(piece)
                        emitted = True
                    if chunk.usage_metadata is not None:  # el usage completo llega al final del stream
                        meta = chunk.usage_metadata
                    finish = _finish_reason(chunk) or finish
                text = "".join(parts)
        except errors.APIError as exc:
            code = exc.code or 0
            if code == 429:
                raise RateLimitError(self.name, exc.message or str(exc), retry_after=_retry_after(exc), partial_output=emitted) from exc
            if code in (401, 403):
                raise AuthError(self.name, exc.message or str(exc)) from exc
            if code >= 500:
                raise ServerError(self.name, exc.message or str(exc), retry_after=_retry_after(exc), partial_output=emitted) from exc
            if 400 <= code < 500:
                raise BadRequestError(self.name, exc.message or str(exc)) from exc
            raise ProviderError(self.name, str(exc)) from exc
        except Exception as exc:  # errores de red del cliente HTTP (httpx)
            name = type(exc).__name__.lower()
            if "timeout" in name:
                raise ProviderTimeoutError(self.name, str(exc), partial_output=emitted) from exc
            if "connect" in name or "network" in name:
                raise ProviderConnectionError(self.name, str(exc), partial_output=emitted) from exc
            raise

        latency = time.perf_counter() - start
        if not text.strip():
            raise EmptyResponseError(self.name, f"Respuesta vacía (finish_reason={finish})")

        input_tokens, output_tokens, total = _usage(meta)
        return LLMResponse(
            provider=self.name,
            model=self.model,
            text=text.strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            latency_s=latency,
            finish_reason=finish,
            ttft_s=ttft,
        )


def _finish_reason(resp: Any) -> str | None:
    try:
        reason = resp.candidates[0].finish_reason
        return getattr(reason, "name", None) or (str(reason) if reason is not None else None)
    except (AttributeError, IndexError, TypeError):
        return None
