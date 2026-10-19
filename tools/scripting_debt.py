"""Deux « listes de dette qui ne peuvent que diminuer » du mode script (ADR-0005, étape 5) : ``python -m tools.scripting_debt``.

* ``fields`` — la complétude de ``describe`` : chaque champ des dataclasses du modèle persisté est **décrit**, **exclu** (avec une
  raison) ou **en dette** (``tests/data/describe_field_debt.json``, avec sa section : la « partie B »). Les tables décrit / exclu
  sont dans :mod:`core.scripting.describe_coverage`.
* ``verbs`` — la complétude des verbes : chaque fonction publique d'**édition** du modèle (modules de :data:`EDIT_MODULES`) est
  **référencée** par le code de ``core/scripting/`` (analyse AST) ou **en dette** (``tests/data/script_verb_debt.json``, avec une
  raison de :data:`REASONS`).

Usage ::

    python -m tools.scripting_debt fields --check               # = le test : échoue s'il y a du neuf ou du périmé
    python -m tools.scripting_debt verbs --list                 # la dette, par raison
    python -m tools.scripting_debt verbs --list --reason "lecture seule"
    python -m tools.scripting_debt verbs --summary              # chiffres par raison
    python -m tools.scripting_debt verbs --update-baseline      # retire de la dette ce qui a été résolu (jamais d'ajout)
    python -m tools.scripting_debt verbs --update-baseline --accept-new --label "lecture seule"   # (initialisation) ajoute le neuf

Une dette **ne grossit pas** : une fonction ou un champ nouveau force une décision (un verbe, une exclusion motivée, ou une entrée
de dette écrite à la main, visible en revue) ; une entrée résolue doit sortir du fichier (``--update-baseline``).

Limites assumées de l'analyse des verbes : une fonction est « référencée » quand un module de ``core/scripting/`` l'importe
depuis son module et l'emploie (ou l'appelle par ``module.fonction``) ; une méthode d'une classe de service
(:data:`SERVICE_CLASSES`) l'est quand la classe est employée et que le nom de la méthode apparaît comme attribut. Un appel
indirect (une fonction du modèle qui en appelle une autre) ne compte pas : seul le code des verbes est lu.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIELD_DEBT_PATH = ROOT / "tests" / "data" / "describe_field_debt.json"
VERB_DEBT_PATH = ROOT / "tests" / "data" / "script_verb_debt.json"
SCRIPTING_DIR = ROOT / "core" / "scripting"
_NOT_VERB_MODULES = frozenset({
    "api", "describe", "describe_coverage", "describe_values", "describe_shapes", "describe_color", "describe_audio", "describe_composition",
})
"""Les modules de ``core/scripting/`` qui ne portent pas de verbe (la lecture) : ils ne comptent pas comme référence."""

# --- Verbes ----------------------------------------------------------------------------------------------------------------

EDIT_MODULES: dict[str, str] = {
    "timeline_operations": "montage de base : ajout, déplacement, rognage, coupe, copie, suppression, réglages de clip",
    "timeline_editing": "montage avancé : ripple, glisser, rouler, déplacements groupés, repères",
    "track_operations": "pistes : création, ordre, nom, verrou, visibilité, muet",
    "time_ops": "vitesse et remappage : points de courbe, interpolation, hauteur de l'audio",
    "time_commands": "commandes de vitesse de l'interface (arrêt sur image, inversion, courbes types)",
    "time_presets": "préréglages de vitesse",
    "transitions": "transitions entre deux clips",
    "transition_presets": "préréglages de transitions",
    "audio_effects_model": "effets audio de clip et inserts de piste",
    "audio_automation": "ducking (et son service d'automation de piste)",
    "audio_buses": "bus audio de la séquence, routage, inserts de bus",
    "mograph_layers": "calques graphiques : hiérarchie, ordre, groupes, visibilité, verrou, presse-papiers",
    "mograph_presets": "préréglages de calques graphiques",
    "graphics": "création et mise à jour des calques graphiques",
    "multicam_ops": "sources et clips Multicam",
    "tracking_ops": "suivi (tracking), liaisons et stabilisation",
    "composition_ops": "compositions nodales",
    "keyframe_editing": "images clés des propriétés animables",
    "effects_model": "effets visuels de clip",
    "effects_library": "préréglages d'effets",
    "clip_crop": "recadrage d'un clip (ADR-0006)",
    "media_color_ops": "espace colorimétrique d'un média",
    "sequences": "séquences et séquences imbriquées",
    "format_versions": "versions de format (9:16, 1:1…) d'une séquence",
    "beat_edit": "montage sur la grille rythmique",
    "follow_reframe": "recadrage qui suit un sujet suivi",
    "template_slots": "emplacements de modèle et diaporama de photos",
    "sfx_placement": "effets sonores posés sur les coupes",
    "impact_fx": "effets d'impact (flash) sur les coupes",
    "auto_captions": "sous-titres générés depuis une transcription",
    "leaderboard": "calques générés depuis des données (classement)",
    "scene_detection": "coupe d'un clip aux changements de plan",
    "canvas_guides": "guides du moniteur (séquence)",
    "text_animations": "animations de texte : pose des images clés sur un calque texte",
    "color_grading": "étalonnage (ColorGradingService : un service, pas des fonctions de module)",
    "library_organization": "organisation de la bibliothèque (LibraryOrganization : un service)",
}
"""Les modules de ``core/`` dont les fonctions publiques (premier paramètre ``project``, ``sequence``, ``track`` ou ``clip``) **modifient
le modèle** : chacune est un verbe, ou une dette. La valeur dit pourquoi le module est là."""

READ_MODULES: dict[str, str] = {
    "animation_targets": "registre des propriétés animables (lecture)",
    "beat_detection": "détection de rythme (analyse)",
    "effect_targets": "paramètres d'effets évalués (lecture)",
    "shortcuts": "raccourcis clavier : « sequence » y est une suite de touches, pas une séquence du modèle",
    "time_editing": "restrictions de la courbe de vitesse (lecture)",
    "time_map": "mapping temporel (lecture)",
    "tracking_motion": "conversions de temps du suivi (lecture)",
    "color_windows": "scènes et mattes des fenêtres de couleur : calculs de rendu, pas d'édition (l'édition est dans color_nodes)",
    "composition_cache": "cache de rendu des compositions",
    "compositing_guard": "garde-fous de verrou (refus), appelés par les fonctions d'édition",
    "live_mix": "plan et commande du mixage en direct",
    "mask_handles": "refus d'édition de masque (garde-fou)",
    "multicam": "lecture des sources Multicam",
    "object_ids": "génération et réservation d'identifiants",
    "playhead_snapshot": "projet tel qu'à la tête de lecture (copie, lecture)",
    "preview_segments": "plans de segments de l'aperçu",
    "project_io": "lecture et écriture du fichier .kut",
    "render_plan": "plan de rendu (lecture)",
    "timeline_evaluator": "évaluation de la timeline (lecture)",
    "timeline_index": "index de la timeline (lecture)",
    "timeline_view_model": "modèle de vue de la timeline (lecture)",
    "tracking_bindings": "images clés dérivées d'un suivi (lecture)",
}
"""Les modules de ``core/`` qui ont des fonctions publiques à premier paramètre ``project`` mais qui **ne sont pas** une surface d'édition
du modèle (rendu, cache, persistance, gardes). Un module de ``core/`` absent des deux tables est refusé par le test."""

SERVICE_CLASSES: dict[str, tuple[str, ...]] = {
    "audio_automation": ("AudioAutomationService",),
    "color_grading": ("ColorGradingService",),
    "library_organization": ("LibraryOrganization",),
}
"""Les classes dont les méthodes publiques sont des éditions du modèle (leurs verbes ne sont pas des fonctions de module)."""

FIRST_PARAMS = frozenset({"project", "sequence", "track", "clip"})

REASONS: dict[str, str] = {
    "lecture seule": "Lit le modèle sans le modifier : pas un verbe (c'est le travail de describe).",
    "interne": "Brique d'une autre fonction (qui est un verbe, ou le sera) ou de la persistance : sans intérêt appelée seule.",
    "interface seulement": "Geste de l'interface (sélection, presse-papiers, aimantation, aperçu) sans équivalent utile dans un script.",
    "interface seulement : persistance": "Écrit hors du projet (bibliothèque de presets de l'utilisateur, dans sa configuration) : une frontière de "
                                         "persistance qu'un script ne franchit pas (ADR-0005, décision 5 : un script ne lit ni n'écrit que le projet qu'on lui donne).",
    "interface seulement : analyse": "Applique le résultat d'une analyse de média (corrélation audio, timecode) avec sa méthode, son statut et sa fiabilité : "
                                     "un script n'analyse rien (ADR-0005) et ne peut pas se faire passer pour une mesure ; il pose des décalages à la main.",
    "à faire : montage": "Édition de la timeline qui aura un verbe.",
    "à faire : vitesse": "Vitesse et remappage : verbes à venir.",
    "à faire : calques": "Calques graphiques : verbes à venir.",
    "à faire : audio": "Audio (effets, automation, ducking) : verbes à venir.",
    "à faire : couleur et effets": "Étalonnage, effets visuels, recadrage : verbes à venir.",
    "à faire : images clés": "Images clés : verbes à venir.",
    "à faire : tracking": "Suivi et stabilisation : verbes à venir.",
    "à faire : composition": "Compositions nodales : verbes à venir.",
    "à faire : multicam": "Multicam : verbes à venir.",
    "à faire : séquences": "Séquences et versions de format : verbes à venir.",
    "à faire : transitions": "Transitions : verbes à venir.",
    "à faire : modèles et social": "Emplacements de modèle, montage sur le rythme, légendes, classements : verbes à venir.",
    "à faire : bibliothèque": "Organisation de la bibliothèque : verbes à venir.",
}
"""Les raisons permises d'une entrée de la dette des verbes. Les quatre premières sont durables (pas un verbe) ; ``à faire : …`` est une
promesse : l'entrée sort de la dette quand le verbe existe."""


