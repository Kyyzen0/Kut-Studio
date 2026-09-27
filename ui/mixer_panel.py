"""Panneau Mixeur audio de Kut-Studio.

Le mixeur est un panneau dockable de l'espace de travail : il suit les
mêmes règles que les autres (masquable, détachable, restorable). Il
n'affiche que des **contrôles réels** adossés au modèle : volume de
piste, panoramique, mute, solo, arm, plus une tranche Master.

Aucun vumètre n'est affiché : le niveau réel n'est mesuré nulle part à
ce jour. Un vumètre dessiné à partir d'une estimation donnerait une
indication fausse — elle n'apparaîtra qu.le jour où un relevé de niveau
existera (voir :class:`MixerPanel.level_provider`).

Le panneau ne modifie jamais le projet par lui-même : il émet des
signaux, et c'est :class:`~ui.main_window.MainWindow` qui applique le
changement, enregistre l'historique et marque le projet modifié. Les
pistes verrouillées sont refusées ici **et** refusées à nouveau côté
fenêtre : la règle est appliquée au plus près de la donnée.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MAX_GAIN_DB, MIN_GAIN_DB, Project, Track
from ui.design_system import Iconography, Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconLabel, IconName
from ui.theme import COLORS, label_style


#: Position du panoramique au curseur (0 = gauche, 100 = droite).
_PAN_MIN = -100
_PAN_MAX = 100

#: Hauteur minimale du panneau : une tranche complète (nom, fader,
#: panoramique, boutons) doit tenir sans être rognée.
MIN_PANEL_HEIGHT = 320


def _panel_stylesheet() -> str:
    """Feuille de style du panneau, couleurs résolues à la construction.

    Elle est déclarée localement (et non seulement dans la feuille
    globale) car une feuille posée sur un widget fait autorité sur tout
    son sous-arbre : sans cela les tranches perdraient leur fond et
    leurs boutons perdraient leurs états.
    """
    return (
        f"QWidget#mixer_panel {{ background: {COLORS['panel']};"
        f" color: {COLORS['text']}; }}"
        f"QFrame#mixerStrip {{ background: {COLORS['panel']};"
        f" border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
        f"QFrame#mixerMasterBar {{ background: {COLORS['panel_alt']};"
        f" border-top: 1px solid {COLORS['border']}; }}"
        f"QScrollArea {{ border: none; background: transparent; }}"
    )


class MixerStrip(QFrame):
    """Une tranche de mixage : une piste audio ou la sortie Master.

    Attributes:
        track_id: Identifiant de la piste, ``None`` pour le Master.
    """

    volume_changed = Signal(str, float)
    pan_changed = Signal(str, float)
    mute_toggled = Signal(str, bool)
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    reset_requested = Signal(str)

    def __init__(
        self,
        name: str,
        track_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.track_id = track_id
        self._name = name
        self._locked = False
        self.setObjectName("mixerStrip")
        # Pas de feuille de style locale : le fond de la tranche est
        # déclaré dans la feuille globale, sinon les boutons enfants
        # perdraient leurs règles (hover, checked, icônes).
        self.setFixedWidth(84)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm
        )
        layout.setSpacing(Spacing.xs)
        layout.setAlignment(Qt.AlignTop)

        # Nom de piste (élidé si besoin) + cadenas si verrouillée.
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(label_style(11, "text", 700))
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setToolTip(name)
        layout.addWidget(self.name_label)

        self.lock_badge = IconLabel(IconName.LOCK, size=Iconography.sm)
        self.lock_badge.set_color(_color(COLORS["warning"]))
        self.lock_badge.hide()
        self.lock_badge.setToolTip(translate("mixer.locked"))
        layout.addWidget(self.lock_badge, 0, Qt.AlignHCenter)

        # Fader vertical de volume, en dB.
        self.volume_slider = _VerticalFader()
        self.volume_slider.setRange(int(MIN_GAIN_DB), int(MAX_GAIN_DB))
        self.volume_slider.setValue(0)
        self.volume_slider.valueChanged.connect(
            lambda value: self.volume_changed.emit(
                self.track_id or "", float(value)
            )
        )
        layout.addWidget(self.volume_slider, 1, Qt.AlignHCenter)

        self.volume_value = QLabel("0.0 dB")
        self.volume_value.setStyleSheet(label_style(10, "muted", 600))
        self.volume_value.setAlignment(Qt.AlignCenter)
        self.volume_value.setMinimumWidth(64)
        layout.addWidget(self.volume_value)

        # Panoramique horizontal, avec repos centré.
        self.pan_slider = QSlider(Qt.Horizontal)
        self.pan_slider.setRange(_PAN_MIN, _PAN_MAX)
        self.pan_slider.setValue(0)
        self.pan_slider.setMaximumWidth(64)
        self.pan_slider.setToolTip(translate("mixer.pan"))
        self.pan_slider.valueChanged.connect(
            lambda value: self.pan_changed.emit(
                self.track_id or "", value / 100.0
            )
        )
        layout.addWidget(self.pan_slider, 0, Qt.AlignHCenter)

        # Boutons d'état : M / S / A, puis réinitialisation.
        buttons = QHBoxLayout()
        buttons.setSpacing(Spacing.xs)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.mute_button = IconButton(
            icon=IconName.AUDIO_MUTE,
            tooltip=translate("mixer.track_mute", name=name)
            if track_id
            else translate("mixer.master_mute"),
            checkable=True,
            size=22,
        )
        self.mute_button.toggled.connect(
            lambda checked: self.mute_toggled.emit(
                self.track_id or "", checked
            )
        )
        buttons.addWidget(self.mute_button)
        layout.addLayout(buttons)

        master_buttons = QHBoxLayout()
        master_buttons.setSpacing(Spacing.xs)
        master_buttons.setContentsMargins(0, 0, 0, 0)
        self.solo_button = IconButton(
            icon=IconName.AUDIO_SOLO,
            tooltip=translate("mixer.track_solo", name=name),
            checkable=True,
            size=22,
        )
        self.solo_button.toggled.connect(
            lambda checked: self.solo_toggled.emit(
                self.track_id or "", checked
            )
        )
        self.arm_button = IconButton(
            icon=IconName.AUDIO_ARM,
            tooltip=translate("mixer.track_arm", name=name),
            checkable=True,
            size=22,
        )
        self.arm_button.toggled.connect(
            lambda checked: self.arm_toggled.emit(
                self.track_id or "", checked
            )
        )
        master_buttons.addWidget(self.solo_button)
        master_buttons.addWidget(self.arm_button)
        layout.addLayout(master_buttons)

        self.reset_button = IconButton(
            icon=IconName.PANEL_RESET,
            tooltip=translate("mixer.reset"),
            size=22,
        )
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(self.track_id or "")
        )
        layout.addWidget(self.reset_button, 0, Qt.AlignHCenter)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_track(self, *, name: str, volume_db: float, pan: float, locked: bool) -> None:
        """Met à jour la tranche depuis le modèle, sans émettre de signal."""
        self._name = name
        self._locked = locked
        self.name_label.setText(name)
        self.name_label.setToolTip(name)
        self.lock_badge.setVisible(locked)
        self.name_label.setStyleSheet(
            label_style(11, "disabled_text" if locked else "text", 700)
        )
        self._apply_volume(volume_db)
        self._apply_pan(pan)
        for widget in (
            self.volume_slider,
            self.pan_slider,
            self.mute_button,
            self.solo_button,
            self.arm_button,
            self.reset_button,
        ):
            widget.setEnabled(not locked)

    def set_states(self, *, muted: bool, solo: bool, armed: bool) -> None:
        """Synchronise les boutons d'état sans réémettre de changement."""
        for button, value in (
            (self.mute_button, muted),
            (self.solo_button, solo),
            (self.arm_button, armed),
        ):
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)

    def is_locked(self) -> bool:
        return self._locked

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _apply_volume(self, value: float) -> None:
        bounded = int(round(max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(value)))))
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(bounded)
        self.volume_slider.blockSignals(False)
        self._update_volume_label(bounded)

    def _update_volume_label(self, value: int) -> None:
        if value <= int(MIN_GAIN_DB):
            self.volume_value.setText(translate("mixer.mute"))
        else:
            self.volume_value.setText(f"{value:+.1f} dB".replace("+", "+"))

    def _apply_pan(self, value: float) -> None:
        position = int(round(max(_PAN_MIN, min(_PAN_MAX, float(value) * 100))))
        self.pan_slider.blockSignals(True)
        self.pan_slider.setValue(position)
        self.pan_slider.blockSignals(False)
        self.pan_slider.setToolTip(_pan_label(value))


