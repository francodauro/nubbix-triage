# Nubbix Triage

Servicio de triage de consultas de soporte para **Nubbix** (SaaS de gestión para pymes de LATAM).
Proyecto integrador del Módulo 1 de la carrera **AI Engineering de Henry** (cohorte Ai-Engineering-Pt08).

Recibe la consulta de un cliente en texto libre y devuelve un **JSON validado** con el contrato de la consigna: la categoría (`billing`, `technical`, `account` u `other`), una primera respuesta para el cliente, la confianza y las acciones sugeridas para el agente humano. Usa un LLM gratuito con respaldo pago y registra tokens, latencia y costo de cada llamada.

```json
{"answer": "...", "confidence": 0.95, "category": "technical", "actions": ["investigate_incident", "request_more_info"]}
```

> **Estado actual: etapa M1L3 completa** (contrato Pydantic forzado por tool calling en Gemini y OpenAI, reintento con el error inyectado, errores tipados con códigos de salida; vigente `triage_v6`). M1L2: prompts versionados en YAML con changelog y comparación sobre un set etiquetado. M1L1: cliente multi-proveedor, reintentos, streaming con TTFT, costo, proyección a 10.000 consultas y presupuesto.
> Lo que falta para cumplir la consigna completa está en [docs/ROADMAP.md](docs/ROADMAP.md).
> Cada decisión y su fuente en el material de Henry están en [docs/DECISIONES.md](docs/DECISIONES.md).

---

## Cómo funciona

```
consulta ──► ¿vacía? ──sí──► error 2 (no se llama al modelo)
                 │ no
                 ▼
          ┌─► Gemini (gratis) ──ok──────────────────────┐
          │      │ falla (después de reintentos)        │
          │      ▼                                      ▼
          │  ¿queda presupuesto? ──no──► error 3   tool call (argumentos)
          │      │ sí                                   │
          │      ▼                                      ▼
          │  OpenAI gpt-6-luna (pago) ──ok──────► ¿cumple el contrato Pydantic?
          │                                       │ sí            │ no
          │                                       ▼               ▼
          │                              JSON validado     ¿quedan intentos? ──no──► error 4
          │                                                       │ sí
          └──────── se reintenta con el error en el system ◄──────┘
```

- **Primario gratuito**: Google Gemini `gemini-3.5-flash-lite` (plan gratuito de la API de Gemini).
- **Respaldo pago**: OpenAI `gpt-6-luna` (USD 0.10 por 1M de tokens de entrada, USD 0.50 por 1M de salida), con `reasoning_effort="none"` para que no "piense" (más barato y rápido).
- **Avisos**: si un proveedor falla, la CLI lo avisa en pantalla con el motivo, aunque el otro haya respondido.
- **Reintentos** (M1L1): solo para 429, 5xx, timeouts y errores de red; backoff exponencial con jitter; si el proveedor manda `Retry-After`, se respeta. Si pide esperar más de 10 s, se pasa directo al respaldo.
- **Presupuesto**: antes de llamar al proveedor pago se suma lo gastado en `data/metrics.jsonl`; si llegó a `BUDGET_USD` (USD 4.50 por defecto), no se lo llama.
- **Contrato** (M1L3): el modelo está obligado a "llenar un formulario" (tool calling) cuyo molde sale del contrato Pydantic; el código lo valida y, si no cumple, lo pide de nuevo diciendo qué campo falló (hasta 3 intentos).
- **Métricas**: cada intento queda en `data/metrics.jsonl` (sin el texto de la consulta ni de la respuesta).

### ¿Cuánto cuesta?

Con el prompt vigente (`triage_v6`, contrato completo) una consulta usa unos 975 tokens de entrada (el schema de la tool call se envía en cada llamada) y unos 80 de salida (la respuesta para el cliente). Con `gpt-6-luna` eso es alrededor de **USD 0.00014 por consulta** (USD 1.39 cada 10.000), y solo se paga cuando Gemini falla. Con USD 4.50 alcanza para unas treinta mil consultas pagas. Solo clasificar (`triage_v2`) costaba 4 veces menos: el resto es el precio de pedir respuesta y acciones.

---

## Prompts y context engineering (M1L2)

Los prompts viven en `prompts/triage_vN.yaml`, fuera del código. Cada versión es un archivo nuevo que **nunca se edita**; qué cambió, por qué y qué resultado dio está en [prompts/CHANGELOG.md](prompts/CHANGELOG.md).

