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
| CLI que recibe la consulta | L1 | 🟡 | `src/main.py` devuelve la categoría en texto; falta el contrato JSON |
| Prompts versionados en `prompts/*.yaml` (≥2 versiones) | L2 | 🟡 | `prompts/triage_v1.txt` provisional; migrar a YAML y crear más versiones |
| Técnica de context engineering justificada con datos | L2 + L5 | ⬜ | |
| Contrato JSON `{answer, confidence, category, actions}` validado con Pydantic | L3 | ⬜ | |
| Retry automático ante respuesta no parseable | L3 | ⬜ | (ya existe el retry de red de L1) |
| Guardrail de entrada (moderación / detección de injection) | L4 | ⬜ | |
| Guardrail de salida (validación de contenido) | L4 | ⬜ | |
| `evals/dataset.jsonl` con ≥30 casos etiquetados | L5 | ⬜ | |
| `evals/runner.py`: score global y por categoría | L5 | ⬜ | |
| README con setup, uso, técnica elegida, tabla de evals y decisiones | todas | 🟡 | se completa en cada etapa |
| Bonus: `evals/adversarial.jsonl` con 10 prompts maliciosos | L4 + L5 | ⬜ | |
| Extra credit (al menos uno): FastAPI, caché MD5, LLM-as-judge en runtime o comparativa de modelos | — | ⬜ | a definir |

## Fechas

- Entrega del Proyecto integrador M1: **viernes 04/12/2026 21:00 → lunes 07/12/2026 21:00** (Mi Cohorte → Evaluaciones).
- Aprobación: 70/100 por rúbrica.
