# CHANGELOG — prompt de triage de Nubbix

> Regla (M1L2): **nunca se edita una versión anterior**. Cada cambio es un archivo nuevo en `prompts/`.
> Cada entrada registra **qué cambió**, **por qué** y **qué resultado se espera medir**; el resultado
> medido se completa con `python -m src.comparar` (reporte en `docs/comparacion_versiones.md`).
> Las mediciones de M1L2 son provisionales (20 casos); la elección con datos llega en M1L5.

## triage_v1 — 2026-11-18 · zero-shot
- **Qué:** rol de agente de soporte de Nubbix, las 4 categorías incluida `other`, respuesta de una sola
  palabra. La consulta va como mensaje de usuario, sin delimitadores. Es la migración literal del
  `triage_v1.txt` provisional de M1L1 (D-21 → D-36).
- **Por qué:** línea base. Es el prompt con el que se hicieron las pruebas reales de M1L1.
- **Resultado esperado:** categorías correctas en casos claros; respuestas que no respetan el formato o
  confusiones en los casos que necesitan reglas de desempate (R1–R5 de `evals/guia_etiquetado.md`).
- **Resultado medido (19/11/2026, Gemini, 2 corridas):** dev 70 % · holdout 90 %. Respetó el formato
  en las 20 respuestas, pero confundió permisos de usuario con un problema técnico (NX-003) y mandó
  preguntas comerciales a `billing`/`technical` (NX-004, NX-009). Idéntico en las dos corridas.

## triage_v2 — 2026-11-19 · zero-shot con los 4 principios
- **Qué:** rol acotado ("derivas, no respondes"), contexto de quién escribe y cómo llega la consulta,
  consulta delimitada con `<consulta>…</consulta>`, categorías con descripción inyectadas desde el código
  (`{categorias}`, única fuente de verdad en `src/prompts.py`) y salida JSON de una línea
  `{"category": "..."}`.
- **Por qué:** M1L2 (4 principios; listar todas las categorías con su cajón; delimitadores) y el ejemplo del
  profesor, donde v2 fue el mayor salto de exactitud. El JSON permite leer la respuesta con `json.loads` en
  lugar de adivinar (video 1 de la lección).
- **Resultado esperado:** igual o más exactitud que v1, con 0 respuestas inválidas y parseo siempre por
  JSON; más tokens de entrada (más costo).
- **Resultado medido (19/11/2026, Gemini, 2 corridas):** dev 90 % · holdout **100 %** · 0 inválidas, siempre
  por JSON. 309 tokens de entrada y 6 de salida (≈ USD 0.34 cada 10.000 consultas con `gpt-6-luna`, el triple
  que v1). Único error en dev: NX-004 (R5, pregunta de descuentos → `billing`). Idéntico en las dos corridas.

## triage_v3 — 2026-11-19 · few-shot (sobre v2) · DESCARTADA
- **Qué:** v2 + un ejemplo few-shot por cada error de v2 en el lote dev. Hubo un solo error (NX-004, regla
  R5), así que hay un ejemplo, redactado de nuevo: "¿Hay algún precio promocional para pymes que recién
  empiezan?" → `other`.
- **Por qué:** M1L2 ("un ejemplo útil representa la ambigüedad real donde el modelo falló") y el ejemplo del
  profesor (v3 armada con los errores de v2).
- **Resultado esperado:** acertar NX-004 sin empeorar el resto, con unos 30 tokens más de entrada.
- **Resultado medido (19/11/2026, Gemini):** **igual que v2** (dev 90 %, holdout 100 %) y NX-004 sigue en
  `billing`. Cuesta un 9 % más (342 tokens de entrada). Se descarta y se conserva el archivo.
- **Lectura:** el ejemplo era demasiado fácil (M1L2: "si tras agregarlo el modelo responde igual, el ejemplo
  era demasiado fácil"; es el mismo caso que `v3_obvio` en el ejemplo del profesor). "Precio promocional"
  es claramente comercial; NX-004 ("descuento si pagamos el año por adelantado") se parece a cambiar la forma
  de pago de lo ya contratado, que por R5 sería `billing`. No se escribió otro ejemplo calcado de NX-004:
  sería ajustar el prompt a un único caso ambiguo. Queda como pendiente para M1L5 (ver D-49).

## triage_v4 — 2026-11-19 · chain-of-thought (sobre v2)
- **Qué:** v2 + razonamiento en 3 líneas fijas (`Tema` / `Pide` / `Decisión`), con la instrucción de
  clasificar lo que el cliente **pide** y no lo que menciona, y última línea `CATEGORIA: <valor>` que el
  código lee sin mirar el resto. Reemplaza el JSON de v2. No incluye los ejemplos de v3 (se escribió antes de
  medir v2; combinarlos sería otra versión).
- **Por qué:** M1L2 (CoT con salida parseable; "¿mejoró lo suficiente para justificar el costo?") y el ejemplo
  del profesor (v4 mejoró solo en holdout y costó ~60 % más).
- **Resultado esperado:** acierta mejor los casos con reglas R2–R4 (lo que pide vs. lo que menciona), con
  bastantes más tokens de salida y latencia que v2.
- **Resultado medido (19/11/2026, Gemini, 2 corridas):** holdout 100 % en las dos. En dev, 90 % en la primera
  corrida y 100 % en la segunda: **NX-004 cambió de respuesta** (`billing` → `other`) con temperature 0, así que
  ese acierto no es estable. Unos 65 tokens de salida (máximo 87, dentro del tope de 200: D-47 verificada),
  ≈ USD 0.70 cada 10.000 consultas (el doble que v2) y la latencia más alta de las cuatro versiones.

## Decisión vigente (19/11/2026, provisional hasta M1L5)

**`triage_v2` queda como versión por defecto** (`PROMPT_VERSION`, D-48).
- En **holdout** (el lote que no se miró para diseñar) v2, v3 y v4 empatan en 100 %; v1 queda en 90 %.
- **Costo:** v2 es la más barata de las que empatan (USD 0.34 cada 10.000 consultas contra 0.37 de v3 y 0.70
  de v4).
- **Consistencia:** v2 dio exactamente las mismas respuestas en las dos corridas, siempre en JSON. v4 cambió
  una respuesta entre corridas.
- Lo único que v4 hace mejor es acertar NX-004 en una de dos corridas: no justifica pagar el doble (M1L2: "¿mejoró
  lo suficiente para justificar el costo?").
- Con 20 casos, un caso son 10 puntos: la decisión con datos se repite en M1L5 con ≥30 casos y score por
  categoría.
