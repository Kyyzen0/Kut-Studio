"""État de l'espace de travail de Kut-Studio (hors projet).

Ce module décrit **l'organisation de l'interface** : quels panneaux sont
visibles, dans quelle zone ils sont dockés, leurs dimensions, lequel est
maximisé, lesquels sont détachés dans une fenêtre séparée.

Il est volontairement **pur** (aucune dépendance PySide6 ni Qt) afin de
pouvoir être testé en ligne de commande et sérialisé sans risque.

Séparation des responsabilités (voir §10 du cahier des charges) :

- :mod:`core.project_model` porte le **Project State** (contenu vidéo) ;
- ce module porte le **Workspace State** (disposition de l'UI).

Les deux ne sont jamais mélangés : un projet ``.kut`` ne décrit pas la
taille des panneaux, et les préférences d'interface ne décrivent pas le
montage.

Format de persistance : JSON UTF-8 versionné. Un snapshot décrit au
minimum les panneaux visibles, leurs dimensions, leur zone, le panneau
maximisé et la géométrie des fenêtres détachées.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .atomic_io import atomic_write_text


# ---------------------------------------------------------------------------
# Identifiants
# ---------------------------------------------------------------------------


class PanelId(str, Enum):
    """Identité stable d'un panneau.

    L'énumération est la source de vérité du registre de panneaux : la
    position d'un panneau dans le layout n'est jamais codée en dur, elle
    est lue depuis :class:`WorkspaceState`.
    """

    TIMELINE = "timeline"
    VIEWER = "viewer"
    MEDIA = "media"
    INSPECTOR = "inspector"
    MIXER = "mixer"
    HISTORY = "history"

    def label(self) -> str:
        """Nom lisible du panneau (utilisé dans les menus)."""
        return _PANEL_LABELS[self]


_PANEL_LABELS: dict[PanelId, str] = {
    PanelId.TIMELINE: "Timeline",
    PanelId.VIEWER: "Viewer",
    PanelId.MEDIA: "Médias",
    PanelId.INSPECTOR: "Inspecteur",
    PanelId.MIXER: "Mixeur",
    PanelId.HISTORY: "Historique",
}


class DockArea(str, Enum):
    """Zone d'accueil d'un panneau dans la fenêtre principale."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    BOTTOM = "bottom"


#: Zone d'accueil par défaut de chaque panneau.
DEFAULT_AREA: dict[PanelId, DockArea] = {
    PanelId.MEDIA: DockArea.LEFT,
    PanelId.VIEWER: DockArea.CENTER,
    PanelId.INSPECTOR: DockArea.RIGHT,
    PanelId.TIMELINE: DockArea.BOTTOM,
    # Le mixeur démarre replié : il n'occupe de la place que lorsque
    # l'utilisateur l'ouvre, sans leAnon disruptive pour l'édition.
    PanelId.MIXER: DockArea.BOTTOM,
    # L'historique aussi : une colonne à droite, ouverte depuis le menu Fenêtre.
    PanelId.HISTORY: DockArea.RIGHT,
}

#: Taille préférée initiale (px) — sert au premier démarrage.
DEFAULT_SIZE: dict[PanelId, int] = {
    PanelId.MEDIA: 270,
    PanelId.VIEWER: 720,
    PanelId.INSPECTOR: 300,
    PanelId.TIMELINE: 300,
    PanelId.MIXER: 320,
    PanelId.HISTORY: 240,
}

#: Taille minimale d'un panneau : en dessous, le panneau devient inutilisable.
MIN_SIZE: dict[PanelId, int] = {
    PanelId.MEDIA: 220,
    PanelId.VIEWER: 320,
    PanelId.INSPECTOR: 280,
    PanelId.TIMELINE: 240,
    PanelId.MIXER: 320,
    PanelId.HISTORY: 200,
}

#: Panneaux repliés au premier démarrage (désactivés à l'ouverture).
DEFAULT_HIDDEN: frozenset[PanelId] = frozenset({PanelId.MIXER, PanelId.HISTORY})

#: Taille de la barre d'outils d'options d'un panneau (px).
PANEL_TOOLBAR_SIZE: int = 28

