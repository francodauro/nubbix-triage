"""Contrato común para todos los proveedores de LLM.

Fuentes:
- M0L1 · "Patrón clave del curso: abstracción cliente de LLM": todos los proveedores
  derivan de la misma abstracción y devuelven el mismo tipo de resultado (Liskov).
- M1L1 · Familias de modelos: "una única función ask(provider, prompt) que devuelve
  siempre el mismo objeto (LLMResponse)".
- M1L1 · Rate limits: 429 y 5xx se reintentan; Retry-After tiene prioridad.
- M0L2 · Excepciones propias para errores tipados.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

# Callback que recibe cada fragmento de texto en modo streaming.
TokenCallback = Callable[[str], None]


@dataclass
class LLMResponse:
    """Respuesta normalizada: el resto del sistema no sabe qué proveedor respondió."""

    provider: str
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_s: float
    cost_usd: float = 0.0
    finish_reason: Optional[str] = None
    # Solo en streaming: segundos hasta el primer fragmento de texto (TTFT, M1L1 · Streaming).
    ttft_s: Optional[float] = None


# --------------------------------------------------------------------------- errores


class ProviderError(Exception):
    """Falla de un proveedor. ``retryable`` indica si tiene sentido reintentar."""

    kind = "provider_error"
    retryable = False

    def __init__(self, provider: str, message: str, *, retry_after: float | None = None, partial_output: bool = False):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retry_after = retry_after
        # True si en streaming ya se mostró texto al usuario: no se puede reintentar ni pasar
        # a otro proveedor sin duplicar la salida.
        self.partial_output = partial_output


class RateLimitError(ProviderError):
    """429: el proveedor pide esperar (M1L1: backoff exponencial con jitter, respetar Retry-After)."""

    kind = "rate_limit"
    retryable = True


class QuotaExhaustedError(ProviderError):
    """429 sin saldo / cuota agotada: esperar no lo resuelve, se pasa al otro proveedor."""

    kind = "quota_exhausted"
    retryable = False


class ServerError(ProviderError):
    """5xx: error interno del proveedor (M1L1: reintentos limitados)."""

    kind = "server_error"
    retryable = True


class ProviderTimeoutError(ProviderError):
    kind = "timeout"
    retryable = True


class ProviderConnectionError(ProviderError):
    kind = "connection_error"
    retryable = True


class AuthError(ProviderError):
    """401/403 o API key faltante: reintentar no sirve."""

    kind = "auth_error"


class BadRequestError(ProviderError):
    """4xx por request mal formada (ej. modelo inexistente): reintentar no sirve."""

    kind = "bad_request"


class EmptyResponseError(ProviderError):
    """El proveedor respondió sin texto (ej. se agotó max_tokens pensando)."""

    kind = "empty_response"


class BudgetExceededError(Exception):
    """Se alcanzó el tope de gasto (BUDGET_USD): no se llama al proveedor pago."""


class AllProvidersFailedError(Exception):
    """Ningún proveedor pudo responder."""

    def __init__(self, errors: list[Exception]):
        detail = "; ".join(str(e) for e in errors)
        super().__init__(f"Todos los proveedores fallaron: {detail}")
        self.errors = errors


# --------------------------------------------------------------------------- abstracción


class LLMProvider(ABC):
    """Abstracción de un proveedor. Sumar un proveedor = sumar una subclase (M0L1: Abierto/Cerrado)."""

    name: str = "base"

    def __init__(self, model: str, max_tokens: int, temperature: float, timeout_s: float):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_s = timeout_s

    @abstractmethod
    def complete(self, prompt: str, system: str, *, on_token: TokenCallback | None = None) -> LLMResponse:
        """Envía ``prompt`` con instrucciones ``system`` y devuelve un ``LLMResponse``.

        Si ``on_token`` no es None, usa streaming y llama a ``on_token`` con cada fragmento.
        Debe traducir los errores del SDK a subclases de ``ProviderError``.
        ``cost_usd`` lo completa el router con ``config/pricing.json``.
        """
