"""Comparación de versiones del prompt sobre el set etiquetado (entregable de M1L2).

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python -m src.comparar                         # todas las versiones, con Gemini
    python -m src.comparar --versiones v1 v2 v4    # solo algunas
    python -m src.comparar --proveedor openai      # con el proveedor pago (tiene costo)

Fuentes:
- M1L2 · "decidir con datos y no con intuición"; "la pregunta es si mejoró lo suficiente para
  justificar el costo".
- Ejemplo del profesor (``L2/orbitair/pasos/paso5_comparativa.py`` y ``evaluacion.py``): correr
  cada versión sobre los lotes dev y holdout, medir exactitud, respuestas inválidas, tokens y costo
  × 10.000, y generar un reporte markdown + un CSV con el detalle.

Decisiones (docs/DECISIONES.md): set provisional hasta M1L5 (D-45); un solo proveedor fijo y sin
fallback, con pausa entre llamadas (D-46). La suite de evals completa del PI (≥30 casos, score por
categoría, umbral) es de M1L5 y vivirá en ``evals/runner.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import PROJECT_ROOT, ConfigError, load_settings
from .costs import Pricing, PricingError
from .llm.base import AllProvidersFailedError
from .llm.factory import build_providers
from .llm.router import Router
from .metrics import MetricsLog
from .parsing import METHOD_FAILED, METHOD_HEURISTIC, metrics_fields, parse_category
from .prompts import CATEGORIES, PromptError, PromptTemplate, list_versions, load_prompt, render_system, render_user

DATASET_PATH = PROJECT_ROOT / "evals" / "dataset.jsonl"
REPORT_PATH = PROJECT_ROOT / "docs" / "comparacion_versiones.md"
DETAIL_PATH = PROJECT_ROOT / "docs" / "comparacion_detalle.csv"
LOTS = ("dev", "holdout")
DATASET_FIELDS = ("id", "texto", "lote", "categoria", "regla", "justificacion")


class DatasetError(Exception):
    """El set etiquetado tiene un caso mal formado."""


@dataclass
class Row:
    version: str
    id: str
    lote: str
    esperada: str
    obtenida: str
    acierto: bool
    valida: bool
    metodo: str
    regla: str
    tokens_in: int
    tokens_out: int
    latencia_s: float
    error: str
    respuesta: str


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    """Lee el set etiquetado (JSONL) y valida cada caso antes de gastar una sola llamada."""
    cases, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, start=1):
            if not raw.strip():
                continue
            case = json.loads(raw)
            missing = [k for k in DATASET_FIELDS if k not in case]
            if missing:
                raise DatasetError(f"Línea {n}: faltan campos {missing}")
            if case["categoria"] not in CATEGORIES:
                raise DatasetError(f"{case['id']}: categoría {case['categoria']!r} no existe")
            if case["lote"] not in LOTS:
                raise DatasetError(f"{case['id']}: lote {case['lote']!r} debe ser uno de {LOTS}")
            if case["id"] in seen:
                raise DatasetError(f"{case['id']} está repetido")
            seen.add(case["id"])
            cases.append(case)
    return cases


def evaluate(
    router: Router,
    template: PromptTemplate,
    cases: list[dict],
    *,
    pause_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> list[Row]:
    """Clasifica cada caso con una versión del prompt y lo compara con su etiqueta."""
    system = render_system(template)
    rows = []
    for i, case in enumerate(cases):
        if i and pause_s:
            sleep(pause_s)  # D-46: no pasar el límite por minuto del plan gratuito
        try:
            resp = router.ask(
                render_user(template, case["texto"]),
                system,
                prompt_version=template.version,
                extra_fields=lambda r: metrics_fields(r.text),
            )
        except AllProvidersFailedError as exc:
            log(f"  {case['id']}: ERROR {exc}")
            rows.append(Row(template.version, case["id"], case["lote"], case["categoria"], "", False, False,
                            "error_api", case["regla"] or "", 0, 0, 0.0, str(exc)[:200], ""))
            continue
        parsed = parse_category(resp.text)
        ok = parsed.category == case["categoria"]
        log(f"  {case['id']} ({case['lote']}): esperada={case['categoria']:<9} "
            f"obtenida={parsed.category or '-':<9} {'✓' if ok else '✗'} [{parsed.method}]")
        rows.append(Row(template.version, case["id"], case["lote"], case["categoria"], parsed.category or "",
                        ok, parsed.valid, parsed.method, case["regla"] or "", resp.input_tokens,
                        resp.output_tokens, round(resp.latency_s, 3), "", resp.text.strip().replace("\n", " | ")))
    return rows


def summarize(rows: list[Row], pricing: Pricing, reference_model: str, scale: int = 10_000) -> dict:
    """Resume una versión. El costo se proyecta con el modelo de referencia (el pago), porque
    con Gemini gratuito el costo real es 0 y no permitiría comparar versiones."""
    answered = [r for r in rows if not r.error]
    by_lot = {}
    for lot in LOTS:
        in_lot = [r for r in rows if r.lote == lot]
        by_lot[lot] = (sum(r.acierto for r in in_lot), len(in_lot))
    avg_in = statistics.mean(r.tokens_in for r in answered) if answered else 0.0
    avg_out = statistics.mean(r.tokens_out for r in answered) if answered else 0.0
    return {
        "version": rows[0].version,
        "n": len(rows),
        "aciertos": by_lot,
        "invalidas": sum(not r.valida for r in answered),
        "parseo_fragil": sum(r.metodo in (METHOD_HEURISTIC, METHOD_FAILED) for r in answered),
        "errores_api": len(rows) - len(answered),
        "tokens_in_medio": avg_in,
        "tokens_out_medio": avg_out,
        "latencia_media_s": statistics.mean(r.latencia_s for r in answered) if answered else 0.0,
        "costo_ref_usd": pricing.cost(reference_model, avg_in, avg_out) * scale,
    }


def _pct(hits: int, total: int) -> str:
    return f"{hits / total:.0%} ({hits}/{total})" if total else "—"


def render_report(summaries: list[dict], rows: list[Row], meta: dict) -> str:
    lines = [
        "# Comparación de versiones del prompt de triage — Nubbix",
        "",
        f"Generado por `python -m src.comparar` el {meta['fecha']}. "
        f"Proveedor: **{meta['proveedor']}** (`{meta['modelo']}`), temperature {meta['temperature']}, "
        f"sin fallback. Set: `evals/dataset.jsonl` ({meta['n_dev']} casos dev + {meta['n_holdout']} holdout).",
        "",
        "> **Provisional (M1L2).** Con 20 casos, una diferencia de un caso son 10 puntos: sirve para elegir",
        "> qué versión seguir trabajando, no para cerrar la decisión. La elección con datos llega en M1L5",
        "> (≥30 casos, score por categoría y umbral).",
        "",
        f"| Versión | Exactitud dev | Exactitud holdout | Inválidas | Parseo frágil | Errores API | "
        f"Tokens entrada | Tokens salida | Latencia media (s) | USD × 10.000 con `{meta['modelo_ref']}` |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        dev, hold = s["aciertos"]["dev"], s["aciertos"]["holdout"]
        lines.append(
            f"| {s['version']} | {_pct(*dev)} | {_pct(*hold)} | {s['invalidas']} | {s['parseo_fragil']} | "
            f"{s['errores_api']} | {s['tokens_in_medio']:.0f} | {s['tokens_out_medio']:.0f} | "
            f"{s['latencia_media_s']:.2f} | {s['costo_ref_usd']:.2f} |"
        )
    lines += [
        "",
        "Cómo leerla: *inválidas* = la respuesta no trajo una de las 4 categorías; *parseo frágil* = la",
        "categoría se encontró buscándola en texto libre (el formato pedido no se respetó). El costo se",
        f"proyecta con los tokens medidos y el precio de `{meta['modelo_ref']}` (`config/pricing.json`),",
        "porque con Gemini gratuito el costo real es 0.",
        "",
        "## Errores en el lote dev",
        "",
        "Solo se listan los de **dev**: es el lote que se mira para diseñar (por ejemplo, para escribir los",
        "ejemplos few-shot de v3). Los errores de **holdout** no se muestran a propósito: si se usaran para",
        "ajustar el prompt, el holdout dejaría de medir. El detalle completo está en `docs/comparacion_detalle.csv`.",
        "",
    ]
    for s in summaries:
        errs = [r for r in rows if r.version == s["version"] and r.lote == "dev" and not r.acierto]
        lines.append(f"**{s['version']}**: " + ("sin errores" if not errs else ""))
        for r in errs:
            got = r.obtenida or ("error de API" if r.error else "sin categoría")
            rule = f" · regla {r.regla}" if r.regla else ""
            lines.append(f"- {r.id}: esperada `{r.esperada}`, obtenida `{got}`{rule}")
        lines.append("")
    lines += [
        "## Decisión",
        "",
        "Se completa a mano en `prompts/CHANGELOG.md` y en el README, citando exactitud en holdout, costo",
        "y consistencia del formato (no una impresión general).",
        "",
    ]
    return "\n".join(lines)


def write_detail(rows: list[Row], path: Path = DETAIL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel lo abre bien
        writer = csv.DictWriter(f, fieldnames=list(Row.__dataclass_fields__))
        writer.writeheader()
        for r in rows:
            writer.writerow(r.__dict__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.comparar",
                                     description="Compara versiones del prompt sobre evals/dataset.jsonl.")
    parser.add_argument("--versiones", nargs="+", metavar="V",
                        help="Versiones a comparar (ej. v1 v2). Por defecto, todas las de prompts/.")
    parser.add_argument("--proveedor", choices=["gemini", "openai"], default="gemini",
                        help="Proveedor fijo para toda la corrida, sin fallback (D-46). Por defecto gemini (gratis).")
    parser.add_argument("--pausa", type=float, default=6.0,
                        help="Segundos entre llamadas (por defecto 6 ≈ 10 por minuto, para el plan gratuito).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings()
        pricing = Pricing(settings.pricing_path)
        cases = load_dataset()
        names = args.versiones or list_versions(settings.prompts_dir)
        templates = [load_prompt(v, settings.prompts_dir) for v in names]
        reference_model = settings.providers["openai"].model
        pricing.price(reference_model)
    except (ConfigError, PricingError, PromptError, DatasetError, FileNotFoundError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2

    # Un solo proveedor y sin fallback: si respondieran modelos distintos, la comparación no sería justa.
    settings_one = replace(settings, provider_order=[args.proveedor])
    providers, warnings = build_providers(settings_one)
    for w in warnings:
        print(f"Aviso: {w}", file=sys.stderr)
    if not providers:
        print(f"Error: {args.proveedor} no está configurado (falta la API key en .env).", file=sys.stderr)
        return 2
    provider = providers[0]
    pricing.price(provider.model)

    total_calls = len(cases) * len(templates)
    print(f"Comparando {', '.join(t.version for t in templates)} con {provider.name} ({provider.model}): "
          f"{total_calls} llamadas, pausa {args.pausa:g} s.")
    if pricing.is_paid(provider.model):
        print("Atención: este proveedor es pago; las llamadas suman al presupuesto (BUDGET_USD).")

    router = Router([provider], pricing, MetricsLog(settings.metrics_path), budget_usd=settings.budget_usd,
                    max_retries=settings.max_retries, max_wait_s=settings.max_wait_seconds,
                    on_failure=lambda name, exc: None)

    all_rows: list[Row] = []
    summaries = []
    for template in templates:
        print(f"\n== {template.version} ({template.tecnica})")
        rows = evaluate(router, template, cases, pause_s=args.pausa)
        all_rows += rows
        summaries.append(summarize(rows, pricing, reference_model))
        time.sleep(args.pausa)

    meta = {
        "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "proveedor": provider.name,
        "modelo": provider.model,
        "modelo_ref": reference_model,
        "temperature": settings.temperature,
        "n_dev": sum(c["lote"] == "dev" for c in cases),
        "n_holdout": sum(c["lote"] == "holdout" for c in cases),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(summaries, all_rows, meta), encoding="utf-8")
    write_detail(all_rows)
    print(f"\nListo: {REPORT_PATH.relative_to(PROJECT_ROOT)} y {DETAIL_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
