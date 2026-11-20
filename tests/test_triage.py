"""Tests del triage validado: reintento con el error inyectado y errores tipados (M1L3). Sin APIs."""

import json
import tempfile
import unittest
from pathlib import Path

from src.config import PROJECT_ROOT
from src.costs import Pricing
from src.contract import TRIAGE_TOOL
from src.llm.base import AllProvidersFailedError, ServerError
from src.llm.router import Router
from src.metrics import MetricsLog
from src.prompts import load_prompt
from src.triage import ContractError, InvalidQueryError, classify_validated
from tests.fakes_l3 import VALID, FakeToolProvider, invalid

V6 = load_prompt("v6", PROJECT_ROOT / "prompts")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "pricing.json").write_text(json.dumps({"models": {
            "free-model": {"input": 0.0, "output": 0.0},
            "paid-model": {"input": 0.10, "output": 0.50},
        }}), encoding="utf-8")
        self.pricing = Pricing(root / "pricing.json")
        self.metrics = MetricsLog(root / "metrics.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def run_triage(self, outcomes, query="me cobraron dos veces", **kwargs):
        provider = FakeToolProvider(outcomes)
        router = Router([provider], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        return provider, classify_validated(query, router, V6, **kwargs)


class TestConsultaInvalida(Base):
    def test_consulta_vacia_no_llama_al_modelo(self):
        provider = FakeToolProvider([])
        router = Router([provider], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        for query in ("", "   ", None):
            with self.assertRaises(InvalidQueryError):
                classify_validated(query, router, V6)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.metrics.read_all(), [])


class TestPrimerIntento(Base):
    def test_contrato_valido_en_el_primer_intento(self):
        provider, outcome = self.run_triage([VALID])
        self.assertEqual(outcome.result.category, "billing")
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(provider.calls, 1)

    def test_se_fuerza_la_tool_del_contrato(self):
        provider, _ = self.run_triage([VALID])
        self.assertIs(provider.tools[0], TRIAGE_TOOL)

    def test_metricas_del_contrato_sin_texto(self):
        _, outcome = self.run_triage([VALID], query="me cobraron dos veces")
        record = self.metrics.read_all()[-1]
        self.assertEqual(record["contract_attempt"], 1)
        self.assertTrue(record["contract_valid"])
        self.assertEqual(record["contract_errors"], [])
        self.assertEqual(record["parse_method"], "tool_call")
        self.assertEqual(record["request_id"], outcome.request_id)
        dumped = json.dumps(self.metrics.read_all(), ensure_ascii=False)
        self.assertNotIn("cobraron", dumped)              # texto del cliente
        self.assertNotIn("cobro duplicado", dumped)       # texto generado por el modelo


class TestReintentos(Base):
    def test_reintenta_e_inyecta_el_error_en_el_system(self):
        provider, outcome = self.run_triage([invalid(confidence=1.4), VALID])
        self.assertEqual(len(outcome.attempts), 2)
        self.assertNotIn("anterior fue inválida", provider.systems[0])
        self.assertIn("anterior fue inválida", provider.systems[1])
        self.assertIn("confidence", provider.systems[1])
        self.assertIn(TRIAGE_TOOL.name, provider.systems[1])

    def test_el_reintento_no_repite_el_valor_recibido(self):
        provider, _ = self.run_triage([invalid(category="categoria_inventada"), VALID])
        self.assertIn("category", provider.systems[1])
        self.assertNotIn("categoria_inventada", provider.systems[1])

    def test_solo_se_inyecta_el_ultimo_error(self):  # D-61
        provider, _ = self.run_triage([invalid(confidence=1.4), invalid(actions=[]), VALID])
        self.assertIn("actions", provider.systems[2])
        self.assertNotIn("confidence:", provider.systems[2])
        self.assertEqual(provider.systems[2].count("anterior fue inválida"), 1)

    def test_si_no_llama_a_la_funcion_se_le_dice(self):
        provider, outcome = self.run_triage([None, VALID])
        self.assertEqual(outcome.attempts[0].check.method, "sin_tool_call")
        self.assertIn("no llamaste a la función", provider.systems[1])

    def test_cada_intento_queda_en_las_metricas_con_el_mismo_request_id(self):
        _, outcome = self.run_triage([invalid(confidence=1.4), VALID])
        records = self.metrics.read_all()
        self.assertEqual([r["contract_attempt"] for r in records], [1, 2])
        self.assertEqual([r["contract_valid"] for r in records], [False, True])
        self.assertEqual(records[0]["contract_errors"], ["confidence:less_than_equal"])
        self.assertEqual({r["request_id"] for r in records}, {outcome.request_id})

    def test_se_agotan_los_intentos(self):
        provider = FakeToolProvider([invalid(category="factura")] * 3)
        router = Router([provider], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        with self.assertRaises(ContractError) as ctx:
            classify_validated("hola", router, V6, max_attempts=3)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(ctx.exception.attempts), 3)
        self.assertIn("3 intentos", str(ctx.exception))

    def test_max_intentos_configurable(self):
        provider = FakeToolProvider([invalid(category="factura")])
        router = Router([provider], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        with self.assertRaises(ContractError):
            classify_validated("hola", router, V6, max_attempts=1)
        self.assertEqual(provider.calls, 1)


class TestErroresDeProveedor(Base):
    def test_si_ningun_proveedor_responde_no_es_error_de_contrato(self):
        with self.assertRaises(AllProvidersFailedError):
            self.run_triage([ServerError("gemini", "high demand")])

    def test_el_fallback_del_router_sigue_funcionando_con_tool_calling(self):
        free = FakeToolProvider([ServerError("gemini", "caído")])
        paid = FakeToolProvider([VALID], name="openai", model="paid-model")
        router = Router([free, paid], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        outcome = classify_validated("hola", router, V6)
        self.assertEqual(outcome.attempts[0].response.provider, "openai")
        self.assertIs(paid.tools[0], TRIAGE_TOOL)


if __name__ == "__main__":
    unittest.main()
