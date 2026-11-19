"""CLI de triage de Nubbix.

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python -m src.main "me cobraron dos veces la suscripción"
    python -m src.main --stream "no me llega la factura de agosto"
    python -m src.main --prompt v2 "quiero agregar un usuario de solo lectura"
    python -m src.main --gasto

Estado: etapa M1L2. Devuelve la categoría (extraída de la respuesta según el formato de cada
versión del prompt) + métricas de la llamada. El contrato JSON final
{answer, confidence, category, actions} llega con M1L3 (ver docs/ROADMAP.md).
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
from .parsing import metrics_fields, parse_category
from .prompts import PromptError, load_prompt, render_system, render_user

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
    parser.add_argument("--prompt", metavar="VERSION",
                        help="Versión del prompt (ej. v2 o triage_v2). Por defecto, PROMPT_VERSION del .env (M1L2).")
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

    try:
        template = load_prompt(args.prompt or settings.prompt_version, settings.prompts_dir)
    except PromptError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    # M1L2 · Variables de contexto: la plantilla versionada + los datos de este request.
    system = render_system(template)
    user_message = render_user(template, args.consulta)

    providers, warnings = build_providers(settings)
    for w in warnings:
        print(f"Aviso: {w}", file=sys.stderr)
    try:
        for p in providers:
            pricing.price(p.model)  # falla temprano si falta un precio
    except PricingError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return EXIT_CONFIG

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
        resp = router.ask(
            user_message,
            system,
            prompt_version=template.version,
            on_token=on_token,
            extra_fields=lambda r: metrics_fields(r.text),
        )
    except AllProvidersFailedError as exc:
        if args.stream:
            print(file=sys.stderr)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_PROVIDERS

    if args.stream:
        print(file=sys.stderr)

    parsed = parse_category(resp.text)
    if not parsed.valid:
        print(f"Aviso: no se obtuvo una categoría válida (método: {parsed.method}). "
              "El reintento ante respuestas inválidas llega en M1L3.", file=sys.stderr)
    result = {
        "category": parsed.category,
        "category_valid": parsed.valid,
        "parse_method": parsed.method,
        "prompt_version": template.version,
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
    if resp.text.strip() != (parsed.category or ""):
        # Respuesta completa del modelo (ej. el razonamiento de chain-of-thought), para auditar.
        # Solo se muestra en pantalla: nunca se guarda en metrics.jsonl (M1L4).
        result["model_output"] = resp.text.strip()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
