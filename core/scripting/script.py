"""Le décodeur du format déclaratif (ADR-0005, §3) : un script JSON devient une suite d'opérations **déjà validées**, ou une liste d'erreurs.

Principes :

- **Strict.** Version obligatoire (une plus récente est refusée), champ inconnu = erreur (un verbe mal orthographié ne doit pas « réussir »),
  types et bornes vérifiés, identifiants sûrs (``core.node_graph.is_safe_id``), tailles plafonnées. Toutes les erreurs trouvées sont rendues
  d'un coup (jusqu'à :data:`MAX_ISSUES`) : on corrige un script en une passe, pas une erreur par essai.
- **Rien n'est évalué.** Une valeur est une donnée ; un alias (``"as"``) est un nom lié à l'identifiant d'un objet créé, résolu **ici**, dans
  une table locale au script : ce n'est pas une variable (pas de calcul, pas d'expression). Un alias sert d'identifiant à l'objet créé s'il
  n'a pas d'``id`` explicite : le script rejoué donne le même projet, identifiant par identifiant.
- **Les messages sont des codes.** Une erreur porte un ``code`` stable et ses ``params`` ; le CLI la traduit (``ui/i18n_cli.py``).
"""

from __future__ import annotations

import difflib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..node_graph import MAX_ID_LENGTH, is_safe_id
from .session import ApplyReport, Operation, Session
from .verbs import MAX_PARAMS, MAX_TEXT, VERBS, Kind, Param, ScriptContext, Verb

SCRIPT_VERSION = 1
"""``kut_script`` : la version du format. Une version plus récente que celle-ci est refusée, comme pour le ``.kut``."""
MAX_SCRIPT_BYTES = 1_048_576
MAX_OPS = 5_000
MAX_ISSUES = 50
TOP_LEVEL_KEYS = ("kut_script", "description", "ops")
MAX_PATH_TEXT = 1024
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


@dataclass(frozen=True)
class ScriptIssue:
    """Une erreur de validation. ``index`` : l'opération (``None`` pour le script entier) ; ``path`` : le champ (``ops[2].at``)."""

    code: str
    index: int | None = None
    verb: str | None = None
    path: str = ""
    params: tuple[tuple[str, Any], ...] = ()

    def values(self) -> dict[str, Any]:
        return dict(self.params)


class ScriptInvalid(ValueError):
    """Le script ne passe pas la validation ; ``issues`` les dit toutes."""

    def __init__(self, issues: list[ScriptIssue]) -> None:
        self.issues = tuple(issues)
        first = issues[0] if issues else None
        super().__init__(f"{len(issues)} erreur(s) de script" + (f" ; première : {first.code} {first.path}" if first else ""))


@dataclass(frozen=True)
class ScriptOp:
    index: int
    verb: Verb
    args: Mapping[str, Any]
    alias: str | None = None

    @property
    def name(self) -> str:
        return f"{self.index}:{self.verb.name}"

    def operation(self, context: ScriptContext | None = None) -> Operation:
        """L'opération prête à appliquer ; un verbe qui lit un fichier reçoit le contexte (dossier du script, ``--allow-outside``)."""
        return self.verb.build(self.args, context) if self.verb.needs_context else self.verb.build(self.args)


@dataclass(frozen=True)
class Script:
    version: int
    ops: tuple[ScriptOp, ...]
    description: str = ""
    aliases: Mapping[str, str] = field(default_factory=dict)


# --- Lecture ----------------------------------------------------------------------------------------------------------


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON refusée : {name}")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:                       # une seule passe : un fichier de 1 Mio peut porter des dizaines de milliers de clés
        if key in result:
            raise ValueError(f"clé en double : {key}")
        result[key] = value
    return result


def loads_script(text: str) -> Any:
    """Le JSON d'un script : ni ``NaN``/``Infinity``, ni clé en double (qui laisserait la dernière gagner sans rien dire)."""
    try:
        return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates)
    except RecursionError as error:                          # un JSON imbriqué à l'extrême
        raise ScriptInvalid([ScriptIssue("not_json", params=(("error", "imbrication excessive"),))]) from error
    except ValueError as error:
        raise ScriptInvalid([ScriptIssue("not_json", params=(("error", str(error)),))]) from error


def read_script(path: str | Path) -> Any:
    """Lit un fichier de script (taille plafonnée, UTF-8) et rend le JSON décodé ; ``OSError`` si illisible, ``ScriptInvalid`` sinon."""
    target = Path(path)
    with target.open("rb") as handle:
        raw = handle.read(MAX_SCRIPT_BYTES + 1)
    if len(raw) > MAX_SCRIPT_BYTES:
        raise ScriptInvalid([ScriptIssue("too_large", params=(("limit", MAX_SCRIPT_BYTES),))])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScriptInvalid([ScriptIssue("not_json", params=(("error", str(error)),))]) from error
    return loads_script(text)


