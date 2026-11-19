"""Carga de prompts versionados en YAML y armado de los mensajes.

Fuentes:
- PI · "prompts versionados en YAML (fuera del código)".
- M1L2 · Plantillas y versionado: cada versión es un archivo nuevo que nunca se edita, y la
  plantilla (se versiona) se separa de las variables de cada request (no se versionan).
- M1L2 · System prompts: listar TODAS las categorías, incluida la categoría cajón ``other``.
- Ejemplo del profesor (repo OrbitAir, ``L2/orbitair/orbitair/prompts.py``): categorías con
  descripción en un diccionario como única fuente de verdad, inyectadas como ``{categorias}``,
  y la consulta delimitada en el mensaje de usuario.

Esquema de cada ``prompts/<version>.yaml`` (D-34):
    version:     igual al nombre del archivo (ej. triage_v2)
    fecha:       fecha de creación
    tecnica:     zero-shot, few-shot, chain-of-thought…
    descripcion: qué tiene esta versión (el porqué va en prompts/CHANGELOG.md)
    system:      plantilla del system prompt; puede usar {empresa} y {categorias}
    user:        plantilla del mensaje de usuario; debe usar {consulta}
Las llaves literales (por ejemplo las del JSON de respuesta) se escriben dobles: {{ }}.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path

import yaml

# Variable de contexto: si se agrega una categoría, cambia este diccionario y no las plantillas.
# Las categorías y sus nombres en inglés salen de la consigna del PI.
# Las descripciones resumen la guía de etiquetado (evals/guia_etiquetado.md) en términos
# generales, sin copiar casos del dataset: si copiaran casos, el modelo "vería las respuestas".
CATEGORIES: dict[str, str] = {
    "billing": "la suscripción a Nubbix y lo que Nubbix le cobra al cliente: cobros, devoluciones, facturas de Nubbix, medios de pago, plan contratado",
    "technical": "algo de la plataforma no funciona como debería: errores, caídas, lentitud, fallas de integraciones, de la app o de los reportes",
    "account": "la cuenta y los accesos: usuarios, roles y permisos, inicio de sesión, contraseñas, datos de la cuenta",
    "other": "cualquier consulta que no encaje en las anteriores: información comercial o general",
}

# Variables fijas del negocio (no cambian por request, pero tampoco viven en la plantilla).
FIXED_VARIABLES = {"empresa": "Nubbix"}

SYSTEM_VARIABLES = {"empresa", "categorias"}
USER_VARIABLES = {"consulta"}
REQUIRED_FIELDS = ("version", "fecha", "tecnica", "descripcion", "system", "user")
PREFIX = "triage_"


class PromptError(Exception):
    """El prompt pedido no existe o su archivo YAML es inválido (error de configuración)."""


@dataclass(frozen=True)
class PromptTemplate:
    version: str
    fecha: str
    tecnica: str
    descripcion: str
    system: str
    user: str
    path: Path


def normalize_version(version: str) -> str:
    """Acepta ``v2`` o ``triage_v2`` y devuelve ``triage_v2``."""
    version = version.strip()
    return version if version.startswith(PREFIX) else PREFIX + version


def list_versions(prompts_dir: Path) -> list[str]:
    """Versiones disponibles, en orden: ['triage_v1', 'triage_v2', ...]."""
    return sorted(p.stem for p in prompts_dir.glob(f"{PREFIX}*.yaml"))


def format_categories(categories: dict[str, str] = CATEGORIES) -> str:
    return "\n".join(f"- {name}: {description}" for name, description in categories.items())


def _fields(template: str) -> set[str]:
    """Nombres de las variables {asi} que usa una plantilla (las llaves dobles no cuentan)."""
    try:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError as exc:  # llave suelta, por ejemplo un { sin cerrar
        raise PromptError(f"Plantilla mal formada: {exc}. Las llaves literales van dobles: {{{{ }}}}") from exc


def load_prompt(version: str, prompts_dir: Path) -> PromptTemplate:
    """Lee y valida ``prompts/<version>.yaml``.

    :raises PromptError: si no existe, si falta un campo, o si la plantilla usa variables
        desconocidas (por ejemplo un ``{categoria}`` mal escrito).
    """
    version = normalize_version(version)
    path = prompts_dir / f"{version}.yaml"
    if not path.exists():
        raise PromptError(f"No existe {path.name}. Versiones disponibles: {list_versions(prompts_dir)}")

    with open(path, encoding="utf-8") as f:
        # safe_load: solo tipos básicos de YAML, nunca construye objetos de Python (D-35).
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PromptError(f"{path.name} debe ser un mapa YAML con los campos {REQUIRED_FIELDS}")

    missing = [k for k in REQUIRED_FIELDS if not str(data.get(k) or "").strip()]
    if missing:
        raise PromptError(f"A {path.name} le faltan campos: {missing}")
    if data["version"] != version:
        raise PromptError(f"{path.name} dice version={data['version']!r}; debe coincidir con el nombre del archivo")

    system, user = str(data["system"]), str(data["user"])
    unknown_system = _fields(system) - SYSTEM_VARIABLES
    if unknown_system:
        raise PromptError(f"{path.name}: el system usa variables desconocidas {sorted(unknown_system)}. "
                          f"Válidas: {sorted(SYSTEM_VARIABLES)}")
    user_fields = _fields(user)
    if user_fields != USER_VARIABLES:
        raise PromptError(f"{path.name}: el user debe usar solo {{consulta}}; usa {sorted(user_fields)}")

    return PromptTemplate(
        version=version,
        fecha=str(data["fecha"]),
        tecnica=str(data["tecnica"]),
        descripcion=str(data["descripcion"]).strip(),
        system=system,
        user=user,
        path=path,
    )


def render_system(template: PromptTemplate, **overrides: str) -> str:
    """Completa la plantilla del system con las variables fijas y las categorías."""
    values = {**FIXED_VARIABLES, "categorias": format_categories(), **overrides}
    return template.system.format(**values).strip()


def render_user(template: PromptTemplate, consulta: str) -> str:
    """Inserta el texto del cliente en la plantilla del mensaje de usuario.

    ``.format()`` solo interpreta las llaves de la plantilla, no las del valor: si el cliente
    escribe ``{algo}`` en su consulta, llega tal cual.
    """
    return template.user.format(consulta=consulta.strip())
