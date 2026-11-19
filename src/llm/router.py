"""Router con fallback entre proveedores y control de presupuesto.

Fuentes:
- M1L1 · Patrón de Router con fallback: el primario responde; si falla, responde el otro,
  sin que quien llama se entere. Lo central es el contrato común (``LLMProvider``).
- M0L1 · Inversión de dependencias: el router recibe proveedores ya construidos.
- M1L1 · Rate limits: "gestionar el presupuesto de uso de API estimando costos por request".
"""

from __future__ import annotations

from typing import Callable

from ..costs import Pricing
from ..metrics import MetricsLog
from ..retry import call_with_backoff
from .base import AllProvidersFailedError, BudgetExceededError, LLMProvider, LLMResponse, ProviderError, TokenCallback


class Router:
    def __init__(
        self,
        providers: list[LLMProvider],
        pricing: Pricing,
        metrics: MetricsLog,
        *,
        budget_usd: float,
        max_retries: int,
        max_wait_s: float,
        sleep: Callable[[float], None] | None = None,
        on_failure: Callable[[str, Exception], None] | None = None,
    ):
        if not providers:
            raise ValueError("El router necesita al menos un proveedor")
        self.providers = providers
        self.pricing = pricing
        self.metrics = metrics
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.max_wait_s = max_wait_s
        self._sleep = sleep
        # Se llama cada vez que un proveedor falla o se omite, para avisar al usuario (D-33).
        self._on_failure = on_failure

    def _notify(self, provider_name: str, exc: Exception) -> None:
        if self._on_failure is not None:
            self._on_failure(provider_name, exc)

    def ask(
        self,
        prompt: str,
        system: str,
        *,
        prompt_version: str,
        on_token: TokenCallback | None = None,
        extra_fields: Callable[[LLMResponse], dict] | None = None,
    ) -> LLMResponse:
        """Pide la respuesta al primer proveedor disponible, en orden.

        Cada intento (exitoso o fallido) queda en ``data/metrics.jsonl``.
        ``extra_fields`` recibe la respuesta exitosa y devuelve campos para agregar a su línea
        de métricas (ej. cómo se parseó la categoría, D-44). Nunca debe devolver texto del
        cliente ni del modelo (M1L4).
        """
        errors: list[Exception] = []
        for index, provider in enumerate(self.providers):
            base = {
                "provider": provider.name,
                "model": provider.model,
                # M1L1 · Sampling: "loguear siempre el valor elegido" de temperature.
                "temperature": provider.temperature,
                "prompt_version": prompt_version,
                "stream": on_token is not None,
                "fallback": index > 0,
            }

            if self.pricing.is_paid(provider.model):
                spent = self.metrics.total_cost()
                if spent >= self.budget_usd:
                    err = BudgetExceededError(
                        f"Presupuesto agotado: gastado USD {spent:.4f} de USD {self.budget_usd:.2f}. "
                        f"No se llama a {provider.name}."
                    )
                    self.metrics.append({**base, "status": "skipped", "error_kind": "budget_exceeded", "cost_usd": 0.0})
                    errors.append(err)
                    self._notify(provider.name, err)
                    continue

            kwargs = {"max_retries": self.max_retries, "max_wait_s": self.max_wait_s}
            if self._sleep is not None:
                kwargs["sleep"] = self._sleep
            try:
                response, attempts = call_with_backoff(
                    lambda p=provider: p.complete(prompt, system, on_token=on_token), **kwargs
                )
            except ProviderError as exc:
                self.metrics.append(
                    {
                        **base,
                        "status": "error",
                        "error_kind": exc.kind,
                        # Mensaje del proveedor (recortado) para poder diagnosticar; no incluye la consulta (D-33).
                        "error_message": str(exc)[:300],
                        "attempts": getattr(exc, "attempts", 1),
                        "cost_usd": 0.0,
                    }
                )
                errors.append(exc)
                self._notify(provider.name, exc)
                if exc.partial_output:
                    # Ya se mostró texto: pasar a otro proveedor duplicaría la salida (D-08).
                    break
                continue

            response.cost_usd = self.pricing.cost(response.model, response.input_tokens, response.output_tokens)
            record = {
                **base,
                "status": "ok",
                "attempts": attempts,
                "prompt_tokens": response.input_tokens,
                "completion_tokens": response.output_tokens,
                "total_tokens": response.total_tokens,
                "latency_s": round(response.latency_s, 3),
                "cost_usd": round(response.cost_usd, 8),
            }
            if response.ttft_s is not None:
                record["ttft_s"] = round(response.ttft_s, 3)  # M1L1 · Streaming: latencia percibida
            if extra_fields is not None:
                record.update(extra_fields(response))
            self.metrics.append(record)
            return response

        raise AllProvidersFailedError(errors)