| Versión | Técnica | Qué agrega |
|---|---|---|
| `triage_v1` | zero-shot | Línea base: rol, las 4 categorías con `other`, respuesta de una palabra (migración del `.txt` de M1L1) |
| `triage_v2` | zero-shot con los 4 principios | Rol, contexto, tarea y formato; consulta entre `<consulta>…</consulta>`; categorías con descripción inyectadas desde el código; salida `{"category": "..."}` |
| `triage_v3` | few-shot | v2 + un ejemplo obvio a partir del único error de v2 en dev (descartada: no cambió nada) |
| `triage_v4` | chain-of-thought | v2 + razonamiento `Tema` / `Pide` / `Decisión` y última línea `CATEGORIA: <valor>` |
| `triage_v5` | few-shot con par de contraste | v2 + dos ejemplos del mismo tema con distinta intención (preguntar una condición → `other`; pedir un cambio en lo que se paga → `billing`). Candidata para M1L5 |
| `triage_v6` ✅ | v2 + contrato por tool calling (M1L3) | Clasifica, propone una respuesta (de "usted") y sugiere acciones; `salida: contrato`; acciones inyectadas con `{acciones}`. Se quitó "Respondé SOLO con un JSON" (contradice al schema) |

v1 a v5 son de **salida texto** (solo la categoría): se conservan para comparar y se pueden usar con `--prompt`, pero no producen el contrato. v6 es de **salida contrato**.

**Plantilla y variables.** El YAML tiene la plantilla fija (`system` y `user`); en cada consulta `src/prompts.py` completa `{empresa}`, `{categorias}` (de `CATEGORIES`, única fuente de verdad) y `{consulta}`. Las llaves literales del JSON van dobles (`{{ }}`); si una plantilla usa una variable desconocida, el error aparece al cargarla, antes de llamar al modelo.

**Lectura de la respuesta (versiones de texto).** `src/parsing.py` busca la categoría en este orden: JSON → última línea `CATEGORIA:` → respuesta de una sola palabra → nombre válido suelto en el texto, y registra el método usado. Si la categoría es inválida, la CLI solo avisa. Desde M1L3, v6 no se parsea: llega por tool calling y se valida con el contrato.

### Comparación de versiones

```powershell
python -m src.comparar                        # todas las versiones, con Gemini (gratis)
python -m src.comparar --versiones v2 v4      # solo algunas
```

Usa el set etiquetado `evals/dataset.jsonl` (20 consultas: 10 **dev** para diseñar y 10 **holdout** para medir al final; reglas en [evals/guia_etiquetado.md](evals/guia_etiquetado.md)), con un solo proveedor fijo y sin fallback, y genera [docs/comparacion_versiones.md](docs/comparacion_versiones.md) + `docs/comparacion_detalle.csv`.

Resultados del 19/11/2026 (Gemini `gemini-3.5-flash-lite`, temperature 0; tres corridas, tabla de la última). La exactitud se calcula sobre las consultas respondidas; en la última corrida Gemini rechazó 3 llamadas por "high demand" (1 de v2, 2 de v4).

| Versión | Exactitud dev | Exactitud holdout | Inválidas | Tokens entrada / salida | USD × 10.000 con `gpt-6-luna` | Consistencia entre corridas |
|---|---|---|---:|---|---:|---|
| triage_v1 | 70 % | 90 % | 0 | 108 / 1 | 0.11 | Igual en las 3 |
| **triage_v2** | 90 % | **100 %** | 0 | 309 / 6 | **0.34** | Igual en las 3 |
| triage_v3 | 90 % | 100 % | 0 | 342 / 6 | 0.37 | Igual en las 2 |
| triage_v4 | 88 % | 100 % | 0 | 365 / 65 | 0.69 | NX-004 cambió: billing → other → billing |
| triage_v5 | 100 % | 100 % | 0 | 378 / 6 | 0.41 | 1 corrida |

**Técnica elegida en M1L2: `triage_v2` (zero-shot con los 4 principios).** Desde M1L3 la vigente es `triage_v6`, que es v2 con el contrato (ver más abajo). En holdout empata con v3, v4 y v5, es la más barata y respondió igual en las tres corridas. Lo que se aprendió de cada técnica:
- **Few-shot**: un ejemplo obvio (v3) no cambió ninguna respuesta; un par de contraste que representa la ambigüedad real (v5) sí corrigió el único error de v2 sin romper el holdout. v5 queda como candidata: su mejora está en el lote dev con el que se eligieron los ejemplos y cuesta un 21 % más, así que se confirma en M1L5.
- **Chain-of-thought** (v4): cuesta el doble y su acierto extra cambia entre corridas: no justifica el costo.

**Es una elección provisional**: con 20 casos, un caso son 10 puntos; la decisión con datos se repite en M1L5 con ≥30 casos y score por categoría.

### Comparativa de modelos (extra credit)

Los mismos 5 prompts sobre el mismo set, con el modelo pago (`python -m src.comparar --proveedor openai`, 100 llamadas, **USD 0.0038 en total**). Con un proveedor distinto del de por defecto, el reporte se guarda aparte: [docs/comparacion_versiones_openai.md](docs/comparacion_versiones_openai.md).

