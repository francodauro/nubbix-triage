# Registro de decisiones

Cada decisión indica **de dónde sale**. Si el material de Henry la define, se cita la lección. Si Henry no dice nada al respecto, se marca como **decisión propia** y se explica por qué.

Abreviaturas: **PI** = consigna del Proyecto integrador M1 · **M0L1/M0L2** = lecciones del Módulo 0 · **M1L1…M1L5** = lecciones del Módulo 1.

| ID | Decisión | Fuente | Etapa |
|---|---|---|---|
| D-01 | Estructura `src/`, `prompts/`, `evals/`, `data/` | PI · tabla de entregables y guía ("definí una estructura simple de archivos (src/, prompts/, evals/, data/)") | PI |
| D-02 | Python en entorno virtual (`.venv`) con `requirements.txt` de versiones exactas | M0L2 · Modularidad y entornos virtuales | PI |
| D-03 | Dos proveedores: **Gemini (gratuito) como primario** y **OpenAI (pago) como respaldo** | PI pide "≥2 proveedores configurables (por ejemplo OpenAI y Anthropic)". M1L1 incluye Gemini en la tabla de familias de modelos y dice que "funciona bajo la misma lógica". **Elegir Gemini en lugar de Anthropic es decisión propia**: Anthropic no tiene plan gratuito y el objetivo es gastar menos de USD 5 | L1 |
| D-04 | Modelo gratuito: `gemini-3.5-flash-lite` | **Decisión propia** (Henry no define modelos de Gemini). Es uno de los 4 modelos de texto con plan gratuito según ai.google.dev/pricing (consultado el 18/11/2026). Se cambia con `GEMINI_MODEL` en el `.env` | L1 |
| D-05 | ~~Modelo pago: `gpt-4.1-nano` (USD 0.10 / 0.40 por 1M de tokens)~~ **Reemplazada por D-30** | M1L1 · Costo por token: "gpt-4.1-nano · Alternativa más barata, misma familia". El hands-on usa `gpt-4.1-mini`; se había elegido el más barato de la tabla por el tope de gasto | L1 |
| D-06 | `temperature=0.0` y tope de salida chico (OpenAI: 200 tokens) | M1L1 · Sampling: "temperature baja (0.0–0.2)… max_tokens chico" para clasificación | L1 |
| D-07 | Gemini con `max_tokens=1024` | **Decisión propia**. Los modelos Gemini pueden "pensar" antes de responder y esos tokens cuentan contra el límite; con un tope muy bajo la respuesta puede salir vacía y forzar un fallback al proveedor pago. En el plan gratuito no cuesta nada | L1 |
| D-08 | Reintentos: hasta 2 reintentos solo para 429, 5xx, timeout y error de red; backoff 1 s, 2 s… + jitter; **Retry-After tiene prioridad** | M1L1 · Rate limits y manejo de errores (`call_with_backoff`) | L1 |
| D-09 | Si el proveedor pide esperar más de `LLM_MAX_WAIT_SECONDS` (10 s), no se espera: se pasa directo al respaldo | **Decisión propia** sobre la base de M1L1. Cuando el plan gratuito agota la cuota diaria, Google puede pedir horas de espera; en una CLI eso no tiene sentido | L1 |
| D-10 | Los reintentos internos de los SDK se desactivan (`max_retries=0` en OpenAI; sin `retry_options` en Gemini) | **Decisión propia**: así la única política de reintentos es la de M1L1 (D-08) y no se multiplican los intentos | L1 |
| D-11 | 429 por falta de saldo en OpenAI (`insufficient_quota`) no se reintenta | **Decisión propia** derivada de M1L1 (esperar no resuelve la falta de saldo) | L1 |
| D-12 | Fallback ante cualquier falla del primario (después de sus reintentos), registrando el motivo | M1L1 · Patrón de Router con fallback | L1 |
| D-13 | Streaming opcional (`--stream`) con `stream_options={"include_usage": True}` en OpenAI y lectura del `usage_metadata` final en Gemini | M1L1 · Streaming ("sin esto, el usage llega vacío") | L1 |
| D-14 | En streaming, si ya se mostró texto y la conexión se corta, **no** se reintenta ni se pasa al respaldo | **Decisión propia**: evita mostrar dos respuestas mezcladas | L1 |
| D-15 | Circuit breaker: **no se implementa por ahora** | M1L1 lo menciona. **Decisión propia** postergarlo: la CLI corre una consulta por ejecución y no guarda estado entre ejecuciones, así que un circuit breaker no tendría efecto. Revisar si se hace el extra credit de FastAPI | L1 |
| D-16 | Costo = `input/1M × precio_in + output/1M × precio_out`, con tokens del `usage` de la API y precios en `config/pricing.json` | M1L1 · Costo por token y `pricing.json` del hands-on | L1 |
| D-17 | Tope de gasto `BUDGET_USD=4.50`: antes de llamar a un proveedor pago se suma el costo registrado en `data/metrics.jsonl`; si se alcanzó el tope, no se llama | M1L1 menciona "gestionar el presupuesto de uso de API estimando costos por request". El monto es objetivo del alumno (< USD 5 en 4 meses), con margen de seguridad. Limitación: si se borra `metrics.jsonl`, el contador vuelve a cero → por eso también se recomienda cargar crédito prepago acotado en OpenAI | L1 |
| D-18 | `data/metrics.jsonl`: una línea por intento con timestamp, proveedor, modelo, tokens prompt/completion/total, latencia, costo, intentos, fallback y tipo de error | PI · Registro de métricas ("append-only por request… sin SQLite") | PI |
| D-19 | Las métricas **no** guardan el texto de la consulta ni la respuesta | M1L4 · Manejo de PII ("nunca registrar entradas o salidas en bruto") | PI / L4 |
| D-20 | `data/metrics.jsonl` **se sube** al repo (el resto de `data/` no) | PI · Checklist: "al menos una ejecución de la CLI registrada que produzca JSON válido y una línea de métricas" | PI |
| D-21 | Prompt provisional en `prompts/triage_v1.txt`, con las 4 categorías listadas incluida `other` | M1L2 usa `prompts/triage_v1.txt` como ejemplo y pide listar todas las categorías con la "categoría cajón". **El PI exige YAML**: se migra en la etapa L2 | L1 → L2 |
| D-22 | Por ahora la CLI devuelve la categoría como texto + métricas; el contrato `{answer, confidence, category, actions}` llega en L3 | PI (contrato) + M1L3 (Pydantic). Se respeta el orden de las lecciones | L1 → L3 |
| D-23 | Tests con `unittest` y proveedores falsos (sin llamar APIs reales) | M0L2 · Testing (unittest, caja negra). El runner de evals del PI (L5) es otra cosa y será un script simple | PI |
| D-24 | La CLI se ejecuta con `python -m src.main` | **Decisión propia**: permite que `src` sea un paquete con imports internos; el PI acepta "src/main.py (o similar)" | PI |
| D-25 | Plataforma: Windows 11 (PowerShell), rutas con `pathlib`, archivos en UTF-8 | Requisito del alumno | PI |
| D-26 | Python 3.12 o superior (probado con 3.12 y 3.13) | **Decisión propia**: Henry no fija versión | PI |
| D-27 | Cada línea de `metrics.jsonl` guarda la `temperature` usada (también en los intentos fallidos) | M1L1 · Sampling, tabla de parámetros: "loguear siempre el valor elegido" | L1 |
| D-28 | En streaming se mide el **TTFT** (segundos hasta el primer fragmento de texto) y se guarda como `ttft_s` en la salida y en las métricas; sin streaming no se registra | M1L1 · Streaming: diferencia entre latencia real y latencia percibida (TTFT) | L1 |
| D-29 | `--gasto` proyecta el costo de 10.000 consultas (configurable con `--consultas N`): promedio de tokens de las llamadas exitosas × precio de cada modelo de `pricing.json` | M1L1 · Costo por token: "en el hands-on vas a multiplicar ese valor por 10.000 consultas". **El método (promedio de tokens reales registrados) es decisión propia** | L1 |

