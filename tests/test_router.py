"""Tests unitarios (M0L2 · unittest, caja negra) con proveedores falsos.

No llaman a ninguna API real: no gastan dinero ni cuota y dan siempre el mismo resultado.
Correr con:  python -m unittest discover -s tests -v
"""

import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path

from src.costs import Pricing, project_cost
from src.llm.base import (
    AllProvidersFailedError,
    AuthError,
    BadRequestError,
    LLMProvider,
    LLMResponse,
    QuotaExhaustedError,
    RateLimitError,
    ServerError,
)
from src.llm.gemini_provider import _find_retry_delay
from src.llm.openai_provider import OpenAIProvider
from src.llm.router import Router
from src.metrics import MetricsLog
from src.retry import call_with_backoff


class FakeProvider(LLMProvider):
    """Proveedor de mentira: devuelve o lanza lo que se le indique, en orden."""

    def __init__(self, name, model, outcomes):
        super().__init__(model=model, max_tokens=100, temperature=0.0, timeout_s=5)
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, prompt, system, *, on_token=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        ttft = None
        if on_token:
            on_token(outcome)
            ttft = 0.05
        return LLMResponse(self.name, self.model, outcome, input_tokens=1000, output_tokens=500,
                           total_tokens=1500, latency_s=0.1, ttft_s=ttft)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        pricing_file = root / "pricing.json"
        pricing_file.write_text(json.dumps({"models": {
            "free-model": {"input": 0.0, "output": 0.0},
            "paid-model": {"input": 0.10, "output": 0.40},
        }}), encoding="utf-8")
        self.pricing = Pricing(pricing_file)
        self.metrics = MetricsLog(root / "metrics.jsonl")
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def router(self, providers, budget=4.50, max_retries=2, max_wait=10):
        return Router(providers, self.pricing, self.metrics, budget_usd=budget,
                      max_retries=max_retries, max_wait_s=max_wait, sleep=self.sleeps.append)


class TestRouter(Base):
    def test_usa_el_gratuito_si_responde(self):
        free = FakeProvider("gemini", "free-model", ["billing"])
        paid = FakeProvider("openai", "paid-model", ["technical"])
        resp = self.router([free, paid]).ask("q", "s", prompt_version="v1")
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(resp.cost_usd, 0.0)
        self.assertEqual(paid.calls, 0)

    def test_fallback_al_pago_cuando_falla_el_gratuito(self):
        free = FakeProvider("gemini", "free-model", [ServerError("gemini", "500")] * 3)
        paid = FakeProvider("openai", "paid-model", ["billing"])
        resp = self.router([free, paid]).ask("q", "s", prompt_version="v1")
        self.assertEqual(resp.provider, "openai")
        self.assertEqual(free.calls, 3)  # 1 intento + 2 reintentos
        # costo = 1000/1M*0.10 + 500/1M*0.40 = 0.0003 (fórmula de M1L1)
        self.assertAlmostEqual(resp.cost_usd, 0.0003)
        records = self.metrics.read_all()
        self.assertEqual([r["status"] for r in records], ["error", "ok"])
        self.assertTrue(records[1]["fallback"])

    def test_no_llama_al_pago_si_se_agoto_el_presupuesto(self):
        self.metrics.append({"provider": "openai", "status": "ok", "cost_usd": 4.50})
        free = FakeProvider("gemini", "free-model", [AuthError("gemini", "sin key")])
        paid = FakeProvider("openai", "paid-model", ["billing"])
        with self.assertRaises(AllProvidersFailedError):
            self.router([free, paid], budget=4.50).ask("q", "s", prompt_version="v1")
        self.assertEqual(paid.calls, 0)
        self.assertEqual(self.metrics.read_all()[-1]["error_kind"], "budget_exceeded")

    def test_error_no_reintentable_pasa_directo_al_fallback(self):
        free = FakeProvider("gemini", "free-model", [AuthError("gemini", "401")])
        paid = FakeProvider("openai", "paid-model", ["account"])
        self.router([free, paid]).ask("q", "s", prompt_version="v1")
        self.assertEqual(free.calls, 1)
        self.assertEqual(self.sleeps, [])

    def test_streaming_con_salida_parcial_no_hace_fallback(self):
        free = FakeProvider("gemini", "free-model", [ServerError("gemini", "cortado", partial_output=True)])
        paid = FakeProvider("openai", "paid-model", ["billing"])
        with self.assertRaises(AllProvidersFailedError):
            self.router([free, paid]).ask("q", "s", prompt_version="v1", on_token=lambda t: None)
        self.assertEqual(paid.calls, 0)

    def test_metricas_tienen_los_campos_de_la_consigna(self):
        free = FakeProvider("gemini", "free-model", ["other"])
        consulta = "me cobraron dos veces la suscripcion"
        self.router([free, FakeProvider("openai", "paid-model", [])]).ask(consulta, "s", prompt_version="v1")
        record = self.metrics.read_all()[0]
        for field in ("timestamp", "prompt_tokens", "completion_tokens", "total_tokens", "latency_s", "cost_usd"):
            self.assertIn(field, record)
        self.assertNotIn(consulta, json.dumps(record))  # no se guarda el texto de la consulta

    def test_metricas_registran_la_temperature(self):
        free = FakeProvider("gemini", "free-model", [ServerError("gemini", "500")] * 3)
        paid = FakeProvider("openai", "paid-model", ["billing"])
        self.router([free, paid]).ask("q", "s", prompt_version="v1")
        # Tanto el intento fallido como el exitoso registran la temperature (M1L1).
        self.assertEqual([r["temperature"] for r in self.metrics.read_all()], [0.0, 0.0])

    def test_streaming_registra_ttft(self):
        free = FakeProvider("gemini", "free-model", ["billing"])
        resp = self.router([free, FakeProvider("openai", "paid-model", [])]).ask(
            "q", "s", prompt_version="v1", on_token=lambda t: None)
        self.assertEqual(resp.ttft_s, 0.05)
        self.assertEqual(self.metrics.read_all()[0]["ttft_s"], 0.05)

    def test_sin_streaming_no_hay_ttft(self):
        free = FakeProvider("gemini", "free-model", ["billing"])
        self.router([free, FakeProvider("openai", "paid-model", [])]).ask("q", "s", prompt_version="v1")
        self.assertNotIn("ttft_s", self.metrics.read_all()[0])