| Prompt | Gemini `gemini-3.5-flash-lite` (dev / holdout) | OpenAI `gpt-6-luna` (dev / holdout) |
|---|---|---|
| triage_v1 | 70 % / 90 % | 90 % / 90 % |
| **triage_v2** | 90 % / 100 % | **100 % / 100 %** |
| triage_v3 | 90 % / 100 % | 100 % / 100 % |
| triage_v4 | 88 % / 100 % | 100 % / 90 % |
| triage_v5 | 100 % / 100 % | 100 % / 100 % |
| Latencia media | ≈ 9–13 s (plan gratuito) | ≈ 1–1.7 s |
| Costo real por consulta con v2 | 0 | ≈ USD 0.000035 |

**Lectura de la comparativa:** `gpt-6-luna` acierta más y es unas 7 veces más rápido, pero cuesta; Gemini es gratis y, con un buen prompt (v2), solo falla el caso ambiguo NX-004. Se mantiene **Gemini como primario y `gpt-6-luna` como respaldo**, por el objetivo de gastar menos de USD 5 en el curso. `triage_v2` es la mejor o está empatada con los dos modelos, lo que refuerza su elección. Con `gpt-6-luna`, el razonamiento de v4 llevó a un error en holdout (NX-015) que v2 no comete. Es una sola corrida con 20 casos: orientativa.

### Lecciones de la etapa M1L2

- **No hay un prompting "mejor" en general.** Depende del modelo, de la tarea y del costo aceptable. El mismo error (NX-004) necesitó ejemplos con Gemini y ninguno con `gpt-6-luna`. Como dice el cierre de M1L2, la técnica más sofisticada no es automáticamente la mejor.
- **Regla práctica que surge de los datos:** primero un prompt bien escrito con los 4 principios (rol, contexto, tarea, formato) y todas las categorías; medirlo; y agregar ejemplos o razonamiento solo donde la medición muestre un error que lo justifique. En este proyecto, los 4 principios dieron casi toda la mejora (v1 → v2).
- **Few-shot sirve si el ejemplo representa la ambigüedad real.** Un ejemplo obvio (v3) no cambió nada; un par de contraste (v5: mismo tema, distinta intención) sí corrigió el error, porque obliga al modelo a decidir por la intención y no por las palabras sobre dinero.
- **Chain-of-thought conviene en tareas de razonamiento de varios pasos**, no en una clasificación en 4 categorías: acá costó el doble, no fue estable entre corridas y con `gpt-6-luna` llevó a un error.
- **Dev y holdout:** los ejemplos se eligen mirando los errores del lote dev; la decisión se toma con el holdout, que no se usó para diseñar. Si se midiera con el mismo lote, el resultado saldría inflado.
- **Test vs. eval:** los tests (`unittest`) verifican que el código funcione (cargar los YAML, leer la respuesta, registrar métricas) y no llaman al modelo. La comparación es un eval: mide la calidad de las respuestas del modelo con cada prompt, que no son determinísticas. Es la diferencia entre testing (M0L2) y evals (M1L5).
- **Comparar sin fallback:** al comparar prompts se usa un solo modelo (D-46); si respondiera otro modelo en algunas consultas, no se podría atribuir el resultado al prompt.

---

## Contrato de salida y tool calling (M1L3)

La salida deja de ser "lo que respondió el modelo" (texto a interpretar) y pasa a ser un **contrato**: una estructura con tipos y valores permitidos que el código **verifica** antes de entregarla.

### El contrato (`src/contract.py`)

| Campo | Regla | Qué evita |
|---|---|---|
| `answer` | texto de 1 a 500 caracteres (se recortan espacios) | respuesta vacía o un ensayo |
| `confidence` | número entre 0 y 1; un `"0.9"` como texto se rechaza | `47.0`, `"alta"` |
| `category` | una de `CATEGORIES` (`Literal` derivado de la lista) | categorías inventadas (`"factura"`) |
| `actions` | lista de 1 o más acciones de `ACTIONS`, sin repetir; `no_action` va sola | acciones inventadas o contradictorias |
| (otros) | no se aceptan campos de más | campos que nadie pidió |

Las categorías y las acciones viven en `src/prompts.py` (**única fuente de verdad**): de ahí salen el contrato, el schema de la tool call y el texto del prompt. Acciones para el agente humano:

| Acción | Para qué | Categoría típica |
|---|---|---|
| `review_charge` | revisar un cobro, duplicado o devolución | billing |
| `resend_invoice` | reenviar o corregir una factura | billing |
| `investigate_incident` | investigar una falla técnica | technical |
| `manage_access` | usuarios, permisos, acceso | account |
| `contact_sales` | derivar a ventas una consulta comercial | other |
| `request_more_info` | pedir los datos que faltan | cualquiera |
| `escalate_to_human` | urgencia o riesgo | cualquiera |
| `no_action` | no hace falta nada (va sola) | cualquiera |

### Cómo se obtiene: la escalera de M1L3