# --- Validation -------------------------------------------------------------------------------------------------------


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class _Decoder:
    def __init__(self) -> None:
        self.issues: list[ScriptIssue] = []
        self.aliases: dict[str, tuple[str, str]] = {}      # alias -> (genre, identifiant)
        self.created: dict[str, set[str]] = {}              # clé « genre » ou « genre:portée » (nœuds : locaux au clip)

    def issue(self, code: str, index: int | None = None, verb: str | None = None, path: str = "", **params: Any) -> None:
        if len(self.issues) < MAX_ISSUES:
            self.issues.append(ScriptIssue(code, index, verb, path, tuple(params.items())))

    # -- un paramètre -------------------------------------------------------------------------------------------------
    def value(self, param: Param, raw: Any, index: int, verb: Verb, path: str) -> Any:
        """La valeur validée (``None`` après avoir signalé l'erreur)."""
        kind = param.kind
        if kind is Kind.BOOLEAN:
            if not isinstance(raw, bool):
                self.bad(index, verb, path, "boolean")
                return None
            return raw
        if kind is Kind.NUMBER:
            if not _finite(raw):
                self.bad(index, verb, path, "number")
                return None
            low = -math.inf if param.minimum is None else param.minimum
            high = math.inf if param.maximum is None else param.maximum
            if not low <= raw <= high:
                self.issue("out_of_range", index, verb.name, path, minimum=param.minimum, maximum=param.maximum)
                return None
            return float(raw)
        if kind is Kind.INTEGER:
            if isinstance(raw, bool) or not isinstance(raw, int):
                self.bad(index, verb, path, "integer")
                return None
            low = -math.inf if param.minimum is None else param.minimum
            high = math.inf if param.maximum is None else param.maximum
            if not low <= raw <= high:
                self.issue("out_of_range", index, verb.name, path, minimum=param.minimum, maximum=param.maximum)
                return None
            return raw
        if kind is Kind.STRING:
            return self.text(raw, index, verb, path, limit=param.max_length or MAX_TEXT, multiline=param.multiline,
                             allow_empty=param.allow_empty)
        if kind is Kind.PATH:
            return self.text(raw, index, verb, path, limit=MAX_PATH_TEXT)
        if kind is Kind.COLOR:
            if not isinstance(raw, str) or not _COLOR.match(raw):
                self.issue("bad_color", index, verb.name, path)
                return None
            return raw
        if kind is Kind.ENUM:
            if raw not in param.choices:
                close = difflib.get_close_matches(str(raw), param.choices, n=1)
                self.issue("not_in_choices", index, verb.name, path, choices=", ".join(param.choices), suggestion=close[0] if close else "")
                return None
            return raw
        if kind is Kind.ID:
            if not isinstance(raw, str) or not is_safe_id(raw):
                self.issue("bad_id", index, verb.name, path, limit=MAX_ID_LENGTH)
                return None
            return raw
        if kind is Kind.SELECTOR:
            return self.selector(param, raw, index, verb, path)
        if kind is Kind.OBJECT:
            return self.mapping(raw, index, verb, path)
        if kind is Kind.LIST:
            return self.sequence(param, raw, index, verb, path)
        return None                                           # pragma: no cover - tous les genres sont traités ci-dessus

    def bad(self, index: int, verb: Verb, path: str, expected: str) -> None:
        """Signale un type incorrect ; l'appelant rend ``None`` (valeur absente)."""
        self.issue("wrong_type", index, verb.name, path, expected=expected)

    def text(self, raw: Any, index: int, verb: Verb, path: str, *, limit: int = MAX_TEXT, multiline: bool = False,
             allow_empty: bool = False) -> str | None:
        if not isinstance(raw, str):
            self.bad(index, verb, path, "string")
            return None
        controls = any((ord(char) < 32 and not (multiline and char == "\n")) or ord(char) == 127 for char in raw)
        # Le modèle retire les espaces en bordure (noms, textes) : un texte qui en porte serait enregistré autrement qu'écrit.
        if (not raw and not allow_empty) or len(raw) > limit or controls or raw != raw.strip():
            self.issue("bad_text", index, verb.name, path, limit=limit)
            return None
        return raw

    def selector(self, param: Param, raw: Any, index: int, verb: Verb, path: str) -> str | None:
        if isinstance(raw, Mapping):
            if set(raw) != {"ref"}:
                self.issue("bad_ref", index, verb.name, path)
                return None
            alias = raw["ref"]
            known = self.aliases.get(alias) if isinstance(alias, str) else None
            if known is None:
                self.issue("unknown_alias", index, verb.name, path, alias=str(alias))
                return None
            if known[0] != param.of:
                self.issue("alias_kind", index, verb.name, path, alias=str(alias), expected=param.of, found=known[0])
                return None
            return known[1]
        return self.text(raw, index, verb, path)

    def sequence(self, param: Param, raw: Any, index: int, verb: Verb, path: str) -> list[Any] | None:
        """Une liste bornée dont chaque élément est validé comme un paramètre (valeur seule ou objet à champs fixes) ; ``None`` au moindre défaut."""
        if not isinstance(raw, list):
            self.bad(index, verb, path, "array")
            return None
        low = 0 if param.minimum is None else param.minimum
        high = math.inf if param.maximum is None else param.maximum
        if not low <= len(raw) <= high:
            self.issue("out_of_range", index, verb.name, path, minimum=param.minimum, maximum=param.maximum)
            return None
        before = len(self.issues)
        result: list[Any] = []
        for position, element in enumerate(raw):
            where = f"{path}[{position}]"
            if param.item is not None:
                checked = self.value(param.item, element, index, verb, where)
                if checked is not None:
                    result.append(checked)
                continue
            if not isinstance(element, Mapping):
                self.bad(index, verb, where, "object")
                continue
            names = [field.name for field in param.fields]
            for key in element:
                if key not in names:
                    close = difflib.get_close_matches(str(key), names, n=1)
                    self.issue("unknown_field", index, verb.name, f"{where}.{key}", suggestion=close[0] if close else "")
            fields: dict[str, Any] = {}
            for field_param in param.fields:
                if field_param.name not in element:
                    if field_param.required:
                        self.issue("missing_field", index, verb.name, f"{where}.{field_param.name}")
                    continue
                checked = self.value(field_param, element[field_param.name], index, verb, f"{where}.{field_param.name}")
                if checked is not None:
                    fields[field_param.name] = checked
            result.append(fields)
        return result if len(self.issues) == before else None

    def mapping(self, raw: Any, index: int, verb: Verb, path: str) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            self.bad(index, verb, path, "object")
            return None
        if len(raw) > MAX_PARAMS:
            self.issue("too_many_params", index, verb.name, path, limit=MAX_PARAMS)
            return None
        result: dict[str, Any] = {}
        for key, item in raw.items():
            if not isinstance(key, str) or not is_safe_id(key):
                self.issue("bad_param_name", index, verb.name, f"{path}.{key}")
            elif isinstance(item, bool) or _finite(item):
                result[key] = item
            elif isinstance(item, str) and len(item) <= MAX_TEXT and not any(ord(char) < 32 for char in item):
                result[key] = item
            else:
                self.issue("bad_param_value", index, verb.name, f"{path}.{key}")
        return result

    # -- une opération ------------------------------------------------------------------------------------------------
    def operation(self, index: int, raw: Any) -> ScriptOp | None:
        base = f"ops[{index}]"
        if not isinstance(raw, Mapping):
            self.issue("op_not_object", index, None, base)
            return None
        name = raw.get("op")
        if not isinstance(name, str) or name not in VERBS:
            close = difflib.get_close_matches(str(name), list(VERBS), n=1) if isinstance(name, str) else []
            self.issue("unknown_verb", index, str(name) if isinstance(name, str) else None, f"{base}.op",
                       suggestion=close[0] if close else "")
            return None
        verb = VERBS[name]
        before = len(self.issues)
        for key in raw:
            if key != "op" and key not in verb.accepts:
                close = difflib.get_close_matches(str(key), verb.accepts, n=1)
                self.issue("unknown_field", index, name, f"{base}.{key}", suggestion=close[0] if close else "")
        args: dict[str, Any] = {}
        for param in verb.params:
            if param.name not in raw:
                if param.required:
                    self.issue("missing_field", index, name, f"{base}.{param.name}")
                continue
            checked = self.value(param, raw[param.name], index, verb, f"{base}.{param.name}")
            if checked is not None:
                args[param.name] = checked
        alias = None
        if verb.creates:
            alias = self.creation(index, verb, raw, args, base)
        if len(self.issues) > before:
            return None
        return ScriptOp(index, verb, args, alias)

    def creation(self, index: int, verb: Verb, raw: Mapping[str, Any], args: dict[str, Any], base: str) -> str | None:
        """Identifiant imposé (``id``) et alias (``as``) d'un verbe créateur ; l'alias sert d'identifiant s'il n'y a pas d'``id``."""
        explicit = raw.get("id")
        alias = raw.get("as")
        identifier: str | None = None
        if "id" in raw:
            identifier = self.value(Param("id", Kind.ID, ""), explicit, index, verb, f"{base}.id")
        if "as" in raw:
            checked = self.value(Param("as", Kind.ID, ""), alias, index, verb, f"{base}.as")
            if checked is not None and checked in self.aliases:
                self.issue("duplicate_alias", index, verb.name, f"{base}.as", alias=checked)
                checked = None
            alias = checked
            if alias is not None and identifier is None and "id" not in raw:
                identifier = alias
        else:
            alias = None
        kind = verb.creates or ""
        if identifier is not None:
            # Unicité globale par genre, ou bornée à un paramètre (nœuds d'étalonnage : locaux au clip).
            if verb.creates_scope:
                key = f"{kind}:{args.get(verb.creates_scope, '')}"
            else:
                key = kind
            taken = self.created.setdefault(key, set())
            if identifier in taken:
                self.issue("duplicate_id", index, verb.name, f"{base}.id" if "id" in raw else f"{base}.as", id=identifier)
            taken.add(identifier)
            args["id"] = identifier
            if alias is not None:
                self.aliases[alias] = (kind, identifier)
        return alias


