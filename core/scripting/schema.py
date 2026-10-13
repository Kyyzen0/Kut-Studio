"""Le schéma JSON (draft 2020-12) du format déclaratif, **généré** depuis la table :data:`core.scripting.verbs.VERBS` : il ne peut pas
dériver du décodeur, qui lit la même table. Un éditeur ou un outil (LLM compris) valide ainsi un script avant de le lancer.

Le schéma dit la forme (champs, types, bornes, valeurs permises) ; ce qu'il ne peut pas dire (un alias défini avant d'être cité, un
identifiant libre) reste le travail du décodeur et de la session.
"""

from __future__ import annotations

from typing import Any

from ..node_graph import MAX_ID_LENGTH
from .script import MAX_OPS, MAX_PATH_TEXT, SCRIPT_VERSION
from .verbs import MAX_PARAMS, MAX_TEXT, VERBS, Kind, Param, Verb

_ID_PATTERN = f"^[A-Za-z0-9_]{{1,{MAX_ID_LENGTH}}}$"


def _param_schema(param: Param) -> dict[str, Any]:
    schema: dict[str, Any]
    if param.kind is Kind.BOOLEAN:
        schema = {"type": "boolean"}
    elif param.kind is Kind.NUMBER:
        schema = {"type": "number"}
        if param.minimum is not None:
            schema["minimum"] = param.minimum
        if param.maximum is not None:
            schema["maximum"] = param.maximum
    elif param.kind is Kind.INTEGER:
        schema = {"type": "integer"}
        if param.minimum is not None:
            schema["minimum"] = int(param.minimum)
        if param.maximum is not None:
            schema["maximum"] = int(param.maximum)
    elif param.kind is Kind.COLOR:
        schema = {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"}
    elif param.kind is Kind.PATH:
        schema = {"type": "string", "minLength": 1, "maxLength": MAX_PATH_TEXT}
    elif param.kind is Kind.STRING:
        schema = {"type": "string", "minLength": 1, "maxLength": param.max_length or MAX_TEXT}
    elif param.kind is Kind.ENUM:
        schema = {"enum": list(param.choices)}
    elif param.kind is Kind.ID:
        schema = {"type": "string", "pattern": _ID_PATTERN}
    elif param.kind is Kind.SELECTOR:
        schema = {"anyOf": [
            {"type": "string", "minLength": 1, "maxLength": MAX_TEXT},
            {"type": "object", "properties": {"ref": {"type": "string", "pattern": _ID_PATTERN}}, "required": ["ref"],
             "additionalProperties": False},
        ]}
    else:
        schema = {"type": "object", "maxProperties": MAX_PARAMS, "propertyNames": {"pattern": _ID_PATTERN},
                  "additionalProperties": {"anyOf": [
                      {"type": "number"}, {"type": "boolean"}, {"type": "string", "maxLength": MAX_TEXT},
                  ]}}
    schema["description"] = param.doc
    return schema


def _verb_schema(verb: Verb) -> dict[str, Any]:
    properties: dict[str, Any] = {"op": {"const": verb.name}}
    properties.update({param.name: _param_schema(param) for param in verb.params})
    if verb.creates:
        properties["id"] = {"type": "string", "pattern": _ID_PATTERN, "description": f"Identifiant imposé du {verb.creates} créé."}
        properties["as"] = {"type": "string", "pattern": _ID_PATTERN,
                            "description": "Alias : un nom du script pour citer l'objet créé ({\"ref\": alias}) ; sert d'identifiant sans « id »."}
    return {
        "title": verb.name, "description": verb.doc, "type": "object", "properties": properties,
        "required": ["op", *[param.name for param in verb.params if param.required]], "additionalProperties": False,
    }


def json_schema() -> dict[str, Any]:
    """Le schéma complet d'un script."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Kut-Studio script",
        "type": "object",
        "properties": {
            "kut_script": {"const": SCRIPT_VERSION, "description": "Version du format."},
            "description": {"type": "string", "maxLength": 4 * MAX_TEXT},
            "ops": {"type": "array", "maxItems": MAX_OPS, "items": {"oneOf": [_verb_schema(verb) for verb in VERBS.values()]}},
        },
        "required": ["kut_script", "ops"],
        "additionalProperties": False,
    }


__all__ = ["json_schema"]