def _pan_label(value: float) -> str:
    """Infobulle lisible du panoramique."""
    if abs(value) < 0.02:
        return translate("mixer.pan_center")
    if value < 0:
        return f"{translate('mixer.pan_left')} {abs(value) * 100:.0f}%"
    return f"{translate('mixer.pan_right')} {value * 100:.0f}%"


class _VerticalFader(QSlider):
    """Fader vertical de volume, en décibels.

    Qt ne sait dessiner un fader vertical qu'avec des feuilles de style ;
    on fournit donc une règle explicite pour que le poignée reste lisible
    dans les deux thèmes.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Vertical, parent)
        self.setObjectName("verticalFader")
        # Assez long pour être précis au manche, assez court pour que la
        # tranche entière tienne dans le panneau sans défilement.
        self.setMinimumHeight(72)
        self.setMaximumHeight(200)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setPageStep(6)
        self.setTickInterval(6)
        self.setTickPosition(QSlider.TicksLeft)
        self.setStyleSheet(self._style())

    @staticmethod
    def _style() -> str:
        return (
            f"QSlider#verticalFader {{ background: transparent; }}"
            f"QSlider::groove:vertical {{"
            f" background: {COLORS['border']}; width: 4px; border-radius: 2px; }}"
            f"QSlider::sub-page:vertical {{"
            f" background: {COLORS['accent']}; border-radius: 2px; }}"
            f"QSlider::handle:vertical {{"
            f" background: {COLORS['text']}; width: 12px; height: 12px;"
            f" margin: 0 -4px; border-radius: 6px; }}"
            f"QSlider::handle:vertical:hover {{"
            f" background: {COLORS['accent_hover']}; }}"
            f"QSlider::handle:vertical:disabled {{"
            f" background: {COLORS['disabled_text']}; }}"
            f"QSlider::add-page:vertical {{ background: {COLORS['border']}; }}"
        )


def _color(value: str):
    from PySide6.QtGui import QColor

    return QColor(value)


class _MasterBar(QFrame):
    """Barre de sortie Master, compacte et horizontale.

    Une tranche verticale prendrait une place excessive pour trois
    contrôles ; la barre rend le Master lisible sans voler de hauteur
    aux tranches de pistes.
    """

    volume_changed = Signal(float)
    mute_toggled = Signal(bool)
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mixerMasterBar")
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Spacing.md, Spacing.sm, Spacing.md, Spacing.sm
        )
        layout.setSpacing(Spacing.md)

        icon = IconLabel(IconName.AUDIO_VOLUME, size=Iconography.md)
        icon.set_color(_color(COLORS["muted_strong"]))
        layout.addWidget(icon)

        self.name_label = QLabel(translate("mixer.master"))
        self.name_label.setStyleSheet(label_style(11, "text", 700))
        layout.addWidget(self.name_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(int(MIN_GAIN_DB), int(MAX_GAIN_DB))
        self.volume_slider.setValue(0)
        self.volume_slider.setMinimumWidth(140)
        self.volume_slider.valueChanged.connect(
            lambda value: self.volume_changed.emit(float(value))
        )
        layout.addWidget(self.volume_slider, 1)

        self.volume_value = QLabel("0.0 dB")
        self.volume_value.setStyleSheet(label_style(11, "muted", 600))
        self.volume_value.setAlignment(Qt.AlignRight)
        self.volume_value.setFixedWidth(64)
        layout.addWidget(self.volume_value)

        self.mute_button = IconButton(
            icon=IconName.AUDIO_MUTE,
            tooltip=translate("mixer.master_mute"),
            checkable=True,
            size=26,
        )
        self.mute_button.toggled.connect(self.mute_toggled)
        layout.addWidget(self.mute_button)

        self.reset_button = IconButton(
            icon=IconName.PANEL_RESET,
            tooltip=translate("mixer.master_reset"),
            size=26,
        )
        self.reset_button.clicked.connect(self.reset_requested)
        layout.addWidget(self.reset_button)

    def set_master(self, gain_db: float, muted: bool) -> None:
        """Met à jour gain et coupure globale, sans réémettre de signal."""
        bounded = int(round(max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(gain_db)))))
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(bounded)
        self.volume_slider.blockSignals(False)
        if bounded <= int(MIN_GAIN_DB):
            self.volume_value.setText(translate("mixer.mute"))
        else:
            self.volume_value.setText(f"{bounded:+.1f} dB")
        self.mute_button.blockSignals(True)
        self.mute_button.setChecked(bool(muted))
        self.mute_button.blockSignals(False)

    def retranslate(self) -> None:
        self.name_label.setText(translate("mixer.master"))
        self.mute_button.setToolTip(translate("mixer.master_mute"))
        self.reset_button.setToolTip(translate("mixer.master_reset"))


class MixerPanel(QWidget):
    """Panneau de mixage dockable."""

    volume_changed = Signal(str, float)
    pan_changed = Signal(str, float)
    mute_toggled = Signal(str, bool)
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    reset_requested = Signal(str)
    master_volume_changed = Signal(float)
    master_mute_toggled = Signal(bool)
    master_reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mixer_panel")
        self.setMinimumHeight(MIN_PANEL_HEIGHT)
        # Les fonds des enfants sont déclarés **ici** et pas seulement
        # dans la feuille globale : une feuille locale fait autorité sur
        # son sous-arbre, les tranches resteraient sinon sans fond.
        self.setStyleSheet(_panel_stylesheet())
        self._strips: dict[str, MixerStrip] = {}
        self._master_bar: MixerPanel._MasterBar | None = None
        #: Fournisseur optionnel de niveaux réels. Tant qu'il n'est pas
        #: branché, aucun vumètre n'est dessiné : on ne fabrique pas de
        #: mesure approximative.
        self.level_provider = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # En-tête.
        header = QWidget()
        header.setFixedHeight(40)
        header.setStyleSheet(
            f"background: {COLORS['panel']};"
            f" border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(
            Spacing.md, 0, Spacing.sm, 0
        )
        header_layout.setSpacing(Spacing.sm)
        icon = IconLabel(IconName.AUDIO_MIXER, size=Iconography.md)
        icon.set_color(_color(COLORS["muted_strong"]))
        header_layout.addWidget(icon)
        self.title_label = QLabel(translate("mixer.title"))
        self.title_label.setStyleSheet(label_style(11, "muted", 800))
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        self.solo_hint = QLabel("")
        self.solo_hint.setStyleSheet(label_style(10, "warning", 600))
        header_layout.addWidget(self.solo_hint)
        layout.addWidget(header)

        # Tranches, horizontalement défilantes.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Le défilement vertical reste possible : sur une fenêtre basse
        # une tranche peut dépasser la zone, et son contenu doit rester
        # accessible plutôt que d'être coupé.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea { border: none; }")

        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_layout = QHBoxLayout(self._body)
        self._body_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        self._body_layout.setSpacing(Spacing.md)
        self._body_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.empty_label = QLabel(translate("mixer.no_tracks"))
        self.empty_label.setStyleSheet(label_style(12, "muted", 500))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self._body_layout.addWidget(self.empty_label)
        self._body_layout.addStretch(1)
        self.scroll.setWidget(self._body)
        layout.addWidget(self.scroll, 1)

        self._master_bar = _MasterBar(self)
        self._master_bar.volume_changed.connect(self.master_volume_changed)
        self._master_bar.mute_toggled.connect(self.master_mute_toggled)
        self._master_bar.reset_requested.connect(self.master_reset_requested)
        layout.addWidget(self._master_bar)

        self._retranslate()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        """Reconstruit les tranches depuis le projet.

        L'état du panneau est un reflet du modèle : le panneau ne
        conserve aucun état de mixage propre.
        """
        for strip in self._strips.values():
            strip.setParent(None)
            strip.deleteLater()
        self._strips.clear()

        audio_tracks = [t for t in project.tracks if t.type == "audio"]
        self.empty_label.setVisible(not audio_tracks)
        for track in audio_tracks:
            strip = MixerStrip(track.name, track_id=track.id)
            strip.set_track(
                name=track.name,
                volume_db=track.volume_db,
                pan=track.pan,
                locked=track.locked,
            )
            strip.set_states(
                muted=track.muted, solo=track.solo, armed=track.armed
            )
            strip.volume_changed.connect(self.volume_changed)
            strip.pan_changed.connect(self.pan_changed)
            strip.mute_toggled.connect(self.mute_toggled)
            strip.solo_toggled.connect(self.solo_toggled)
            strip.arm_toggled.connect(self.arm_toggled)
            strip.reset_requested.connect(self.reset_requested)
            # Inséré avant l'étirement final pour rester à gauche.
            self._body_layout.insertWidget(
                self._body_layout.count() - 1, strip
            )
            self._strips[track.id] = strip

        self._refresh_solo_hint(project)

    def set_master(self, gain_db: float, muted: bool) -> None:
        """Met à jour la sortie Master (gain, coupure globale)."""
        if self._master_bar is not None:
            self._master_bar.set_master(gain_db, muted)

    def refresh_track(self, track: Track) -> None:
        """Rafraîchit une tranche après une modification externe."""
        strip = self._strips.get(track.id)
        if strip is None:
            return
        strip.set_track(
            name=track.name,
            volume_db=track.volume_db,
            pan=track.pan,
            locked=track.locked,
        )
        strip.set_states(
            muted=track.muted, solo=track.solo, armed=track.armed
        )

    def strip_count(self) -> int:
        return len(self._strips)

    def strip_for(self, track_id: str) -> MixerStrip | None:
        return self._strips.get(track_id)

    def master_bar(self) -> "_MasterBar | None":
        return self._master_bar

    # ------------------------------------------------------------------
    # Internationalisation
    # ------------------------------------------------------------------

    def _retranslate(self) -> None:
        self.title_label.setText(translate("mixer.title"))
        self.empty_label.setText(translate("mixer.no_tracks"))
        if self._master_bar is not None:
            self._master_bar.retranslate()
        for strip in self._strips.values():
            name = strip.name_label.text()
            strip.mute_button.setToolTip(translate("mixer.track_mute", name=name))
            strip.solo_button.setToolTip(translate("mixer.track_solo", name=name))
            strip.arm_button.setToolTip(translate("mixer.track_arm", name=name))
            strip.reset_button.setToolTip(translate("mixer.reset"))
            strip.lock_badge.setToolTip(translate("mixer.locked"))

    def update_translations(self) -> None:
        """Point d'entrée utilisé par ``MainWindow._retranslate_ui``."""
        self._retranslate()

    def _refresh_solo_hint(self, project: Project) -> None:
        solo = [t for t in project.tracks if t.type == "audio" and t.solo]
        self.solo_hint.setText(translate("mixer.solo_active") if solo else "")


__all__ = ["MixerPanel", "MixerStrip"]
