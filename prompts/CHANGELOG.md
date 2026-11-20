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
- **Resultado medido (19/11/2026, Gemini, 3 corridas):** holdout 100 % en las tres. En NX-004 respondió
  `billing`, `other` y `billing`: **cambió de respuesta entre corridas** con temperature 0, así que ese acierto
  no es estable. Unos 65 tokens de salida (máximo 87, dentro del tope de 200: D-47 verificada),
  ≈ USD 0.70 cada 10.000 consultas (el doble que v2) y la latencia más alta de las cuatro versiones.

## triage_v5 — 2026-11-19 · few-shot con par de contraste (sobre v2)
- **Qué:** v2 + dos ejemplos del mismo tema (pagar por un período más largo) con distinta intención:
  "Si pasamos a pagar cada seis meses, ¿nos hacen alguna bonificación?" → `other` (pregunta una condición
  comercial) y "A partir del mes que viene quiero que me cobren el plan anual en vez de mensual." → `billing`
  (pide un cambio sobre lo que ya le cobran). Sin ninguna instrucción extra, para medir solo el efecto de los
  ejemplos. Se apoya en v2, no en v3.
- **Por qué:** v3 mostró que un ejemplo obvio no aporta. El Cierre de M1L2 pide "un ejemplo que represente un
  caso ambiguo real, no uno obvio": la ambigüedad real de NX-004 es *preguntar* vs. *pedir un cambio* sobre el
  pago, no "precio = comercial".
- **Resultado esperado:** NX-004 → `other` sin que los ejemplos empujen casos de `billing` del holdout a `other`.
- **Resultado medido (19/11/2026, Gemini, 1 corrida):** dev **100 %**, holdout **100 %**, 0 inválidas. Acertó
  NX-004 y no rompió ningún `billing` del holdout. 378 tokens de entrada (≈ USD 0.41 cada 10.000 consultas con
  `gpt-6-luna`, un 21 % más que v2). El 100 % de dev está inflado por diseño: los ejemplos se eligieron mirando dev.

## triage_v6 — 2026-11-19 · v2 + contrato por tool calling (M1L3)
- **Qué:** v2 adaptada al contrato del PI `{answer, confidence, category, actions}`. El rol pasa de "derivas, no
  respondes" a clasificar, proponer una primera respuesta para el cliente (de "usted") y sugerir acciones para el
  agente humano. Se agregan reglas para la respuesta (breve, en el idioma de la consulta, pedir los datos que faltan,
  no prometer devoluciones ni plazos ni inventar políticas), qué significa la confianza y la lista de acciones
  (`{acciones}`, desde `ACTIONS` en `src/prompts.py`). Se quita "Responde SOLO con un JSON…": con tool calling el
  formato lo define el schema de la función. Campo nuevo `salida: contrato`. Se apoya en v2, no en v5.
- **Por qué:** M1L3 (contrato validado, tool calling; la instrucción de formato textual "queda obsoleta" y contradice
  al schema) y el ejemplo del profesor (su `triage_v5.txt` de L3). Partir de v2 aísla un solo cambio: el mecanismo de
  salida.
- **Resultado esperado:** la misma exactitud que v2, contratos válidos (pocos o ningún reintento) y bastante más costo
  (schema en cada llamada + la respuesta escrita).
- **Resultado medido (19/11/2026, `python -m src.comparar --versiones v2 v6 --etiqueta l3`, 1 corrida por modelo):**
  - Gemini: dev 90 % · holdout **100 %**, igual que v2. El error de dev cambió de lugar: v6 acierta NX-004 (que v2
    falla) y falla NX-008 (`billing` → `other` + `contact_sales`, confianza 0.85). Ambos son R5.
  - `gpt-6-luna`: dev 90 % · holdout **100 %**; v2 dio 100 % / 100 %. v6 falla NX-008 igual que con Gemini (confianza
    0.99).
  - 40 de 40 contratos válidos al primer intento (intentos medios 1.00). ≈ 975 tokens de entrada y ≈ 83 de salida:
    USD 1.39–1.41 cada 10.000 consultas con `gpt-6-luna`, unas 4 veces v2.
- **Lectura:** el contrato no hizo perder exactitud en holdout. La regresión de NX-008 en dev aparece con los dos
  modelos solo con v6: no parece ruido. Hipótesis: la descripción de la acción `contact_sales` ("planes, precios,
  descuentos o condiciones") atrae los cambios de plan hacia ventas. Agregar lo que se pide (acciones) cambió cómo se
  clasifica, sin tocar las reglas de clasificación.

## Decisión vigente (19/11/2026, provisional hasta M1L5) — actualizada en M1L3

**`triage_v6` es la versión por defecto desde M1L3** (D-69): empata con v2 en holdout con los dos modelos y es la
única que produce el contrato del PI. No clasifica mejor que v2 y cuesta unas 4 veces más; v2 sigue disponible con
`--prompt v2`. La regresión de NX-008 (R5) se corrige en M1L5 con más casos de ese tipo y una v7 = par de contraste
de v5 + contrato. Lo que sigue es la decisión de M1L2, que v6 hereda (v6 = v2 + contrato).

### Decisión de M1L2

**`triage_v2` sigue como versión por defecto** (`PROMPT_VERSION`, D-48, D-51); **`triage_v5` queda como candidata
para M1L5.**
- En **holdout** (el lote que no se miró para diseñar) v2, v3, v4 y v5 empatan en 100 %; v1 queda en 90 %. El
  holdout no distingue entre ellas.
- **Costo:** v2 es la más barata de las que empatan (USD 0.34 cada 10.000 consultas; v3 0.37, v5 0.41, v4 0.70).
- **Consistencia:** v2 dio las mismas respuestas en las tres corridas, siempre en JSON. v4 cambió NX-004 entre
  corridas. v5 se midió una sola vez.
- La ventaja de v5 (acertar NX-004) está en dev, el lote con el que se diseñaron sus ejemplos: es la mejora
  esperada, no una evidencia independiente. Que generalice se prueba en M1L5 con más casos del tipo de R5; si allí
  acierta más sin perder consistencia, el 21 % de costo extra se justifica y pasa a ser la vigente.
- v4 no justifica pagar el doble (M1L2: "¿mejoró lo suficiente para justificar el costo?").
- Con 20 casos, un caso son 10 puntos: la decisión con datos se repite en M1L5 con ≥30 casos y score por
  categoría.

### Mismos prompts con `gpt-6-luna` (19/11/2026, 1 corrida, comparativa de modelos, D-53)
Reporte: `docs/comparacion_versiones_openai.md`. Dev / holdout: v1 90 % / 90 % · **v2 100 % / 100 %** ·
v3 100 % / 100 % · v4 100 % / 90 % · v5 100 % / 100 %.
- v2 también es la mejor (o empatada) con el modelo pago: acierta NX-004 sin necesidad de ejemplos. La ventaja de
  v5 sobre v2 aparece solo con Gemini.
- v4 (CoT) falló NX-015 en holdout: razonó "las funciones del plan no se activaron" → `technical`, aunque lo
  primero que pide el cliente es la devolución (R1). El razonamiento explícito también puede llevar a un error.
