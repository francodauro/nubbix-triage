"""Tests de la etapa M1L2: prompts YAML, parser de la respuesta, CLI y comparador.

Igual que test_router.py: no llaman a ninguna API (proveedores falsos), no gastan cuota.
Correr con:  python -m unittest discover -s tests -v
"""

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
from src.comparar import DatasetError, evaluate, load_dataset, render_report, summarize
from src.config import PROJECT_ROOT, load_settings
from src.costs import Pricing
from src.metrics import MetricsLog
from src.llm.router import Router
from src.parsing import parse_category
from src.prompts import (
    CATEGORIES,
    PromptError,
    list_versions,
    load_prompt,
    normalize_version,
    render_system,
    render_user,
)
from tests.test_router import FakeProvider

PROMPTS_DIR = PROJECT_ROOT / "prompts"

VALID_YAML = """\
version: triage_v9
fecha: 2026-11-19
tecnica: prueba
descripcion: prompt de prueba
system: |
  Empresa {empresa}. Categorías:
  {categorias}
  Responde {{"category": "..."}}
user: "<consulta>{consulta}</consulta>"
"""


class TempPrompts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, name="triage_v9.yaml"):
        (self.dir / name).write_text(text, encoding="utf-8")


class TestPromptsDelRepo(unittest.TestCase):
    def test_hay_al_menos_dos_versiones_en_yaml(self):
        # PI: "≥2 versiones" en prompts/*.yaml
        self.assertGreaterEqual(len(list_versions(PROMPTS_DIR)), 2)
        self.assertFalse(list(PROMPTS_DIR.glob("*.txt")), "Los prompts viven en YAML (D-34)")

    def test_todas_las_versiones_listan_las_4_categorias_incluida_other(self):
        # M1L2: listar TODAS las categorías, incluida la categoría cajón
        for version in list_versions(PROMPTS_DIR):
            with self.subTest(version=version):
                system = render_system(load_prompt(version, PROMPTS_DIR))
                for category in CATEGORIES:
                    self.assertIn(category, system)
                self.assertNotIn("{", system.replace('{"category"', ""), "Quedó una variable sin completar")

    def test_v1_es_la_migracion_literal_del_txt(self):
        system = render_system(load_prompt("v1", PROMPTS_DIR))
        self.assertTrue(system.startswith("Eres un agente de soporte de nivel 1 de Nubbix"))
        self.assertTrue(system.endswith("en minúsculas, sin explicación."))

    def test_cot_pide_la_ultima_linea_parseable(self):
        system = render_system(load_prompt("v4", PROMPTS_DIR))
        self.assertIn("CATEGORIA: <una de las categorías>", system)


class TestCargaYPlantilla(TempPrompts):
    def test_acepta_v9_o_triage_v9(self):
        self.write(VALID_YAML)
        self.assertEqual(normalize_version("v9"), "triage_v9")
        self.assertEqual(load_prompt("v9", self.dir).version, "triage_v9")

    def test_version_inexistente_da_error_con_las_disponibles(self):
        self.write(VALID_YAML)
        with self.assertRaises(PromptError) as ctx:
            load_prompt("v7", self.dir)
        self.assertIn("triage_v9", str(ctx.exception))

    def test_falta_un_campo_obligatorio(self):
        self.write(VALID_YAML.replace("tecnica: prueba\n", ""))
        with self.assertRaisesRegex(PromptError, "tecnica"):
            load_prompt("v9", self.dir)

    def test_version_distinta_del_nombre_del_archivo(self):
        self.write(VALID_YAML.replace("version: triage_v9", "version: triage_v8"))
        with self.assertRaisesRegex(PromptError, "coincidir"):
            load_prompt("v9", self.dir)

    def test_variable_mal_escrita_se_detecta_al_cargar(self):
        self.write(VALID_YAML.replace("{categorias}", "{categoria}"))
        with self.assertRaisesRegex(PromptError, "categoria"):
            load_prompt("v9", self.dir)

    def test_el_user_debe_usar_consulta(self):
        self.write(VALID_YAML.replace("{consulta}", "{texto}"))
        with self.assertRaisesRegex(PromptError, "consulta"):
            load_prompt("v9", self.dir)

    def test_llave_simple_en_el_json_da_error_claro(self):
        # La trampa que marca el profesor: el JSON literal va con llaves dobles.
        self.write(VALID_YAML.replace('{{"category": "..."}}', '{"category": "..."}'))
        with self.assertRaises(PromptError):
            load_prompt("v9", self.dir)

    def test_plantilla_fija_y_variables_por_request(self):
        # M1L2 · Variables de contexto: la plantilla no cambia; cambian los datos.
        self.write(VALID_YAML)
        tpl = load_prompt("v9", self.dir)
        system = render_system(tpl, empresa="OtraEmpresa")
        self.assertIn("Empresa OtraEmpresa", system)
        self.assertIn('{"category": "..."}', system)  # las llaves dobles quedan simples
        self.assertEqual(render_user(tpl, "  hola {x}  "), "<consulta>hola {x}</consulta>")


class TestParser(unittest.TestCase):
    def check(self, text, category, method, valid=True):
        parsed = parse_category(text)
        self.assertEqual((parsed.category, parsed.method, parsed.valid), (category, method, valid))

    def test_json(self):
        self.check('{"category": "billing"}', "billing", "json")

    def test_json_envuelto_en_bloque_de_codigo(self):
        self.check('```json\n{"category": "Account"}\n```', "account", "json")

    def test_cot_lee_solo_la_ultima_linea(self):
        text = "Tema: cobro de usuarios.\nPide: que no le cobren.\nDecisión: technical no, billing.\nCATEGORIA: billing"
        self.check(text, "billing", "linea_final")

    def test_cot_con_tilde_y_negrita(self):
        self.check("Tema: x\n**CATEGORÍA:** technical.", "technical", "linea_final")

    def test_una_sola_palabra(self):
        self.check("other\n", "other", "texto")

    def test_heuristica_en_texto_libre(self):
        self.check("La consulta corresponde a la categoría account.", "account", "heuristica")

    def test_categoria_inventada_queda_invalida(self):
        self.check('{"category": "facturacion"}', "facturacion", "json", valid=False)

    def test_sin_categoria_reconocible(self):
        self.check("No estoy seguro, podría ser billing o technical.", None, "fallo", valid=False)