#: Hauteur de la barre de titre d'une fenêtre détachée (px).
FLOATING_TITLEBAR_SIZE: int = 30

#: Version du format de persistance. Permet une migration laterale.
STATE_VERSION: int = 1

WORKSPACE_FILE_NAME: str = "workspace.json"


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PanelState:
    """Disposition d'un panneau.

    Attributes:
        panel: identité stable du panneau.
        area: zone d'accueil (``None`` si le panneau est détaché).
        visible: panneau ouvert ou fermé.
        floating: panneau dans une fenêtre séparée.
        size: taille préférée (px) dans sa zone, ou taille de la fenêtre
            détachée.
    """

    panel: PanelId
    area: DockArea | None = None
    visible: bool = True
    floating: bool = False
    size: int = 0

    def normalized(self) -> "PanelState":
        """Renvoie une entrée cohérente (zone, taille, visibilité)."""
        area = self.area
        if self.floating:
            # Un panneau détaché n'appartient plus à une zone de la
            # fenêtre principale, mais on conserve la zone de retour
            # pour pouvoir le rattacher là où il était.
            area = area if area is not None else DEFAULT_AREA[self.panel]
        else:
            area = area if area is not None else DEFAULT_AREA[self.panel]
        size = int(self.size) if int(self.size) > 0 else DEFAULT_SIZE[self.panel]
        size = max(size, MIN_SIZE[self.panel])
        return replace(self, area=area, size=size, visible=bool(self.visible))


@dataclass(frozen=True)
class FloatingGeometry:
    """Position et taille d'une fenêtre de panneau détaché."""

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def normalized(self, panel: PanelId) -> "FloatingGeometry":
        """Force une taille minimale exploitable."""
        width = max(int(self.width) or DEFAULT_SIZE[panel], MIN_SIZE[panel])
        height = max(
            int(self.height) or MIN_SIZE[panel],
            MIN_SIZE[panel] + PANEL_TOOLBAR_SIZE,
        )
        return FloatingGeometry(int(self.x), int(self.y), width, height)