class TestAvisos(Base):
    def test_avisa_cuando_falla_un_proveedor_y_guarda_el_mensaje(self):
        avisos = []
        free = FakeProvider("gemini", "free-model", ["billing"])
        paid = FakeProvider("openai", "paid-model", [BadRequestError("openai", "404 model deprecated")])
        router = Router([paid, free], self.pricing, self.metrics, budget_usd=4.5, max_retries=2,
                        max_wait_s=10, sleep=self.sleeps.append, on_failure=lambda n, e: avisos.append((n, e.kind)))
        resp = router.ask("q", "s", prompt_version="v1")
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(avisos, [("openai", "bad_request")])
        self.assertIn("404 model deprecated", self.metrics.read_all()[0]["error_message"])


class TestOpenAIParams(unittest.TestCase):
    """Verifica los parámetros que se mandan a OpenAI, sin llamar a la API (cliente reemplazado)."""

    def _provider(self, effort):
        p = OpenAIProvider(api_key="sk-test", model="gpt-6-luna", max_tokens=200, temperature=0.0,
                           timeout_s=5, reasoning_effort=effort)
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="billing"), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=1, total_tokens=101))

        p._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
        return p, captured

    def test_usa_max_completion_tokens_y_reasoning_effort(self):
        p, captured = self._provider("none")
        p.complete("q", "s")
        self.assertEqual(captured["max_completion_tokens"], 200)
        self.assertNotIn("max_tokens", captured)
        self.assertEqual(captured["reasoning_effort"], "none")
        self.assertEqual(captured["temperature"], 0.0)

    def test_sin_reasoning_effort_no_se_envia(self):
        p, captured = self._provider("")
        p.complete("q", "s")
        self.assertNotIn("reasoning_effort", captured)


class TestProyeccion(Base):
    def test_proyecta_10000_consultas_con_tokens_promedio(self):
        self.metrics.append({"status": "ok", "prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0})
        self.metrics.append({"status": "ok", "prompt_tokens": 140, "completion_tokens": 6, "cost_usd": 0})
        self.metrics.append({"status": "error", "cost_usd": 0})  # los errores no cuentan
        p = project_cost(self.metrics.read_all(), self.pricing, 10_000)
        self.assertEqual(p["base"]["llamadas_exitosas"], 2)
        self.assertEqual(p["base"]["promedio_tokens_entrada"], 120)
        self.assertEqual(p["base"]["promedio_tokens_salida"], 8)
        # 120/1M*0.10 + 8/1M*0.40 = 0.0000152 por consulta → × 10.000 = 0.152
        self.assertAlmostEqual(p["costo_usd_si_responde_solo"]["paid-model"], 0.152)
        self.assertEqual(p["costo_usd_si_responde_solo"]["free-model"], 0.0)

    def test_sin_datos_devuelve_nota(self):
        p = project_cost([], self.pricing)
        self.assertIn("nota", p)


class TestRetry(Base):
    def test_respeta_retry_after(self):
        outcomes = [RateLimitError("x", "429", retry_after=3.0), "ok"]
        fake = FakeProvider("x", "free-model", outcomes)
        resp, attempts = call_with_backoff(lambda: fake.complete("q", "s"), max_retries=2, max_wait_s=10, sleep=self.sleeps.append)
        self.assertEqual(attempts, 2)
        self.assertEqual(self.sleeps, [3.0])

    def test_no_espera_si_retry_after_es_demasiado_largo(self):
        fake = FakeProvider("x", "free-model", [RateLimitError("x", "429", retry_after=3600)])
        with self.assertRaises(RateLimitError):
            call_with_backoff(lambda: fake.complete("q", "s"), max_retries=2, max_wait_s=10, sleep=self.sleeps.append)
        self.assertEqual(self.sleeps, [])

    def test_backoff_exponencial_con_jitter(self):
        fake = FakeProvider("x", "free-model", [ServerError("x", "500"), ServerError("x", "500"), "ok"])
        call_with_backoff(lambda: fake.complete("q", "s"), max_retries=2, max_wait_s=10, sleep=self.sleeps.append)
        self.assertTrue(1 <= self.sleeps[0] < 2)
        self.assertTrue(2 <= self.sleeps[1] < 3)

    def test_sin_saldo_no_se_reintenta(self):
        fake = FakeProvider("x", "paid-model", [QuotaExhaustedError("x", "insufficient_quota")])
        with self.assertRaises(QuotaExhaustedError):
            call_with_backoff(lambda: fake.complete("q", "s"), max_retries=2, max_wait_s=10, sleep=self.sleeps.append)
        self.assertEqual(fake.calls, 1)


class TestGeminiRetryDelay(unittest.TestCase):
    def test_lee_retry_delay_del_error_de_google(self):
        body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]}}
        self.assertEqual(_find_retry_delay(body), 34.0)


if __name__ == "__main__":
    unittest.main()
