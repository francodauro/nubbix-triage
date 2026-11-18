"""Construcción de proveedores a partir de la configuración.

Fuente: M0L2 · Patrones creacionales, Factory Method ("en el curso: elegir proveedor de LLM").
"""

from __future__ import annotations

from ..config import Settings
from .base import AuthError, LLMProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


def build_providers(settings: Settings) -> tuple[list[LLMProvider], list[str]]:
    """Devuelve (proveedores listos en el orden configurado, avisos).

    Un proveedor sin API key se omite con un aviso, así el otro sigue funcionando.
    """
    providers: list[LLMProvider] = []
    warnings: list[str] = []
    for name in settings.provider_order:
        cfg = settings.providers[name]
        kwargs = {
            "api_key": cfg.api_key,
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "temperature": settings.temperature,
            "timeout_s": settings.timeout_seconds,
        }
        if name == "openai":
            kwargs["reasoning_effort"] = cfg.reasoning_effort
        try:
            providers.append(_PROVIDER_CLASSES[name](**kwargs))
        except AuthError as exc:
            warnings.append(f"Se omite {name}: {exc}")
    return providers, warnings
