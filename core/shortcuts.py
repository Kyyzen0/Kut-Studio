"""Raccourcis clavier de Kut-Studio : source de vérité unique.

Ce module est **pur** (aucune dépendance Qt) : il décrit les commandes,
leurs raccourcis par défaut, la validation, la détection de conflits et
la sérialisation. Le branchement sur Qt (``QAction``, événements
clavier, focus) vit dans :mod:`ui.shortcut_manager`.

Format des raccourcis
---------------------

Un raccourci est une chaîne « portable » au sens de Qt
(``QKeySequence.PortableText``) : ``"Ctrl+Shift+Z"``, ``"Space"``,
``"["``. ``Ctrl`` désigne la touche de commande native : Qt l'affiche
et la capture comme ``⌘`` sur macOS et ``Ctrl`` ailleurs, sans que le
modèle ait à connaître la plateforme. Les formes équivalentes (casse,
ordre des modificateurs, ``Del``/``Delete``) sont ramenées à une forme
canonique par :func:`normalize_sequence`. Les accords à plusieurs
étapes (``Ctrl+K, Ctrl+C``, jusqu'à quatre) sont pris en charge : un
raccourci simple ne peut pas être le début d'un accord existant, et
inversement (:func:`sequences_overlap`).

Ajouter une commande
--------------------

1. Ajouter une entrée à :data:`COMMANDS` (identifiant stable, catégorie,
   raccourcis par défaut, portée) ;
2. ajouter ses libellés ``shortcuts.command.<id>`` dans
   :mod:`ui.i18n` ;
3. associer sa fonction dans ``MainWindow._shortcut_handlers``.

Les tests refusent une commande sans libellé, sans fonction ou dont le
raccourci par défaut entre en conflit avec un autre. Une commande dotée
d'un raccourci *par défaut* doit aussi être ajoutée à l'ensemble attendu
de ``test_defaults_have_no_extra_shortcut_beyond_legacy_ones`` : ce test
fige volontairement les défauts pour qu'ils ne changent jamais par
inadvertance.

Persistance
-----------

Seuls les écarts par rapport aux valeurs par défaut sont stockés
(:meth:`ShortcutMap.overrides`). Ajouter plus tard une commande ou
changer une valeur par défaut n'écrase donc aucun choix de
l'utilisateur, et un ancien fichier sans clé ``shortcuts`` redonne
exactement les raccourcis par défaut.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Catégories et commandes
# ---------------------------------------------------------------------------


class Category(str, Enum):
    """Catégories de commandes, dans l'ordre d'affichage."""

    PLAYBACK = "playback"
    TIMELINE = "timeline"
    TOOLS = "tools"
    EDIT = "edit"
    PROJECT = "project"
    NAVIGATION = "navigation"
    VIEW = "view"
    AUDIO = "audio"
    MARKERS = "markers"
    ANIMATION = "animation"
    MOTION = "motion"
    SEQUENCES = "sequences"
    MULTICAM = "multicam"
    TIME = "time"
    SOCIAL = "social"


class Scope(str, Enum):
    """Comment une commande reçoit sa touche.

    ``ACTION`` : portée par une ``QAction`` (menu) ; Qt gère l'ambiguïté
    et laisse les champs texte consommer leurs touches.
    ``KEY`` : résolue par le gestionnaire quand la touche n'a été
    consommée par aucun widget (un bouton garde son Espace) et jamais
    quand un champ texte a le focus.
    """

    ACTION = "action"
    KEY = "key"


MAX_SEQUENCES_PER_COMMAND = 2
"""Un raccourci principal et, au plus, un raccourci secondaire."""


@dataclass(frozen=True)
class Command:
    """Une commande clavier configurable.

    Attributes:
        id: identifiant stable, utilisé dans les préférences. Ne jamais
            le renommer : il serait perdu dans les fichiers existants.
        category: catégorie d'affichage.
        default: raccourcis par défaut (principal puis secondaire).
        scope: mécanisme d'activation, voir :class:`Scope`.
        application_wide: pour une commande ``ACTION``, ``True`` si le
            raccourci reste actif quand un autre dialogue a le focus.
        lenient_modifiers: pour une commande ``KEY`` liée à un symbole,
            ``True`` si la frappe fonctionne aussi avec ``Ctrl``/``Alt``
            en plus (voir :func:`loose_candidates`).
    """

    id: str
    category: Category
    default: tuple[str, ...] = ()
    scope: Scope = Scope.KEY
    application_wide: bool = False
    lenient_modifiers: bool = False

    @property
    def name_key(self) -> str:
        """Clé i18n du nom affichable."""
        return f"shortcuts.command.{self.id}"


def _cmd(
    command_id: str,
    category: Category,
    *default: str,
    scope: Scope = Scope.KEY,
    application_wide: bool = False,
    lenient_modifiers: bool = False,
) -> Command:
    return Command(
        id=command_id,
        category=category,
        default=tuple(normalize_sequence(item) or item for item in default),
        scope=scope,
        application_wide=application_wide,
        lenient_modifiers=lenient_modifiers,
    )


# ---------------------------------------------------------------------------
# Normalisation des raccourcis
# ---------------------------------------------------------------------------

_MODIFIER_ORDER: tuple[str, ...] = ("Ctrl", "Alt", "Shift", "Meta")

_MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "cmd": "Ctrl",
    "command": "Ctrl",
    "alt": "Alt",
    "option": "Alt",
    "opt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "win": "Meta",
    "super": "Meta",
}

_NAMED_KEYS: dict[str, str] = {
    "space": "Space",
    "tab": "Tab",
    "backtab": "Tab",
    "backspace": "Backspace",
    "return": "Return",
    "enter": "Enter",
    "esc": "Esc",
    "escape": "Esc",
    "del": "Del",
    "delete": "Del",
    "ins": "Ins",
    "insert": "Ins",
    "home": "Home",
    "end": "End",
    "pgup": "PgUp",
    "pageup": "PgUp",
    "pgdown": "PgDown",
    "pagedown": "PgDown",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
}

_PUNCTUATION_KEYS = frozenset("`-=[]\\;',./+")
_DIGIT_KEYS = frozenset("0123456789")
_FUNCTION_KEY = re.compile(r"^f([1-9]|1[0-9]|2[0-4])$", re.IGNORECASE)


def _split_sequence(text: str) -> tuple[list[str], str] | None:
    """Sépare modificateurs et touche, en gérant la touche ``+`` elle-même."""
    parts = text.split("+")
    if parts[-1] == "":
        # ``"+"`` ou ``"Ctrl++"`` : la touche est le signe plus.
        if len(parts) < 2 or parts[-2] != "":
            return None
        return parts[:-2], "+"
    return parts[:-1], parts[-1]


MAX_CHORD_STEPS = 4
"""Nombre maximal d'étapes d'un accord (limite de ``QKeySequence``)."""

_CHORD_SEPARATOR = re.compile(r",\s+")


def normalize_sequence(text: object) -> str | None:
    """Ramène un raccourci à sa forme canonique, ou ``None`` s'il est invalide.

    La forme canonique ordonne les modificateurs ``Ctrl+Alt+Shift+Meta``,
    met les lettres en majuscules et utilise les noms de touche de Qt
    (``Del``, ``Esc``, ``PgUp``…). Un accord se note ``"Ctrl+K, Ctrl+C"``
    (jusqu'à :data:`MAX_CHORD_STEPS` étapes). Un modificateur seul ou
    une touche inconnue sont refusés ; la virgule seule est une touche.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    parts = _CHORD_SEPARATOR.split(raw)
    if len(parts) > MAX_CHORD_STEPS:
        return None
    steps = [_normalize_step(part) for part in parts]
    if any(step is None for step in steps):
        return None
    return ", ".join(steps)  # type: ignore[arg-type]


def sequence_steps(sequence: str) -> list[str]:
    """Étapes d'un raccourci canonique (une seule hors accord)."""
    return sequence.split(", ")


def sequences_overlap(first: str, second: str) -> bool:
    """``True`` si deux raccourcis ne peuvent pas coexister.

    Ils se recouvrent quand ils sont identiques ou que l'un est le
    début de l'autre : avec ``Ctrl+K`` seul, l'accord ``Ctrl+K, Ctrl+C``
    ne pourrait jamais aboutir.
    """
    a, b = sequence_steps(first), sequence_steps(second)
    shortest = min(len(a), len(b))
    return a[:shortest] == b[:shortest]


def _normalize_step(raw: str) -> str | None:
    """Normalise une étape (une combinaison de touches)."""
    raw = raw.strip()
    if not raw:
        return None
    split = _split_sequence(raw)
    if split is None:
        return None
    modifier_tokens, key_token = split
    modifiers: set[str] = set()
    for token in modifier_tokens:
        canonical = _MODIFIER_ALIASES.get(token.strip().lower())
        if canonical is None or canonical in modifiers:
            return None
        modifiers.add(canonical)
    key = key_token.strip() if key_token != " " else "Space"
    lowered = key.lower()
    if lowered in _MODIFIER_ALIASES:
        return None  # modificateur seul
    if lowered in _NAMED_KEYS:
        canonical_key = _NAMED_KEYS[lowered]
    elif _FUNCTION_KEY.match(key) or len(key) == 1 and key.isalnum() and key.isascii():
        canonical_key = key.upper()
    elif len(key) == 1 and key in _PUNCTUATION_KEYS:
        canonical_key = key
    else:
        return None
    ordered = [name for name in _MODIFIER_ORDER if name in modifiers]
    return "+".join([*ordered, canonical_key])


def key_candidates(sequence: str) -> tuple[str, ...]:
    """Raccourcis à essayer pour une frappe, du plus précis au plus souple.

    Sur beaucoup de claviers, un symbole s'obtient avec Maj (``+`` est
    ``Maj+=`` sur un clavier US) et Qt rapporte alors ``Shift++``. Pour
    une touche de ponctuation, la frappe est donc aussi essayée sans
    ``Shift`` : ``+`` reste utilisable quelle que soit la disposition.
    Même règle pour un chiffre : sur un clavier AZERTY, ``1`` s'obtient avec
    Maj (Qt rapporte ``Shift+1``), donc les angles Multicam restent
    atteignables au clavier principal.
    """
    normalized = _normalize_step(sequence)
    if normalized is None:
        return ()
    modifiers, key = _split_sequence(normalized) or ([], "")
    if (key in _PUNCTUATION_KEYS or key in _DIGIT_KEYS) and "Shift" in modifiers:
        rest = [name for name in modifiers if name != "Shift"]
        return (normalized, "+".join([*rest, key]))
    return (normalized,)


def loose_candidates(step: str) -> tuple[str, ...]:
    """Étape sans ``Ctrl``/``Alt``, pour une touche de ponctuation.

    Réservé aux commandes ``lenient_modifiers`` (le zoom) : ``Ctrl+=``
    et ``Ctrl+-`` zooment comme ``=`` et ``-``, ce que font déjà la
    plupart des applications. Vide si la touche n'est pas un symbole.
    """
    normalized = _normalize_step(step)
    if normalized is None:
        return ()
    modifiers, key = _split_sequence(normalized) or ([], "")
    if key not in _PUNCTUATION_KEYS or not {"Ctrl", "Alt"} & set(modifiers):
        return ()
    rest = "+".join([name for name in modifiers if name not in ("Ctrl", "Alt")] + [key])
    return tuple(c for c in key_candidates(rest) if c != normalized)


def format_sequence(sequence: str, platform: str | None = None) -> str:
    """Texte d'affichage d'un raccourci selon les conventions de la plateforme.

    macOS : symboles ``⌃⌥⇧⌘`` accolés (``⌘⇧Z``) ; ailleurs ``Ctrl+Shift+Z``.
    ``Ctrl`` du modèle est la touche de commande native (``⌘`` sur
    macOS) ; ``Meta`` est la touche physique Contrôle sur macOS et la
    touche Windows ailleurs.
    """
    normalized = normalize_sequence(sequence)
    if normalized is None:
        return str(sequence)
    steps = sequence_steps(normalized)
    if len(steps) > 1:
        return ", ".join(format_sequence(step, platform) for step in steps)
    split = _split_sequence(normalized)
    assert split is not None
    modifiers, key = split
    is_mac = (platform or sys.platform) == "darwin"
    if is_mac:
        symbols = {"Ctrl": "⌘", "Alt": "⌥", "Shift": "⇧", "Meta": "⌃"}
        arrows = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "Backspace": "⌫", "Del": "⌦", "Return": "↩", "Esc": "⎋"}
        ordered = [name for name in ("Meta", "Alt", "Shift", "Ctrl") if name in modifiers]
        return "".join(symbols[name] for name in ordered) + arrows.get(key, key)
    labels = {"Meta": "Win" if (platform or sys.platform).startswith("win") else "Meta"}
    return "+".join([*(labels.get(name, name) for name in modifiers), key])


