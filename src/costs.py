"""Cálculo de costo por llamada.

Fuente: M1L1 · Costo por token:
    costo = input_tokens / 1_000_000 × precio_input + output_tokens / 1_000_000 × precio_output
Los tokens salen siempre del ``usage`` que devuelve la API (nunca de una estimación).
Los precios viven en ``config/pricing.json`` (en el hands-on de M1L1: ``pricing.json``).
"""

from __future__ import annotations

import json
from pathlib import Path


class PricingError(Exception):
    """Falta el precio de un modelo en config/pricing.json."""


class Pricing:
    def __init__(self, path: Path):
        with open(path, encoding="utf-8") as f:
            self._models: dict[str, dict[str, float]] = json.load(f)["models"]

    def price(self, model: str) -> dict[str, float]:
        try:
            return self._models[model]
        except KeyError as exc:
            raise PricingError(
                f"El modelo {model!r} no tiene precio en config/pricing.json. "
                "Agregalo antes de usarlo, así el costo queda auditable."
            ) from exc

    def is_paid(self, model: str) -> bool:
        p = self.price(model)
        return p["input"] > 0 or p["output"] > 0

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.price(model)
        return input_tokens / 1_000_000 * p["input"] + output_tokens / 1_000_000 * p["output"]

    def models(self) -> list[str]:
        return list(self._models)


def project_cost(records: list[dict], pricing: Pricing, n_queries: int = 10_000) -> dict:
    """Proyecta el costo de ``n_queries`` consultas a partir de los tokens reales registrados.

    Fuente: M1L1 · Costo por token ("en el hands-on vas a multiplicar ese valor por
    10.000 consultas para evidenciar por qué esto sí importa a escala").
    Método (decisión propia, D-29): se toma el promedio de tokens de entrada y salida de
    las llamadas exitosas de ``data/metrics.jsonl`` y se calcula cuánto costarían
    ``n_queries`` consultas si todas las respondiera cada modelo de ``config/pricing.json``.

    :return: dict con la base del cálculo y el costo proyectado por modelo, o una nota si
        todavía no hay llamadas exitosas registradas.
    """
    ok = [r for r in records if r.get("status") == "ok"]
    if not ok:
        return {"consultas": n_queries, "nota": "Todavía no hay llamadas exitosas registradas para proyectar."}
    avg_in = sum(r.get("prompt_tokens", 0) for r in ok) / len(ok)
    avg_out = sum(r.get("completion_tokens", 0) for r in ok) / len(ok)
    per_model = {
        model: round(pricing.cost(model, avg_in, avg_out) * n_queries, 6) for model in pricing.models()
    }
    return {
        "consultas": n_queries,
        "base": {
            "llamadas_exitosas": len(ok),
            "promedio_tokens_entrada": round(avg_in, 1),
            "promedio_tokens_salida": round(avg_out, 1),
        },
        "costo_usd_si_responde_solo": per_model,
    }
