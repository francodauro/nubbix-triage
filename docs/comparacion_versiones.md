# Comparación de versiones del prompt de triage — Nubbix

Generado por `python -m src.comparar` el 2026-11-19 14:53 UTC. Proveedor: **gemini** (`gemini-3.5-flash-lite`), temperature 0.0, sin fallback. Set: `evals/dataset.jsonl` (10 casos dev + 10 holdout).

> **Provisional (M1L2).** Con 20 casos, una diferencia de un caso son 10 puntos: sirve para elegir
> qué versión seguir trabajando, no para cerrar la decisión. La elección con datos llega en M1L5
> (≥30 casos, score por categoría y umbral).

| Versión | Exactitud dev | Exactitud holdout | Inválidas | Parseo frágil | Errores API | Tokens entrada | Tokens salida | Latencia media (s) | USD × 10.000 con `gpt-6-luna` |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| triage_v1 | 70% (7/10) | 90% (9/10) | 0 | 0 | 0 | 108 | 1 | 7.95 | 0.11 |
| triage_v2 | 90% (9/10) | 100% (10/10) | 0 | 0 | 0 | 309 | 6 | 8.93 | 0.34 |
| triage_v3 | 90% (9/10) | 100% (10/10) | 0 | 0 | 0 | 342 | 6 | 8.45 | 0.37 |
| triage_v4 | 100% (10/10) | 100% (10/10) | 0 | 0 | 0 | 365 | 67 | 9.30 | 0.70 |

Cómo leerla: *inválidas* = la respuesta no trajo una de las 4 categorías; *parseo frágil* = la
categoría se encontró buscándola en texto libre (el formato pedido no se respetó). El costo se
proyecta con los tokens medidos y el precio de `gpt-6-luna` (`config/pricing.json`),
porque con Gemini gratuito el costo real es 0.

## Errores en el lote dev

Solo se listan los de **dev**: es el lote que se mira para diseñar (por ejemplo, para escribir los
ejemplos few-shot de v3). Los errores de **holdout** no se muestran a propósito: si se usaran para
ajustar el prompt, el holdout dejaría de medir. El detalle completo está en `docs/comparacion_detalle.csv`.

**triage_v1**: 
- NX-003: esperada `account`, obtenida `technical`
- NX-004: esperada `other`, obtenida `billing` · regla R5
- NX-009: esperada `other`, obtenida `technical`

**triage_v2**: 
- NX-004: esperada `other`, obtenida `billing` · regla R5

**triage_v3**: 
- NX-004: esperada `other`, obtenida `billing` · regla R5

**triage_v4**: sin errores

## Decisión

Se completa a mano en `prompts/CHANGELOG.md` y en el README, citando exactitud en holdout, costo
y consistencia del formato (no una impresión general).
