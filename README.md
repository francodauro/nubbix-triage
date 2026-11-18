# Nubbix Triage

Servicio de triage de consultas de soporte para **Nubbix** (SaaS de gestión para pymes de LATAM).
Proyecto integrador del Módulo 1 de la carrera **AI Engineering de Henry** (cohorte Ai-Engineering-Pt08).

Recibe la consulta de un cliente en texto libre y la clasifica en `billing`, `technical`, `account` u `other`, usando un LLM gratuito con respaldo pago y registrando tokens, latencia y costo de cada llamada.

> **Estado actual: etapa M1L1 completa** (cliente multi-proveedor, reintentos, streaming con TTFT, costo, proyección a 10.000 consultas y presupuesto).
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

Una clasificación usa unos 120 tokens de entrada y menos de 10 de salida. Con `gpt-6-luna` eso es alrededor de **USD 0.000016 por consulta**, y solo se paga cuando Gemini falla. Con USD 4.50 alcanza para cientos de miles de consultas pagas.

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
# Clasificar una consulta
python -m src.main "me cobraron dos veces la suscripción de este mes"

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

En modo `--stream` la línea incluye además `ttft_s`.

`status` puede ser `ok`, `error` (con `error_kind`: `rate_limit`, `server_error`, `timeout`, `auth_error`…) o `skipped` (presupuesto agotado).

---

## Tests

Usan proveedores falsos: no llaman a ninguna API ni gastan cuota.

```powershell
python -m unittest discover -s tests -v
```

Son 19 pruebas. Cubren: uso del gratuito, fallback al pago, bloqueo por presupuesto, respeto de `Retry-After`, backoff con jitter, errores no reintentables, streaming parcial, campos de las métricas (incluida la `temperature`), TTFT en streaming, proyección de costo a 10.000 consultas, aviso cuando falla un proveedor y parámetros enviados a OpenAI (`max_completion_tokens`, `reasoning_effort`).

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
│   └── triage_v1.txt     prompt de clasificación (provisional; pasa a YAML en L2)
├── src/
│   ├── main.py           CLI
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
├── evals/                (L5) dataset y runner de evals
├── data/                 metrics.jsonl
└── docs/
    ├── DECISIONES.md     cada decisión con su fuente en Henry
    └── ROADMAP.md        consigna del PI vs. estado
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
- **Por qué no hay circuit breaker todavía**: la CLI procesa una consulta por ejecución y no guarda estado; se evalúa si se hace la API con FastAPI.

El detalle completo, con la lección de Henry que respalda cada punto, está en [docs/DECISIONES.md](docs/DECISIONES.md).

## Uso de IA en el desarrollo

La consigna pide documentar el uso de herramientas de IA. La estructura inicial y el código de la etapa M1L1 se armaron con asistencia de Claude (Anthropic), a partir de la consigna del PI y las lecciones de Henry, y fueron revisados por el alumno.

## Limitaciones conocidas

- La salida todavía no es el contrato JSON de la consigna (llega en M1L3).
- No hay guardrails de seguridad (llegan en M1L4) ni evals (M1L5).
- Si se borra `data/metrics.jsonl`, el contador de presupuesto vuelve a cero.
- El plan gratuito de Gemini puede usar lo que se le envía para mejorar los productos de Google: no enviar datos reales de clientes.
