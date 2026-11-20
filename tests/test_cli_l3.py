"""Tests de la CLI con el contrato (M1L3): salida estándar = solo el contrato, códigos de salida tipados.
Sin APIs: el proveedor es falso."""

import contextlib
import dataclasses
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import main as cli
from src.config import load_settings
from src.llm.base import ServerError
from src.metrics import MetricsLog
from tests.fakes_l3 import VALID, FakeToolProvider, invalid


class TestCLIContrato(unittest.TestCase):
    def run_cli(self, argv, outcomes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba"}):
            settings = load_settings()
        # max_retries=0: sin esperas de backoff en los tests (los reintentos de red se prueban en test_router).
        settings = dataclasses.replace(settings, metrics_path=Path(tmp.name) / "metrics.jsonl", max_retries=0)
        provider = FakeToolProvider(outcomes, model="gemini-3.5-flash-lite")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                mock.patch.object(cli, "build_providers", return_value=([provider], [])), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue(), MetricsLog(settings.metrics_path).read_all(), provider

    def test_stdout_es_solo_el_contrato(self):
        code, out, err, _, _ = self.run_cli(["--prompt", "v6", "me cobraron dos veces"], [VALID])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(out), VALID)  # ni un campo más: consumible sin transformaciones
        self.assertEqual(err, "")

    def test_consulta_vacia_es_codigo_2_sin_llamar_al_modelo(self):
        for query in (["   "], []):
            code, out, err, records, provider = self.run_cli(["--prompt", "v6", *query], [])
            self.assertEqual(code, cli.EXIT_INVALID_QUERY)
            self.assertEqual(out, "")
            self.assertIn("no puede estar vacía", err)
            self.assertEqual(provider.calls, 0)
            self.assertEqual(records, [])

    def test_reintento_exitoso_avisa_por_stderr(self):
        code, out, err, records, _ = self.run_cli(["--prompt", "v6", "hola"], [invalid(confidence=1.4), VALID])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(out), VALID)
        self.assertIn("intento 1 no cumplió el contrato", err)
        self.assertEqual(len(records), 2)

    def test_contrato_invalido_tras_los_intentos_es_codigo_4(self):
        code, out, err, _, provider = self.run_cli(["--prompt", "v6", "hola"], [invalid(category="factura")] * 3)
        self.assertEqual(code, cli.EXIT_CONTRACT)
        self.assertEqual(out, "")
        self.assertIn("Se agotaron los 3 intentos", err)
        self.assertEqual(provider.calls, 3)

    def test_proveedores_caidos_es_codigo_3(self):
        code, out, _, _, _ = self.run_cli(["--prompt", "v6", "hola"], [ServerError("gemini", "high demand")])
        self.assertEqual(code, cli.EXIT_PROVIDERS)
        self.assertEqual(out, "")

    def test_detalle_va_a_stderr(self):
        code, out, err, _, _ = self.run_cli(["--prompt", "v6", "--detalle", "hola"], [invalid(actions=[]), VALID])
        self.assertEqual(json.loads(out), VALID)
        detail = json.loads(err[err.index("{"):])
        self.assertEqual(detail["intentos"], 2)
        self.assertEqual(detail["prompt_version"], "triage_v6")
        self.assertEqual(detail["total_tokens"], 720)  # suma de los 2 intentos

    def test_stream_se_ignora_con_aviso(self):
        code, out, err, _, _ = self.run_cli(["--prompt", "v6", "--stream", "hola"], [VALID])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("--stream no se usa", err)

    def test_version_de_texto_avisa_que_no_produce_el_contrato(self):
        from tests.test_router import FakeProvider
        provider = FakeProvider("gemini", "gemini-3.5-flash-lite", ['{"category": "billing"}'])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba"}):
            settings = dataclasses.replace(load_settings(), metrics_path=Path(tmp.name) / "m.jsonl")
        err = io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                mock.patch.object(cli, "build_providers", return_value=([provider], [])), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(["--prompt", "v2", "hola"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no produce el contrato", err.getvalue())

    def test_codigos_de_salida_distintos(self):
        codes = [cli.EXIT_OK, cli.EXIT_INVALID_QUERY, cli.EXIT_PROVIDERS, cli.EXIT_CONTRACT, cli.EXIT_CONFIG]
        self.assertEqual(codes, [0, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
