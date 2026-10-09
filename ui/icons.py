"""Bibliothèque d'icônes SVG inline pour Kut-Studio.

Toutes les icônes sont dessinées en SVG à 24×24, avec une approche
``currentColor`` : la couleur d'application est définie par l'attribut
``color`` du widget Qt hôte. Cela permet aux icônes de s'adapter aux
thèmes sombre et clair sans multiplier les assets.

Trois classes sont exposées :

- :class:`IconName` : énumération des noms logiques d'icônes ;
- :func:`svg_for` : retourne la chaîne SVG correspondant à un nom ;
- :class:`IconLabel` : petit widget Qt qui peint l'icône en suivant la
  palette courante (couleur de premier plan dérivée de la couleur du
  texte du widget parent) ;
- :class:`IconButton` : ``QToolButton`` qui combine icône + libellé
  optionnel + tooltip, prêt à être utilisé comme contrôle d'interface.

Aucun emoji n'est utilisé dans le reste de l'application : tout passe
par ce module.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Iterable

from PySide6.QtCore import QByteArray, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton, QWidget


# ---------------------------------------------------------------------------
# Énumération
# ---------------------------------------------------------------------------


class IconName(str, Enum):
    """Noms logiques d'icônes disponibles dans l'application."""

    PLAY = "play"
    PAUSE = "pause"
    STOP = "stop"
    REWIND = "rewind"
    FORWARD = "forward"
    SKIP_TO_START = "skip_to_start"
    SKIP_TO_END = "skip_to_end"

    CUT = "cut"
    SCISSORS = "scissors"
    DUPLICATE = "duplicate"
    TRASH = "trash"
    RESET = "reset"
    SNAP = "snap"
    SOLO = "solo"
    MARKER = "marker"
    HEIGHT = "height"

    LOCK = "lock"
    UNLOCK = "unlock"
    EYE = "eye"
    EYE_OFF = "eye_off"
    SPEAKER = "speaker"
    MUTE = "mute"

    ARROW_UP = "arrow_up"
    ARROW_DOWN = "arrow_down"
    EDIT = "edit"
    REMOVE = "remove"
    CLOSE = "close"
    PLUS = "plus"
    MORE = "more"

    MEDIA = "media"
    FILM = "film"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    TEXT = "text"
    EFFECTS = "effects"
    COLOR = "color"
    TRANSITIONS = "transitions"
    LIBRARY = "library"

    IMPORT = "import"
    EXPORT = "export"
    SAVE = "save"
    SAVE_AS = "save_as"
    OPEN = "open"
    NEW = "new"
    REFRESH = "refresh"
    INFO = "info"

    DIAMOND = "diamond"
    KEY = "key"
    COMPARE = "compare"
    PROJECT = "project"
    TIMER = "timer"
    LIST = "list"
    MENU = "menu"
    PANEL_FLOAT = "panel_float"
    PANEL_DOCK = "panel_dock"
    PANEL_CLOSE = "panel_close"
    PANEL_MAXIMIZE = "panel_maximize"
    PANEL_RESTORE = "panel_restore"
    PANEL_RESET = "panel_reset"
    AUDIO_VOLUME = "audio_volume"
    AUDIO_MUTE = "audio_mute"
    AUDIO_SOLO = "audio_solo"
    AUDIO_ARM = "audio_arm"
    AUDIO_FADE = "audio_fade"
    AUDIO_PAN = "audio_pan"
    AUDIO_MIXER = "audio_mixer"
    MIC = "mic"

    CHEVRON_DOWN = "chevron_down"
    CHEVRON_RIGHT = "chevron_right"
    STAR = "star"
    STAR_FILLED = "star_filled"
    SEARCH = "search"
    CHECK = "check"
    WARNING = "warning"

    # Types de calque graphique (panneau des calques) : remplacent les glyphes texte « ◆ ▭ ■ ▤ ◐ ⊕ ».
    SHAPE = "shape"
    RECTANGLE = "rectangle"
    SOLID = "solid"
    GROUP = "group"
    ADJUSTMENT = "adjustment"
    NULL_OBJECT = "null_object"


# ---------------------------------------------------------------------------
# SVG sources (24×24, currentColor)
# ---------------------------------------------------------------------------


_SVG_TEMPLATES: dict[str, str] = {
    IconName.PLAY: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M7 5.5v13a.5.5 0 0 0 .76.43l10.5-6.5a.5.5 0 0 0 0-.86l-10.5-6.5A.5.5 0 0 0 7 5.5Z" '
        'fill="currentColor" stroke="none"/></svg>'
    ),
    IconName.PAUSE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="6" y="5" width="4" height="14" rx="1.2"/>'
        '<rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>'
    ),
    IconName.STOP: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>'
    ),
    IconName.REWIND: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="5" y="6" width="2.4" height="12" rx="1"/>'
        '<path d="M20 6.5v11a.5.5 0 0 1-.79.41l-6.5-5.5a.5.5 0 0 1 0-.82l6.5-5.5A.5.5 0 0 1 20 6.5Z"/>'
        '<path d="M12 6.5v11a.5.5 0 0 1-.79.41l-6.5-5.5a.5.5 0 0 1 0-.82l6.5-5.5A.5.5 0 0 1 12 6.5Z"/></svg>'
    ),
    IconName.FORWARD: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="16.6" y="6" width="2.4" height="12" rx="1"/>'
        '<path d="M4 6.5v11a.5.5 0 0 0 .79.41l6.5-5.5a.5.5 0 0 0 0-.82l-6.5-5.5A.5.5 0 0 0 4 6.5Z"/>'
        '<path d="M12 6.5v11a.5.5 0 0 0 .79.41l6.5-5.5a.5.5 0 0 0 0-.82l-6.5-5.5A.5.5 0 0 0 12 6.5Z"/></svg>'
    ),
    IconName.SKIP_TO_START: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="5" y="6" width="2.4" height="12" rx="1"/>'
        '<path d="M19 6v12l-9-6Z"/></svg>'
    ),
    IconName.SKIP_TO_END: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="16.6" y="6" width="2.4" height="12" rx="1"/>'
        '<path d="M5 6v12l9-6Z"/></svg>'
    ),
    IconName.CUT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="5" cy="7" r="2.2"/>'
        '<circle cx="5" cy="17" r="2.2"/>'
        '<path d="M7 9 20 17M7 15 20 7"/></svg>'
    ),
    IconName.SCISSORS: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="5" cy="7" r="2.2"/>'
        '<circle cx="5" cy="17" r="2.2"/>'
        '<path d="M7 9 19.5 17M7 15 19.5 7"/>'
        '<path d="M12 12h9"/></svg>'
    ),
    IconName.DUPLICATE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="8" y="3" width="13" height="13" rx="2"/>'
        '<path d="M16 16v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2"/></svg>'
    ),
    IconName.TRASH: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 7h16"/>'
        '<path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>'
        '<path d="M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7"/>'
        '<path d="M10 11v6M14 11v6"/></svg>'
    ),
    IconName.RESET: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 12a9 9 0 1 0 3-6.7"/>'
        '<path d="M3 4v5h5"/></svg>'
    ),
    IconName.SOLO: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 10v4"/><path d="M8 7v10"/><path d="M12 4v16"/>'
        '<path d="M16 7v10"/><path d="M20 10v4"/></svg>'
    ),
    IconName.MARKER: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<path d="M12 3 6 8.2V21h12V8.2L12 3Z"/></svg>'
    ),
    IconName.HEIGHT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round">'
        '<path d="M8 7h8"/><path d="M8 12h8"/><path d="M8 17h8"/></svg>'
    ),
    IconName.SNAP: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 12 12 5l7 7-7 7z"/>'
        '<path d="M5 12h7M12 12v0"/></svg>'
    ),
    IconName.LOCK: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="11" width="16" height="10" rx="2"/>'
        '<path d="M8 11V8a4 4 0 1 1 8 0v3"/></svg>'
    ),
    IconName.UNLOCK: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="11" width="16" height="10" rx="2"/>'
        '<path d="M8 11V8a4 4 0 0 1 7-2.7"/></svg>'
    ),
    IconName.EYE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/>'
        '<circle cx="12" cy="12" r="3"/></svg>'
    ),
    IconName.EYE_OFF: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 3l18 18"/>'
        '<path d="M10.6 5.6A10 10 0 0 1 22 12c-.6 1.4-1.5 2.8-2.7 4"/>'
        '<path d="M6 6c-2.5 1.6-4 4-4 6s3.5 7 10 7c1.5 0 2.9-.3 4.1-.8"/>'
        '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>'
    ),
    IconName.SPEAKER: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 9h3l5-4v14l-5-4H4z" fill="currentColor" fill-opacity=".15"/>'
        '<path d="M16 8a5 5 0 0 1 0 8"/>'
        '<path d="M19 5a9 9 0 0 1 0 14"/></svg>'
    ),
    IconName.MUTE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 9h3l5-4v14l-5-4H4z" fill="currentColor" fill-opacity=".15"/>'
        '<path d="M16 9l5 6M21 9l-5 6"/></svg>'
    ),
    IconName.ARROW_UP: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 19V5"/>'
        '<path d="m6 11 6-6 6 6"/></svg>'
    ),
    IconName.ARROW_DOWN: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 5v14"/>'
        '<path d="m6 13 6 6 6-6"/></svg>'
    ),
    IconName.EDIT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 20h4l11-11-4-4L4 16Z"/>'
        '<path d="m14 6 4 4"/></svg>'
    ),
    IconName.REMOVE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 6l12 12M18 6 6 18"/></svg>'
    ),
    IconName.CLOSE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 6l12 12M18 6 6 18"/></svg>'
    ),
    IconName.PLUS: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 5v14M5 12h14"/></svg>'
    ),
    IconName.MORE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<circle cx="6" cy="12" r="1.7"/>'
        '<circle cx="12" cy="12" r="1.7"/>'
        '<circle cx="18" cy="12" r="1.7"/></svg>'
    ),
    IconName.MEDIA: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="5" width="18" height="14" rx="2"/>'
        '<path d="m10 9 5 3-5 3z" fill="currentColor"/></svg>'
    ),
    IconName.FILM: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4M11 4v16"/></svg>'
    ),
    IconName.AUDIO: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 10v4h4l5 4V6L8 10z" fill="currentColor" fill-opacity=".15"/>'
        '<path d="M16 8a5 5 0 0 1 0 8"/>'
        '<path d="M19 5a9 9 0 0 1 0 14"/></svg>'
    ),
    IconName.SUBTITLE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="5" width="18" height="14" rx="2"/>'
        '<path d="M7 12h4M13 12h4M7 16h7"/></svg>'
    ),
    IconName.TEXT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 6h14M12 6v14M9 20h6"/></svg>'
    ),
    IconName.EFFECTS: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m12 3 1.9 4.5 4.9.4-3.7 3.2 1.2 4.7L12 13.6l-4.3 2.2 1.2-4.7L5.2 7.9l4.9-.4z"/></svg>'
    ),
    IconName.COLOR: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="8.5"/>'
        '<path d="M12 3.5a8.5 8.5 0 0 1 7.36 12.75L12 12Z" '
        'fill="currentColor" fill-opacity=".2"/>'
        '<path d="M4.64 16.25A8.5 8.5 0 0 1 12 3.5V12Z" '
        'fill="currentColor" fill-opacity=".35"/>'
        '<path d="M19.36 16.25A8.5 8.5 0 0 1 4.64 16.25L12 12Z" '
        'fill="currentColor" fill-opacity=".55"/>'
        '<circle cx="12" cy="12" r="2" fill="currentColor" stroke="none"/></svg>'
    ),
    IconName.TRANSITIONS: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 12h18"/>'
        '<path d="m14 7 5 5-5 5"/>'
        '<path d="M3 6h6M3 18h6"/></svg>'
    ),
    IconName.LIBRARY: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="4" width="4" height="16" rx="1"/>'
        '<rect x="10" y="4" width="4" height="16" rx="1"/>'
        '<path d="M16 5l4 1-3 15-4-1z"/></svg>'
    ),
    IconName.IMPORT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 4v12"/>'
        '<path d="m7 11 5 5 5-5"/>'
        '<path d="M5 20h14"/></svg>'
    ),
    IconName.EXPORT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 20V8"/>'
        '<path d="m7 13 5-5 5 5"/>'
        '<path d="M5 4h14"/></svg>'
    ),
    IconName.SAVE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 4h11l3 3v13H5z"/>'
        '<path d="M8 4v5h7V4"/>'
        '<path d="M8 14h8v6H8z"/></svg>'
    ),
    IconName.SAVE_AS: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 4h11l3 3v13H5z"/>'
        '<path d="M8 4v5h7V4"/>'
        '<path d="m14 17 2 2 4-4"/></svg>'
    ),
    IconName.OPEN: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"/>'
        '<path d="M4 10h16"/></svg>'
    ),
    IconName.NEW: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 3v5h5"/>'
        '<path d="M12 12v6M9 15h6"/></svg>'
    ),
    IconName.REFRESH: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>'
        '<path d="M21 4v4h-4"/>'
        '<path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>'
        '<path d="M3 20v-4h4"/></svg>'
    ),
    IconName.INFO: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M12 11v6M12 8h.01"/></svg>'
    ),
    IconName.DIAMOND: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<path d="M12 3 21 12l-9 9-9-9z"/></svg>'
    ),
    IconName.KEY: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="8" cy="12" r="4"/>'
        '<path d="M12 12h9M17 12v4M21 12v3"/></svg>'
    ),
    IconName.COMPARE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 6h8v12H4z" fill="currentColor" stroke="none" opacity="0.35"/>'
        '<rect x="3" y="5" width="18" height="14" rx="2"/>'
        '<path d="M12 3v18"/></svg>'
    ),
    IconName.PROJECT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 7h7l2 2h9v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'
        '<path d="M3 11h18"/></svg>'
    ),
    IconName.TIMER: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="13" r="8"/>'
        '<path d="M12 9v4l2 2"/>'
        '<path d="M9 3h6"/></svg>'
    ),
    IconName.LIST: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 6h12M4 12h12M4 18h12"/>'
        '<circle cx="20" cy="6" r="1" fill="currentColor"/>'
        '<circle cx="20" cy="12" r="1" fill="currentColor"/>'
        '<circle cx="20" cy="18" r="1" fill="currentColor"/></svg>'
    ),
    IconName.MENU: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="3" y="6" width="18" height="2" rx="1"/>'
        '<rect x="3" y="11" width="18" height="2" rx="1"/>'
        '<rect x="3" y="16" width="18" height="2" rx="1"/></svg>'
    ),
    IconName.PANEL_FLOAT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="4" width="11" height="9" rx="1.6"/>'
        '<path d="M15 20H6a2 2 0 0 1-2-2V9"/></svg>'
    ),
    IconName.PANEL_DOCK: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<rect x="12" y="13" width="7" height="5" rx="1" fill="currentColor" '
        'fill-opacity=".25"/>'
        '<path d="M12 15.5h7"/></svg>'
    ),
    IconName.PANEL_CLOSE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<path d="m9.5 9.5 5 5M14.5 9.5l-5 5"/></svg>'
    ),
    IconName.PANEL_MAXIMIZE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<path d="M9 8h6M9 8v3M15 16h-6M15 16v-3"/></svg>'
    ),
    IconName.PANEL_RESTORE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<rect x="7" y="8" width="10" height="8" rx="1.4" '
        'fill="currentColor" fill-opacity=".25"/>'
        '<path d="M9.5 10.5 14.5 14.5M14.5 10.5 9.5 14.5"/></svg>'
    ),
    IconName.PANEL_RESET: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<path d="M8 9h8M8 15h5"/></svg>'
    ),
    IconName.AUDIO_VOLUME: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" '
        'fill-opacity=".2"/>'
        '<path d="M15.6 9.2a4 4 0 0 1 0 5.6"/>'
        '<path d="M18.4 6.4a8 8 0 0 1 0 11.2"/></svg>'
    ),
    IconName.AUDIO_MUTE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" '
        'fill-opacity=".2"/>'
        '<path d="m16 9.5 5 5M21 9.5l-5 5"/></svg>'
    ),
    IconName.AUDIO_SOLO: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="8"/>'
        '<circle cx="12" cy="12" r="3" fill="currentColor" '
        'stroke="none"/></svg>'
    ),
    IconName.AUDIO_ARM: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="3" width="6" height="10" rx="3"/>'
        '<path d="M5.5 11a6.5 6.5 0 0 0 13 0"/>'
        '<path d="M12 17.5V21"/></svg>'
    ),
    IconName.AUDIO_FADE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 18h18"/>'
        '<path d="M3 18 11 6"/>'
        '<path d="M21 18 13 6"/>'
        '<circle cx="11" cy="6" r="1.6" fill="currentColor"/>'
        '<circle cx="13" cy="6" r="1.6" fill="currentColor"/></svg>'
    ),
    IconName.AUDIO_PAN: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 12h16"/>'
        '<circle cx="8" cy="12" r="2.4" fill="currentColor" '
        'fill-opacity=".2"/>'
        '<circle cx="16" cy="12" r="2.4" fill="currentColor" '
        'fill-opacity=".2"/>'
        '<path d="M12 7v10"/></svg>'
    ),
    IconName.AUDIO_MIXER: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 4v6M6 14v6M12 4v3M12 11v9M18 4v9M18 17v3"/>'
        '<path d="M3.5 10h5M9.5 7h5M15.5 13h5"/></svg>'
    ),
    IconName.MIC: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="3" width="6" height="10" rx="3"/>'
        '<path d="M5.5 11a6.5 6.5 0 0 0 13 0"/>'
        '<path d="M12 17.5V21"/>'
        '<path d="M8.5 21h7"/></svg>'
    ),
    IconName.CHEVRON_DOWN: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m6 9 6 6 6-6"/></svg>'
    ),
    IconName.CHEVRON_RIGHT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m9 6 6 6-6 6"/></svg>'
    ),
    IconName.STAR: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3.6l2.5 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.1 2.7 1-5.7-4.1-4 5.7-.8z"/></svg>'
    ),
    IconName.STAR_FILLED: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3.6l2.5 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.1 2.7 1-5.7-4.1-4 5.7-.8z"/></svg>'
    ),
    IconName.SEARCH: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>'
    ),
    IconName.CHECK: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m5 12.5 4.5 4.5L19 7.5"/></svg>'
    ),
    IconName.WARNING: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 4.2 3.2 19.3h17.6z"/><path d="M12 10v4.2"/><path d="M12 17.1h.01"/></svg>'
    ),
    IconName.SHAPE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3.5 20 8v8l-8 4.5L4 16V8z"/></svg>'
    ),
    IconName.RECTANGLE: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3.5" y="6.5" width="17" height="11" rx="1.6"/></svg>'
    ),
    IconName.SOLID: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="5" y="5" width="14" height="14" rx="2"/></svg>'
    ),
    IconName.GROUP: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m12 4 8.5 4.6L12 13.2 3.5 8.6z"/><path d="m3.5 13 8.5 4.6 8.5-4.6"/></svg>'
    ),
    IconName.ADJUSTMENT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>'
    ),
    IconName.NULL_OBJECT: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="3"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/></svg>'
    ),
}


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def svg_for(name: IconName | str) -> str:
    """Retourne la chaîne SVG associée à ``name``.

    Lève :class:`KeyError` si le nom est inconnu. Cela évite de renvoyer
    silencieusement une icône générique (qui masquerait une faute de
    frappe au pire moment).
    """
    key = IconName(name) if not isinstance(name, IconName) else name
    return _SVG_TEMPLATES[key]


def _render(name: IconName, size: int, color: QColor) -> QPixmap:
    """Génère un pixmap de l'icône dans la couleur ``color``."""
    renderer = QSvgRenderer(QByteArray(svg_for(name).encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    # Recolore chaque pixel avec ``color`` en respectant l'alpha.
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()
    return pixmap


def _default_icon_color() -> QColor:
    """Couleur de repli des icônes : le texte de la palette active.

    Sans cela les icônes resteraient claires (donc invisibles) sur un
    thème clair. ``ui.theme`` n'importe pas ``ui.icons`` : il n'y a donc
    pas de cycle d'import.
    """
    try:
        from ui.theme import COLORS

        return QColor(COLORS["text"])
    except Exception:  # pragma: no cover - garde-fou
        return QColor(Qt.white)


@lru_cache(maxsize=1024)
def _cached_pixmap(name: IconName, side: int, rgba: int) -> QPixmap:
    """Le pixmap d'une icône, en cache (nom, côté, couleur) : un moteur d'icône est interrogé à chaque repeint."""
    return _render(name, side, QColor.fromRgba(rgba))


class _PaletteIconEngine(QIconEngine):
    """Icône qui se peint **à la demande**, dans la couleur de la palette active (ou dans une couleur imposée).

    Un ``QIcon`` bâti sur un pixmap figé garde la couleur du thème où il est né : à un changement de thème en direct, la moitié des
    icônes restait claire sur fond clair (ou sombre sur fond sombre). Celle-ci relit la palette à chaque peinture : le thème change,
    l'icône suit, sans que le widget qui la porte n'ait rien à refaire. Le SVG est rendu à la taille *physique* demandée par Qt :
    net sur un écran HiDPI. Le mode désactivé prend la couleur du texte désactivé du thème.
    """

    def __init__(self, name: IconName, color: QColor | None = None) -> None:
        super().__init__()
        self._name = name
        self._color = QColor(color) if color is not None else None

    def clone(self) -> "_PaletteIconEngine":
        return _PaletteIconEngine(self._name, self._color)

    def _rgba(self, mode) -> int:
        if self._color is not None:
            color = QColor(self._color)
            if mode == QIcon.Mode.Disabled:
                color.setAlphaF(color.alphaF() * 0.4)
            return color.rgba()
        from ui.theme import active_palette

        palette = active_palette()
        return QColor(palette.disabled_text if mode == QIcon.Mode.Disabled else palette.text).rgba()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        side = max(1, min(size.width(), size.height()))
        icon = _cached_pixmap(self._name, side, self._rgba(mode))
        if size.width() == size.height():
            return icon
        canvas = QPixmap(size)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap((size.width() - side) // 2, (size.height() - side) // 2, icon)
        painter.end()
        return canvas

    def paint(self, painter: QPainter, rect: QRect, mode, state) -> None:
        ratio = max(1.0, painter.device().devicePixelRatioF()) if painter.device() is not None else 1.0
        target = QSize(max(1, round(rect.width() * ratio)), max(1, round(rect.height() * ratio)))
        pixmap = self.pixmap(target, mode, state)
        pixmap.setDevicePixelRatio(ratio)
        painter.drawPixmap(QPoint(rect.x(), rect.y()), pixmap)

    def actualSize(self, size: QSize, mode, state) -> QSize:
        return size


def make_icon(
    name: IconName | str,
    size: int = 18,
    color: QColor | None = None,
) -> QIcon:
    """Retourne un :class:`QIcon` peint dans ``color``.

    Si ``color`` est ``None``, l'icône suit la couleur de texte du thème **actif au moment de la peinture** (lisible sur les fonds
    clairs comme sombres, et qui change avec le thème sans être reconstruite : voir :class:`_PaletteIconEngine`).

    ``size`` n'est plus qu'un indice : le moteur rend l'icône à la taille que Qt lui demande. Il n'ajoute aucun pixmap de
    taille fixe, ce qui évite l'ancien défaut (plusieurs pixmaps de tailles différentes faisaient passer Qt à la plus grande taille
    physique et gonflaient la hauteur des lignes de listes de la bibliothèque).
    """
    return QIcon(_PaletteIconEngine(IconName(name) if not isinstance(name, IconName) else name, color))


# ---------------------------------------------------------------------------
# Widgets haut-niveau
# ---------------------------------------------------------------------------


class IconLabel(QWidget):
    """Petit widget qui ne peint qu'une icône.

    La couleur courante suit la palette du thème en lisant
    ``palette.color(QPalette.Text)`` ou, à défaut, la propriété
    ``icon_color`` du widget parent. Cela permet à l'icône de suivre
    automatiquement les changements de thème sombre / clair.
    """

    def __init__(
        self,
        name: IconName,
        size: int = 16,
        color: QColor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._size = size
        self._explicit_color = color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_color(self, color: QColor | str | None) -> None:
        """Couleur de l'icône : un ``QColor`` figé, ou le **nom d'un jeton** de la palette (``"muted"``), lu à chaque peinture et qui
        suit donc le thème sans rien à refaire."""
        self._token = color if isinstance(color, str) else None
        self._explicit_color = None if self._token else color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt
        color = self._explicit_color
        token = getattr(self, "_token", None)
        if token is not None:
            from ui.theme import active_palette

            color = QColor(getattr(active_palette(), token))
        if color is None:
            color = self.palette().color(self.foregroundRole())
            if not color.isValid():
                color = _default_icon_color()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pixmap = _render(self._name, max(self._size, 1), color)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()


class IconButton(QToolButton):
    """Bouton ``QToolButton`` avec icône cohérente, tooltip et taille stable.

    Utilisé partout dans l'interface pour les contrôles denses (barre
    d'outils timeline, panneau propriétés, etc.). Le bouton applique
    automatiquement les états ``hover``, ``checked``, ``disabled`` via
    le style global.
    """

    def __init__(
        self,
        icon: IconName | None = None,
        text: str | None = None,
        tooltip: str | None = None,
        *,
        checkable: bool = False,
        checked: bool = False,
        accent: bool = False,
        square: bool = True,
        size: int = 28,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCheckable(checkable)
        self.setChecked(checked)
        # Un bouton avec texte ne peut pas être « carré » : on force le
        # mode extensible pour que le libellé soit visible.
        if text:
            square = False
        self._square = square
        self._accent = accent
        # Pour les boutons carrés on réduit un peu la taille d'icône pour
        # conserver une zone tactile confortable ; sans cela les glyphes
        # paraissent écrasés sur les petites tailles.
        self._icon_size_px = size - 8 if square else size - 4
        if square:
            self.setFixedSize(size, size)
            # Neutralise le padding global de QToolButton, qui rognerait
            # l'icône dans un bouton de cette taille.
            self.setObjectName("accentIcon" if accent else "iconOnly")
        elif accent:
            self.setObjectName("accentText")
        if icon is not None:
            self.setIcon(make_icon(icon, size=self._icon_size_px))
        if text:
            self.setText(text)
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.setMinimumHeight(size)
            self.setMinimumWidth(size + 60)
        if tooltip:
            self.setToolTip(tooltip)
        self._apply_style()

    def setToolTip(self, tip: str) -> None:  # noqa: D401 - Qt
        """Surcharge : un bouton sans texte n'a que son infobulle pour se présenter aux technologies d'assistance."""
        super().setToolTip(tip)
        if not self.text():
            self.setAccessibleName(tip)

    def setIcon(self, icon: QIcon) -> None:  # noqa: D401 - Qt
        super().setIcon(icon)
        self.setIconSize(QSize(self._icon_size_px, self._icon_size_px))

    def setText(self, text: str) -> None:  # noqa: D401 - Qt
        """Surcharge : si du texte est ajouté après création, on libère la taille fixe."""
        super().setText(text)
        if text:
            # Si on a déjà été contraint en taille carrée, on libère.
            if self.maximumWidth() != 16777215:
                self.setMaximumWidth(16777215)
                self.setMinimumWidth(self._icon_size_px + 60)
                self.setMinimumHeight(self._icon_size_px + 10)
                # Le mode « icône seule » ne convient plus : on rend la
                # main au padding de la feuille de style globale.
                if self.objectName() in ("iconOnly", "accentIcon"):
                    self.setObjectName(
                        "accentText" if self._accent else ""
                    )
                    self._square = False
                    new_icon = self._icon_size_px + 4
                    self._icon_size_px = new_icon
                    self.setIconSize(QSize(new_icon, new_icon))
            elif self.minimumWidth() == 0:
                # Bouton à libellé créé sans taille fixe : sans largeur minimale explicite, un layout lui impose
                # la largeur de son libellé et une colonne étroite (bibliothèque à 1180 px) déborde ou coupe
                # la page. Il peut se comprimer (libellé coupé) plutôt que de faire déborder son conteneur.
                self.setMinimumWidth(self._icon_size_px + 60)
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

    def _apply_style(self) -> None:
        # Le style global de l'application couvre les états. On laisse
        # donc Qt appliquer la feuille courante : aucune surcharge locale.
        return

    def set_enabled_visual(self, enabled: bool) -> None:
        self.setEnabled(enabled)


def available_icons() -> Iterable[str]:
    """Retourne les noms d'icônes connus (utile en debug / autocomplétion)."""
    return tuple(member.value for member in IconName)


__all__ = [
    "IconButton",
    "IconLabel",
    "IconName",
    "available_icons",
    "make_icon",
    "svg_for",
]
