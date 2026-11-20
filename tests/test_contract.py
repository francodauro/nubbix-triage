"""Tests del contrato Pydantic (M1L3 · Validación con Pydantic). Sin APIs.

Correr con:  python -m unittest discover -s tests -v
"""

import unittest

from pydantic import ValidationError

from src.contract import TRIAGE_TOOL, TriageResult, describe_errors, error_codes
from src.prompts import ACTIONS, CATEGORIES
from tests.fakes_l3 import VALID, invalid


def errors_of(data):
    try:
        TriageResult.model_validate(data)
    except ValidationError as exc:
        return exc
    raise AssertionError("se esperaba ValidationError")


class TestContratoValido(unittest.TestCase):
    def test_un_contrato_correcto_pasa(self):
        result = TriageResult.model_validate(VALID)
        self.assertEqual(result.category, "billing")
        self.assertEqual(result.model_dump(), VALID)

    def test_la_salida_tiene_exactamente_los_campos_del_pi(self):
        self.assertEqual(list(TriageResult.model_validate(VALID).model_dump()),
                         ["answer", "confidence", "category", "actions"])

    def test_confianza_entera_1_se_acepta(self):  # Gemini a veces manda 1 en lugar de 1.0
        self.assertEqual(TriageResult.model_validate(invalid(confidence=1)).confidence, 1.0)

    def test_no_action_sola_es_valida(self):
        self.assertEqual(TriageResult.model_validate(invalid(actions=["no_action"])).actions, ["no_action"])

    def test_recorta_espacios(self):
        self.assertEqual(TriageResult.model_validate(invalid(answer="  Hola  ")).answer, "Hola")


class TestDominio(unittest.TestCase):
    def test_el_ejemplo_de_la_leccion_da_tres_errores(self):
        # M1L3 / video 2: con el contrato débil esto pasa; con el que protege, falla en 3 campos.
        exc = errors_of({"answer": "ok", "confidence": 47.0, "category": "factura", "actions": ["bailar"]})
        fields = sorted(str(e["loc"][0]) for e in exc.errors())
        self.assertEqual(fields, ["actions", "category", "confidence"])

    def test_confianza_como_texto_se_rechaza(self):  # M1L3 §1: "0.9" como string es un tipo incorrecto
        self.assertIn("confidence:float_type", error_codes(errors_of(invalid(confidence="0.9"))))

    def test_confianza_fuera_de_rango(self):
        self.assertIn("confidence:less_than_equal", error_codes(errors_of(invalid(confidence=1.4))))
        self.assertIn("confidence:greater_than_equal", error_codes(errors_of(invalid(confidence=-0.1))))

    def test_answer_vacio_o_solo_espacios(self):
        self.assertIn("answer:string_too_short", error_codes(errors_of(invalid(answer="   "))))

    def test_answer_demasiado_largo(self):
        self.assertIn("answer:string_too_long", error_codes(errors_of(invalid(answer="x" * 501))))

    def test_acciones_vacias(self):
        self.assertIn("actions:too_short", error_codes(errors_of(invalid(actions=[]))))

    def test_accion_inexistente(self):
        self.assertIn("actions.0:literal_error", error_codes(errors_of(invalid(actions=["llamar_al_gerente"]))))

    def test_acciones_repetidas(self):
        exc = errors_of(invalid(actions=["review_charge", "review_charge"]))
        self.assertIn("repetidas", describe_errors(exc))

    def test_no_action_no_se_combina(self):
        exc = errors_of(invalid(actions=["no_action", "review_charge"]))
        self.assertIn("no_action", describe_errors(exc))

    def test_campo_de_mas_se_rechaza(self):
        self.assertIn("priority:extra_forbidden", error_codes(errors_of(invalid(priority="alta"))))

    def test_campo_faltante(self):
        data = dict(VALID)
        del data["actions"]
        self.assertIn("actions:missing", error_codes(errors_of(data)))


class TestUnicaFuenteDeVerdad(unittest.TestCase):
    def test_las_categorias_del_schema_salen_de_CATEGORIES(self):
        schema = TRIAGE_TOOL.parameters
        self.assertEqual(schema["properties"]["category"]["enum"], list(CATEGORIES))

    def test_las_acciones_del_schema_salen_de_ACTIONS(self):
        schema = TRIAGE_TOOL.parameters
        self.assertEqual(schema["properties"]["actions"]["items"]["enum"], list(ACTIONS))

    def test_el_schema_de_la_tool_es_el_del_contrato(self):
        self.assertEqual(TRIAGE_TOOL.parameters, TriageResult.model_json_schema())
        self.assertEqual(sorted(TRIAGE_TOOL.parameters["required"]), ["actions", "answer", "category", "confidence"])
        self.assertFalse(TRIAGE_TOOL.parameters["additionalProperties"])


class TestMensajesDeError(unittest.TestCase):
    def test_el_mensaje_dice_campo_y_motivo_sin_el_valor_recibido(self):
        exc = errors_of(invalid(answer="texto secreto que no debe repetirse " * 20))
        message = describe_errors(exc)
        self.assertIn("answer:", message)
        self.assertNotIn("texto secreto", message)
        self.assertIn("texto secreto", str(exc))  # Pydantic sí lo incluye: por eso no se usa str(exc)


if __name__ == "__main__":
    unittest.main()
