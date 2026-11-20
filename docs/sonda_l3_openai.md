# Sonda de M1L3 · openai (`gpt-6-luna`)

Generado por `python -m src.sonda_l3 --proveedor openai` el 2026-11-20 00:33 UTC. Prompt `triage_v6`, temperature 0.0, strict según el modo, reasoning_effort `none`.

| Modo | Consulta | ¿JSON legible? | ¿Cumple el contrato? | Tokens entrada / salida | finish | USD |
|---|---|---|---|---|---|---:|
| tool_calling_strict | Me cobraron dos veces la suscripción de este … | sí | sí | 975 / 87 | tool_calls | 0.000141 |
| tool_calling_sin_strict | Me cobraron dos veces la suscripción de este … | sí | sí | 975 / 85 | tool_calls | 0.000140 |

## Salidas

**tool_calling_strict** · Me cobraron dos veces la suscripción de este …

```
{"answer":"Entiendo que se le cobró dos veces la suscripción de este mes y solicita la devolución de uno de los cargos. Revisaremos el cobro; para identificarlo, ¿podría compartir las fechas y los importes de ambos cargos?","confidence":0.99,"category":"billing","actions":["review_charge","request_more_info"]}
```

**tool_calling_sin_strict** · Me cobraron dos veces la suscripción de este …

```
{"category":"billing","answer":"Entiendo que se le cobró dos veces la suscripción de este mes y solicita la devolución de uno de los cargos. Revisaremos el cobro; para identificarlo, compártanos las fechas y los importes de ambos cargos.","confidence":0.99,"actions":["review_charge","request_more_info"]}
```
