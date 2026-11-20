# Hoja de ruta: consigna del PI vs. lecciones de M1

El proyecto avanza lección por lección. Esta tabla muestra qué pide la consigna del Proyecto integrador M1, en qué lección se aprende y en qué estado está.

Estados: ✅ hecho · 🟡 parcial / provisional · ⬜ pendiente

| Requisito del PI | Lección | Estado | Dónde |
|---|---|---|---|
| Cliente LLM con ≥2 proveedores configurables | L1 | ✅ | `src/llm/` (Gemini + OpenAI) |
| Controlar costo, latencia y errores | L1 | ✅ | `src/costs.py`, `src/retry.py`, `src/llm/router.py` |
| Claves por variable de entorno, `.env.example` | L1 / M0L2 | ✅ | `.env.example`, `src/config.py` |
| `requirements.txt` con dependencias | M0L2 | ✅ | `requirements.txt` |
| `data/metrics.jsonl` append-only (timestamp, tokens, latencia, costo) | L1 / PI | ✅ | `src/metrics.py` |
| CLI que recibe la consulta y devuelve JSON validado | L1 / L2 / L3 | ✅ | `src/main.py`: salida estándar = solo el contrato; códigos de salida 0/2/3/4/5; `--detalle` |
| Prompts versionados en `prompts/*.yaml` (≥2 versiones) + changelog | L2 | ✅ | `prompts/triage_v1…v5.yaml`, `prompts/CHANGELOG.md`, `src/prompts.py` |
| Técnica de context engineering justificada con datos | L2 + L5 | 🟡 | Comparación provisional sobre 20 casos (`docs/comparacion_versiones.md`, `docs/comparacion_versiones_l3.md`): vigente `triage_v6` (= v2 + contrato). Se confirma con la suite de L5 |
| Contrato JSON `{answer, confidence, category, actions}` validado con Pydantic | L3 | ✅ | `src/contract.py` (tipo + dominio, única fuente de verdad), forzado por tool calling en Gemini y OpenAI |
| Retry automático ante respuesta no parseable | L3 | ✅ | `src/triage.py`: hasta 3 intentos con el error inyectado en el system; errores tipados `InvalidQueryError` / `ContractError` |
| Guardrail de entrada (moderación / detección de injection) | L4 | ⬜ | |
| Guardrail de salida (validación de contenido) | L4 | ⬜ | |
| `evals/dataset.jsonl` con ≥30 casos etiquetados | L5 | 🟡 | 20 casos dev/holdout + `evals/guia_etiquetado.md` (provisional de L2); ampliar a ≥30 |
| `evals/runner.py`: score global y por categoría | L5 | ⬜ | |
| README con setup, uso, técnica elegida, tabla de evals y decisiones | todas | 🟡 | se completa en cada etapa |
| Bonus: `evals/adversarial.jsonl` con 10 prompts maliciosos | L4 + L5 | ⬜ | |
| Extra credit (al menos uno): FastAPI, caché MD5, LLM-as-judge en runtime o comparativa de modelos | L2 | 🟡 | Comparativa de modelos hecha con el set de L2 (Gemini vs `gpt-6-luna`, `docs/comparacion_versiones_openai.md`). Repetir con el dataset de L5 para que sea concluyente |

## Pendientes anotados para próximas etapas

- ~~**L3**: contrato Pydantic y reintento~~ Hecho (D-54 a D-69).
- **L4**: guardrail de salida para `answer`: en las pruebas de L3 el modelo prometió plazos ("a la brevedad") o insinuó devoluciones, que el contrato no detecta (valida forma, no significado).
- **L5**: agregar más casos del tipo de R5 (preguntar una condición comercial vs. pedir un cambio en lo que se paga) y decidir la versión con datos: v6 (vigente) contra una v7 = v5 (par de contraste) + contrato. v6 falla NX-008 en dev con los dos modelos (D-49, D-51, D-69).
- **L5**: medir también la calidad de `answer` y `actions` (no solo la categoría) y si `confidence` está calibrada.

## Fechas

- Entrega del Proyecto integrador M1: **viernes 04/12/2026 21:00 → lunes 07/12/2026 21:00** (Mi Cohorte → Evaluaciones).
- Aprobación: 70/100 por rúbrica.
