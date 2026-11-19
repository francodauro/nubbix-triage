# Nubbix Triage

Servicio de triage de consultas de soporte para **Nubbix** (SaaS de gestión para pymes de LATAM).
Proyecto integrador del Módulo 1 de la carrera **AI Engineering de Henry** (cohorte Ai-Engineering-Pt08).

Recibe la consulta de un cliente en texto libre y la clasifica en `billing`, `technical`, `account` u `other`, usando un LLM gratuito con respaldo pago y registrando tokens, latencia y costo de cada llamada.

> **Estado actual: etapa M1L2 completa** (prompts versionados en YAML con changelog, 4 versiones comparadas sobre un set etiquetado; vigente `triage_v2`). M1L1: cliente multi-proveedor, reintentos, streaming con TTFT, costo, proyección a 10.000 consultas y presupuesto.
> Lo que falta para cumplir la consigna completa está en [docs/ROADMAP.md](docs/ROADMAP.md).
> Cada decisión y su fuente en el material de Henry están en [docs/DECISIONES.md](docs/DECISIONES.md).

---

## Cómo funciona

```
consulta ──► Gemini (gratis) ──ok──► respuesta + métricas
                 │
           falla (después de reintentos)
                 ▼
        ¿queda presupuesto? ──no──► error controlado
                 │ sí
                 ▼
           OpenAI gpt-6-luna (pago) ──► respuesta + métricas
```

- **Primario gratuito**: Google Gemini `gemini-3.5-flash-lite` (plan gratuito de la API de Gemini).
- **Respaldo pago**: OpenAI `gpt-6-luna` (USD 0.10 por 1M de tokens de entrada, USD 0.50 por 1M de salida), con `reasoning_effort="none"` para que no "piense" (más barato y rápido).
- **Avisos**: si un proveedor falla, la CLI lo avisa en pantalla con el motivo, aunque el otro haya respondido.
- **Reintentos** (M1L1): solo para 429, 5xx, timeouts y errores de red; backoff exponencial con jitter; si el proveedor manda `Retry-After`, se respeta. Si pide esperar más de 10 s, se pasa directo al respaldo.
- **Presupuesto**: antes de llamar al proveedor pago se suma lo gastado en `data/metrics.jsonl`; si llegó a `BUDGET_USD` (USD 4.50 por defecto), no se lo llama.
- **Métricas**: cada intento queda en `data/metrics.jsonl` (sin el texto de la consulta).

### ¿Cuánto cuesta?

Con el prompt vigente (`triage_v2`) una clasificación usa unos 310 tokens de entrada y 6 de salida. Con `gpt-6-luna` eso es alrededor de **USD 0.000034 por consulta** (USD 0.34 cada 10.000), y solo se paga cuando Gemini falla. Con USD 4.50 alcanza para más de cien mil consultas pagas.

---

## Prompts y context engineering (M1L2)

Los prompts viven en `prompts/triage_vN.yaml`, fuera del código. Cada versión es un archivo nuevo que **nunca se edita**; qué cambió, por qué y qué resultado dio está en [prompts/CHANGELOG.md](prompts/CHANGELOG.md).

| Versión | Técnica | Qué agrega |
|---|---|---|
| `triage_v1` | zero-shot | Línea base: rol, las 4 categorías con `other`, respuesta de una palabra (migración del `.txt` de M1L1) |
| `triage_v2` ✅ | zero-shot con los 4 principios | Rol, contexto, tarea y formato; consulta entre `<consulta>…</consulta>`; categorías con descripción inyectadas desde el código; salida `{"category": "..."}` |
| `triage_v3` | few-shot | v2 + un ejemplo obvio a partir del único error de v2 en dev (descartada: no cambió nada) |
| `triage_v4` | chain-of-thought | v2 + razonamiento `Tema` / `Pide` / `Decisión` y última línea `CATEGORIA: <valor>` |
| `triage_v5` | few-shot con par de contraste | v2 + dos ejemplos del mismo tema con distinta intención (preguntar una condición → `other`; pedir un cambio en lo que se paga → `billing`). Candidata para M1L5 |

