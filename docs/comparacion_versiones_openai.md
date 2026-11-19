# Comparación de versiones del prompt de triage — Nubbix

Generado por `python -m src.comparar` el 2026-11-19 17:51 UTC. Proveedor: **openai** (`gpt-6-luna`), temperature 0.0, sin fallback. Set: `evals/dataset.jsonl` (10 casos dev + 10 holdout).

> **Provisional (M1L2).** Con 20 casos, una diferencia de un caso son 10 puntos: sirve para elegir
> qué versión seguir trabajando, no para cerrar la decisión. La elección con datos llega en M1L5
> (≥30 casos, score por categoría y umbral).

| Versión | Exactitud dev | Exactitud holdout | Inválidas | Parseo frágil | Errores API | Tokens entrada | Tokens salida | Latencia media (s) | USD × 10.000 con `gpt-6-luna` |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| triage_v1 | 90% (9/10) | 90% (9/10) | 0 | 0 | 0 | 115 | 4 | 1.67 | 0.13 |
| triage_v2 | 100% (10/10) | 100% (10/10) | 0 | 0 | 0 | 313 | 8 | 1.32 | 0.35 |
| triage_v3 | 100% (10/10) | 100% (10/10) | 0 | 0 | 0 | 344 | 9 | 1.00 | 0.39 |
| triage_v4 | 100% (10/10) | 90% (9/10) | 0 | 0 | 0 | 366 | 49 | 1.73 | 0.61 |
| triage_v5 | 100% (10/10) | 100% (10/10) | 0 | 0 | 0 | 376 | 9 | 0.92 | 0.42 |

Cómo leerla: la *exactitud* se calcula sobre las consultas respondidas (entre paréntesis, aciertos /
respondidas); las que fallaron por la API se cuentan en *errores API* y no como desaciertos.
*Inválidas* = la respuesta no trajo una de las 4 categorías; *parseo frágil* = la
categoría se encontró buscándola en texto libre (el formato pedido no se respetó). El costo se
proyecta con los tokens medidos y el precio de `gpt-6-luna` (`config/pricing.json`),
porque con Gemini gratuito el costo real es 0.

## Errores en el lote dev

Solo se listan los de **dev**: es el lote que se mira para diseñar (por ejemplo, para escribir los
ejemplos few-shot de v3). Los errores de **holdout** no se muestran a propósito: si se usaran para
ajustar el prompt, el holdout dejaría de medir. El detalle completo está en `docs/comparacion_detalle.csv`.

**triage_v1**: 
- NX-004: esperada `other`, obtenida `billing` · regla R5

**triage_v2**: sin errores

**triage_v3**: sin errores

**triage_v4**: sin errores

**triage_v5**: sin errores

## Decisión

Se completa a mano en `prompts/CHANGELOG.md` y en el README, citando exactitud en holdout, costo
y consistencia del formato (no una impresión general).
