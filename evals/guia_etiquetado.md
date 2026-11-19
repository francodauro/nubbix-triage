# Guía de etiquetado — triage de Nubbix

Cada consulta lleva **exactamente una** categoría: la del equipo que tiene que actuar.
La etiqueta la decide una persona **antes** de correr el modelo (M1L5: "referencia fijada antes de correr el
modelo y documentada"). Si dos personas etiquetan distinto la misma consulta, el problema es la guía: se
agrega o ajusta una regla.

Fuente del formato: guía de etiquetado del ejemplo del profesor (`L2/orbitair/data/guia_etiquetado.md`).
Las categorías salen de la consigna del PI; las descripciones y las reglas de desempate son **propias**
(Henry no las define para Nubbix).

| Categoría | Equipo / qué cubre |
|---|---|
| `billing` | La suscripción a Nubbix y lo que **Nubbix le cobra al cliente**: cobros duplicados o indebidos, devoluciones, facturas que emite Nubbix, medios de pago, cambio del plan contratado |
| `technical` | Algo de la plataforma **no funciona** como debería: errores, caídas, lentitud, integraciones desconectadas, la app que se cierra, reportes o exportaciones con datos mal calculados |
| `account` | La cuenta y los **accesos**: alta y baja de usuarios, roles y permisos, inicio de sesión, contraseñas, bloqueos, email del administrador, datos de la empresa |
| `other` | Todo lo que no encaja arriba: preguntas comerciales antes de contratar (precios, descuentos, planes), si Nubbix tiene una función, horarios de atención |

## Reglas de desempate

- **R1 — El dinero que cobra Nubbix manda.** Si el cliente reclama un cobro de Nubbix o pide una
  devolución, va a `billing`, aunque el motivo sea un usuario, un plan o una función.
- **R2 — Lo que falla es `technical`, aunque hable de facturas o pagos.** Si no funciona la facturación
  electrónica que el cliente emite a *sus* clientes, o una integración de cobros (Mercado Pago), es una
  falla de la plataforma, no un tema de la suscripción.
- **R3 — "No puedo entrar": acceso propio vs. caída general.** Si el problema es de su usuario
  (contraseña, bloqueo, permisos, recuperar el acceso) va a `account`; si la plataforma no carga para nadie
  o da un error del sistema, va a `technical`.
- **R4 — Se clasifica lo que pide, no lo que menciona.** Si hay varias preguntas, gana la primera acción
  concreta que pide.
- **R5 — Antes de contratar es `other`; sobre lo ya contratado es `billing`.** Preguntar precios,
  descuentos o planes para empresas más grandes es `other`; cambiar el plan que ya paga o el medio de pago
  es `billing`.

## Lotes

| Lote | Para qué |
|---|---|
| `dev` | Para **diseñar**: se miran sus errores para decidir cambios (por ejemplo, los ejemplos few-shot de v3) |
| `holdout` | Para **medir al final**. No se miran sus errores mientras se diseña: si se usaran para ajustar el prompt, dejaría de medir |

Ambos lotes tienen las 4 categorías, incluida `other` (un prompt sin cajón puede parecer perfecto si el
set no tiene ninguna consulta `other`).

Regla para los ejemplos few-shot: **nunca copiar una consulta del set** como ejemplo; se escribe un caso
parecido. Si se copia, el modelo "ve la respuesta" y la exactitud sube sin que haya aprendido nada.

Este set es **provisional** (M1L2, D-45): en M1L5 se amplía a ≥30 casos para la suite de evals del PI.
