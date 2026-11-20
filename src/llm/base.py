"""Contrato común para todos los proveedores de LLM.

Fuentes:
- M0L1 · "Patrón clave del curso: abstracción cliente de LLM": todos los proveedores
  derivan de la misma abstracción y devuelven el mismo tipo de resultado (Liskov).
- M1L1 · Familias de modelos: "una única función ask(provider, prompt) que devuelve
  siempre el mismo objeto (LLMResponse)".
- M1L1 · Rate limits: 429 y 5xx se reintentan; Retry-After tiene prioridad.
- M0L2 · Excepciones propias para errores tipados.
- M1L3 · Tool use / function calling: ``ask_tool()`` devuelve siempre un ``ToolResponse`` con los
  argumentos como dict, aunque OpenAI los entregue como string JSON y Gemini (como Anthropic) como
  dict. Acá eso se resuelve con el mismo ``LLMResponse`` + ``tool_args`` (D-57).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

# Callback que recibe cada fragmento de texto en modo streaming.
TokenCallback = Callable[[str], None]


@dataclass(frozen=True)
class ToolSpec:
    """Herramienta (función) que se obliga al modelo a llamar (M1L3 · Tool use).

    La función no existe en Python: es un formulario que el modelo tiene que llenar. ``parameters`` es el
    JSON Schema de sus argumentos y sale del contrato Pydantic (``model_json_schema()``), así el schema
    no se escribe dos veces.
    """

    name: str
    description: str
    parameters: dict[str, Any]


# Por qué ``tool_args`` quedó en None (se registra en métricas y se le explica al modelo al reintentar).
TOOL_NO_CALL = "sin_tool_call"        # el modelo respondió texto en vez de llamar a la función
TOOL_BAD_JSON = "json_invalido"       # OpenAI: arguments no es JSON válido (ej. cortado por el tope de tokens)
TOOL_NOT_OBJECT = "args_no_objeto"    # el JSON es válido pero no es un objeto {...}


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
    # Solo con tool calling (M1L3): argumentos ya convertidos a dict, o None si no se pudieron leer.
    # En ese caso ``tool_error`` dice por qué (TOOL_NO_CALL, TOOL_BAD_JSON, TOOL_NOT_OBJECT) y
    # ``text`` guarda lo que llegó, para poder explicárselo al modelo en el reintento.
    tool_args: Optional[dict[str, Any]] = None
    tool_error: Optional[str] = None


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
    def complete(
        self, prompt: str, system: str, *, on_token: TokenCallback | None = None, tool: ToolSpec | None = None
    ) -> LLMResponse:
        """Envía ``prompt`` con instrucciones ``system`` y devuelve un ``LLMResponse``.

        Si ``on_token`` no es None, usa streaming y llama a ``on_token`` con cada fragmento.
        Si ``tool`` no es None, obliga al modelo a llamar a esa función (M1L3) y completa ``tool_args``
        con un dict, o lo deja en None con el motivo en ``tool_error``. No valida el contenido: eso lo
        hace el contrato Pydantic. Con ``tool`` no se usa streaming.
        Debe traducir los errores del SDK a subclases de ``ProviderError``.
        ``cost_usd`` lo completa el router con ``config/pricing.json``.
        """