class TestCLI(unittest.TestCase):
    def run_cli(self, argv, outcomes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Clave falsa: load_settings exige al menos una; nunca se usa porque el proveedor es falso.
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba"}):
            settings = load_settings()
        settings = dataclasses.replace(settings, metrics_path=Path(tmp.name) / "metrics.jsonl")
        provider = FakeProvider("gemini", "gemini-3.5-flash-lite", outcomes)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                mock.patch.object(cli, "build_providers", return_value=([provider], [])), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue(), MetricsLog(settings.metrics_path).read_all(), provider

    def test_prompt_elegido_por_argumento_queda_en_la_salida_y_en_las_metricas(self):
        code, out, _, records, _ = self.run_cli(["--prompt", "v2", "me cobraron dos veces"], ['{"category": "billing"}'])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["category"], "billing")
        self.assertEqual(result["prompt_version"], "triage_v2")
        self.assertEqual(result["parse_method"], "json")
        self.assertEqual(records[-1]["prompt_version"], "triage_v2")
        self.assertEqual(records[-1]["parse_method"], "json")
        self.assertTrue(records[-1]["category_valid"])
        self.assertNotIn("me cobraron", json.dumps(records))  # M1L4: sin texto del cliente

    def test_cot_muestra_el_razonamiento_pero_no_lo_guarda(self):
        answer = "Tema: x\nPide: y\nDecisión: z\nCATEGORIA: account"
        code, out, _, records, _ = self.run_cli(["--prompt", "v4", "no puedo entrar"], [answer])
        result = json.loads(out)
        self.assertEqual(result["category"], "account")
        self.assertIn("Pide: y", result["model_output"])
        self.assertNotIn("Pide", json.dumps(records))

    def test_version_inexistente_es_error_de_configuracion_sin_llamar_al_modelo(self):
        code, _, err, _, provider = self.run_cli(["--prompt", "v99", "hola"], [])
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("No existe", err)
        self.assertEqual(provider.calls, 0)

    def test_categoria_invalida_avisa(self):
        code, out, err, _, _ = self.run_cli(["--prompt", "v2", "hola"], ['{"category": "ventas"}'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["category_valid"])
        self.assertIn("no se obtuvo una categoría válida", err)


class TestComparar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "pricing.json").write_text(json.dumps({"models": {
            "free-model": {"input": 0.0, "output": 0.0},
            "paid-model": {"input": 0.10, "output": 0.50},
        }}), encoding="utf-8")
        self.pricing = Pricing(root / "pricing.json")
        self.metrics = MetricsLog(root / "metrics.jsonl")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_el_dataset_del_repo_es_valido_y_tiene_todas_las_categorias_en_cada_lote(self):
        cases = load_dataset()
        for lot in ("dev", "holdout"):
            self.assertEqual({c["categoria"] for c in cases if c["lote"] == lot}, set(CATEGORIES))

    def test_dataset_con_categoria_inexistente_falla_antes_de_llamar(self):
        path = self.root / "malo.jsonl"
        path.write_text(json.dumps({"id": "X", "texto": "t", "lote": "dev", "categoria": "ventas",
                                    "regla": None, "justificacion": ""}) + "\n", encoding="utf-8")
        with self.assertRaises(DatasetError):
            load_dataset(path)

    def test_resumen_por_lote_con_proveedor_falso(self):
        cases = [
            {"id": "A", "texto": "a", "lote": "dev", "categoria": "billing", "regla": None},
            {"id": "B", "texto": "b", "lote": "dev", "categoria": "account", "regla": "R3"},
            {"id": "C", "texto": "c", "lote": "holdout", "categoria": "other", "regla": None},
        ]
        provider = FakeProvider("gemini", "free-model", ['{"category": "billing"}', "technical", "otra cosa"])
        router = Router([provider], self.pricing, self.metrics, budget_usd=1, max_retries=0, max_wait_s=1)
        rows = evaluate(router, load_prompt("v2", PROMPTS_DIR), cases, log=lambda *_: None)
        summary = summarize(rows, self.pricing, "paid-model")
        self.assertEqual(summary["aciertos"], {"dev": (1, 2), "holdout": (0, 1)})
        self.assertEqual(summary["invalidas"], 1)       # "otra cosa" no trae categoría
        self.assertEqual(summary["parseo_fragil"], 1)
        # FakeProvider: 1000 tokens in, 500 out → (1000×0.10 + 500×0.50)/1M × 10.000
        self.assertAlmostEqual(summary["costo_ref_usd"], 3.5)
        report = render_report([summary], rows, {"fecha": "hoy", "proveedor": "gemini", "modelo": "free-model",
                                                 "modelo_ref": "paid-model", "temperature": 0.0,
                                                 "n_dev": 2, "n_holdout": 1})
        self.assertIn("B: esperada `account`, obtenida `technical` · regla R3", report)
        self.assertNotIn("C: esperada", report)  # los errores de holdout no se muestran
        self.assertEqual(self.metrics.read_all()[0]["prompt_version"], "triage_v2")


if __name__ == "__main__":
    unittest.main()
