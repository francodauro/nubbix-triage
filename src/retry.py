"""Reintentos con backoff exponencial + jitter, respetando Retry-After.

Fuente: M1L1 · Rate limits y manejo de errores, función ``call_with_backoff``:
- 429 → backoff exponencial con jitter (1 s, 2 s, 4 s… + aleatorio).
- 5xx → reintentos limitados.
- Si el proveedor manda Retry-After, ese valor tiene prioridad sobre el cálculo.

Agregado propio (D-07 en docs/DECISIONES.md): si la espera pedida supera
``max_wait_s`` no esperamos; cortamos para que el router pase al otro proveedor.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from .llm.base import ProviderError

T = TypeVar("T")


def call_with_backoff(
    fn: Callable[[], T],
    *,
    max_retries: int,
    max_wait_s: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    """Ejecuta ``fn`` reintentando ante errores reintentables.

    :param fn: función sin argumentos que hace la llamada al proveedor.
    :param max_retries: reintentos además del primer intento.
    :param max_wait_s: espera máxima aceptable entre intentos.
    :param sleep: función de espera (inyectable para los tests).
    :return: (resultado, cantidad de intentos realizados).
    :raises ProviderError: el último error si no es reintentable o se agotaron los intentos.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(), attempt
        except ProviderError as exc:
            exc.attempts = attempt  # para registrarlo en las métricas
            if not exc.retryable or exc.partial_output or attempt > max_retries:
                raise
            # Retry-After tiene prioridad sobre el backoff calculado (M1L1).
            wait = exc.retry_after if exc.retry_after is not None else (2 ** (attempt - 1)) + random.uniform(0, 1)
            if wait > max_wait_s:
                raise
            sleep(wait)
