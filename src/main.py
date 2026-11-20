"""CLI de triage de Nubbix.

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python -m src.main "me cobraron dos veces la suscripción"
    python -m src.main --detalle "no me llega la factura de agosto"
    python -m src.main --prompt v2 "quiero agregar un usuario de solo lectura"
    python -m src.main --gasto

Con un prompt de salida "contrato" (triage_v6 en adelante, M1L3) la salida estándar es **solo** el
contrato del PI, listo para que otro sistema lo consuma sin transformaciones:
    {"answer": "...", "confidence": 0.9, "category": "billing", "actions": ["review_charge"]}
Los avisos, errores y el detalle (``--detalle``: tokens, costo, intentos) van a la salida de errores.

Con un prompt de salida "texto" (v1…v5, M1L2) la CLI muestra la categoría parseada y las métricas, como
en M1L2: esas versiones se conservan para comparar, pero no producen el contrato.

Códigos de salida (M1L3 · Errores tipados, D-62; entre paréntesis, el equivalente HTTP de la lección):
    0  OK (200)
    2  consulta inválida, por ejemplo vacía: error de quien llama, no se reintenta (400)
    3  ningún proveedor respondió (red, cuota, presupuesto) (502/503)
    4  el modelo no cumplió el contrato después de todos los intentos (502)
    5  error de configuración (.env, precios, versión de prompt)
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigError, Settings, load_settings
from .costs import Pricing, PricingError, project_cost
from .llm.base import AllProvidersFailedError
from .llm.factory import build_providers
from .llm.router import Router
from .metrics import MetricsLog
from .parsing import metrics_fields, parse_category
from .prompts import OUTPUT_CONTRACT, PromptError, PromptTemplate, load_prompt, render_system, render_user
from .triage import Attempt, ContractError, InvalidQueryError, classify_validated, validate_query

# Códigos de salida de la CLI (D-62)
EXIT_OK = 0
EXIT_INVALID_QUERY = 2
EXIT_PROVIDERS = 3
EXIT_CONTRACT = 4
EXIT_CONFIG = 5


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Triage de consultas de soporte de Nubbix.")
    parser.add_argument("consulta", nargs="?", help="Texto de la consulta del cliente (entre comillas).")
    parser.add_argument("--stream", action="store_true",
                        help="Muestra la respuesta a medida que se genera (M1L1). Solo con prompts de salida texto.")
    parser.add_argument("--detalle", action="store_true",
                        help="Muestra en la salida de errores tokens, costo, latencia e intentos (M1L3).")
    parser.add_argument("--gasto", action="store_true",
                        help="Muestra el gasto acumulado, el presupuesto y la proyección de costo, y termina.")
    parser.add_argument("--prompt", metavar="VERSION",
                        help="Versión del prompt (ej. v6 o triage_v6). Por defecto, PROMPT_VERSION del .env (M1L2).")
    parser.add_argument("--consultas", type=int, default=10_000,
                        help="Cantidad de consultas para la proyección de --gasto (por defecto 10000, como en M1L1).")
    return parser.parse_args(argv)


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _build_router(settings: Settings, pricing: Pricing, metrics: MetricsLog) -> Router:
    providers, warnings = build_providers(settings)
    for w in warnings:
        _err(f"Aviso: {w}")
    for p in providers:
        pricing.price(p.model)  # falla temprano si falta un precio (PricingError)

    def warn_failure(provider_name: str, exc: Exception) -> None:
        kind = getattr(exc, "kind", "budget_exceeded")
        _err(f"Aviso: falló {provider_name} ({kind}): {exc}")

    return Router(
        providers,
        pricing,
        metrics,
        budget_usd=settings.budget_usd,
        max_retries=settings.max_retries,
        max_wait_s=settings.max_wait_seconds,
        on_failure=warn_failure,
    )


def _warn_invalid_attempts(attempts: list[Attempt]) -> None:
    for a in attempts:
        if not a.check.valid:
            _err(f"Aviso: el intento {a.number} no cumplió el contrato ({a.check.error}).")


def _detail(template: PromptTemplate, attempts: list[Attempt], request_id: str) -> dict:
    responses = [a.response for a in attempts]
    last = responses[-1]
    return {
        "prompt_version": template.version,
        "request_id": request_id,
        "intentos": len(attempts),
        "provider": last.provider,
        "model": last.model,
        "prompt_tokens": sum(r.input_tokens for r in responses),
        "completion_tokens": sum(r.output_tokens for r in responses),
        "total_tokens": sum(r.total_tokens for r in responses),
        "latency_s": round(sum(r.latency_s for r in responses), 3),
        "cost_usd": round(sum(r.cost_usd for r in responses), 8),
    }


def _run_contract(args: argparse.Namespace, settings: Settings, template: PromptTemplate, router: Router) -> int:
    """Flujo de M1L3: tool call → contrato Pydantic → reintento con el error → errores tipados."""
    if args.stream:
        _err("Aviso: --stream no se usa con prompts de contrato: la respuesta se valida completa antes de mostrarse.")
    try:
        outcome = classify_validated(args.consulta, router, template, max_attempts=settings.contract_max_attempts)
    except AllProvidersFailedError as exc:
        _err(f"Error: {exc}")
        return EXIT_PROVIDERS
    except ContractError as exc:
        _warn_invalid_attempts(exc.attempts[:-1])
        _err(f"Error: {exc}")
        if args.detalle and exc.attempts:
            _err(json.dumps(_detail(template, exc.attempts, "-"), ensure_ascii=False, indent=2))
        return EXIT_CONTRACT

    _warn_invalid_attempts(outcome.attempts)
    # Salida estándar = solo el contrato (PI: "consumible por otro sistema sin transformaciones").
    print(json.dumps(outcome.result.model_dump(), ensure_ascii=False, indent=2))
    if args.detalle:
        _err(json.dumps(_detail(template, outcome.attempts, outcome.request_id), ensure_ascii=False, indent=2))
    return EXIT_OK


def _run_text(args: argparse.Namespace, template: PromptTemplate, router: Router) -> int:
    """Flujo de M1L2 para las versiones de salida texto (v1…v5): categoría parseada + métricas."""
    _err(f"Aviso: {template.version} es una versión de salida texto (M1L2): no produce el contrato "
         "{answer, confidence, category, actions}. Para el contrato validado usá --prompt v6.")
    system = render_system(template)
    user_message = render_user(template, args.consulta)

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
        _err(f"Error: {exc}")
        return EXIT_PROVIDERS

    if args.stream:
        print(file=sys.stderr)

    parsed = parse_category(resp.text)
    if not parsed.valid:
        _err(f"Aviso: no se obtuvo una categoría válida (método: {parsed.method}). "
             "Las versiones de salida texto no reintentan; el reintento está en las de contrato (v6).")
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings()
        pricing = Pricing(settings.pricing_path)
    except (ConfigError, PricingError) as exc:
        _err(f"Error de configuración: {exc}")
        return EXIT_CONFIG

    metrics = MetricsLog(settings.metrics_path)

    if args.gasto:
        if args.consultas <= 0:
            _err("Error: --consultas debe ser mayor que 0.")
            return EXIT_CONFIG
        spent = metrics.total_cost()
        print(json.dumps({
            "gastado_usd": round(spent, 6),
            "presupuesto_usd": settings.budget_usd,
            "restante_usd": round(settings.budget_usd - spent, 6),
            "proyeccion": project_cost(metrics.read_all(), pricing, args.consultas),
        }, ensure_ascii=False, indent=2))
        return EXIT_OK

    # M1L3 · Errores tipados: la consulta se valida antes de cargar nada y sin llamar al modelo.
    try:
        validate_query(args.consulta)
    except InvalidQueryError as exc:
        _err(f"Error: {exc}")
        return EXIT_INVALID_QUERY

    try:
        template = load_prompt(args.prompt or settings.prompt_version, settings.prompts_dir)
        router = _build_router(settings, pricing, metrics)
    except (PromptError, PricingError) as exc:
        _err(f"Error de configuración: {exc}")
        return EXIT_CONFIG

    if template.salida == OUTPUT_CONTRACT:
        return _run_contract(args, settings, template, router)
    return _run_text(args, template, router)


if __name__ == "__main__":
    sys.exit(main())
