"""Registro de métricas por request en ``data/metrics.jsonl`` (append-only).

Fuente: consigna del PI · "Registro de métricas: log estructurado append-only por
request: timestamp, tokens (prompt/completion/total), latencia (s) y costo estimado (USD)".
"JSONL alcanza; no uses SQLite ni una base de datos".
Por privacidad no se guarda el texto de la consulta ni la respuesta (M1L4 · Manejo de PII:
"nunca registrar entradas o salidas en bruto").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MetricsLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Agrega una línea JSON con timestamp UTC (ISO 8601) y la devuelve."""
        line = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return line

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        with open(self.path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if raw:
                    records.append(json.loads(raw))
        return records

    def total_cost(self) -> float:
        """Gasto acumulado en USD de todas las llamadas registradas."""
        return sum(float(r.get("cost_usd") or 0.0) for r in self.read_all())