| Nivel | Cuándo actúa | Qué garantiza | En este proyecto |
|---|---|---|---|
| Modo JSON | al generar | que sea JSON | no se usa: solo garantiza sintaxis |
| **Tool calling** | al generar | la forma del schema (en general) | Gemini `mode="ANY"`; OpenAI `tool_choice` + `strict` |
| **Pydantic** | después | tipo y dominio, siempre | `TriageResult.model_validate` |
| **Reintento** | después de un rechazo | otra oportunidad, con el error explicado | hasta 3 intentos (`CONTRACT_MAX_ATTEMPTS`) |

- **Tool calling**: la "función" `registrar_triage` no existe en Python; es un formulario que el modelo está obligado a llenar. Su schema es `TriageResult.model_json_schema()` (se escribe una sola vez). La asimetría de la lección se ve tal cual: **Gemini entrega los argumentos como dict y OpenAI como string JSON**, que hay que convertir y puede llegar cortado. Los dos terminan en el mismo `LLMResponse.tool_args`.
- **Reintento que informa**: si falla, se agrega al system *"Tu respuesta anterior fue inválida: confidence: Input should be less than or equal to 1…"*. Repetir la misma llamada sería apostar al azar; decirle qué falló es información nueva. El mensaje dice campo y motivo, **sin repetir el valor recibido**. Se agrega solo el último error.
- **Dos capas de reintento que no se mezclan**: errores de red/proveedor (M1L1: 429, 5xx → backoff y fallback) y contrato inválido (M1L3: la respuesta llegó pero no sirve → reintento con el error).

### Errores tipados y códigos de salida

| Situación | Error | ¿Se reintenta? | Código de la CLI (≈ HTTP de la lección) |
|---|---|---|---|
| Todo bien | — | — | `0` (200) |
| Consulta vacía | `InvalidQueryError` (culpa de quien llama) | No: 0 llamadas al modelo | `2` (400) |
| Ningún proveedor respondió | `AllProvidersFailedError` | Sí, en la capa de M1L1 | `3` (502/503) |
| 3 intentos sin contrato válido | `ContractError` | Ya se reintentó | `4` (502) |
| `.env`, precios o versión de prompt inválidos | `ConfigError` / `PromptError` | No | `5` |

### Verificación con la API real (`python -m src.sonda_l3`)

Antes de adoptar tool calling se probó con los dos SDKs ([docs/sonda_l3_gemini.md](docs/sonda_l3_gemini.md), [docs/sonda_l3_openai.md](docs/sonda_l3_openai.md)):

