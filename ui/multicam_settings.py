"""Réglages d'une source Multicam : angles (nom, couleur, décalage, état de synchronisation), politique audio, relink.

La boîte ne modifie jamais le projet : chaque réglage émet un signal que la fenêtre applique (une entrée d'historique par
réglage, annulable), puis la boîte se relit (``set_state``). Elle reste ouverte pendant qu'on règle la synchronisation :
corriger un décalage à la main, c'est entrer une valeur et voir le résultat dans le moniteur.

Angles hors ligne : leur ligne le dit et propose « Relier… ». Les autres continuent de fonctionner.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.media_describe import describe_media
from core.multicam import angle_offset
from core.multicam_model import AudioMode, MulticamAudio, SyncStatus
from core.multicam_ops import angle_has_audio
from core.project_model import Project
from ui.design_system import DIALOG_MARGINS, Spacing
from ui.i18n import translate
from ui.keyboard_navigation import set_single_default
from ui.theme import active_palette, label_style


class _AngleRow(QFrame):
    """Une ligne d'angle : couleur, nom, décalage, état de synchronisation, relink, suppression."""

    def __init__(self, owner: MulticamSettingsDialog, angle, offset: float | None, tooltip: str, offline: bool,
                 asset_id: str, removable: bool) -> None:
        super().__init__()
        self._owner = owner
        self._angle_id = angle.id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)
        palette = active_palette()
        colors = palette.angle_colors
        self.color_button = QToolButton()
        self.color_button.setFixedSize(22, 22)
        self.color_button.setStyleSheet(
            f"QToolButton {{ background: {colors[angle.color_index % len(colors)]}; border: 1px solid {palette.text}; "
            f"border-radius: 4px; }}"
        )
        self.color_button.setToolTip(translate("multicam.settings.color"))
        self.color_button.setAccessibleName(translate("multicam.settings.color_of", name=angle.name))
        self.color_button.clicked.connect(lambda: owner.color_requested.emit(angle.id, angle.color_index + 1))
        layout.addWidget(self.color_button)
        self.name_edit = QLineEdit(angle.name)
        self.name_edit.setMaxLength(60)
        self.name_edit.setToolTip(tooltip)
        self.name_edit.setAccessibleName(translate("multicam.dialog.angle_name"))
        self.name_edit.setMinimumWidth(110)
        self.name_edit.editingFinished.connect(self._rename)
        layout.addWidget(self.name_edit, 2)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setDecimals(3)
        self.offset_spin.setRange(0.0, 86400.0)
        self.offset_spin.setSingleStep(0.04)
        self.offset_spin.setSuffix(translate("multicam.settings.seconds_suffix"))
        self.offset_spin.setAccessibleName(translate("multicam.settings.offset"))
        self.offset_spin.setToolTip(translate("multicam.settings.offset"))
        self.offset_spin.setEnabled(offset is not None)
        self.offset_spin.setValue(offset or 0.0)
        self.offset_spin.setMinimumWidth(96)
        self.offset_spin.editingFinished.connect(self._offset_changed)
        layout.addWidget(self.offset_spin)
        self.status_label = QLabel(self._status_text(angle, offline))
        self.status_label.setStyleSheet(
            f"color: {palette.danger if offline or angle.sync_status is SyncStatus.FAILED else palette.muted};"
        )
        self.status_label.setMinimumWidth(0)
        layout.addWidget(self.status_label, 2)
        self.relink_button = QPushButton(translate("multicam.settings.relink"))
        self.relink_button.setVisible(offline and bool(asset_id))
        self.relink_button.clicked.connect(lambda: owner.relink_requested.emit(asset_id))
        layout.addWidget(self.relink_button)
        self.remove_button = QToolButton()
        self.remove_button.setText("×")  # i18n-ignore: signe de suppression, nommé par accessibleName
        self.remove_button.setAutoRaise(True)
        self.remove_button.setToolTip(translate("multicam.dialog.remove"))
        self.remove_button.setAccessibleName(translate("multicam.dialog.remove"))
        self.remove_button.setEnabled(removable)
        self.remove_button.clicked.connect(lambda: owner.remove_requested.emit(angle.id))
        layout.addWidget(self.remove_button)

    @staticmethod
    def _status_text(angle, offline: bool) -> str:
        if offline:
            return translate("multicam.viewer.offline")
        text = translate(f"multicam.sync.{angle.sync_status.value}")
        if angle.sync_confidence is not None:
            text += f" ({round(angle.sync_confidence * 100)} %)"
        return text

    def _rename(self) -> None:
        name = self.name_edit.text().strip()
        self._owner.rename_requested.emit(self._angle_id, name)

    def _offset_changed(self) -> None:
        self._owner.offset_requested.emit(self._angle_id, float(self.offset_spin.value()))


