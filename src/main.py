"""CLI de triage de Nubbix.

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python -m src.main "me cobraron dos veces la suscripción"
    python -m src.main --stream "no me llega la factura de agosto"
    python -m src.main --gasto

Estado: etapa M1L1. Devuelve la categoría como texto + métricas de la llamada.
El contrato JSON final {answer, confidence, category, actions} llega con M1L3
(ver docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigError, load_settings
from .costs import Pricing, PricingError, project_cost
from .llm.base import AllProvidersFailedError
from .llm.factory import build_providers
from .llm.router import Router
from .metrics import MetricsLog

PROMPT_VERSION = "triage_v1"

# Códigos de salida de la CLI
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_PROVIDERS = 3


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Triage de consultas de soporte de Nubbix.")
    parser.add_argument("consulta", nargs="?", help="Texto de la consulta del cliente (entre comillas).")
    parser.add_argument("--stream", action="store_true", help="Muestra la respuesta a medida que se genera (M1L1 · Streaming).")
    parser.add_argument("--gasto", action="store_true",
                        help="Muestra el gasto acumulado, el presupuesto y la proyección de costo, y termina.")
    parser.add_argument("--consultas", type=int, default=10_000,
                        help="Cantidad de consultas para la proyección de --gasto (por defecto 10000, como en M1L1).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings()
        pricing = Pricing(settings.pricing_path)
    except (ConfigError, PricingError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    metrics = MetricsLog(settings.metrics_path)

    if args.gasto:
        if args.consultas <= 0:
            print("Error: --consultas debe ser mayor que 0.", file=sys.stderr)
            return EXIT_CONFIG
        spent = metrics.total_cost()
        print(json.dumps({
            "gastado_usd": round(spent, 6),
            "presupuesto_usd": settings.budget_usd,
            "restante_usd": round(settings.budget_usd - spent, 6),
            "proyeccion": project_cost(metrics.read_all(), pricing, args.consultas),
        }, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not args.consulta or not args.consulta.strip():
        print("Error: la consulta no puede estar vacía.", file=sys.stderr)
        return EXIT_CONFIG

    providers, warnings = build_providers(settings)
    for w in warnings:
        print(f"Aviso: {w}", file=sys.stderr)
    try:
        for p in providers:
            pricing.price(p.model)  # falla temprano si falta un precio
    except PricingError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    system = (settings.prompts_dir / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    def warn_failure(provider_name: str, exc: Exception) -> None:
        kind = getattr(exc, "kind", "budget_exceeded")
        print(f"Aviso: falló {provider_name} ({kind}): {exc}", file=sys.stderr)

    router = Router(
        providers,
        pricing,
        metrics,
        budget_usd=settings.budget_usd,
        max_retries=settings.max_retries,
        max_wait_s=settings.max_wait_seconds,
        on_failure=warn_failure,
    )

    on_token = None
    if args.stream:
        def on_token(piece: str) -> None:
            print(piece, end="", flush=True, file=sys.stderr)

    try:
        resp = router.ask(args.consulta.strip(), system, prompt_version=PROMPT_VERSION, on_token=on_token)
    except AllProvidersFailedError as exc:
        if args.stream:
            print(file=sys.stderr)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_PROVIDERS

    if args.stream:
        print(file=sys.stderr)

    result = {
        "category": resp.text,
        "provider": resp.provider,
        "model": resp.model,
        "prompt_tokens": resp.input_tokens,
        "completion_tokens": resp.output_tokens,
        "total_tokens": resp.total_tokens,
        "latency_s": round(resp.latency_s, 3),
        "cost_usd": round(resp.cost_usd, 8),
    }
    if resp.ttft_s is not None:
        result["ttft_s"] = round(resp.ttft_s, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
