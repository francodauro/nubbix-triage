"""Tests de tool calling en cada proveedor (M1L3 · Tool use). Sin APIs: el cliente del SDK se reemplaza
por uno falso que devuelve objetos reales de cada SDK, armados a mano.

Lo que se prueba es la asimetría de la lección: OpenAI entrega los argumentos como **string JSON** (hay que
hacer json.loads, que puede fallar) y Gemini como **dict**. Los dos terminan en ``LLMResponse.tool_args``.
"""

import json
import unittest
from types import SimpleNamespace

from google.genai import types
from openai.types.chat import ChatCompletion

from src.contract import TRIAGE_TOOL
from src.llm.base import TOOL_BAD_JSON, TOOL_NO_CALL, TOOL_NOT_OBJECT
from src.llm.gemini_provider import GeminiProvider
from src.llm.openai_provider import OpenAIProvider, _parse_arguments
from tests.fakes_l3 import VALID


class FakeCreate:
    """Reemplaza ``client.chat.completions.create`` / ``client.models.generate_content``."""

    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def openai_completion(*, arguments=None, content=None, finish="stop"):
    message = {"role": "assistant", "content": content}
    if arguments is not None:
        message["tool_calls"] = [{"id": "c1", "type": "function",
                                  "function": {"name": TRIAGE_TOOL.name, "arguments": arguments}}]
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 120, "total_tokens": 720},
    })


def gemini_response(*, args=None, text=None):
    part = types.Part(function_call=types.FunctionCall(name=TRIAGE_TOOL.name, args=args)) if args is not None \
        else types.Part(text=text)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[part]), finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=600, candidates_token_count=100, total_token_count=700),
    )


class TestOpenAITool(unittest.TestCase):
    def provider(self, response, strict=True):
        p = OpenAIProvider(api_key="clave-de-prueba", model="gpt-6-luna", max_tokens=400, temperature=0.0,
                           timeout_s=5, reasoning_effort="none", strict_tools=strict)
        fake = FakeCreate(response)
        p._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake)))
        return p, fake

    def test_arguments_string_se_convierte_en_dict(self):
        p, _ = self.provider(openai_completion(arguments=json.dumps(VALID)))
        r = p.complete("<consulta>x</consulta>", "system", tool=TRIAGE_TOOL)
        self.assertEqual(r.tool_args, VALID)
        self.assertIsNone(r.tool_error)
        self.assertEqual((r.input_tokens, r.output_tokens), (600, 120))

    def test_fuerza_la_funcion_y_envia_el_schema_del_contrato(self):
        p, fake = self.provider(openai_completion(arguments=json.dumps(VALID)))
        p.complete("x", "system", tool=TRIAGE_TOOL)
        self.assertEqual(fake.kwargs["tool_choice"], {"type": "function", "function": {"name": TRIAGE_TOOL.name}})
        function = fake.kwargs["tools"][0]["function"]
        self.assertEqual(function["parameters"], TRIAGE_TOOL.parameters)
        self.assertTrue(function["strict"])
        self.assertIs(fake.kwargs["parallel_tool_calls"], False)
        self.assertEqual(fake.kwargs["reasoning_effort"], "none")
        self.assertEqual(fake.kwargs["max_completion_tokens"], 400)

    def test_sin_strict_no_se_envian_strict_ni_parallel(self):
        p, fake = self.provider(openai_completion(arguments=json.dumps(VALID)), strict=False)
        p.complete("x", "system", tool=TRIAGE_TOOL)
        self.assertNotIn("strict", fake.kwargs["tools"][0]["function"])
        self.assertNotIn("parallel_tool_calls", fake.kwargs)

    def test_json_cortado_no_explota_y_se_informa(self):
        p, _ = self.provider(openai_completion(arguments='{"answer": "Hola, vamos a rev', finish="length"))
        r = p.complete("x", "system", tool=TRIAGE_TOOL)
        self.assertIsNone(r.tool_args)
        self.assertEqual(r.tool_error, TOOL_BAD_JSON)
        self.assertEqual(r.finish_reason, "length")

    def test_si_responde_texto_en_vez_de_llamar(self):
        p, _ = self.provider(openai_completion(content="Le cuento que..."))
        r = p.complete("x", "system", tool=TRIAGE_TOOL)
        self.assertIsNone(r.tool_args)
        self.assertEqual(r.tool_error, TOOL_NO_CALL)

    def test_json_que_no_es_objeto(self):
        self.assertEqual(_parse_arguments("[1, 2]"), (None, TOOL_NOT_OBJECT))
        self.assertEqual(_parse_arguments('{"a": 1}'), ({"a": 1}, None))


class TestGeminiTool(unittest.TestCase):
    def provider(self, response):
        p = GeminiProvider(api_key="clave-de-prueba", model="gemini-3.5-flash-lite", max_tokens=1024,
                           temperature=0.0, timeout_s=5)
        fake = FakeCreate(response)
        p._client = SimpleNamespace(models=SimpleNamespace(generate_content=fake))
        return p, fake

    def test_args_ya_vienen_como_dict(self):
        p, _ = self.provider(gemini_response(args=VALID))
        r = p.complete("x", "system", tool=TRIAGE_TOOL)
        self.assertEqual(r.tool_args, VALID)
        self.assertIsNone(r.tool_error)
        self.assertEqual(json.loads(r.text), VALID)

    def test_config_obliga_a_llamar_a_la_funcion(self):
        p, fake = self.provider(gemini_response(args=VALID))
        p.complete("x", "system", tool=TRIAGE_TOOL)
        config = fake.kwargs["config"]
        fcc = config.tool_config.function_calling_config
        self.assertEqual(fcc.mode, types.FunctionCallingConfigMode.ANY)
        self.assertEqual(fcc.allowed_function_names, [TRIAGE_TOOL.name])
        self.assertTrue(config.automatic_function_calling.disable)
        declaration = config.tools[0].function_declarations[0]
        self.assertEqual(declaration.parameters_json_schema, TRIAGE_TOOL.parameters)
        self.assertEqual(config.system_instruction, "system")

    def test_si_responde_texto_en_vez_de_llamar(self):
        p, _ = self.provider(gemini_response(text="Le cuento que..."))
        r = p.complete("x", "system", tool=TRIAGE_TOOL)
        self.assertIsNone(r.tool_args)
        self.assertEqual(r.tool_error, TOOL_NO_CALL)
        self.assertEqual(r.text, "Le cuento que...")


if __name__ == "__main__":
    unittest.main()