| Prueba | Resultado |
|---|---|
| Gemini, JSON pedido **solo en el prompt** (2 consultas) | **0 de 2 legibles**: Gemini envolvió el JSON en un bloque ```` ```json ````, y `json.loads` falla aunque el contenido era correcto |
| Gemini, **tool calling** `mode="ANY"` (2 consultas) | 2 de 2 válidas al primer intento; ≈ 990 tokens de entrada contra ≈ 660 sin tool (el schema viaja en cada llamada) |
| `gpt-6-luna` con `tool_choice` forzado, `temperature=0`, `reasoning_effort=none` | Funciona, con y sin `strict`; `strict` acepta `minLength`, `minimum`, `maximum` y `minItems`. ≈ 86 tokens de salida (tope 400). USD 0.00028 las dos |

### ¿Se perdió exactitud? v2 contra v6

`python -m src.comparar --versiones v2 v6 --etiqueta l3` (mismo set de 20 casos; reportes [Gemini](docs/comparacion_versiones_l3.md) y [OpenAI](docs/comparacion_versiones_openai_l3.md), USD 0.0035 la corrida con OpenAI):

| | Gemini v2 | Gemini v6 | `gpt-6-luna` v2 | `gpt-6-luna` v6 |
|---|---|---|---|---|
| Exactitud dev / holdout | 90 % / 100 % | 90 % / 100 % | 100 % / 100 % | 90 % / 100 % |
| Error en dev | NX-004 (R5) | NX-008 (R5) | — | NX-008 (R5) |
| Contratos válidos al primer intento | — | 20 / 20 | — | 20 / 20 |
| Tokens entrada / salida | 309 / 6 | 988 / 84 | 313 / 8 | 972 / 83 |
| USD × 10.000 con `gpt-6-luna` | 0.34 | 1.41 | 0.35 | 1.39 |

**Lectura:** en **holdout** (el lote que decide) v6 empata con v2 con los dos modelos, y los 40 contratos fueron válidos sin reintentos. En **dev** aparece una regresión consistente: los dos modelos mandan NX-008 ("quiero pasar del plan Básico al Pro, ¿cuánto me cobran la diferencia?") a `other` + `contact_sales` solo con v6. La hipótesis es que la descripción de `contact_sales` ("planes, precios…") atrae los cambios de plan. Es el mismo punto débil de siempre (regla R5): **se corrige en M1L5** con más casos de ese tipo y una v7 que combine el par de contraste de v5 con el contrato.

**Decisión (provisional hasta M1L5): `triage_v6` por defecto.** No clasifica mejor que v2; es la única que cumple el contrato del PI, y lo hace sin perder exactitud en holdout. v2 sigue disponible con `--prompt v2` si solo se necesita la categoría.

### Lecciones de la etapa M1L3

- **Pedir JSON no es garantizar JSON.** Con la instrucción en el prompt, Gemini acertó el contenido y aun así el programa no pudo leerlo (bloque de markdown). La sintaxis, la estructura y el dominio son tres problemas distintos.
- **Pydantic no le dice al modelo qué escribir: verifica después.** Tool calling actúa al generar (previene); Pydantic, después (detecta); el reintento recupera. Cada capa cubre lo que la anterior no garantiza, y ninguna elimina el error: lo que cambia es que el sistema lo detecta, lo distingue y decide.
- **Un contrato débil da falsa seguridad.** `category: str` deja pasar `"factura"`; hay que validar el **dominio**, y derivarlo de una única fuente de verdad para que contrato, schema y prompt no se desincronicen.
- **No todos los errores se tratan igual.** Una consulta vacía no se arregla reintentando (código 2, cero llamadas); un contrato inválido quizás sí (hasta 3 intentos, después código 4). Reintentar lo irresoluble gasta plata y esconde de quién es la culpa.
- **El formato tiene costo.** El schema viaja en cada llamada: v6 cuesta 4 veces más que v2 con `gpt-6-luna`. Es el precio de pedir respuesta y acciones, y conviene medirlo.
- **Cambiar qué se pide cambia cómo se clasifica.** Agregar las acciones al prompt movió el error de R5 de un caso a otro, sin tocar las reglas de clasificación. Y como `ACTIONS` vive en el código, cambiar una descripción cambia lo que recibe v6 sin que su YAML cambie: cualquier ajuste debe ser una versión nueva.
- **El contrato valida la forma, no el significado.** Respuestas que prometen "a la brevedad" o acciones que no coinciden con lo que dice `answer` pasan la validación: eso es trabajo de los guardrails de salida (M1L4) y de las evals (M1L5).
- **Verificar antes de adoptar.** La lección usa `gpt-4.1-mini` y Anthropic; acá se probó con la API real que `gpt-6-luna` y Gemini aceptan exactamente los parámetros que usa el código.

---

## Requisitos

- Windows 11 con PowerShell
- Python 3.12 o superior (probado con 3.13): https://www.python.org/downloads/ (al instalar, marcá **"Add python.exe to PATH"**)
- Git: https://git-scm.com/download/win
- Una API key de Gemini (gratis) y una de OpenAI (con crédito cargado)

## Instalación (PowerShell)

```powershell
# 1. Entrar a la carpeta del proyecto
cd C:\ruta\a\nubbix-triage

# 2. Crear el entorno virtual
py -m venv .venv

# 3. Activarlo (el prompt pasa a mostrar "(.venv)")
.\.venv\Scripts\Activate.ps1
#    Si PowerShell dice que la ejecución de scripts está deshabilitada, correr una sola vez:
#    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#    y volver a activar.

# 4. Instalar dependencias
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Crear el archivo de credenciales a partir del ejemplo y editarlo
Copy-Item .env.example .env
notepad .env
```

### Conseguir las API keys

**Gemini (gratis)**
1. Entrá a https://aistudio.google.com con tu cuenta de Google.
2. Botón **Get API key** → crear clave.
3. Pegala en `GEMINI_API_KEY` del `.env`.
4. **No actives la facturación (billing)** en ese proyecto de Google: así se mantiene gratis.
5. Los límites del plan gratuito se ven en Google AI Studio. La cuota diaria se reinicia a medianoche hora del Pacífico.

**OpenAI (pago)**
1. Entrá a https://platform.openai.com/api-keys y creá una clave.
2. En Billing cargá el crédito prepago mínimo y **no actives la recarga automática**: el crédito cargado funciona como un segundo tope de gasto, además de `BUDGET_USD`.
3. Pegala en `OPENAI_API_KEY` del `.env`.

> El `.env` nunca se sube a Git (está en `.gitignore`). Si una clave se filtra, revocala y creá otra.

---

## Uso

Siempre desde la raíz del proyecto y con el entorno virtual activo:

```powershell
# Triage de una consulta: devuelve el contrato (prompt de PROMPT_VERSION, por defecto triage_v6)
python -m src.main "me cobraron dos veces la suscripción de este mes"

# Lo mismo, mostrando tokens, costo, latencia e intentos (en la salida de errores)
python -m src.main --detalle "no me llega la factura de agosto"

# Usar una versión de salida texto (solo la categoría, para comparar)
python -m src.main --prompt v4 "no puedo entrar, mi usuario está bloqueado"

# Ver la respuesta a medida que se genera (solo con versiones de salida texto)
python -m src.main --prompt v2 --stream "el dashboard de reportes me tira error 500"