# ---------------------------------------------------------------------------
# Séquences réservées
# ---------------------------------------------------------------------------

_RESERVED_EVERYWHERE: dict[str, str] = {
    "Ctrl+C": "clipboard",
    "Ctrl+V": "clipboard",
    "Ctrl+X": "clipboard",
    "Esc": "escape",
    "Tab": "focus",
    "Shift+Tab": "focus",
}

_RESERVED_BY_PLATFORM: dict[str, dict[str, str]] = {
    "darwin": {
        "Ctrl+H": "system",
        "Ctrl+M": "system",
        "Ctrl+Space": "system",
        "Ctrl+Tab": "system",
        "Ctrl+Alt+Esc": "system",
    },
    "win32": {
        "Alt+F4": "system",
        "Ctrl+Alt+Del": "system",
        "Alt+Tab": "system",
    },
    "linux": {
        "Alt+F4": "system",
        "Ctrl+Alt+Del": "system",
        "Alt+Tab": "system",
    },
}


def reserved_reason(sequence: str, platform: str | None = None) -> str | None:
    """Pourquoi un raccourci ne peut pas être assigné, ou ``None``.

    Raisons : ``"clipboard"`` (copier/couper/coller des champs texte),
    ``"escape"`` et ``"focus"`` (fermeture de dialogue, navigation au
    clavier), ``"system"`` (capturé par le système d'exploitation), et
    ``"system"`` aussi pour toute combinaison avec la touche Windows.
    """
    normalized = normalize_sequence(sequence)
    if normalized is None:
        return None
    steps = sequence_steps(normalized)
    # Seule la première étape d'un accord est interceptée par les champs
    # texte ; la touche Windows l'est à toute étape.
    first = steps[0]
    if first in _RESERVED_EVERYWHERE:
        return _RESERVED_EVERYWHERE[first]
    name = (platform or sys.platform).lower()
    family = "win32" if name.startswith("win") else "darwin" if name == "darwin" else "linux"
    if family == "win32" and any(
        "Meta" in (_split_sequence(step) or ([], ""))[0] for step in steps
    ):
        return "system"
    if len(steps) == 1:
        return _RESERVED_BY_PLATFORM[family].get(first)
    return None


