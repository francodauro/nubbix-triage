"""Dobles de prueba para M1L3: un proveedor falso que "responde" tool calls sin llamar a ninguna API."""

from src.llm.base import TOOL_NO_CALL, LLMProvider, LLMResponse

VALID = {
    "answer": "Gracias por avisarnos. Vamos a revisar el cobro duplicado y le escribimos a la brevedad.",
    "confidence": 0.92,
    "category": "billing",
    "actions": ["review_charge"],
}


def invalid(**changes):
    """Copia de VALID con los cambios indicados (para provocar errores de contrato)."""
    return {**VALID, **changes}


class FakeToolProvider(LLMProvider):
    """Devuelve, en orden, lo que se le indique: un dict (argumentos de la tool call), ``None`` (el modelo
    respondió texto en vez de llamar a la función) o una excepción. Guarda cada system y tool recibidos."""

    def __init__(self, outcomes, name="gemini", model="free-model"):
        super().__init__(model=model, max_tokens=100, temperature=0.0, timeout_s=5)
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0
        self.systems = []
        self.tools = []

    def complete(self, prompt, system, *, on_token=None, tool=None):
        self.calls += 1
        self.systems.append(system)
        self.tools.append(tool)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return LLMResponse(self.name, self.model, "Le respondo en texto.", 300, 20, 320, 0.1,
                               tool_args=None, tool_error=TOOL_NO_CALL)
        return LLMResponse(self.name, self.model, str(outcome), 300, 60, 360, 0.1, tool_args=dict(outcome))