# Ver lo gastado, el presupuesto y cuánto costarían 10.000 consultas
python -m src.main --gasto

# Lo mismo, proyectando otra cantidad de consultas
python -m src.main --gasto --consultas 50000
```

Ejemplo real (19/11/2026, Gemini) de `python -m src.main --detalle "No me llega la factura de agosto"`. La salida estándar es **solo el contrato**, consumible por otro sistema sin transformaciones:

```json
{
  "answer": "Comprendemos que está necesitando la factura correspondiente al mes de agosto. Con mucho gusto vamos a revisar su caso y se la reenviaremos a la brevedad.",
  "confidence": 1.0,
  "category": "billing",
  "actions": ["resend_invoice", "request_more_info"]
}
```

y con `--detalle`, en la salida de errores:

```json
{"prompt_version": "triage_v6", "request_id": "804d0e553207", "intentos": 1, "provider": "gemini",
 "model": "gemini-3.5-flash-lite", "prompt_tokens": 975, "completion_tokens": 78, "total_tokens": 1053,
 "latency_s": 1.241, "cost_usd": 0.0}
```

> Si un intento no cumple el contrato, la CLI lo avisa en la salida de errores y reintenta. Los avisos nunca van a la salida estándar.
> Con una versión de salida texto (`--prompt v1` … `v5`) la CLI muestra la categoría parseada y las métricas, como en M1L2; con `--prompt v4` se agrega `model_output` y con `--stream`, `ttft_s`.

Ejemplo de `--gasto`:

```json
{
  "gastado_usd": 0.000152,
  "presupuesto_usd": 4.5,
  "restante_usd": 4.499848,
  "proyeccion": {
    "consultas": 10000,
    "base": {
      "llamadas_exitosas": 12,
      "promedio_tokens_entrada": 120.0,
      "promedio_tokens_salida": 8.0
    },
    "costo_usd_si_responde_solo": {
      "gpt-6-luna": 0.16,
      "gpt-4.1-mini": 0.608,
      "gemini-3.5-flash-lite": 0.0
    }
  }
}
```

La proyección toma el promedio de tokens de tus llamadas exitosas reales y calcula cuánto costarían N consultas si las respondiera solo cada modelo (como el ejercicio de 10.000 consultas de M1L1).

Códigos de salida: `0` ok · `2` consulta inválida (vacía) · `3` fallaron todos los proveedores · `4` contrato inválido después de todos los intentos · `5` error de configuración. En PowerShell se ven con `echo $LASTEXITCODE`.

### Métricas

`data/metrics.jsonl` tiene una línea por intento:

```json
{"timestamp": "2026-11-18T12:42:41+00:00", "provider": "gemini", "model": "gemini-3.5-flash-lite", "temperature": 0.0, "prompt_version": "triage_v1", "stream": false, "fallback": false, "status": "ok", "attempts": 1, "prompt_tokens": 84, "completion_tokens": 2, "total_tokens": 86, "latency_s": 0.612, "cost_usd": 0.0}
```

En modo `--stream` la línea incluye además `ttft_s`. Desde M1L2, las llamadas exitosas registran también `parse_method` (cómo se leyó la categoría) y `category_valid`. Desde M1L3, con las versiones de contrato cada intento registra además:

```json
{"request_id": "804d0e553207", "contract_attempt": 1, "parse_method": "tool_call", "contract_valid": true, "contract_errors": [], "category_valid": true}
```

`request_id` agrupa los intentos de una misma consulta; `contract_errors` guarda solo campo y tipo de error (por ejemplo `confidence:less_than_equal` o `tool:json_invalido`), nunca el texto.

`status` puede ser `ok`, `error` (con `error_kind`: `rate_limit`, `server_error`, `timeout`, `auth_error`…) o `skipped` (presupuesto agotado).

---

## Tests

Usan proveedores falsos: no llaman a ninguna API ni gastan cuota.

```powershell
python -m unittest discover -s tests -v
```

Son 110 pruebas. Las 62 de M1L3 cubren: el contrato (el ejemplo de la lección con 3 errores, confianza como texto o fuera de rango, `answer` vacío o largo, acciones vacías, inexistentes, repetidas o `no_action` combinada, campos de más o de menos, que las categorías y acciones del schema salgan de la única fuente de verdad y que el mensaje de error no repita el valor recibido) (`tests/test_contract.py`); el reintento (error inyectado en el system, solo el último, sin el valor recibido, intentos agotados, consulta vacía sin llamar al modelo, métricas por intento con `request_id` y sin texto, fallback del router con tool calling) (`tests/test_triage.py`); tool calling en cada SDK con objetos reales de `openai` y `google-genai` armados a mano (argumentos string vs. dict, JSON cortado, respuesta en texto, `mode="ANY"`, `strict`) (`tests/test_tool_calling.py`); la CLI (salida estándar = solo el contrato, códigos 2, 3 y 4, `--detalle`, `--stream` ignorado) (`tests/test_cli_l3.py`); y el prompt v6, el campo `salida`, la configuración nueva y el comparador con versiones de contrato (`tests/test_l3_prompts_config.py`). Las 29 de M1L2 (`tests/test_prompts.py`) cubren: carga y validación de los YAML (versión inexistente, campo faltante, variable mal escrita, llaves del JSON), que todas las versiones listen las 4 categorías, la separación plantilla/variables, el parser (JSON, última línea, una palabra, heurística, categoría inventada), la elección de versión por `--prompt` y su registro en las métricas, y el resumen del comparador (incluido que un error de la API no cuente como desacierto y que cada proveedor escriba en sus propios archivos). Las 19 de M1L1 (`tests/test_router.py`) cubren: uso del gratuito, fallback al pago, bloqueo por presupuesto, respeto de `Retry-After`, backoff con jitter, errores no reintentables, streaming parcial, campos de las métricas (incluida la `temperature`), TTFT en streaming, proyección de costo a 10.000 consultas, aviso cuando falla un proveedor y parámetros enviados a OpenAI (`max_completion_tokens`, `reasoning_effort`).

---

## Configuración (`.env`)

| Variable | Por defecto | Para qué |
|---|---|---|
| `GEMINI_API_KEY` | — | Clave de Gemini (gratis) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Modelo gratuito |
| `GEMINI_MAX_TOKENS` | `1024` | Tope de salida de Gemini (incluye tokens de "pensamiento") |
| `OPENAI_API_KEY` | — | Clave de OpenAI |
| `OPENAI_MODEL` | `gpt-6-luna` | Modelo pago de respaldo |
| `OPENAI_MAX_TOKENS` | `400` | Tope de salida de OpenAI (se envía como `max_completion_tokens`); 400 desde M1L3 porque el modelo también escribe la respuesta |
| `OPENAI_REASONING_EFFORT` | `none` | Esfuerzo de razonamiento; vacío para modelos que no razonan |
| `OPENAI_STRICT_TOOLS` | `true` | Tool calling con `strict`: OpenAI restringe la respuesta al schema del contrato |
| `LLM_TEMPERATURE` | `0.0` | Aleatoriedad (baja para clasificar) |
| `LLM_PROVIDER_ORDER` | `gemini,openai` | Primario, respaldo |
| `LLM_MAX_RETRIES` | `2` | Reintentos por proveedor |
| `LLM_MAX_WAIT_SECONDS` | `10` | Espera máxima aceptada antes de pasar al respaldo |
| `LLM_TIMEOUT_SECONDS` | `30` | Timeout por llamada |
| `BUDGET_USD` | `4.50` | Tope de gasto acumulado del proveedor pago |
| `PROMPT_VERSION` | `triage_v6` | Versión del prompt (`prompts/<version>.yaml`); `--prompt` la pisa en cada ejecución |
| `CONTRACT_MAX_ATTEMPTS` | `3` | Intentos para obtener un contrato válido (distinto de `LLM_MAX_RETRIES`, que es para errores de red) |

Si cambiás un modelo, agregá su precio en `config/pricing.json`; si falta, la CLI no arranca (así el costo siempre es auditable).

---

## Estructura

```
nubbix-triage/
├── .env.example          variables documentadas (sin claves reales)
├── .gitignore            solo se suben archivos de desarrollo
├── requirements.txt      dependencias con versión exacta
├── config/
│   └── pricing.json      precios por modelo (USD por 1M de tokens)
├── prompts/
│   ├── triage_v1.yaml … triage_v6.yaml   versiones del prompt (nunca se editan)
│   └── CHANGELOG.md      qué cambió, por qué y resultado medido
├── src/
│   ├── main.py           CLI (salida = contrato; códigos de salida tipados)
│   ├── prompts.py        carga de los YAML, categorías, acciones y plantilla + variables
│   ├── contract.py       contrato Pydantic + schema de la tool call (M1L3)
│   ├── triage.py         tool call → contrato → reintento con el error; errores tipados (M1L3)
│   ├── parsing.py        extracción de la categoría de las versiones de texto (v1…v5)
│   ├── comparar.py       comparación de versiones sobre el set etiquetado
│   ├── sonda_l3.py       prueba real de tool calling con cada proveedor (M1L3)
│   ├── config.py         lectura y validación del .env
│   ├── costs.py          cálculo de costo
│   ├── metrics.py        registro en data/metrics.jsonl
│   ├── retry.py          backoff con jitter y Retry-After
│   └── llm/
│       ├── base.py       LLMResponse, LLMProvider y errores tipados
│       ├── gemini_provider.py
│       ├── openai_provider.py
│       ├── factory.py    arma los proveedores según el .env
│       └── router.py     fallback + presupuesto + métricas
├── tests/                tests con unittest
├── evals/
│   ├── dataset.jsonl     20 consultas etiquetadas (dev/holdout; se amplía en L5)
│   └── guia_etiquetado.md  categorías y reglas de desempate
├── data/                 metrics.jsonl
└── docs/
    ├── DECISIONES.md     cada decisión con su fuente en Henry
    ├── ROADMAP.md        consigna del PI vs. estado
    ├── comparacion_versiones.md  reporte de la comparación de prompts (Gemini)
    ├── comparacion_detalle.csv   detalle por consulta y versión (Gemini)
    ├── comparacion_*_openai.*    lo mismo con gpt-6-luna (comparativa de modelos)
    ├── comparacion_*_l3.*        v2 vs v6 (M1L3), con Gemini y con gpt-6-luna
    └── sonda_l3_*.md             resultado de la sonda de tool calling (M1L3)