**Plantilla y variables.** El YAML tiene la plantilla fija (`system` y `user`); en cada consulta `src/prompts.py` completa `{empresa}`, `{categorias}` (de `CATEGORIES`, única fuente de verdad) y `{consulta}`. Las llaves literales del JSON van dobles (`{{ }}`); si una plantilla usa una variable desconocida, el error aparece al cargarla, antes de llamar al modelo.

**Lectura de la respuesta.** `src/parsing.py` busca la categoría en este orden: JSON → última línea `CATEGORIA:` → respuesta de una sola palabra → nombre válido suelto en el texto, y registra el método usado. Si la categoría es inválida, la CLI avisa (el reintento llega en M1L3).

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

**Técnica elegida: `triage_v2` (zero-shot con los 4 principios).** En holdout empata con v3, v4 y v5, es la más barata y respondió igual en las tres corridas. Lo que se aprendió de cada técnica:
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
# Clasificar una consulta (con el prompt de PROMPT_VERSION, por defecto triage_v2)
python -m src.main "me cobraron dos veces la suscripción de este mes"

# Elegir otra versión del prompt sin tocar código
python -m src.main --prompt v4 "no puedo entrar, mi usuario está bloqueado"

# Ver la respuesta a medida que se genera
python -m src.main --stream "el dashboard de reportes me tira error 500"

# Ver lo gastado, el presupuesto y cuánto costarían 10.000 consultas
python -m src.main --gasto