class MulticamSettingsDialog(QDialog):
    """« Réglages Multicam » : un signal par réglage, la fenêtre applique et rappelle :meth:`set_state`."""

    rename_requested = Signal(str, str)         # (angle, nouveau nom)
    color_requested = Signal(str, int)          # (angle, rang de palette)
    offset_requested = Signal(str, float)       # (angle, décalage en secondes)
    audio_requested = Signal(object)            # MulticamAudio
    add_requested = Signal(str)                 # asset_id
    remove_requested = Signal(str)              # angle
    resync_requested = Signal()
    relink_requested = Signal(str)              # asset_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("multicamSettingsDialog")
        self.setWindowTitle(translate("multicam.settings.title"))
        self.setMinimumWidth(560)
        self.setModal(False)
        self._rows: list[_AngleRow] = []
        self._audio_options: list[MulticamAudio] = []
        self._blocking = False
        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(Spacing.sm)
        self.source_label = QLabel()
        self.source_label.setStyleSheet(label_style(13, "text", 700))
        root.addWidget(self.source_label)
        header = QLabel(translate("multicam.settings.angles"))
        header.setStyleSheet(label_style(11, "muted", 700))
        root.addWidget(header)
        self._host = QWidget()
        self._rows_layout = QVBoxLayout(self._host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(Spacing.xs)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFocusPolicy(Qt.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll)
        audio_row = QHBoxLayout()
        audio_label = QLabel(translate("multicam.summary.audio"))
        audio_label.setStyleSheet(label_style(11, "muted", 700))
        audio_row.addWidget(audio_label)
        self.audio_combo = QComboBox()
        self.audio_combo.setAccessibleName(translate("multicam.summary.audio"))
        self.audio_combo.currentIndexChanged.connect(self._audio_changed)
        audio_row.addWidget(self.audio_combo, 1)
        root.addLayout(audio_row)
        actions = QHBoxLayout()
        self.add_button = QToolButton()
        self.add_button.setText(translate("multicam.dialog.add_source"))
        self.add_button.setPopupMode(QToolButton.InstantPopup)
        self._add_menu = QMenu(self.add_button)
        self.add_button.setMenu(self._add_menu)
        actions.addWidget(self.add_button)
        self.resync_button = QPushButton(translate("multicam.settings.resync"))
        self.resync_button.setToolTip(translate("multicam.method.audio.tip"))
        self.resync_button.clicked.connect(self.resync_requested.emit)
        actions.addWidget(self.resync_button)
        actions.addStretch(1)
        root.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close = buttons.button(QDialogButtonBox.Close)
        close.setText(translate("multicam.settings.close"))
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        root.addWidget(buttons)
        set_single_default(self, close)

    # -- état ------------------------------------------------------------------------------------------------------------

    def set_state(self, project: Project, sequence_id: str) -> None:
        """Relit la source ``sequence_id`` du projet : lignes d'angles, politique audio, angles ajoutables."""
        sequence = project.get_sequence(sequence_id)
        source = sequence.multicam if sequence is not None else None
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        if sequence is None or source is None:
            self.source_label.setText("")
            return
        self.source_label.setText(sequence.name)
        assets = {asset.id: asset for asset in project.media_assets}
        from core.cache_keys import file_exists

        used: set[str] = set()
        for angle in source.angles:
            track = next((item for item in sequence.tracks if item.id == angle.track_id), None)
            clip = track.clips[0] if track is not None and track.clips else None
            asset = assets.get(clip.asset_id) if clip is not None else None
            if asset is not None:
                used.add(asset.id)
            offline = clip is not None and not clip.is_nested and (asset is None or not file_exists(asset.path))
            tooltip = "\n".join(
                f"{translate('multicam.info.' + key)} : {value}" for key, value in describe_media(asset, decimal=",")
            ) if asset is not None else ""
            row = _AngleRow(
                self, angle, angle_offset(sequence, angle), tooltip, offline, asset.id if asset is not None else "",
                removable=len(source.angles) > 1,
            )
            self._rows.append(row)
            self._rows_layout.addWidget(row)
        self._fill_audio(source, sequence, project)
        self._add_menu.clear()
        candidates = [a for a in project.media_assets if a.id not in used and a.media_type in {"video", "audio"}]
        for asset in candidates:
            self._add_menu.addAction(asset.name).triggered.connect(
                lambda _checked=False, asset_id=asset.id: self.add_requested.emit(asset_id)
            )
        self.add_button.setEnabled(bool(candidates))
        self.resync_button.setEnabled(len(source.angles) >= 2)
        height = min(len(self._rows), 7) * 34 + 6
        self._scroll.setMinimumHeight(height)
        self._scroll.setMaximumHeight(height)

    def _fill_audio(self, source, sequence, project: Project) -> None:
        self._blocking = True
        self.audio_combo.clear()
        self._audio_options = [MulticamAudio(AudioMode.FOLLOW_VIDEO, ())]
        self.audio_combo.addItem(translate("multicam.audio.follow"))
        capable = [angle for angle in source.angles if angle_has_audio(project, sequence, angle)]   # pas de caméra muette
        for angle in capable:
            self._audio_options.append(MulticamAudio(AudioMode.FIXED, (angle.id,)))
            self.audio_combo.addItem(translate("multicam.audio.fixed", name=angle.name))
        if len(capable) > 1:
            self._audio_options.append(MulticamAudio(AudioMode.MIX, tuple(angle.id for angle in capable)))
            self.audio_combo.addItem(translate("multicam.audio.mix"))
        current = source.audio
        index = next((i for i, option in enumerate(self._audio_options) if option == current), -1)
        if index < 0 and current.mode is AudioMode.MIX:
            index = len(self._audio_options) - 1 if len(capable) > 1 else 0   # mixage partiel : on affiche « tout mixer »
        self.audio_combo.setCurrentIndex(max(0, index))
        self._blocking = False

    def _audio_changed(self, index: int) -> None:
        if self._blocking or not 0 <= index < len(self._audio_options):
            return
        self.audio_requested.emit(self._audio_options[index])

    def row_widgets(self) -> list[_AngleRow]:
        return list(self._rows)


__all__ = ["MulticamSettingsDialog"]
