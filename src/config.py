"""Carga de configuración desde variables de entorno (.env).

Fuente: M0L2 (python-dotenv: ``load_dotenv()`` + ``os.getenv``) y M1L1
(las API keys y los modelos por defecto viven en el ``.env``).
Todo llega como texto, así que se castea explícitamente (M0L2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto: la carpeta que contiene src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """La configuración es inválida o incompleta (error del usuario, no se reintenta)."""


@dataclass(frozen=True)
class ProviderSettings:
    """Configuración de un proveedor de LLM."""

    name: str
    api_key: str
    model: str
    max_tokens: int
    # Solo OpenAI: esfuerzo de razonamiento ("none", "low"…). Vacío = no se envía el parámetro.
    reasoning_effort: str = ""
    # Solo OpenAI: tool calling con strict=True (la generación se restringe al schema, D-59).
    strict_tools: bool = False


@dataclass(frozen=True)
class Settings:
    """Configuración completa del servicio."""

    providers: dict[str, ProviderSettings]
    provider_order: list[str]
    temperature: float
    max_retries: int
    max_wait_seconds: float
    timeout_seconds: float
    budget_usd: float
    pricing_path: Path
    metrics_path: Path
    prompts_dir: Path
    # Versión del prompt por defecto (prompts/<version>.yaml); la CLI la pisa con --prompt (D-43, D-48).
    # triage_v6 desde M1L3 (D-69): la única que produce el contrato del PI. Provisional hasta M1L5.
    prompt_version: str = "triage_v6"
    # M1L3: intentos para obtener un contrato válido (el primero + reintentos con el error inyectado, D-60).
    contract_max_attempts: int = 3


def _get(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _get_float(name: str, default: float) -> float:
    raw = _get(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número, llegó: {raw!r}") from exc


def _get_int(name: str, default: int) -> int:
    raw = _get(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero, llegó: {raw!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name, "true" if default else "false").lower()
    if raw in ("1", "true", "si", "sí", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    raise ConfigError(f"{name} debe ser true o false, llegó: {raw!r}")


def load_settings(env_file: Path | None = None) -> Settings:
    """Lee el ``.env`` de la raíz del proyecto y devuelve la configuración validada."""
    load_dotenv(env_file or PROJECT_ROOT / ".env")

    providers = {
        "gemini": ProviderSettings(
            name="gemini",
            api_key=_get("GEMINI_API_KEY", ""),
            model=_get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            max_tokens=_get_int("GEMINI_MAX_TOKENS", 1024),
        ),
        "openai": ProviderSettings(
            name="openai",
            api_key=_get("OPENAI_API_KEY", ""),
            model=_get("OPENAI_MODEL", "gpt-6-luna"),
            # 400 desde M1L3 (D-65): ahora el modelo escribe una respuesta además de la categoría y, si los
            # argumentos de la tool call se cortan, el JSON queda inválido. Solo se paga lo que se usa.
            max_tokens=_get_int("OPENAI_MAX_TOKENS", 400),
            reasoning_effort=_get("OPENAI_REASONING_EFFORT", "none").lower(),
            strict_tools=_get_bool("OPENAI_STRICT_TOOLS", True),
        ),
    }

    order = [p.strip().lower() for p in _get("LLM_PROVIDER_ORDER", "gemini,openai").split(",") if p.strip()]
    unknown = [p for p in order if p not in providers]
    if unknown:
        raise ConfigError(f"LLM_PROVIDER_ORDER tiene proveedores desconocidos: {unknown}. Válidos: {list(providers)}")
    if len(order) < 2:
        raise ConfigError("LLM_PROVIDER_ORDER debe tener al menos 2 proveedores (consigna del PI).")

    configured = [p for p in order if providers[p].api_key]
    if not configured:
        raise ConfigError("No hay ninguna API key cargada. Completá GEMINI_API_KEY y/o OPENAI_API_KEY en el archivo .env")

    temperature = _get_float("LLM_TEMPERATURE", 0.0)
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError("LLM_TEMPERATURE debe estar entre 0.0 y 2.0")

    contract_max_attempts = _get_int("CONTRACT_MAX_ATTEMPTS", 3)
    if contract_max_attempts < 1:
        raise ConfigError("CONTRACT_MAX_ATTEMPTS debe ser al menos 1")

    return Settings(
        providers=providers,
        provider_order=order,
        temperature=temperature,
        max_retries=_get_int("LLM_MAX_RETRIES", 2),
        max_wait_seconds=_get_float("LLM_MAX_WAIT_SECONDS", 10.0),
        timeout_seconds=_get_float("LLM_TIMEOUT_SECONDS", 30.0),
        budget_usd=_get_float("BUDGET_USD", 4.50),
        pricing_path=PROJECT_ROOT / "config" / "pricing.json",
        metrics_path=PROJECT_ROOT / "data" / "metrics.jsonl",
        prompts_dir=PROJECT_ROOT / "prompts",
        prompt_version=_get("PROMPT_VERSION", "triage_v6"),
        contract_max_attempts=contract_max_attempts,
    )
