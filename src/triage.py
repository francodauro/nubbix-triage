"""Triage validado: consulta → tool call → contrato Pydantic, con reintentos que informan el error.

Fuentes:
- M1L3 · Reintentos ante parsing fallido (``clasificar_validado``): se valida la consulta **antes** de
  gastar una llamada; si la respuesta no cumple el contrato, se reintenta diciéndole al modelo **qué**
  falló. El error va al **system prompt** (es una corrección de cómo responder, no un dato nuevo del
  cliente). Repetir la misma llamada sería apostar al azar del sampling.
- M1L3 · Errores tipados: ``ConsultaInvalidaError`` (culpa de quien llama, no se reintenta, HTTP 400) y
  ``ContratoInvalidoError`` (se agotaron los reintentos, HTTP 502). Acá se llaman ``InvalidQueryError`` y
  ``ContractError``, en inglés como el resto de las excepciones del proyecto.
- Ejemplo del profesor (``L3/orbitair/contrato.py``): mismo esquema de 3 intentos.

Dos capas de reintento distintas, que no se mezclan:
- Red y proveedor (M1L1): 429, 5xx, timeouts → ``src/retry.py`` + fallback del router.
- Contrato (M1L3): la respuesta llegó pero no sirve → este módulo, hasta ``max_attempts`` veces.

Decisión propia (D-61): al reintentar se agrega solo el **último** error al system base, en vez de
acumularlos como en el ejemplo del profesor, para que el prompt no crezca en cada intento.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from pydantic import ValidationError

from .contract import TRIAGE_TOOL, TriageResult, describe_errors, error_codes
from .llm.base import TOOL_BAD_JSON, TOOL_NO_CALL, TOOL_NOT_OBJECT, LLMResponse
from .llm.router import Router
from .prompts import CATEGORIES, PromptTemplate, render_system, render_user

METHOD_TOOL = "tool_call"

# Nota que se agrega al system en el reintento. Las llaves dobles no hacen falta: se usa .format solo acá.
RETRY_NOTE = (
    "\n\n(Tu respuesta anterior fue inválida: {error}. Corrígela y vuelve a llamar a la función {tool}.)"
)

_TOOL_ERROR_TEXT = {
    TOOL_NO_CALL: "no llamaste a la función {tool}, respondiste con texto",
    TOOL_BAD_JSON: "los argumentos de la función no son un JSON válido (¿se cortaron?)",
    TOOL_NOT_OBJECT: "los argumentos de la función deben ser un objeto JSON con los campos del contrato",
}


class InvalidQueryError(Exception):
    """La consulta no se puede procesar (ej. vacía). Es un error de quien llama: no se reintenta y no se
    gasta ninguna llamada al modelo (M1L3 · Errores tipados → HTTP 400, código de salida 2 en la CLI)."""


class ContractError(Exception):
    """Se agotaron los intentos sin obtener un contrato válido (M1L3 → HTTP 502, código de salida 4)."""

    def __init__(self, message: str, attempts: list["Attempt"]):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class Check:
    """Resultado de revisar una respuesta contra el contrato."""

    result: TriageResult | None
    method: str            # tool_call · sin_tool_call · json_invalido · args_no_objeto
    error: str | None      # explicación para el modelo (sin valores recibidos)
    codes: list[str] = field(default_factory=list)  # versión corta para las métricas

    @property
    def valid(self) -> bool:
        return self.result is not None


@dataclass
class Attempt:
    number: int
    response: LLMResponse
    check: Check


@dataclass
class TriageOutcome:
    result: TriageResult
    attempts: list[Attempt]
    request_id: str

    @property
    def responses(self) -> list[LLMResponse]:
        return [a.response for a in self.attempts]


def validate_query(text: str | None) -> str:
    """Valida la consulta antes de llamar al modelo (M1L3: "antes de gastar una llamada")."""
    if text is None or not text.strip():
        raise InvalidQueryError("La consulta no puede estar vacía.")
    return text.strip()


def check_response(response: LLMResponse) -> Check:
    """Revisa una respuesta: primero que haya argumentos (tool call), después el contrato (Pydantic)."""
    if response.tool_args is None:
        reason = response.tool_error or TOOL_NO_CALL
        message = _TOOL_ERROR_TEXT.get(reason, reason).format(tool=TRIAGE_TOOL.name)
        return Check(None, reason, message, [f"tool:{reason}"])
    try:
        return Check(TriageResult.model_validate(response.tool_args), METHOD_TOOL, None)
    except ValidationError as exc:
        return Check(None, METHOD_TOOL, describe_errors(exc), error_codes(exc))


def metrics_fields(check: Check, *, request_id: str, attempt: int, args: dict | None) -> dict:
    """Campos de ``data/metrics.jsonl`` para observar el contrato (D-66). Nunca texto del cliente ni del modelo."""
    category = (args or {}).get("category")
    return {
        "request_id": request_id,
        "contract_attempt": attempt,
        "parse_method": check.method,
        "contract_valid": check.valid,
        "contract_errors": check.codes,
        "category_valid": isinstance(category, str) and category in CATEGORIES,
    }


def classify_validated(
    query: str,
    router: Router,
    template: PromptTemplate,
    *,
    max_attempts: int = 3,
    request_id: str | None = None,
) -> TriageOutcome:
    """Clasifica la consulta y devuelve un contrato válido, o lanza un error tipado.

    :raises InvalidQueryError: consulta vacía (0 llamadas al modelo).
    :raises ContractError: ``max_attempts`` respuestas seguidas sin cumplir el contrato.
    :raises AllProvidersFailedError: ningún proveedor respondió (viene del router, M1L1).
    """
    text = validate_query(query)
    if max_attempts < 1:
        raise ValueError("max_attempts debe ser al menos 1")
    request_id = request_id or uuid.uuid4().hex[:12]

    base_system = render_system(template)
    user_message = render_user(template, text)
    attempts: list[Attempt] = []
    previous_error: str | None = None

    for number in range(1, max_attempts + 1):
        system = base_system
        if previous_error:
            system += RETRY_NOTE.format(error=previous_error, tool=TRIAGE_TOOL.name)

        response = router.ask(
            user_message,
            system,
            prompt_version=template.version,
            tool=TRIAGE_TOOL,
            extra_fields=lambda r, n=number: metrics_fields(
                check_response(r), request_id=request_id, attempt=n, args=r.tool_args),
        )
        check = check_response(response)
        attempts.append(Attempt(number, response, check))
        if check.valid:
            return TriageOutcome(check.result, attempts, request_id)
        previous_error = check.error

    raise ContractError(
        f"Se agotaron los {max_attempts} intentos sin un contrato válido. Último error: {previous_error}",
        attempts,
    )