# Lo mismo, proyectando otra cantidad de consultas
python -m src.main --gasto --consultas 50000
```

Ejemplo de respuesta:

```json
{
  "category": "billing",
  "category_valid": true,
  "parse_method": "json",
  "prompt_version": "triage_v2",
  "provider": "gemini",
  "model": "gemini-3.5-flash-lite",
  "prompt_tokens": 84,
  "completion_tokens": 2,
  "total_tokens": 86,
  "latency_s": 0.612,
  "cost_usd": 0.0
}
```

> Los números son ilustrativos. El contrato final `{answer, confidence, category, actions}` se implementa en la etapa M1L3.
> Con `--prompt v4` se agrega `model_output` con el razonamiento completo del modelo (solo en pantalla; nunca se guarda en las métricas).
> Con `--stream` se agrega `ttft_s`: segundos hasta que apareció el primer fragmento (la latencia que percibe el usuario, M1L1).

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

Códigos de salida: `0` ok · `2` error de configuración o consulta vacía · `3` fallaron todos los proveedores.

### Métricas

`data/metrics.jsonl` tiene una línea por intento:

```json
{"timestamp": "2026-11-18T12:42:41+00:00", "provider": "gemini", "model": "gemini-3.5-flash-lite", "temperature": 0.0, "prompt_version": "triage_v1", "stream": false, "fallback": false, "status": "ok", "attempts": 1, "prompt_tokens": 84, "completion_tokens": 2, "total_tokens": 86, "latency_s": 0.612, "cost_usd": 0.0}
```

En modo `--stream` la línea incluye además `ttft_s`. Desde M1L2, las llamadas exitosas registran también `parse_method` (cómo se leyó la categoría) y `category_valid`.

`status` puede ser `ok`, `error` (con `error_kind`: `rate_limit`, `server_error`, `timeout`, `auth_error`…) o `skipped` (presupuesto agotado).

---

## Tests

Usan proveedores falsos: no llaman a ninguna API ni gastan cuota.

```powershell
python -m unittest discover -s tests -v
```

Son 48 pruebas. Las 29 de M1L2 (`tests/test_prompts.py`) cubren: carga y validación de los YAML (versión inexistente, campo faltante, variable mal escrita, llaves del JSON), que todas las versiones listen las 4 categorías, la separación plantilla/variables, el parser (JSON, última línea, una palabra, heurística, categoría inventada), la elección de versión por `--prompt` y su registro en las métricas, y el resumen del comparador (incluido que un error de la API no cuente como desacierto y que cada proveedor escriba en sus propios archivos). Las 19 de M1L1 (`tests/test_router.py`) cubren: uso del gratuito, fallback al pago, bloqueo por presupuesto, respeto de `Retry-After`, backoff con jitter, errores no reintentables, streaming parcial, campos de las métricas (incluida la `temperature`), TTFT en streaming, proyección de costo a 10.000 consultas, aviso cuando falla un proveedor y parámetros enviados a OpenAI (`max_completion_tokens`, `reasoning_effort`).

---

## Configuración (`.env`)

| Variable | Por defecto | Para qué |
|---|---|---|
| `GEMINI_API_KEY` | — | Clave de Gemini (gratis) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Modelo gratuito |
| `GEMINI_MAX_TOKENS` | `1024` | Tope de salida de Gemini (incluye tokens de "pensamiento") |
| `OPENAI_API_KEY` | — | Clave de OpenAI |
| `OPENAI_MODEL` | `gpt-6-luna` | Modelo pago de respaldo |
| `OPENAI_MAX_TOKENS` | `200` | Tope de salida de OpenAI (se envía como `max_completion_tokens`) |
| `OPENAI_REASONING_EFFORT` | `none` | Esfuerzo de razonamiento; vacío para modelos que no razonan |
| `LLM_TEMPERATURE` | `0.0` | Aleatoriedad (baja para clasificar) |
| `LLM_PROVIDER_ORDER` | `gemini,openai` | Primario, respaldo |
| `LLM_MAX_RETRIES` | `2` | Reintentos por proveedor |
| `LLM_MAX_WAIT_SECONDS` | `10` | Espera máxima aceptada antes de pasar al respaldo |
| `LLM_TIMEOUT_SECONDS` | `30` | Timeout por llamada |
| `BUDGET_USD` | `4.50` | Tope de gasto acumulado del proveedor pago |
| `PROMPT_VERSION` | `triage_v2` | Versión del prompt (`prompts/<version>.yaml`); `--prompt` la pisa en cada ejecución |

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
│   ├── triage_v1.yaml … triage_v5.yaml   versiones del prompt (nunca se editan)
│   └── CHANGELOG.md      qué cambió, por qué y resultado medido
├── src/
│   ├── main.py           CLI
│   ├── prompts.py        carga de los YAML, categorías y plantilla + variables
│   ├── parsing.py        extracción de la categoría de la respuesta
│   ├── comparar.py       comparación de versiones sobre el set etiquetado
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
    └── comparacion_*_openai.*    lo mismo con gpt-6-luna (comparativa de modelos)
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
- **Por qué `triage_v2`**: ver "Comparación de versiones" arriba y [prompts/CHANGELOG.md](prompts/CHANGELOG.md).
- **Por qué Gemini sigue de primario aunque `gpt-6-luna` acertó más**: ver "Comparativa de modelos": es gratis y con el prompt vigente la diferencia es un solo caso ambiguo.
- **Por qué hay un set etiquetado ya en L2**: el ejemplo de L2 del profesor (caso OrbitAir) compara las versiones sobre 20 consultas en lotes dev/holdout. Es provisional y se amplía en L5.
- **Por qué no hay circuit breaker todavía**: la CLI procesa una consulta por ejecución y no guarda estado; se evalúa si se hace la API con FastAPI.

El detalle completo, con la lección de Henry que respalda cada punto, está en [docs/DECISIONES.md](docs/DECISIONES.md).

## Uso de IA en el desarrollo

La consigna pide documentar el uso de herramientas de IA. La estructura inicial y el código de las etapas M1L1 y M1L2 se armaron con asistencia de Claude (Anthropic), a partir de la consigna del PI, las lecciones de Henry y el repo de ejemplo del profesor, y fueron revisados por el alumno. En M1L2, Claude redactó las consultas del set y propuso sus etiquetas; el alumno las revisó y confirmó antes de la primera corrida.

## Limitaciones conocidas

- La salida todavía no es el contrato JSON de la consigna (llega en M1L3); una categoría inválida se avisa pero no se reintenta.
- La comparación de prompts usa solo 20 casos: la elección de `triage_v2` es provisional hasta M1L5.
- No hay guardrails de seguridad (llegan en M1L4) ni la suite de evals completa (M1L5).
- Si se borra `data/metrics.jsonl`, el contador de presupuesto vuelve a cero.
- El plan gratuito de Gemini puede usar lo que se le envía para mejorar los productos de Google: no enviar datos reales de clientes.