@dataclasses.dataclass(frozen=True)
class ModuleScan:
    """Les fonctions candidates d'un module : ``{nom qualifié: premier paramètre}``."""

    functions: dict[str, str]
    services: dict[str, str]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _first_param(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args.posonlyargs + node.args.args
    return args[0].arg if args else ""


def public_functions(module: str, root: Path = ROOT) -> dict[str, str]:
    """``{core.module.fonction: premier paramètre}`` des fonctions publiques de premier niveau (hors classes) d'un module."""
    tree = _parse(root / "core" / f"{module}.py")
    return {
        f"core.{module}.{node.name}": _first_param(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def service_methods(module: str, class_names: tuple[str, ...], root: Path = ROOT) -> dict[str, str]:
    """``{core.module.Classe.methode: ""}`` des méthodes publiques des classes de service."""
    tree = _parse(root / "core" / f"{module}.py")
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in class_names:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    found[f"core.{module}.{node.name}.{item.name}"] = ""
    return found


def candidate_verbs(root: Path = ROOT) -> dict[str, str]:
    """Toutes les éditions candidates : ``{nom qualifié: module}`` — les fonctions publiques de :data:`EDIT_MODULES` dont le premier
    paramètre est un conteneur du modèle (:data:`FIRST_PARAMS`), et les méthodes des classes de service."""
    found: dict[str, str] = {}
    for module in EDIT_MODULES:
        for name, first in public_functions(module, root).items():
            if first in FIRST_PARAMS:
                found[name] = module
        if module in SERVICE_CLASSES:
            for name in service_methods(module, SERVICE_CLASSES[module], root):
                found[name] = module
    return found


def unclassified_modules(root: Path = ROOT) -> list[str]:
    """Les modules de ``core/`` qui ont une fonction publique à premier paramètre de :data:`FIRST_PARAMS` (le même critère que :func:`candidate_verbs`) sans être dans :data:`EDIT_MODULES` ni
    :data:`READ_MODULES` : un module neuf force la décision « surface d'édition ou non »."""
    missing = []
    for path in sorted((root / "core").glob("*.py")):
        module = path.stem
        if module in EDIT_MODULES or module in READ_MODULES or module == "__init__":
            continue
        if any(first in FIRST_PARAMS for first in public_functions(module, root).values()):
            missing.append(module)
    return missing


def referenced_names(root: Path = ROOT) -> set[str]:
    """Les noms qualifiés (``core.module.fonction``, ``core.module.Classe.methode``) que le code de ``core/scripting/`` emploie."""
    used: set[str] = set()
    modules = {path.stem for path in (root / "core").glob("*.py")}
    for path in sorted((root / "core" / "scripting").glob("*.py")):
        if path.stem in _NOT_VERB_MODULES:
            continue
        tree = _parse(path)
        aliases: dict[str, str] = {}               # nom local -> nom qualifié (fonction, classe ou module de core)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                if node.module:
                    for item in node.names:
                        aliases[item.asname or item.name] = f"core.{node.module}.{item.name}"
                else:
                    for item in node.names:
                        if item.name in modules:
                            aliases[item.asname or item.name] = f"core.{item.name}"
        loaded = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for local, qualified in aliases.items():
            if local in loaded:
                used.add(qualified)
        for node in ast.walk(tree):                 # ``module.fonction``
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                used.add(f"{aliases[node.value.id]}.{node.attr}")
        for module, classes in SERVICE_CLASSES.items():     # ``service.methode`` : la classe est employée et le nom apparaît
            for class_name in classes:
                if f"core.{module}.{class_name}" in used:
                    used.update(f"core.{module}.{class_name}.{name}" for name in attributes)
    return used


def load_verb_debt(path: Path = VERB_DEBT_PATH) -> dict[str, str]:
    return dict(json.loads(path.read_text(encoding="utf-8"))["verbs"])


@dataclasses.dataclass
class VerbComparison:
    undecided: list[str]
    resolved: list[str]
    vanished: list[str]
    bad_reason: list[str]
    modules: list[str]

    @property
    def clean(self) -> bool:
        return not (self.undecided or self.resolved or self.vanished or self.bad_reason or self.modules)


def compare_verbs(debt: dict[str, str], root: Path = ROOT) -> VerbComparison:
    candidates = candidate_verbs(root)
    used = referenced_names(root)
    return VerbComparison(
        undecided=sorted(name for name in candidates if name not in used and name not in debt),
        resolved=sorted(name for name in debt if name in used),
        vanished=sorted(name for name in debt if name not in candidates and name not in used),
        bad_reason=sorted(name for name, reason in debt.items() if reason not in REASONS),
        modules=unclassified_modules(root),
    )


def write_verb_debt(debt: dict[str, str], path: Path = VERB_DEBT_PATH) -> None:
    body = {
        "_comment": "Dette des verbes de script : fonctions d'édition du modèle sans verbe. Ne peut que diminuer "
                    "(python -m tools.scripting_debt verbs --update-baseline). Raisons : tools/scripting_debt.py, REASONS.",
        "version": 1,
        "verbs": dict(sorted(debt.items())),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- Champs ----------------------------------------------------------------------------------------------------------------


def model_classes() -> dict[str, type]:
    """Les dataclasses dont chaque champ est décidé (noms de :data:`core.scripting.describe_coverage.COVERED_CLASSES`)."""
    from core.animation import Keyframe
    from core.audio_automation import AutomationPoint, DuckingConfig, DuckingSidechain, TrackAutomation
    from core.audio_buses import AudioBus
    from core.audio_effects_model import AudioEffect
    from core.color_grading import ColorCurve, ColorCurves, ColorGrade, ColorPreset, LUTResource, Wheel
    from core.color_nodes import ColorMixer, ColorNode, ColorNodeGraph
    from core.color_qualifier import Qualifier
    from core.composition import (
        Composition, CompositionGraph, EffectsNode, GradeNode, GraphicNode, KeyNode, MaskNode, MediaNode, MergeNode, OutputNode,
        SolidNode, TransformNode,
    )
    from core.compositing import ChromaKey, Compositing, Mask, MaskKeyframe
    from core.effects_model import ClipEffect
    from core.graphics import GraphicOverlay
    from core.node_graph import NodeLink
    from core.project_model import Clip, MediaAsset, Marker, Project, Sequence, Track
    from core.time_remapping import TimeRemapping
    from core.transitions import Transition
    from core.visual_effects import ClipTransform

    classes = (
        Project, MediaAsset, Sequence, Track, Clip, Marker, Transition, TimeRemapping, ClipTransform, GraphicOverlay,
        Keyframe, ClipEffect, AudioEffect, AudioBus, TrackAutomation, AutomationPoint, DuckingConfig, DuckingSidechain,
        ColorGrade, ColorCurves, ColorCurve, Wheel, LUTResource, ColorNodeGraph, ColorNode, ColorMixer, NodeLink, Qualifier,
        ColorPreset, Mask, MaskKeyframe, Compositing, ChromaKey, Composition, CompositionGraph, MediaNode, GraphicNode, SolidNode,
        TransformNode, MergeNode, MaskNode, KeyNode, EffectsNode, GradeNode, OutputNode,
    )
    return {cls.__name__: cls for cls in classes}


def model_fields() -> list[str]:
    """Tous les champs des classes couvertes, ``Classe.champ``."""
    return [f"{name}.{field.name}" for name, cls in model_classes().items() for field in dataclasses.fields(cls)]


def load_field_debt(path: Path = FIELD_DEBT_PATH) -> dict[str, str]:
    return dict(json.loads(path.read_text(encoding="utf-8"))["fields"])


@dataclasses.dataclass
class FieldComparison:
    undecided: list[str]
    resolved: list[str]
    vanished: list[str]
    bad_section: list[str]
    bad_tables: list[str]

    @property
    def clean(self) -> bool:
        return not (self.undecided or self.resolved or self.vanished or self.bad_section or self.bad_tables)


def compare_fields(debt: dict[str, str]) -> FieldComparison:
    from core.scripting.describe_coverage import COVERED_CLASSES, DESCRIBED, EXCLUDED, NOT_DESCRIBED_SECTIONS

    classes = model_classes()
    fields = set(model_fields())
    described = {f"{cls}.{field}" for cls, mapping in DESCRIBED.items() for field in mapping}
    excluded = {f"{cls}.{field}": reason for cls, mapping in EXCLUDED.items() for field, reason in mapping.items()}
    bad_tables = [f"classe non couverte : {cls}" for cls in sorted({*DESCRIBED, *EXCLUDED} - set(classes))]
    bad_tables += [f"classe absente de COVERED_CLASSES : {cls}" for cls in sorted(set(classes) - set(COVERED_CLASSES))]
    bad_tables += [f"champ décrit ou exclu qui n'existe pas : {name}" for name in sorted((described | set(excluded)) - fields)]
    bad_tables += [f"raison d'exclusion vide : {name}" for name, reason in sorted(excluded.items()) if not reason.strip()]
    bad_tables += [f"champ à la fois décrit et exclu : {name}" for name in sorted(described & set(excluded))]
    decided = described | set(excluded)
    return FieldComparison(
        undecided=sorted(fields - decided - set(debt)),
        resolved=sorted(name for name in debt if name in decided),
        vanished=sorted(name for name in debt if name not in fields),
        bad_section=sorted(name for name, section in debt.items() if section not in NOT_DESCRIBED_SECTIONS),
        bad_tables=bad_tables,
    )


def write_field_debt(debt: dict[str, str], path: Path = FIELD_DEBT_PATH) -> None:
    body = {
        "_comment": "Dette de describe (partie B) : champs du modèle que la sortie ne décrit pas encore, avec leur section. "
                    "Ne peut que diminuer (python -m tools.scripting_debt fields --update-baseline).",
        "version": 1,
        "fields": dict(sorted(debt.items())),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- Ligne de commande -----------------------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.scripting_debt", description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("fields", "verbs"))
    parser.add_argument("--check", action="store_true", help="échoue s'il y a du neuf ou du périmé (par défaut)")
    parser.add_argument("--list", action="store_true", help="la dette, groupée")
    parser.add_argument("--summary", action="store_true", help="chiffres par section / raison")
    parser.add_argument("--update-baseline", action="store_true", help="retire de la dette ce qui est résolu")
    parser.add_argument("--accept-new", action="store_true", help="avec --update-baseline : ajoute aussi le neuf (initialisation)")
    parser.add_argument("--label", default=None, help="avec --accept-new : la section / raison des entrées ajoutées")
    parser.add_argument("--reason", "--section", dest="only", default=None, help="avec --list : une seule section / raison")
    return parser


def _print_groups(debt: dict[str, str], only: str | None) -> None:
    groups: dict[str, list[str]] = {}
    for name, label in sorted(debt.items()):
        groups.setdefault(label, []).append(name)
    for label, names in sorted(groups.items()):
        if only is not None and label != only:
            continue
        print(f"[{label}] {len(names)}")
        for name in names:
            print(f"  {name}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    is_verbs = args.kind == "verbs"
    debt = load_verb_debt() if is_verbs else load_field_debt()
    if args.list:
        _print_groups(debt, args.only)
        return 0
    if args.summary:
        for label, count in sorted(Counter(debt.values()).items()):
            print(f"{count:4d}  {label}")
        print(f"{len(debt):4d}  total")
        return 0
    comparison: Any = compare_verbs(debt) if is_verbs else compare_fields(debt)
    if args.update_baseline:
        updated = {name: label for name, label in debt.items() if name not in {*comparison.resolved, *comparison.vanished}}
        if comparison.undecided:
            if not (args.accept_new and args.label):
                print("Neuf, sans décision :\n  " + "\n  ".join(comparison.undecided), file=sys.stderr)
                print("\nDette NON modifiée : elle ne peut que diminuer. Initialisation : --accept-new --label ÉTIQUETTE.",
                      file=sys.stderr)
                return 1
            updated.update({name: args.label for name in comparison.undecided})
        (write_verb_debt if is_verbs else write_field_debt)(updated)
        print(f"Dette mise à jour : {len(updated)} entrée(s).")
        return 0
    if comparison.clean:
        print(f"Aucune dette nouvelle ni périmée ({len(debt)} entrée(s) connue(s)).")
        return 0
    for title, names in vars(comparison).items():
        if names:
            print(f"{title} :\n  " + "\n  ".join(names), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
