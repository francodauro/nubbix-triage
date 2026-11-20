"""Contrato de salida del triage: ``{answer, confidence, category, actions}`` validado con Pydantic.

Fuentes:
- PI · Consigna: la CLI devuelve un JSON validado con el contrato ``{answer, confidence, category, actions}``,
  donde ``actions`` es la lista de acciones sugeridas para el agente humano.
- M1L3 · Validación con Pydantic: un contrato débil (``category: str``, ``confidence: float``) valida tipos
  pero no dominio, y deja pasar ``"factura"`` o ``47.0``. El contrato que protege valida **tipo y dominio**,
  con los valores permitidos derivados de una única fuente de verdad.
- M1L3 · Tool use: el JSON Schema de la tool call sale del contrato (``model_json_schema()``), así no se
  escribe dos veces.
- Ejemplo del profesor (``L3/orbitair/contrato.py``): ``Literal[tuple(CATEGORIAS)]`` y
  ``list[Literal[tuple(ACCIONES)]]`` con ``min_length=1``.

Decisiones propias (docs/DECISIONES.md, D-55 y D-56): ``actions`` con descripción y reglas (sin repetidos,
``no_action`` sola), ``confidence`` estricto (un ``"0.9"`` como texto se rechaza, como pide la lección),
campos de más rechazados, espacios recortados y ``answer`` de hasta 500 caracteres.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .llm.base import ToolSpec
from .prompts import ACTIONS, CATEGORIES, NO_ACTION

# Única fuente de verdad: si se agrega una categoría o una acción en src/prompts.py, el contrato, el schema
# de la tool call y el prompt se actualizan solos. Literal[tuple(...)] es el mismo truco del ejemplo del
# profesor (la lección lo escribe como Literal.__getitem__(tuple(...))).
CategoryName = Literal[tuple(CATEGORIES)]
ActionName = Literal[tuple(ACTIONS)]

ANSWER_MAX_CHARS = 500


class TriageResult(BaseModel):
    """El contrato. Si un dato no lo cumple, ``model_validate`` lanza ``ValidationError``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer: str = Field(
        min_length=1,
        max_length=ANSWER_MAX_CHARS,
        description="Primera respuesta breve para el cliente, en el idioma de la consulta (en español, de usted).",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, strict=True, description="Qué tan seguro estás de la categoría, de 0 a 1."
    )
    category: CategoryName = Field(description="Equipo al que se deriva la consulta.")
    actions: list[ActionName] = Field(
        min_length=1, description="Acciones sugeridas para el agente humano, sin repetir."
    )

    @field_validator("actions")
    @classmethod
    def _reglas_de_acciones(cls, actions: list[str]) -> list[str]:
        if len(set(actions)) != len(actions):
            raise ValueError("hay acciones repetidas")
        if NO_ACTION in actions and len(actions) > 1:
            raise ValueError(f"'{NO_ACTION}' no se combina con otras acciones")
        return actions


# --------------------------------------------------------------------------- tool calling

TRIAGE_TOOL = ToolSpec(
    name="registrar_triage",
    description=(
        "Registra el triage de la consulta del cliente: categoría, respuesta breve para el cliente, "
        "confianza y acciones sugeridas para el agente humano."
    ),
    parameters=TriageResult.model_json_schema(),
)


# --------------------------------------------------------------------------- errores de validación


def _loc(error: dict) -> str:
    return ".".join(str(part) for part in error["loc"]) or "contrato"


def describe_errors(exc: ValidationError) -> str:
    """Qué campo falló y por qué, para el reintento (M1L3 · Reintentos).

    A propósito **no** incluye el valor recibido (``input_value``), que Pydantic sí pone en ``str(exc)``:
    el mensaje vuelve al modelo y se muestra en pantalla, y no hace falta repetir el texto generado.
    """
    return "; ".join(f"{_loc(e)}: {e['msg']}" for e in exc.errors())


def error_codes(exc: ValidationError) -> list[str]:
    """Versión corta para ``data/metrics.jsonl``: ``campo:tipo`` (ej. ``confidence:less_than_equal``)."""
    return [f"{_loc(e)}:{e['type']}" for e in exc.errors()]