def parse_script(data: Any) -> Script:
    """Valide ``data`` (le JSON déjà décodé) et rend le :class:`Script` ; ``ScriptInvalid`` avec **toutes** les erreurs trouvées sinon."""
    decoder = _Decoder()
    if not isinstance(data, Mapping):
        decoder.issue("not_object")
        raise ScriptInvalid(decoder.issues)
    for key in data:
        if key not in TOP_LEVEL_KEYS:
            close = difflib.get_close_matches(str(key), TOP_LEVEL_KEYS, n=1)
            decoder.issue("unknown_field", None, None, str(key), suggestion=close[0] if close else "")
    version = data.get("kut_script")
    if isinstance(version, bool) or not isinstance(version, int):
        decoder.issue("missing_version" if "kut_script" not in data else "wrong_type", None, None, "kut_script", expected="integer")
    elif version > SCRIPT_VERSION:
        decoder.issue("newer_version", None, None, "kut_script", found=version, supported=SCRIPT_VERSION)
    elif version < 1:
        decoder.issue("out_of_range", None, None, "kut_script", minimum=1, maximum=SCRIPT_VERSION)
    description = data.get("description", "")
    if not isinstance(description, str) or len(description) > 4 * MAX_TEXT:
        decoder.issue("wrong_type", None, None, "description", expected="string")
        description = ""
    raw_ops = data.get("ops")
    ops: list[ScriptOp] = []
    if not isinstance(raw_ops, list):
        decoder.issue("missing_ops" if "ops" not in data else "wrong_type", None, None, "ops", expected="array")
    elif len(raw_ops) > MAX_OPS:
        decoder.issue("too_many_ops", None, None, "ops", limit=MAX_OPS)
    else:
        for index, raw in enumerate(raw_ops):
            op = decoder.operation(index, raw)
            if op is not None:
                ops.append(op)
    if decoder.issues:
        raise ScriptInvalid(decoder.issues)
    return Script(version if isinstance(version, int) else SCRIPT_VERSION, tuple(ops), description, {alias: ident for alias, (_kind, ident) in decoder.aliases.items()})


