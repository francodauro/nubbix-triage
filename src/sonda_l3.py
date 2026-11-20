"""Sonda de M1L3: verifica con la API real cómo responde cada proveedor antes de adoptar tool calling.

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python -m src.sonda_l3                       # Gemini (gratis): JSON pedido en el prompt vs. tool calling
    python -m src.sonda_l3 --proveedor openai    # gpt-6-luna: tool calling con y sin strict (≈ USD 0.0003)

Qué responde (consigna de la etapa: "verificá con una prueba cómo lo soporta cada SDK"):
- Gemini: ¿respeta ``mode="ANY"``? Y, para ver con datos por qué se usa tool calling, la misma consulta
  pidiendo el JSON solo con una instrucción en el prompt (M1L3 · "pedir JSON no garantiza estructura").
- OpenAI: ¿``gpt-6-luna`` acepta ``tool_choice`` forzado con ``temperature=0`` y ``reasoning_effort=none``?
  ¿Acepta ``strict: true`` con ``minLength``, ``minimum`` y ``minItems`` en el schema (D-59)? ¿Alcanzan
  los tokens de salida (D-65)?

Las llamadas pasan por el router con un solo proveedor y sin fallback, así quedan en ``data/metrics.jsonl``
(``prompt_version: sonda_l3``) y cuentan para el presupuesto. El resultado se guarda en
``docs/sonda_l3_<proveedor>.md``. Las consultas son ejemplos escritos para la sonda, no casos del dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone

from pydantic import ValidationError

from .config import PROJECT_ROOT, ConfigError, load_settings
from .contract import TRIAGE_TOOL, TriageResult, describe_errors
from .costs import Pricing, PricingError
from .llm.base import AllProvidersFailedError, LLMResponse
from .llm.factory import build_providers
from .llm.router import Router
from .metrics import MetricsLog
from .prompts import PromptError, load_prompt, render_system, render_user
from .triage import check_response

PROMPT = "triage_v6"
CONSULTAS = [
    "Me cobraron dos veces la suscripción de este mes y necesito que me devuelvan uno de los cobros.",
    "¿Tienen algún plan para empresas más grandes? Somos 80 personas.",
]
# Modo "solo prompt": el formato se pide con una instrucción de texto, sin tool calling.
JSON_EN_EL_PROMPT = (
    "\n\nResponde SOLO con un objeto JSON con las claves answer, confidence, category y actions, sin texto adicional."
)


def _usage(r: LLMResponse) -> str:
    return f"{r.input_tokens} / {r.output_tokens}"


def _probe_text_json(router: Router, system: str, user: str) -> dict:
    """Sin tool calling: json.loads directo sobre el texto y después el contrato."""
    r = router.ask(user, system + JSON_EN_EL_PROMPT, prompt_version="sonda_l3", extra_fields=lambda _: {"sonda": "json_en_prompt"})
    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        return {"r": r, "json": "no (json.loads falla)", "contrato": "—"}
    try:
        TriageResult.model_validate(data)
        verdict = "sí"
    except ValidationError as exc:
        verdict = f"no: {describe_errors(exc)}"
    return {"r": r, "json": "sí", "contrato": verdict}


def _probe_tool(router: Router, system: str, user: str, label: str) -> dict:
    r = router.ask(user, system, prompt_version="sonda_l3", tool=TRIAGE_TOOL, extra_fields=lambda _: {"sonda": label})
    check = check_response(r)
    return {"r": r, "json": "sí" if r.tool_args is not None else f"no ({r.tool_error})",
            "contrato": "sí" if check.valid else f"no: {check.error}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.sonda_l3", description=__doc__.splitlines()[0])
    parser.add_argument("--proveedor", choices=["gemini", "openai"], default="gemini")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        pricing = Pricing(settings.pricing_path)
        template = load_prompt(PROMPT, settings.prompts_dir)
    except (ConfigError, PricingError, PromptError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 5

    providers, warnings = build_providers(replace(settings, provider_order=[args.proveedor]))
    for w in warnings:
        print(f"Aviso: {w}", file=sys.stderr)
    if not providers:
        print(f"Error: {args.proveedor} no está configurado (falta la API key en .env).", file=sys.stderr)
        return 5
    provider = providers[0]
    router = Router([provider], pricing, MetricsLog(settings.metrics_path), budget_usd=settings.budget_usd,
                    max_retries=settings.max_retries, max_wait_s=settings.max_wait_seconds,
                    on_failure=lambda name, exc: None)
    system = render_system(template)

    if args.proveedor == "gemini":
        plan = [(c, mode) for c in CONSULTAS for mode in ("json_en_prompt", "tool_calling")]
    else:
        plan = [(CONSULTAS[0], "tool_calling_strict"), (CONSULTAS[0], "tool_calling_sin_strict")]
        print("Atención: proveedor pago. 2 llamadas, costo estimado ≈ USD 0.0003.")

    rows = []
    for consulta, mode in plan:
        user = render_user(template, consulta)
        print(f"\n== {mode}: {consulta[:60]}")
        try:
            if mode == "json_en_prompt":
                res = _probe_text_json(router, system, user)
            else:
                if hasattr(provider, "strict_tools"):
                    provider.strict_tools = mode == "tool_calling_strict"
                res = _probe_tool(router, system, user, mode)
        except AllProvidersFailedError as exc:
            print(f"  ERROR: {exc}")
            rows.append({"modo": mode, "consulta": consulta, "json": "—", "contrato": "—",
                         "tokens": "—", "finish": "—", "costo": 0.0, "salida": f"ERROR: {exc}"[:400]})
            continue
        r = res["r"]
        print(f"  ¿se puede leer como JSON? {res['json']} · ¿cumple el contrato? {res['contrato']}")
        print(f"  tokens entrada/salida {_usage(r)} · finish {r.finish_reason} · {r.latency_s:.1f} s · USD {r.cost_usd:.6f}")
        print(f"  salida: {r.text[:300]}")
        rows.append({"modo": mode, "consulta": consulta, "json": res["json"], "contrato": res["contrato"],
                     "tokens": _usage(r), "finish": r.finish_reason, "costo": r.cost_usd,
                     "salida": r.text.replace("\n", " ")[:400]})

    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Sonda de M1L3 · {provider.name} (`{provider.model}`)", "",
             f"Generado por `python -m src.sonda_l3 --proveedor {args.proveedor}` el {fecha}. Prompt `{PROMPT}`, "
             f"temperature {settings.temperature}"
             + (f", strict según el modo, reasoning_effort `{getattr(provider, 'reasoning_effort', '')}`"
                if args.proveedor == "openai" else "") + ".", "",
             "| Modo | Consulta | ¿JSON legible? | ¿Cumple el contrato? | Tokens entrada / salida | finish | USD |",
             "|---|---|---|---|---|---|---:|"]
    for row in rows:
        lines.append(f"| {row['modo']} | {row['consulta'][:45]}… | {row['json']} | {row['contrato']} | "
                     f"{row['tokens']} | {row['finish']} | {row['costo']:.6f} |")
    lines += ["", "## Salidas", ""]
    for row in rows:
        lines += [f"**{row['modo']}** · {row['consulta'][:45]}…", "", "```", row["salida"], "```", ""]
    out = PROJECT_ROOT / "docs" / f"sonda_l3_{provider.name}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nListo: {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