# ---------------------------------------------------------------------------
# Table des commandes
# ---------------------------------------------------------------------------

_A = Scope.ACTION

COMMANDS: tuple[Command, ...] = (
    # --- Lecture ---------------------------------------------------------
    _cmd("play_pause", Category.PLAYBACK, "Space", "K"),
    _cmd("shuttle_back", Category.PLAYBACK, "J"),
    _cmd("shuttle_forward", Category.PLAYBACK, "L"),
    # --- Timeline --------------------------------------------------------
    _cmd("toggle_snap", Category.TIMELINE, "S"),
    _cmd("toggle_ripple", Category.TIMELINE, "N"),
    # --- Outils ----------------------------------------------------------
    _cmd("tool_select", Category.TOOLS, "V"),
    _cmd("tool_blade", Category.TOOLS, "B"),
    _cmd("tool_roll", Category.TOOLS, "R"),
    _cmd("tool_slip", Category.TOOLS, "Y"),
    _cmd("tool_slide", Category.TOOLS, "U"),
    # --- Édition ---------------------------------------------------------
    _cmd("undo", Category.EDIT, "Ctrl+Z", scope=_A, application_wide=True),
    _cmd("redo", Category.EDIT, "Ctrl+Shift+Z", "Ctrl+Y", scope=_A, application_wide=True),
    _cmd("duplicate_clip", Category.EDIT, "Ctrl+D", scope=_A, application_wide=True),
    _cmd("delete_clip", Category.EDIT, "Delete", "Backspace", scope=_A, application_wide=True),
    _cmd("ripple_delete", Category.EDIT, "Ctrl+Backspace", scope=_A, application_wide=True),
    _cmd("toggle_clip_enabled", Category.EDIT, "Ctrl+E", scope=_A, application_wide=True),
    _cmd("cut_at_playhead", Category.EDIT, "Ctrl+K"),
    _cmd("select_all", Category.EDIT, "Ctrl+A"),
    # --- Projet ----------------------------------------------------------
    _cmd("project_new", Category.PROJECT, "Ctrl+N", scope=_A),
    _cmd("project_open", Category.PROJECT, "Ctrl+O", scope=_A),
    _cmd("project_save", Category.PROJECT, "Ctrl+S", scope=_A),
    _cmd("project_save_as", Category.PROJECT, "Ctrl+Shift+S", scope=_A),
    _cmd("quit", Category.PROJECT, "Ctrl+Q", scope=_A),
    # --- Navigation ------------------------------------------------------
    _cmd("frame_back", Category.NAVIGATION, "Left"),
    _cmd("frame_forward", Category.NAVIGATION, "Right"),
    _cmd("second_back", Category.NAVIGATION, "Shift+Left"),
    _cmd("second_forward", Category.NAVIGATION, "Shift+Right"),
    # --- Affichage -------------------------------------------------------
    _cmd("zoom_in", Category.VIEW, "=", "+", lenient_modifiers=True),
    _cmd("zoom_out", Category.VIEW, "-", lenient_modifiers=True),
    _cmd("zoom_fit", Category.VIEW, "Ctrl+0", "Shift+Z"),
    # « Afficher les scopes » partageait Ctrl+Shift+S avec « Enregistrer
    # sous » (ambigu pour Qt : aucun ne se déclenchait) ; il est passé à
    # Ctrl+Alt+S, « Enregistrer sous » garde Ctrl+Shift+S.
    _cmd("toggle_scopes", Category.VIEW, "Ctrl+Alt+S", scope=_A, application_wide=True),
    _cmd("preferences", Category.VIEW, "Ctrl+,", scope=_A, application_wide=True),
    # --- Audio (sans raccourci par défaut) ---------------------------------
    _cmd("audio_record_toggle", Category.AUDIO),
    _cmd("audio_master_mute", Category.AUDIO),
    _cmd("track_toggle_mute", Category.AUDIO),
    # --- Marqueurs -------------------------------------------------------
    _cmd("marker_add", Category.MARKERS, "M"),
    _cmd("marker_previous", Category.MARKERS, "["),
    _cmd("marker_next", Category.MARKERS, "]"),
    # --- Animation (images-clés) -------------------------------------------
    _cmd("keyframe_add", Category.ANIMATION, "Alt+K"),
    _cmd("keyframe_remove", Category.ANIMATION, "Alt+Shift+K"),
    _cmd("keyframe_previous", Category.ANIMATION, "Alt+J"),
    _cmd("keyframe_next", Category.ANIMATION, "Alt+L"),
    _cmd("keyframe_select_all", Category.ANIMATION, "Ctrl+Alt+A"),
    _cmd("keyframe_copy", Category.ANIMATION),
    _cmd("keyframe_paste", Category.ANIMATION),
    _cmd("keyframe_interpolation_hold", Category.ANIMATION),
    _cmd("keyframe_interpolation_linear", Category.ANIMATION),
    _cmd("keyframe_interpolation_ease_in", Category.ANIMATION),
    _cmd("keyframe_interpolation_ease_out", Category.ANIMATION),
    _cmd("keyframe_interpolation_ease_in_out", Category.ANIMATION),
    _cmd("keyframe_interpolation_bezier", Category.ANIMATION),
    _cmd("graph_editor", Category.ANIMATION, "Ctrl+Alt+G", scope=_A, application_wide=True),
    # --- Séquences (imbrication et navigation) ----------------------------
    _cmd("sequence_new", Category.SEQUENCES, scope=_A),
    _cmd("sequence_nest_selection", Category.SEQUENCES, "Ctrl+Shift+N", scope=_A),
    _cmd("sequence_open_nested", Category.SEQUENCES, "Ctrl+Alt+Down", scope=_A),
    _cmd("sequence_parent", Category.SEQUENCES, "Ctrl+Alt+Up", scope=_A),
    _cmd("sequence_back", Category.SEQUENCES, "Alt+Left", scope=_A),
    _cmd("sequence_forward", Category.SEQUENCES, "Alt+Right", scope=_A),
    # --- Multicam : un angle par chiffre (1 à 9), pavé numérique compris ------------------------
    # Touches seules (portée KEY) : elles ne servent que quand aucun champ de saisie n'a le focus. Sur un clavier
    # AZERTY les chiffres s'obtiennent avec Maj : voir ``key_candidates``.
    *(_cmd(f"multicam_angle_{number}", Category.MULTICAM, str(number)) for number in range(1, 10)),
    _cmd("multicam_viewer", Category.MULTICAM, "Ctrl+Shift+M", scope=_A),
    _cmd("multicam_create", Category.MULTICAM, scope=_A),
    _cmd("multicam_open_source", Category.MULTICAM, scope=_A),
    _cmd("multicam_flatten", Category.MULTICAM, scope=_A),
    _cmd("multicam_settings", Category.MULTICAM, scope=_A),
    # --- Temps du clip : points de vitesse et arrêt sur image à la tête de lecture (sans touche par défaut : à configurer) ---
    _cmd("time_add_speed_point", Category.TIME),
    _cmd("time_freeze_frame", Category.TIME),
    # --- Motion graphics (calques, viewer) -----------------------------------
    _cmd("layer_add_text", Category.MOTION, scope=_A),
    _cmd("layer_add_shape", Category.MOTION, scope=_A),
    _cmd("layer_add_null", Category.MOTION, scope=_A),
    _cmd("layer_add_adjustment", Category.MOTION, scope=_A),
    _cmd("layer_group", Category.MOTION, "Ctrl+G", scope=_A),
    _cmd("layer_ungroup", Category.MOTION, "Ctrl+Shift+G", scope=_A),
    _cmd("layer_copy_attributes", Category.MOTION, scope=_A),
    _cmd("layer_paste_attributes", Category.MOTION, scope=_A),
    _cmd("view_safe_areas", Category.MOTION, "Ctrl+'", scope=_A),
    _cmd("view_guides", Category.MOTION, "Ctrl+;", scope=_A),
    _cmd("view_grid", Category.MOTION, scope=_A),
    _cmd("mograph_snapping", Category.MOTION, scope=_A),
    # --- Vidéo sociale (format vertical, cadrage, Ken Burns) : sans touche par défaut -----------------
    _cmd("social_new_project", Category.SOCIAL, scope=_A),
    _cmd("sequence_settings", Category.SOCIAL, scope=_A),
    _cmd("social_fill_frame", Category.SOCIAL, scope=_A),
    _cmd("social_ken_burns", Category.SOCIAL, scope=_A),
    _cmd("beat_grid", Category.SOCIAL, scope=_A),
    _cmd("beat_snap", Category.SOCIAL, scope=_A),
    _cmd("beat_cut", Category.SOCIAL, scope=_A),
    _cmd("beat_distribute", Category.SOCIAL, scope=_A),
)