# --- Application ------------------------------------------------------------------------------------------------------


def apply_script(
    session: Session, script: Script, *, label: str = "Script", context: ScriptContext | None = None,
) -> ApplyReport:
    """Applique le script à la session **en tout ou rien** (une étape d'historique) et rend son bilan.

    ``context`` : ce que le script peut lire hors du projet (voir :class:`ScriptContext`) ; sans lui, ``import_media`` est refusé."""
    return session.apply([(op.name, op.operation(context)) for op in script.ops], label=label)


def created_ids(script: Script, report: ApplyReport) -> list[dict[str, str]]:
    """Ce que chaque opération réussie a créé : ``[{"op": "3:add_clip", "id": "intro"}, ...]`` (les verbes qui ne créent rien sont omis)."""
    created = []
    for result in report.results:
        identifier = getattr(result.value, "id", None)
        if script.ops[result.index].verb.creates and isinstance(identifier, str):
            created.append({"op": result.name, "id": identifier})
    return created


__all__ = [
    "MAX_ISSUES", "MAX_OPS", "MAX_SCRIPT_BYTES", "SCRIPT_VERSION", "Script", "ScriptInvalid", "ScriptIssue", "ScriptOp",
    "ScriptContext", "apply_script", "created_ids", "loads_script", "parse_script", "read_script",
]