```

## Subir a Git

```powershell
git init
git add .
git status        # verificar que NO aparezcan .env ni .venv
git commit -m "feat: cliente multi-proveedor con fallback y presupuesto (M1L1)"
```

---

## Decisiones técnicas (resumen)

- **Por qué Gemini como primario**: tiene plan gratuito y el objetivo es gastar menos de USD 5 en el curso. La consigna pide dos proveedores, "por ejemplo OpenAI y Anthropic"; Anthropic no es gratuito.
- **Por qué gpt-6-luna y no lo que dice Henry**: M1L1 propone `gpt-4.1-mini`/`gpt-4.1-nano`, pero OpenAI dio de baja `gpt-4.1-nano` (las métricas registraron un `bad_request` el 18/11/2026 y el mensaje fue "404 model deprecated"). `gpt-6-luna` es el reemplazo que recomienda OpenAI y cuesta casi lo mismo. Se probó antes de adoptarlo: con `reasoning_effort="none"` acepta `temperature=0` y no gasta tokens de razonamiento.
- **Por qué temperature 0**: clasificar no es creativo; el mismo ticket tiene que recibir siempre la misma categoría (M1L1).
- **Por qué `triage_v6`**: empata con v2 en holdout con los dos modelos y es la única que produce el contrato del PI; provisional hasta M1L5. Ver "Contrato de salida" y [prompts/CHANGELOG.md](prompts/CHANGELOG.md).
- **Por qué tool calling y no solo Pydantic**: Pydantic verifica después; tool calling hace que el modelo acierte el formato al generar. Pedir el JSON en el prompt falló 2 de 2 veces con Gemini en la sonda.
- **Por qué Gemini y OpenAI y no OpenAI y Anthropic, como la lección**: es la pareja elegida en M1L1 (Gemini es gratis). La asimetría que enseña la lección se mantiene: Gemini entrega dict (como Anthropic) y OpenAI string.
- **Por qué Gemini sigue de primario aunque `gpt-6-luna` acertó más**: ver "Comparativa de modelos": es gratis y con el prompt vigente la diferencia es un solo caso ambiguo.
- **Por qué hay un set etiquetado ya en L2**: el ejemplo de L2 del profesor (caso OrbitAir) compara las versiones sobre 20 consultas en lotes dev/holdout. Es provisional y se amplía en L5.
- **Por qué no hay circuit breaker todavía**: la CLI procesa una consulta por ejecución y no guarda estado; se evalúa si se hace la API con FastAPI.

El detalle completo, con la lección de Henry que respalda cada punto, está en [docs/DECISIONES.md](docs/DECISIONES.md).

## Uso de IA en el desarrollo

La consigna pide documentar el uso de herramientas de IA. La estructura inicial y el código de las etapas M1L1, M1L2 y M1L3 se armaron con asistencia de Claude (Anthropic), a partir de la consigna del PI, las lecciones de Henry y el repo de ejemplo del profesor, y fueron revisados por el alumno. En M1L2, Claude redactó las consultas del set y propuso sus etiquetas; el alumno las revisó y confirmó antes de la primera corrida. En M1L3, Claude propuso el plan, el contrato y el prompt v6; el alumno eligió la lista de acciones, el trato ("usted"), los códigos de salida y aprobó v6 como versión por defecto, y corrió todas las pruebas reales.

## Limitaciones conocidas

- La comparación de prompts usa solo 20 casos: la elección de `triage_v6` es provisional hasta M1L5. v6 falla en dev un caso de la regla R5 (NX-008) que v2 acierta con `gpt-6-luna`.
- El contrato valida forma y dominio, no el significado: `answer` puede prometer plazos ("a la brevedad") o no coincidir con las acciones. Lo cubren los guardrails de M1L4 y las evals de M1L5.
- `confidence` lo informa el propio modelo y no está calibrado (Gemini suele poner 1.0).
- No hay guardrails de seguridad (llegan en M1L4) ni la suite de evals completa (M1L5).
- Si se borra `data/metrics.jsonl`, el contador de presupuesto vuelve a cero.
- El plan gratuito de Gemini puede usar lo que se le envía para mejorar los productos de Google: no enviar datos reales de clientes.