COMMANDS_BY_ID: dict[str, Command] = {command.id: command for command in COMMANDS}


# ---------------------------------------------------------------------------
# Carte des raccourcis
# ---------------------------------------------------------------------------


class AssignStatus(str, Enum):
    """Issue d'une tentative d'assignation."""

    OK = "ok"
    INVALID = "invalid"
    RESERVED = "reserved"
    CONFLICT = "conflict"
    DUPLICATE = "duplicate"
    UNKNOWN_COMMAND = "unknown_command"


@dataclass(frozen=True)
class AssignResult:
    """Résultat de :meth:`ShortcutMap.assign`.

    Attributes:
        status: issue de l'opération.
        sequence: raccourci canonique concerné (``None`` si invalide).
        conflicts: identifiants des commandes qui utilisent déjà le
            raccourci (statut ``CONFLICT``).
        reason: raison de la réservation (statut ``RESERVED``).
        displaced: commandes dont le raccourci a été retiré parce que
            ``replace=True`` a été demandé.
    """

    status: AssignStatus
    sequence: str | None = None
    conflicts: tuple[str, ...] = ()
    reason: str | None = None
    displaced: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is AssignStatus.OK


class ShortcutMap:
    """Raccourcis effectifs de toutes les commandes.

    Invariants, maintenus par toutes les méthodes publiques :

    - chaque séquence est canonique et valide ;
    - une séquence n'appartient qu'à **une** commande (aucun conflit) ;
    - une commande a au plus :data:`MAX_SEQUENCES_PER_COMMAND` séquences,
      sans trou : retirer la principale promeut la secondaire.
    """

    def __init__(
        self,
        overrides: Mapping[str, object] | None = None,
        *,
        commands: Iterable[Command] = COMMANDS,
        platform: str | None = None,
    ) -> None:
        self._commands: dict[str, Command] = {c.id: c for c in commands}
        self._platform = platform
        self._effective: dict[str, tuple[str, ...]] = {}
        self._load(overrides or {})

    # -- Lecture ------------------------------------------------------------

    @property
    def commands(self) -> tuple[Command, ...]:
        return tuple(self._commands.values())

    def command(self, command_id: str) -> Command:
        return self._commands[command_id]

    def sequences(self, command_id: str) -> tuple[str, ...]:
        """Raccourcis effectifs (principal, secondaire)."""
        return self._effective.get(command_id, ())

    def command_for(self, sequence: str) -> str | None:
        """Identifiant de la commande qui possède ce raccourci."""
        normalized = normalize_sequence(sequence)
        if normalized is None:
            return None
        for command_id, sequences in self._effective.items():
            if normalized in sequences:
                return command_id
        return None

    def conflicts(self, sequence: str, *, exclude: str | None = None) -> tuple[str, ...]:
        """Commandes (hors ``exclude``) dont un raccourci recouvre celui-ci.

        Le recouvrement inclut l'égalité et le préfixe d'accord (voir
        :func:`sequences_overlap`).
        """
        normalized = normalize_sequence(sequence)
        if normalized is None:
            return ()
        return tuple(
            command_id
            for command_id, sequences in self._effective.items()
            if command_id != exclude
            and any(sequences_overlap(normalized, other) for other in sequences)
        )

    def continuations(self, prefix: str) -> tuple[str, ...]:
        """Commandes dont un accord commence par ``prefix`` sans lui être égal."""
        steps = sequence_steps(prefix)
        return tuple(
            command_id
            for command_id, sequences in self._effective.items()
            if any(
                len(other := sequence_steps(s)) > len(steps) and other[: len(steps)] == steps
                for s in sequences
            )
        )

    def _without_overlap(self, command_id: str, sequence: str) -> None:
        self._effective[command_id] = tuple(
            s for s in self._effective[command_id] if not sequences_overlap(s, sequence)
        )

    def is_default(self, command_id: str) -> bool:
        return self.sequences(command_id) == self._commands[command_id].default

    # -- Modification -------------------------------------------------------

    def assign(
        self,
        command_id: str,
        slot: int,
        sequence: str | None,
        *,
        replace: bool = False,
    ) -> AssignResult:
        """Assigne (ou retire, si ``sequence`` est ``None``) un raccourci.

        Args:
            command_id: commande ciblée.
            slot: ``0`` pour le principal, ``1`` pour le secondaire.
            sequence: nouveau raccourci, ou ``None`` pour vider l'emplacement.
            replace: si ``True``, retire le raccourci aux commandes qui
                l'utilisent déjà au lieu de refuser. Les séquences
                réservées et invalides restent refusées.

        Rien n'est modifié quand le résultat n'est pas ``OK``.
        """
        if command_id not in self._commands or slot not in range(MAX_SEQUENCES_PER_COMMAND):
            return AssignResult(AssignStatus.UNKNOWN_COMMAND)
        current = list(self.sequences(command_id))
        if sequence is None:
            if slot < len(current):
                del current[slot]
            self._effective[command_id] = tuple(current)
            return AssignResult(AssignStatus.OK)
        normalized = normalize_sequence(sequence)
        if normalized is None:
            return AssignResult(AssignStatus.INVALID)
        reason = reserved_reason(normalized, self._platform)
        if reason is not None:
            return AssignResult(AssignStatus.RESERVED, normalized, reason=reason)
        others = [s for index, s in enumerate(current) if index != slot]
        if any(sequences_overlap(normalized, other) for other in others):
            return AssignResult(AssignStatus.DUPLICATE, normalized)
        conflicts = self.conflicts(normalized, exclude=command_id)
        if conflicts and not replace:
            return AssignResult(AssignStatus.CONFLICT, normalized, conflicts=conflicts)
        for other in conflicts:
            self._without_overlap(other, normalized)
        if slot < len(current):
            current[slot] = normalized
        else:
            current.append(normalized)
        self._effective[command_id] = tuple(current)
        return AssignResult(AssignStatus.OK, normalized, displaced=conflicts)

    def reset(self, command_id: str, *, replace: bool = True) -> tuple[str, ...]:
        """Rétablit les raccourcis par défaut d'une commande.

        Si un autre commande occupe aujourd'hui un de ces raccourcis, il
        lui est retiré (``replace=True``, par défaut) : « réinitialiser »
        doit toujours aboutir. Avec ``replace=False``, les raccourcis
        pris sont simplement ignorés. Retourne les commandes affectées.
        """
        default = self._commands[command_id].default
        affected: list[str] = []
        kept: list[str] = []
        for sequence in default:
            owners = self.conflicts(sequence, exclude=command_id)
            if owners and not replace:
                continue
            for owner in owners:
                self._without_overlap(owner, sequence)
                affected.append(owner)
            kept.append(sequence)
        self._effective[command_id] = tuple(kept)
        return tuple(dict.fromkeys(affected))

    def reset_all(self) -> None:
        """Rétablit toutes les valeurs par défaut."""
        self._effective = {c.id: c.default for c in self._commands.values()}

    # -- Sérialisation ------------------------------------------------------

    def overrides(self) -> dict[str, list[str]]:
        """Écarts par rapport aux valeurs par défaut, prêts pour le JSON.

        Une liste vide signifie « volontairement sans raccourci ».
        """
        return {
            command_id: list(self.sequences(command_id))
            for command_id, command in self._commands.items()
            if self.sequences(command_id) != command.default
        }

    @classmethod
    def from_overrides(
        cls,
        data: object,
        *,
        platform: str | None = None,
    ) -> ShortcutMap:
        """Reconstruit une carte depuis des données stockées, sans jamais lever."""
        return cls(data if isinstance(data, Mapping) else {}, platform=platform)

    def _load(self, overrides: Mapping[str, object]) -> None:
        """Construit la carte effective et garantit l'absence de conflit.

        Les choix explicites de l'utilisateur l'emportent sur les valeurs
        par défaut : un défaut déjà pris est retiré, et ce retrait
        apparaît alors dans :meth:`overrides`. Entre deux choix
        explicites en conflit (fichier édité à la main), le premier dans
        l'ordre des commandes gagne.
        """
        claimed: list[str] = []
        explicit: dict[str, tuple[str, ...]] = {}
        for command_id in self._commands:
            if command_id not in overrides:
                continue
            raw = overrides[command_id]
            if not isinstance(raw, (list, tuple)):
                continue  # valeur corrompue : on garde le défaut
            items = raw
            cleaned: list[str] = []
            for item in items:
                normalized = normalize_sequence(item)
                if (
                    normalized is None
                    or any(sequences_overlap(normalized, other) for other in (*cleaned, *claimed))
                    or reserved_reason(normalized, self._platform) is not None
                ):
                    continue
                cleaned.append(normalized)
                claimed.append(normalized)
                if len(cleaned) == MAX_SEQUENCES_PER_COMMAND:
                    break
            if items and not cleaned:
                continue  # rien d'exploitable : le défaut vaut mieux que « aucun »
            explicit[command_id] = tuple(cleaned)
        for command_id, command in self._commands.items():
            if command_id in explicit:
                self._effective[command_id] = explicit[command_id]
                continue
            kept = tuple(
                s
                for s in command.default
                if not any(sequences_overlap(s, other) for other in claimed)
            )
            self._effective[command_id] = kept
            claimed.extend(kept)


def validate_defaults(commands: Iterable[Command] = COMMANDS) -> list[str]:
    """Liste les défauts invalides ou en conflit (vide si tout va bien)."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for command in commands:
        if len(command.default) > MAX_SEQUENCES_PER_COMMAND:
            problems.append(f"{command.id}: trop de raccourcis")
        for sequence in command.default:
            if normalize_sequence(sequence) != sequence:
                problems.append(f"{command.id}: {sequence!r} n'est pas canonique")
            for other, owner in seen.items():
                if sequences_overlap(sequence, other):
                    problems.append(f"{command.id}: {sequence} recouvre {other} ({owner})")
            seen[sequence] = command.id
    return problems


__all__ = [
    "COMMANDS",
    "COMMANDS_BY_ID",
    "MAX_CHORD_STEPS",
    "MAX_SEQUENCES_PER_COMMAND",
    "AssignResult",
    "AssignStatus",
    "Category",
    "Command",
    "Scope",
    "ShortcutMap",
    "format_sequence",
    "key_candidates",
    "loose_candidates",
    "normalize_sequence",
    "reserved_reason",
    "sequence_steps",
    "sequences_overlap",
    "validate_defaults",
]
