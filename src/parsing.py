"""Extracción de la categoría a partir del texto del modelo.

Fuentes:
- M1L2 · Chain-of-thought: "si la salida debe ser parseable, forzar un formato exacto en la
  última línea (CATEGORIA: <valor>). El código toma solo la última línea".
- Ejemplo del profesor (``L2/orbitair/orbitair/clasificacion.py``): intentar en orden de
  confiabilidad JSON → última línea ``CATEGORIA:`` → buscar un nombre válido suelto.

Provisional hasta M1L3 (D-41, D-42): ahí la salida pasa a ser un contrato validado con
Pydantic y un error de formato se reintenta. Hoy una categoría inválida solo se marca.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .prompts import CATEGORIES

# Métodos, del más confiable al más frágil.
METHOD_JSON = "json"              # {"category": "billing"}            (v2, v3)
METHOD_LAST_LINE = "linea_final"  # ... \nCATEGORIA: billing           (v4, chain-of-thought)
METHOD_TEXT = "texto"             # billing                            (v1 pide solo el nombre)
METHOD_HEURISTIC = "heuristica"   # "La categoría es billing porque…" (frágil)
METHOD_FAILED = "fallo"

_LAST_LINE = re.compile(r"^\**\s*CATEGOR(?:I|Í)A\s*\**\s*:\s*\**\s*([\w-]+)", re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedCategory:
    category: str | None  # None si no se pudo extraer
    method: str
    valid: bool  # la categoría está en CATEGORIES


def _clean(value: str) -> str:
    return value.strip().strip(".\"'`*").strip().lower()


def parse_category(text: str, categories: dict[str, str] = CATEGORIES) -> ParsedCategory:
    def result(category: str | None, method: str) -> ParsedCategory:
        return ParsedCategory(category, method, category in categories)

    stripped = text.strip()

    # 1. JSON de una línea, aunque venga envuelto en ```json ... ```
    candidate = _FENCE.sub("", stripped).strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "category" in data:
        return result(_clean(str(data["category"])), METHOD_JSON)

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if lines:
        # 2. Última línea "CATEGORIA: x" (el razonamiento de las líneas anteriores se ignora)
        match = _LAST_LINE.match(lines[-1])
        if match:
            return result(_clean(match.group(1)), METHOD_LAST_LINE)
        # 3. La respuesta entera es una sola palabra (lo que pide v1)
        if len(lines) == 1 and re.fullmatch(r"[\w-]+", _clean(lines[0])):
            return result(_clean(lines[0]), METHOD_TEXT)

    # 4. Heurística: exactamente un nombre válido mencionado en el texto
    found = [c for c in categories if re.search(rf"\b{re.escape(c)}\b", stripped.lower())]
    if len(found) == 1:
        return result(found[0], METHOD_HEURISTIC)
    return ParsedCategory(None, METHOD_FAILED, False)


def metrics_fields(text: str) -> dict:
    """Campos para ``data/metrics.jsonl`` (D-44): cómo se leyó la respuesta, sin guardar texto."""
    parsed = parse_category(text)
    return {"parse_method": parsed.method, "category_valid": parsed.valid}
