"""Proveedor OpenAI (pago, económico): gpt-6-luna por defecto.

M1L1 usaba gpt-4.1-mini / gpt-4.1-nano, pero OpenAI dio de baja gpt-4.1-nano
(error 404 el 18/11/2026). gpt-6-luna es el reemplazo recomendado por OpenAI; es un
modelo de razonamiento y se usa con ``reasoning_effort="none"`` (ver docs/DECISIONES.md, D-30 a D-32).

Fuentes: M1L1 · Familias de modelos (Hello World con ``chat.completions.create``),
Streaming (``stream_options={"include_usage": True}``; sin esto el usage llega vacío),
Costo por token (los tokens salen del campo ``usage``).

Tool calling (M1L3 · Tool use, D-57): ``tools`` + ``tool_choice`` con el nombre de la función obligan
al modelo a llamarla. OpenAI entrega ``arguments`` como **string JSON**: hay que hacer ``json.loads``, que
puede fallar si llegó cortado (por ejemplo por el tope de tokens). Opcional ``strict`` (D-59): OpenAI
restringe la generación al schema; según su documentación no es compatible con llamadas en paralelo, por
eso con ``strict`` se envía ``parallel_tool_calls=False``.
"""

from __future__ import annotations

import json
import time

import openai

from .base import (
    AuthError,
    BadRequestError,
    EmptyResponseError,
    LLMProvider,
    LLMResponse,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    QuotaExhaustedError,
    RateLimitError,
    ServerError,
    TOOL_BAD_JSON,
    TOOL_NO_CALL,
    TOOL_NOT_OBJECT,
    TokenCallback,
    ToolSpec,
)


def _retry_after_seconds(exc: openai.APIStatusError) -> float | None:
    """Lee el header Retry-After (en segundos) si el proveedor lo mandó."""
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, ValueError):
        return None


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout_s: float,
        reasoning_effort: str = "",
        strict_tools: bool = False,
    ):
        super().__init__(model, max_tokens, temperature, timeout_s)
        if not api_key:
            raise AuthError(self.name, "Falta OPENAI_API_KEY en el .env")
        self.reasoning_effort = reasoning_effort
        self.strict_tools = strict_tools
        # max_retries=0: los reintentos los maneja src/retry.py (con la política de M1L1),
        # así no se suman los reintentos internos del SDK.
        self._client = openai.OpenAI(api_key=api_key, max_retries=0, timeout=timeout_s)

    def _tool_params(self, tool: ToolSpec) -> dict:
        function = {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        params = {
            "tools": [{"type": "function", "function": function}],
            # Obliga a llamar a ESTA función (equivale a mode="ANY" de Gemini).
            "tool_choice": {"type": "function", "function": {"name": tool.name}},
        }
        if self.strict_tools:
            function["strict"] = True
            params["parallel_tool_calls"] = False
        return params

    def complete(
        self, prompt: str, system: str, *, on_token: TokenCallback | None = None, tool: ToolSpec | None = None
    ) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        # max_completion_tokens: el parámetro vigente (los modelos de razonamiento no aceptan max_tokens).
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        start = time.perf_counter()
        emitted = False
        ttft: float | None = None
        tool_args: dict | None = None
        tool_error: str | None = None
        try:
            if tool is not None:
                resp = self._client.chat.completions.create(**params, **self._tool_params(tool))
                message = resp.choices[0].message
                finish = resp.choices[0].finish_reason
                usage = resp.usage
                calls = message.tool_calls or []
                if calls:
                    text = calls[0].function.arguments or ""  # string JSON (M1L3: OpenAI → string)
                    tool_args, tool_error = _parse_arguments(text)
                else:
                    text = message.content or ""
                    tool_error = TOOL_NO_CALL
            elif on_token is None:
                resp = self._client.chat.completions.create(**params)
                text = resp.choices[0].message.content or ""
                finish = resp.choices[0].finish_reason
                usage = resp.usage
            else:
                stream = self._client.chat.completions.create(
                    **params,
                    stream=True,
                    stream_options={"include_usage": True},  # sin esto, usage llega vacío (M1L1)
                )
                parts: list[str] = []
                finish, usage = None, None
                for chunk in stream:
                    if chunk.choices:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            if ttft is None:
                                ttft = time.perf_counter() - start  # TTFT: primer fragmento
                            parts.append(delta)
                            on_token(delta)
                            emitted = True
                        finish = chunk.choices[0].finish_reason or finish
                    if chunk.usage is not None:  # el último chunk trae el usage
                        usage = chunk.usage
                text = "".join(parts)
        except openai.RateLimitError as exc:
            body = getattr(exc, "body", None) or {}
            code = body.get("code") if isinstance(body, dict) else None
            if code == "insufficient_quota":
                raise QuotaExhaustedError(self.name, "Sin saldo en la cuenta de OpenAI (insufficient_quota)") from exc
            raise RateLimitError(self.name, str(exc), retry_after=_retry_after_seconds(exc), partial_output=emitted) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise AuthError(self.name, str(exc)) from exc
        except (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError) as exc:
            raise BadRequestError(self.name, str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(self.name, str(exc), partial_output=emitted) from exc
        except openai.APIConnectionError as exc:
            raise ProviderConnectionError(self.name, str(exc), partial_output=emitted) from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ServerError(self.name, str(exc), retry_after=_retry_after_seconds(exc), partial_output=emitted) from exc
            raise ProviderError(self.name, str(exc)) from exc

        latency = time.perf_counter() - start
        if tool is None and not text.strip():
            raise EmptyResponseError(self.name, f"Respuesta vacía (finish_reason={finish})")

        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        return LLMResponse(
            provider=self.name,
            model=self.model,
            text=text.strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=usage.total_tokens if usage else input_tokens + output_tokens,
            latency_s=latency,
            finish_reason=finish,
            ttft_s=ttft,
            tool_args=tool_args,
            tool_error=tool_error,
        )


def _parse_arguments(raw: str) -> tuple[dict | None, str | None]:
    """Convierte el string JSON de OpenAI en dict. Devuelve (args, None) o (None, motivo)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, TOOL_BAD_JSON
    if not isinstance(data, dict):
        return None, TOOL_NOT_OBJECT
    return data, None
