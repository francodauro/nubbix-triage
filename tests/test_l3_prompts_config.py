"""Tests de M1L3 sobre el prompt v6, la configuración nueva y el comparador con versiones de contrato. Sin APIs."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.comparar import evaluate, output_paths, render_report, summarize
from src.config import PROJECT_ROOT, ConfigError, load_settings
from src.costs import Pricing
from src.llm.base import ServerError
from src.llm.router import Router
from src.metrics import MetricsLog
from src.prompts import ACTIONS, CATEGORIES, OUTPUT_CONTRACT, OUTPUT_TEXT, PromptError, load_prompt, render_system
from tests.fakes_l3 import VALID, FakeToolProvider, invalid
from tests.test_prompts import VALID_YAML, TempPrompts

PROMPTS_DIR = PROJECT_ROOT / "prompts"


class TestPromptV6(unittest.TestCase):
    def test_v6_es_de_contrato_y_las_anteriores_de_texto(self):
        self.assertEqual(load_prompt("v6", PROMPTS_DIR).salida, OUTPUT_CONTRACT)
        for v in ("v1", "v2", "v3", "v4", "v5"):
            self.assertEqual(load_prompt(v, PROMPTS_DIR).salida, OUTPUT_TEXT)

    def test_v6_lista_todas_las_categorias_y_acciones(self):
        system = render_system(load_prompt("v6", PROMPTS_DIR))
        for name in [*CATEGORIES, *ACTIONS]:
            self.assertIn(f"- {name}:", system)

    def test_v6_no_pide_responder_solo_con_json(self):  # M1L3: contradice al schema de la tool call
        system = render_system(load_prompt("v6", PROMPTS_DIR))
        self.assertNotIn("Responde SOLO", system)
        self.assertIn("de usted", system)


class TestCampoSalida(TempPrompts):
    def test_salida_invalida(self):
        self.write(VALID_YAML + "salida: tabla\n")
        with self.assertRaises(PromptError):
            load_prompt("v9", self.dir)

    def test_contrato_sin_acciones_da_error(self):
        self.write(VALID_YAML + "salida: contrato\n")
        with self.assertRaisesRegex(PromptError, "acciones"):
            load_prompt("v9", self.dir)

    def test_sin_campo_salida_es_texto(self):
        self.write(VALID_YAML)
        self.assertEqual(load_prompt("v9", self.dir).salida, OUTPUT_TEXT)


class TestConfigL3(unittest.TestCase):
    def settings(self, **env):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba", **env}):
            return load_settings(env_file=Path(os.devnull))

    def test_valores_por_defecto(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for var in ("CONTRACT_MAX_ATTEMPTS", "OPENAI_MAX_TOKENS", "OPENAI_STRICT_TOOLS", "PROMPT_VERSION"):
                os.environ.pop(var, None)
            s = self.settings()
        self.assertEqual(s.contract_max_attempts, 3)
        self.assertEqual(s.providers["openai"].max_tokens, 400)
        self.assertTrue(s.providers["openai"].strict_tools)
        self.assertEqual(s.prompt_version, "triage_v6")  # D-69: la única que produce el contrato

    def test_intentos_invalidos(self):
        with self.assertRaises(ConfigError):
            self.settings(CONTRACT_MAX_ATTEMPTS="0")

    def test_strict_se_puede_apagar(self):
        self.assertFalse(self.settings(OPENAI_STRICT_TOOLS="false").providers["openai"].strict_tools)
        with self.assertRaises(ConfigError):
            self.settings(OPENAI_STRICT_TOOLS="quizas")


class TestCompararContrato(unittest.TestCase):
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

    def test_v6_se_evalua_con_el_contrato(self):
        cases = [
            {"id": "A", "texto": "a", "lote": "dev", "categoria": "billing", "regla": None},
            {"id": "B", "texto": "b", "lote": "dev", "categoria": "account", "regla": None},
            {"id": "C", "texto": "c", "lote": "holdout", "categoria": "other", "regla": None},
            {"id": "D", "texto": "d", "lote": "holdout", "categoria": "other", "regla": None},
        ]
        provider = FakeToolProvider([
            VALID,                                                   # A: acierto al primer intento
            invalid(confidence=2.0), invalid(category="account"),    # B: acierto al segundo intento
            invalid(category="factura"), invalid(category="factura"),  # C: se agotan 2 intentos
            ServerError("gemini", "high demand"),                    # D: error de API
        ])
        router = Router([provider], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        rows = evaluate(router, load_prompt("v6", PROMPTS_DIR), cases, log=lambda *_: None, max_attempts=2)
        self.assertEqual([r.intentos for r in rows], [1, 2, 2, 1])
        self.assertEqual([r.metodo for r in rows], ["tool_call", "tool_call", "contrato_invalido", "error_api"])
        summary = summarize(rows, self.pricing, "paid-model")
        self.assertEqual(summary["aciertos"], {"dev": (2, 2), "holdout": (0, 1)})
        self.assertEqual(summary["invalidas"], 1)
        self.assertEqual(summary["errores_api"], 1)
        self.assertAlmostEqual(summary["intentos_medios"], 5 / 3)
        self.assertEqual(rows[1].tokens_in, 600)  # suma los dos intentos
        report = render_report([summary], rows, {"fecha": "hoy", "proveedor": "gemini", "modelo": "free-model",
                                                 "modelo_ref": "paid-model", "temperature": 0.0,
                                                 "n_dev": 2, "n_holdout": 2})
        self.assertIn("Intentos medios", report)

    def test_etiqueta_no_pisa_el_reporte_completo(self):
        report, detail = output_paths("gemini", "l3")
        self.assertEqual((report.name, detail.name), ("comparacion_versiones_l3.md", "comparacion_detalle_l3.csv"))
        report, _ = output_paths("openai", "l3")
        self.assertEqual(report.name, "comparacion_versiones_openai_l3.md")


if __name__ == "__main__":
    unittest.main()