| D-30 | **Desvío del material de Henry.** Modelo pago: `gpt-6-luna` (USD 0.10 / 0.50 por 1M de tokens) en lugar de `gpt-4.1-nano` | El 18/11/2026 la primera prueba real con OpenAI registró `bad_request`; el mensaje fue `404 - The model gpt-4.1-nano has been deprecated`. La página de deprecaciones de OpenAI indica que gpt-4.1-nano se apagó el 23/10/2026 y que gpt-4.1-mini (el del hands-on) se apaga el 14/01/2027, antes del fin del curso. `gpt-6-luna` es el reemplazo recomendado por OpenAI para la mayoría de los modelos chicos. Precio: página del modelo en developers.openai.com | L1 |
| D-31 | `reasoning_effort="none"` para `gpt-6-luna` (configurable con `OPENAI_REASONING_EFFORT`; vacío = no se envía) | `gpt-6-luna` es un modelo de razonamiento con esfuerzo "medium" por defecto; los tokens de razonamiento se cobran como salida. Para clasificar no hace falta razonar (M1L2: CoT solo si se justifica su costo). **Verificado antes de adoptarlo**: con `none` acepta `temperature=0` y reportó `reasoning_tokens=0` | L1 |
| D-32 | El tope de salida se envía a OpenAI como `max_completion_tokens` (no `max_tokens`) | **Decisión propia**: los modelos de razonamiento no aceptan `max_tokens`; `max_completion_tokens` sirve para todos los modelos de chat | L1 |
| D-33 | Cuando un proveedor falla o se omite, la CLI lo avisa en pantalla (`Aviso: falló openai (bad_request): …`) y `metrics.jsonl` guarda `error_message` (recortado a 300 caracteres) | **Decisión propia**, surgida del caso D-30: el fallback funcionó, pero sin aviso el error pasó desapercibido y el tipo de error solo no alcanzó para diagnosticar. El mensaje es del proveedor y no contiene la consulta (M1L4) | L1 |

## Riesgos conocidos

- **Plan gratuito de Gemini**: Google puede usar el contenido enviado para mejorar sus productos (ai.google.dev/pricing). No mandar datos reales de clientes ni datos personales.
- **Límites del plan gratuito**: son por proyecto de Google, se ven en Google AI Studio y la cuota diaria se reinicia a medianoche hora del Pacífico. Cuando se agota, todo pasa al proveedor pago (con costo).
- **Precios y modelos**: cambian, y los modelos se dan de baja (le pasó a gpt-4.1-nano, D-30). Revisar `config/pricing.json` y la página de deprecaciones de OpenAI antes de cada etapa. `gemini-3.5-flash-lite` podría correr la misma suerte.
- **Facturación de Google**: si se activa la facturación en el proyecto de Google, Gemini deja de ser gratuito. No activarla.