@dataclass(frozen=True)
class WorkspaceState:
    """Instantané complet de l'espace de travail.

    Cet objet est la **source de vérité unique** de l'UI : les
    composants le lisent, ne le mutent jamais sur place. Toute
    modification passe par un nouvel état (voir
    :func:`core.workspace_state.with_panel`).
    """

    panels: tuple[PanelState, ...] = ()
    maximized: PanelId | None = None
    floating: tuple[tuple[PanelId, FloatingGeometry], ...] = ()
    center_ratio: float = 0.5
    version: int = STATE_VERSION

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "WorkspaceState":
        """État par défaut : les panneaux principaux visibles et dockés."""
        panels = tuple(
            PanelState(
                panel=pid,
                area=DEFAULT_AREA[pid],
                visible=pid not in DEFAULT_HIDDEN,
            ).normalized()
            for pid in PanelId
        )
        return cls(panels=panels).normalized()

    @classmethod
    def from_dict(cls, data: object) -> "WorkspaceState":
        """Reconstruit un état depuis un JSON décodé.

        Toute entrée inconnue ou invalide est ignorée : un fichier
        corrompu ne doit jamais empêcher l'application de démarrer.
        """
        if not isinstance(data, dict):
            return cls.default()

        panels: list[PanelState] = []
        raw_panels = data.get("panels")
        if isinstance(raw_panels, list):
            for entry in raw_panels:
                panel = _coerce_panel_id(entry)
                if panel is None or any(p.panel is panel for p in panels):
                    continue
                area = _coerce_area(entry, panel)
                size = _coerce_int(entry.get("size"), DEFAULT_SIZE[panel])
                panels.append(
                    PanelState(
                        panel=panel,
                        area=area,
                        visible=bool(entry.get("visible", True)),
                        floating=bool(entry.get("floating", False)),
                        size=size,
                    )
                )

        known = {p.panel for p in panels}
        # Un panneau absent du fichier est traité comme « choix de
        # l'utilisateur inconnu » : on applique le défaut de
        # l'application (le mixeur reste replié), pas « tout visible ».
        for pid in PanelId:
            if pid not in known:
                panels.append(
                    PanelState(panel=pid, visible=pid not in DEFAULT_HIDDEN).normalized()
                )

        floating: list[tuple[PanelId, FloatingGeometry]] = []
        raw_float = data.get("floating")
        if isinstance(raw_float, list):
            for entry in raw_float:
                floating_id = _coerce_panel_id(entry)
                if floating_id is None:
                    continue
                if not isinstance(entry, dict):
                    continue
                geom = FloatingGeometry(
                    x=_coerce_int(entry.get("x"), 0),
                    y=_coerce_int(entry.get("y"), 0),
                    width=_coerce_int(entry.get("width"), DEFAULT_SIZE[floating_id]),
                    height=_coerce_int(entry.get("height"), MIN_SIZE[floating_id]),
                )
                floating.append((floating_id, geom.normalized(floating_id)))

        maximized = _coerce_panel_id({"panel": data.get("maximized")})
        ratio = data.get("center_ratio")
        center_ratio = (
            float(ratio)
            if isinstance(ratio, (int, float)) and 0.1 <= float(ratio) <= 0.9
            else 0.5
        )
        return cls(
            panels=tuple(panels),
            maximized=maximized,
            floating=tuple(floating),
            center_ratio=center_ratio,
        ).normalized()

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    def normalized(self) -> "WorkspaceState":
        """Répare les incohérences (panneaux manquants, tailles)."""
        by_id: dict[PanelId, PanelState] = {}
        for entry in self.panels:
            by_id.setdefault(entry.panel, entry)
        for pid in PanelId:
            by_id.setdefault(
                pid, PanelState(panel=pid, visible=pid not in DEFAULT_HIDDEN)
            )
        ordered = tuple(
            by_id[pid].normalized() for pid in PanelId
        )
        maximized = self.maximized
        if maximized is not None and not by_id[maximized].visible:
            maximized = None
        float_map = {pid: geom.normalized(pid) for pid, geom in self.floating}
        # Un panneau marqué flottant doit avoir une géométrie connue,
        # et réciproquement.
        for entry in ordered:
            if entry.floating and entry.panel not in float_map:
                geom = FloatingGeometry(
                    width=entry.size, height=MIN_SIZE[entry.panel] + 200
                )
                float_map[entry.panel] = geom
        for pid in list(float_map):
            if not by_id[pid].floating:
                float_map.pop(pid)
        ratio = min(max(float(self.center_ratio), 0.1), 0.9)
        return WorkspaceState(
            panels=ordered,
            maximized=maximized,
            floating=tuple(sorted(float_map.items(), key=lambda kv: kv[0].value)),
            center_ratio=ratio,
            version=self.version,
        )

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    def get(self, panel: PanelId) -> PanelState:
        """Retourne l'état d'un panneau (valeur par défaut si absent)."""
        for entry in self.panels:
            if entry.panel is panel:
                return entry
        return PanelState(panel=panel).normalized()

    def is_visible(self, panel: PanelId) -> bool:
        return self.get(panel).visible

    def is_floating(self, panel: PanelId) -> bool:
        return self.get(panel).floating

    def area_of(self, panel: PanelId) -> DockArea:
        entry = self.get(panel)
        return entry.area or DEFAULT_AREA[panel]

    def visible_panels(self, area: DockArea) -> tuple[PanelId, ...]:
        """Panneaux visibles d'une zone, dans l'ordre d'Enum."""
        return tuple(
            entry.panel
            for entry in self.panels
            if entry.visible
            and not entry.floating
            and self.area_of(entry.panel) is area
        )

    def floating_geometry(self, panel: PanelId) -> FloatingGeometry:
        for pid, geom in self.floating:
            if pid is panel:
                return geom
        return FloatingGeometry(
            width=DEFAULT_SIZE[panel], height=MIN_SIZE[panel] + 200
        ).normalized(panel)

    # ------------------------------------------------------------------
    # Écriture (fonct purs : l'état n'est jamais muté)
    # ------------------------------------------------------------------

    def with_panel(self, panel: PanelId, **changes: object) -> "WorkspaceState":
        """Renvoie un nouvel état avec ``panel`` modifié."""
        current = self.get(panel)
        updated = replace(current, **changes)  # type: ignore[arg-type]
        panels = tuple(
            updated if entry.panel is panel else entry for entry in self.panels
        )
        return replace(self, panels=panels).normalized()

    def with_maximized(
        self, panel: PanelId | None
    ) -> "WorkspaceState":
        """Maximise (ou restaure) un panneau."""
        if panel is not None and not self.is_visible(panel):
            return self
        return replace(self, maximized=panel).normalized()

    def with_floating_geometry(
        self, panel: PanelId, geom: FloatingGeometry
    ) -> "WorkspaceState":
        """Mémorise la géométrie d'une fenêtre détachée."""
        others = tuple(
            (pid, g) for pid, g in self.floating if pid is not panel
        )
        return replace(
            self, floating=others + ((panel, geom.normalized(panel)),)
        ).normalized()

    def with_center_ratio(self, ratio: float) -> "WorkspaceState":
        return replace(self, center_ratio=ratio).normalized()

    # ------------------------------------------------------------------
    # Sérialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Export JSON (structures simples, prêtes pour ``json.dump``)."""
        return {
            "version": STATE_VERSION,
            "center_ratio": round(float(self.center_ratio), 4),
            "maximized": self.maximized.value if self.maximized else None,
            "panels": [
                {
                    "panel": entry.panel.value,
                    "area": entry.area.value if entry.area else None,
                    "visible": entry.visible,
                    "floating": entry.floating,
                    "size": int(entry.size),
                }
                for entry in self.panels
            ],
            "floating": [
                {
                    "panel": pid.value,
                    "x": geom.x,
                    "y": geom.y,
                    "width": geom.width,
                    "height": geom.height,
                }
                for pid, geom in self.floating
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers de coercion (robuste face à un fichier corrompu)
# ---------------------------------------------------------------------------


def _coerce_panel_id(entry: object) -> PanelId | None:
    if not isinstance(entry, dict):
        return None
    raw = entry.get("panel")
    try:
        return PanelId(raw)
    except (ValueError, TypeError):
        return None


def _coerce_area(entry: dict, panel: PanelId) -> DockArea:
    raw = entry.get("area")
    try:
        return DockArea(raw)
    except (ValueError, TypeError):
        return DEFAULT_AREA[panel]


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


# ---------------------------------------------------------------------------
# Persistance sur disque
# ---------------------------------------------------------------------------


def workspace_file_path(settings_dir: str | os.PathLike[str] | None = None) -> Path:
    """Chemin du fichier d'espace de travail.

    Même répertoire que les préférences utilisateur, mais **fichier
    distinct** : l'interface et le projet restent deux concepts séparés.
    """
    if settings_dir is None:
        from core.user_settings import default_settings_dir

        base = default_settings_dir()
    else:
        base = Path(settings_dir)
    return Path(base) / WORKSPACE_FILE_NAME


def load_workspace_state(
    settings_dir: str | os.PathLike[str] | None = None,
) -> WorkspaceState:
    """Charge l'espace de travail ; défaut si absent ou invalide."""
    path = workspace_file_path(settings_dir)
    if not path.exists():
        return WorkspaceState.default()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return WorkspaceState.default()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return WorkspaceState.default()
    return WorkspaceState.from_dict(data).normalized()


def save_workspace_state(
    state: WorkspaceState,
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Sauvegarde l'espace de travail (écriture atomique)."""
    path = workspace_file_path(settings_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, state.to_json())
    return path


# ---------------------------------------------------------------------------
# Espaces de travail nommés
# ---------------------------------------------------------------------------
#
# Un « espace de travail » est un instantané nommé de
# :class:`WorkspaceState` : Editing, Motion, Audio, Color, Compact, ou un
# agencement choisi par l'utilisateur. Ils vivent dans un sous-dossier
# ``workspaces/`` distinct du ``workspace.json`` courant, lui-même
# distinct du projet ``.kut``.


WORKSPACES_DIR_NAME: str = "workspaces"

#: Espaces livrés d'origine. Ils décrivent des intentions, pas des
#: fonctionnalités : ouvrir « Audio » masque le viewer pour lui laisser
#: plus de place, sans prétendre que le mixeur existe déjà.
BUILTIN_WORKSPACES: dict[str, str] = {
    "editing": "Édition",
    "motion": "Motion",
    "audio": "Audio",
    "color": "Color",
    "compact": "Compact",
}


def workspace_display_name(name: str) -> str:
    """Libellé lisible d'un espace de travail (pour les menus)."""
    return BUILTIN_WORKSPACES.get(name, name.replace("-", " ").capitalize())


def _slugify(name: str) -> str:
    """Normalise un nom d'espace de travail en identifiant de fichier."""
    cleaned = "".join(
        ch.lower() if ch.isalnum() else "-" for ch in name.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "workspace"


def workspaces_dir(settings_dir: str | os.PathLike[str] | None = None) -> Path:
    """Répertoire des espaces de travail nommés."""
    if settings_dir is None:
        from core.user_settings import default_settings_dir

        base = default_settings_dir()
    else:
        base = Path(settings_dir)
    return Path(base) / WORKSPACES_DIR_NAME


def named_workspace_path(
    name: str,
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Chemin du fichier d'un espace de travail nommé."""
    return workspaces_dir(settings_dir) / f"{_slugify(name)}.json"


def list_named_workspaces(
    settings_dir: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Espaces de travail disponibles : livrés d'origine puis customized."""
    names = list(BUILTIN_WORKSPACES)
    directory = workspaces_dir(settings_dir)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            label = path.stem
            if label not in names:
                names.append(label)
    return tuple(names)


def save_named_workspace(
    name: str,
    state: WorkspaceState,
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Enregistre un espace de travail sous un nom."""
    path = named_workspace_path(name, settings_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, state.normalized().to_json())
    return path


def load_named_workspace(
    name: str,
    settings_dir: str | os.PathLike[str] | None = None,
) -> WorkspaceState | None:
    """Charge un espace de travail nommé, ou ``None`` s'il n'existe pas.

    Les espaces livrés d'origine ne sont pas des fichiers : ils sont
    dérivés de la disposition par défaut selon leur intention, ce qui
    évite d'embarquer des dispositions figées dans le code.
    """
    if name in BUILTIN_WORKSPACES:
        return _builtin_workspace(name)
    path = named_workspace_path(name, settings_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return WorkspaceState.from_dict(data).normalized()


def delete_named_workspace(
    name: str,
    settings_dir: str | os.PathLike[str] | None = None,
) -> bool:
    """Supprime un espace de travail utilisateur (jamais un natif)."""
    if name in BUILTIN_WORKSPACES:
        return False
    path = named_workspace_path(name, settings_dir)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _builtin_workspace(name: str) -> WorkspaceState:
    """Disposition d'un espace de travail livré d'origine."""
    state = WorkspaceState.default()
    if name == "audio":
        # Le mixeur audio prendra cette place : on libère le viewer.
        return state.with_panel(PanelId.VIEWER, visible=False)
    if name == "motion":
        # Flux de travail centré sur la timeline.
        return state.with_panel(PanelId.MEDIA, visible=False)
    if name == "color":
        return state.with_panel(PanelId.MEDIA, visible=False)
    if name == "compact":
        return state.with_panel(PanelId.INSPECTOR, visible=False)
    return state


__all__ = [
    "BUILTIN_WORKSPACES",
    "DEFAULT_AREA",
    "DEFAULT_SIZE",
    "DockArea",
    "FLOATING_TITLEBAR_SIZE",
    "FloatingGeometry",
    "MIN_SIZE",
    "PANEL_TOOLBAR_SIZE",
    "PanelId",
    "PanelState",
    "STATE_VERSION",
    "WORKSPACES_DIR_NAME",
    "WORKSPACE_FILE_NAME",
    "WorkspaceState",
    "delete_named_workspace",
    "list_named_workspaces",
    "load_named_workspace",
    "load_workspace_state",
    "named_workspace_path",
    "save_named_workspace",
    "save_workspace_state",
    "workspace_display_name",
    "workspace_file_path",
    "workspaces_dir",
]
