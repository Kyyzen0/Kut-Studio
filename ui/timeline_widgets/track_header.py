"""En-tête de piste (à gauche des pistes)."""

from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.project_model import Track
from ui.design_system import Radius, Sizes, Spacing, Typography, Weights
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import ThemePalette, label_style
from ui.timeline_widgets.common import _color_for_track_type, _current_palette

# ---------------------------------------------------------------------------
# En-tête de piste (à gauche des pistes)
# ---------------------------------------------------------------------------


class TrackRowHeader(QFrame):
    """En-tête visuel d'une piste, à gauche de la timeline.

    Affiche le nom, le type de piste et un jeu de boutons d'action
    essentiels (``lock``, ``visible``, ``mute``, ``up``, ``down``,
    ``rename``, ``remove``). Chaque bouton émet un signal
    haute-niveau relayé par :class:`TimelinePanel` à ``MainWindow``.
    """

    lock_toggled = Signal(str, bool)
    visible_toggled = Signal(str, bool)
    mute_toggled = Signal(str, bool)
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    height_cycle_requested = Signal(str)
    collapse_toggled = Signal(str, bool)
    rename_requested = Signal(str)
    move_up_requested = Signal(str)
    move_down_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, track: Track, parent=None) -> None:
        super().__init__(parent)
        self.track = track
        self.menu_extender = None
        """``(menu, identifiant de piste) -> None`` : entrées du menu ⋯ qui dépendent du projet (voir la timeline)."""
        self._extra_actions: list = []
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(Sizes.timeline_track_height + 6)
        # Fond du panneau + filet de séparation bas : donne une limite
        # franche à chaque piste même quand les pistes sont serrées.
        self.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
            f"QFrame#trackHeader {{ background: {_current_palette().track_header_bg};"
            f" border: none; border-bottom: 1px solid"
            f" {_current_palette().track_divider}; }}"
        )
        self.setObjectName("trackHeader")

        from PySide6.QtWidgets import QHBoxLayout
        outer = QHBoxLayout(self)
        outer.setContentsMargins(Spacing.md, Spacing.xs, Spacing.sm, Spacing.xs)
        outer.setSpacing(Spacing.sm)

        # ----- Ligne 1 : pastille + nom + état ------------------------
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(Spacing.sm)

        track_color = _color_for_track_type(track.type, _current_palette())
        self._swatch = QWidget()
        self._swatch.setProperty("track_swatch", True)
        self._swatch.setFixedSize(4, 32)
        self._swatch.setStyleSheet(
            f"background: {track_color}; border-radius: {Radius.pill}px;"
        )
        title_layout.addWidget(self._swatch)

        name_box = QWidget()
        name_layout = QVBoxLayout(name_box)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(0)

        type_keys = {
            "video": "timeline.track.video",
            "audio": "timeline.track.audio",
            "subtitle": "timeline.track.subtitle",
            "graphics": "timeline.track.graphics",
        }
        type_label = translate(type_keys[track.type]) if track.type in type_keys else track.type.upper()
        # Le nom de la piste d'abord (« V1 », ou celui que l'utilisateur lui a donné) ; en dessous, son type, précédé de son
        # identifiant seulement quand le nom l'a remplacé. Plus de « V1 VIDÉO » puis « V1 » : la même information deux fois.
        shown_name = track.name or track.id
        title = QLabel(shown_name)
        title.setStyleSheet(label_style(Typography.small, "text", Weights.semibold))
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        name_layout.addWidget(title)

        state_parts: list[str] = []
        if getattr(track, "locked", False):
            state_parts.append(translate("timeline.track.locked"))
        if not getattr(track, "visible", True):
            state_parts.append(translate("timeline.track.hidden"))
        if getattr(track, "muted", False):
            state_parts.append(translate("timeline.track.muted"))
        if getattr(track, "solo", False):
            state_parts.append(translate("mixer.solo"))
        if getattr(track, "collapsed", False):
            state_parts.append(translate("timeline.track.collapsed"))
        kind = type_label if shown_name == track.id else f"{track.id} · {type_label}"
        state_label = QLabel(" · ".join([kind, *state_parts]))
        state_label.setStyleSheet(label_style(Typography.caption, "muted", Weights.regular))
        name_layout.addWidget(state_label)
        title_layout.addWidget(name_box, 1)

        outer.addWidget(title_row, 1)

        # ----- Ligne 2 : boutons d'action -----------------------------
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(Spacing.xs)
        button_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._add_action_buttons(button_layout, track)
        outer.addWidget(button_row, 0)

    def _add_action_buttons(self, layout, track) -> None:
        """Ajoute les boutons d'action dans ``layout`` (horizontal)."""

        def _btn(
            icon: IconName,
            tooltip: str,
            callback,
            *,
            checkable: bool = False,
            checked: bool = False,
        ) -> IconButton:
            button = IconButton(
                icon=icon,
                tooltip=tooltip,
                checkable=checkable,
                checked=checked,
                size=Sizes.icon_button_sm,
            )
            button.clicked.connect(callback)
            return button

        # Bouton "état" du type de piste (œil / son / sous-titre).
        if track.type in {"video", "graphics"}:
            state_btn = _btn(
                (
                    IconName.COLOR
                    if track.type == "graphics" and getattr(track, "visible", True)
                    else IconName.EYE
                    if getattr(track, "visible", True)
                    else IconName.EYE_OFF
                ),
                translate("tracks.visible_tooltip"),
                lambda checked: self.visible_toggled.emit(track.id, checked),
                checkable=True,
                checked=getattr(track, "visible", True),
            )
        elif track.type == "audio":
            state_btn = _btn(
                IconName.SPEAKER if not getattr(track, "muted", False) else IconName.MUTE,
                translate("tracks.mute_tooltip"),
                lambda checked: self.mute_toggled.emit(track.id, not checked),
                checkable=True,
                checked=not getattr(track, "muted", False),
            )
        else:
            state_btn = _btn(
                IconName.SUBTITLE,
                translate("tracks.visible_tooltip"),
                lambda checked: None,
            )
            state_btn.setEnabled(False)
        layout.addWidget(state_btn)

        # Bouton de verrouillage.
        lock_btn = _btn(
            IconName.LOCK if getattr(track, "locked", False) else IconName.UNLOCK,
            translate("tracks.lock_tooltip"),
            lambda checked: self.lock_toggled.emit(track.id, checked),
            checkable=True,
            checked=getattr(track, "locked", False),
        )
        layout.addWidget(lock_btn)

        solo_btn = _btn(
            IconName.SOLO,
            translate("mixer.solo"),
            lambda checked: self.solo_toggled.emit(track.id, checked),
            checkable=True,
            checked=bool(getattr(track, "solo", False)),
        )
        layout.addWidget(solo_btn)
        if track.type == "audio":
            arm_btn = _btn(
                IconName.MARKER,
                translate("history.track.arm"),
                lambda checked: self.arm_toggled.emit(track.id, checked),
                checkable=True,
                checked=bool(getattr(track, "armed", False)),
            )
            layout.addWidget(arm_btn)
        else:
            # Même largeur que le bouton d'armement des pistes audio : les colonnes de boutons s'alignent d'une piste à l'autre.
            spacer = QWidget()
            spacer.setFixedSize(Sizes.icon_button_sm, Sizes.icon_button_sm)
            layout.addWidget(spacer)

        # Les commandes moins fréquentes restent disponibles sans saturer
        # chaque en-tête de piste.
        more_btn = _btn(IconName.MORE, translate("timeline.track.actions"), lambda: None)
        menu = QMenu(more_btn)
        actions = (
            (translate("history.track.height"), lambda: self.height_cycle_requested.emit(track.id)),
            (
                translate("timeline.track.collapse_toggle"),
                lambda: self.collapse_toggled.emit(
                    track.id, not bool(getattr(track, "collapsed", False))
                ),
            ),
            (translate("tracks.up_tooltip"), lambda: self.move_up_requested.emit(track.id)),
            (translate("tracks.down_tooltip"), lambda: self.move_down_requested.emit(track.id)),
            (translate("tracks.rename_tooltip"), lambda: self.rename_requested.emit(track.id)),
            (translate("tracks.delete_tooltip"), lambda: self.remove_requested.emit(track.id)),
        )
        for label, callback in actions:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, cb=callback: cb()
            )
        # Entrées qui dépendent du projet (courbe de volume, rôle, ducking d'une piste audio) : posées par la timeline
        # à chaque ouverture, pour refléter l'état courant (une annulation a pu le changer).
        menu.aboutToShow.connect(lambda m=menu: self._extend_menu(m))
        self._more_menu = menu
        more_btn.setMenu(menu)
        more_btn.setPopupMode(IconButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(more_btn)

    def _extend_menu(self, menu: QMenu) -> None:
        """Remplace les entrées dynamiques du menu ⋯ par celles du ``menu_extender`` (posé par la timeline)."""
        for action in self._extra_actions:
            menu.removeAction(action)
            submenu = action.menu()
            if submenu is not None:
                submenu.deleteLater()
            action.deleteLater()
        self._extra_actions = []
        if self.menu_extender is None:
            return
        before = set(menu.actions())
        self.menu_extender(menu, self.track.id)
        self._extra_actions = [action for action in menu.actions() if action not in before]

    @staticmethod
    def _prefix_for_type(track_type: str) -> str:
        return {
            "video": "V", "audio": "A", "subtitle": "S", "graphics": "G"
        }.get(track_type, "T")

    def refresh_state(self, palette: ThemePalette) -> None:
        """Met à jour la pastille de type si la palette change."""
        swatch_color = _color_for_track_type(self.track.type, palette)
        if hasattr(self, "_swatch") and self._swatch is not None:
            self._swatch.setStyleSheet(
                f"background: {swatch_color}; border-radius: {Radius.pill}px;"
            )
