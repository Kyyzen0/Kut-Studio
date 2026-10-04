"""Boîtes de dialogue du Multicam : création (compacte) et résultat de la synchronisation.

La boîte de création ne pose que ce qu'il faut : un nom, la liste des sources (renommables, retirables, extensible à un
enregistreur audio) et **la méthode de synchronisation**. Aucun réglage technique par défaut. Une méthode que les sources ne
permettent pas (pas de timecode, pas de repère, pas de son) est grisée, avec la raison en infobulle.

La boîte de résultat ne s'ouvre que si elle sert à quelque chose : une synchronisation incertaine ou ratée, ou une politique
audio que la logique ne peut pas trancher seule. Elle dit toujours la vérité sur la fiabilité de chaque angle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QRadioButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.multicam_model import MulticamAudio, SyncMethod, SyncStatus
from ui.design_system import DIALOG_MARGINS, Spacing
from ui.i18n import translate
from ui.keyboard_navigation import set_single_default
from ui.theme import active_palette, label_style

MAX_VISIBLE_ROWS = 6
"""Au-delà, la liste des sources défile au lieu d'allonger la boîte."""

METHOD_ORDER: tuple[SyncMethod, ...] = (
    SyncMethod.AUDIO, SyncMethod.TIMECODE, SyncMethod.MARKER, SyncMethod.POSITIONS, SyncMethod.START, SyncMethod.MANUAL,
)


@dataclass
class SourceRow:
    """Une source proposée : média de la bibliothèque ou clip de la timeline."""

    key: str
    name: str
    kind: str = "video"
    info: str = ""
    tooltip: str = ""
    has_audio: bool = True
    start_seconds: float | None = None
    """Heure de début lue dans le timecode du média (``None`` : pas de timecode exploitable)."""
    marker_offset: float | None = None
    """Position qui aligne le repère du clip (clip de la timeline portant un repère), ``None`` sinon."""


@dataclass(frozen=True)
class CreationChoice:
    """Réponse de la boîte de création."""

    name: str
    method: SyncMethod
    sources: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    """``(clé de la source, nom de l'angle)`` dans l'ordre affiché (l'angle 1 est le premier)."""


def available_methods(rows: list[SourceRow], *, from_timeline: bool) -> dict[SyncMethod, str]:
    """Pour chaque méthode : ``""`` si elle est disponible, sinon la clé i18n de la raison."""
    reasons: dict[SyncMethod, str] = {}
    reasons[SyncMethod.AUDIO] = "" if sum(1 for row in rows if row.has_audio) >= 2 else "multicam.method.audio.unavailable"
    reasons[SyncMethod.TIMECODE] = (
        "" if sum(1 for row in rows if row.start_seconds is not None) >= 2 else "multicam.method.timecode.unavailable"
    )
    reasons[SyncMethod.MARKER] = (
        "" if from_timeline and rows and all(row.marker_offset is not None for row in rows)
        else "multicam.method.marker.unavailable"
    )
    reasons[SyncMethod.POSITIONS] = "" if from_timeline else "multicam.method.positions.unavailable"
    reasons[SyncMethod.START] = ""
    reasons[SyncMethod.MANUAL] = ""
    return reasons


def default_method(reasons: dict[SyncMethod, str], *, from_timeline: bool) -> SyncMethod:
    """Méthode proposée : positions actuelles depuis la timeline ; sinon timecode s'il existe, sinon le son."""
    if from_timeline and not reasons[SyncMethod.POSITIONS]:
        return SyncMethod.POSITIONS
    for method in (SyncMethod.TIMECODE, SyncMethod.AUDIO):
        if not reasons[method]:
            return method
    return SyncMethod.START


class _RowWidget(QFrame):
    """Une ligne de source : type, nom d'angle modifiable, résumé du média, bouton « Retirer »."""

    def __init__(self, row: SourceRow, on_remove, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = row
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)
        kind = QLabel(translate("multicam.kind.audio" if row.kind == "audio" else "multicam.kind.video"))
        kind.setStyleSheet(label_style(10, "muted", 700))
        kind.setMinimumWidth(40)
        layout.addWidget(kind)
        self.name_edit = QLineEdit(row.name)
        self.name_edit.setMaxLength(60)
        self.name_edit.setToolTip(row.tooltip)
        self.name_edit.setAccessibleName(translate("multicam.dialog.angle_name"))
        self.name_edit.setMinimumWidth(110)
        layout.addWidget(self.name_edit, 2)
        info = QLabel(row.info)
        info.setStyleSheet(label_style(10, "muted", 500))
        info.setToolTip(row.tooltip)
        info.setMinimumWidth(0)
        layout.addWidget(info, 3)
        self.remove_button = QToolButton()
        self.remove_button.setText("×")  # i18n-ignore: signe de suppression, nommé par accessibleName
        self.remove_button.setAutoRaise(True)
        self.remove_button.setToolTip(translate("multicam.dialog.remove"))
        self.remove_button.setAccessibleName(translate("multicam.dialog.remove"))
        self.remove_button.clicked.connect(lambda: on_remove(self))
        layout.addWidget(self.remove_button)

    def angle_name(self) -> str:
        return self.name_edit.text().strip() or self.row.name


class MulticamCreateDialog(QDialog):
    """« Créer une séquence Multicam… » : nom, sources, méthode de synchronisation."""

    def __init__(
        self,
        rows: list[SourceRow],
        *,
        default_name: str,
        from_timeline: bool,
        addable: list[SourceRow] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("multicamCreateDialog")
        self.setWindowTitle(translate("multicam.dialog.create_title"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self._from_timeline = from_timeline
        self._addable = list(addable or [])
        self._rows: list[_RowWidget] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(Spacing.sm)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignLeft)
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setMaxLength(80)
        form.addRow(self._caption(translate("multicam.dialog.name")), self.name_edit)
        root.addLayout(form)

        root.addWidget(self._caption(translate("multicam.dialog.sources")))
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(Spacing.xs)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFocusPolicy(Qt.NoFocus)      # Tab saute la zone défilante : ses champs, eux, se parcourent
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll)
        self.add_button = QToolButton()
        self.add_button.setText(translate("multicam.dialog.add_source"))
        self.add_button.setPopupMode(QToolButton.InstantPopup)
        self.add_button.setAutoRaise(True)
        self._add_menu = QMenu(self.add_button)
        self.add_button.setMenu(self._add_menu)
        root.addWidget(self.add_button, 0, Qt.AlignLeft)

        root.addWidget(self._caption(translate("multicam.dialog.method")))
        self._method_group = QButtonGroup(self)
        self._method_buttons: dict[SyncMethod, QRadioButton] = {}
        for method in METHOD_ORDER:
            button = QRadioButton(translate(f"multicam.method.{method.value}"))
            button.setObjectName(f"method_{method.value}")
            self._method_buttons[method] = button
            self._method_group.addButton(button)
            root.addWidget(button)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(label_style(11, "muted", 500))
        root.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText(translate("multicam.dialog.create"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(translate("multicam.dialog.cancel"))
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        set_single_default(self, self.ok_button)

        for row in rows:
            self._append_row(row)
        self._refresh()

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(label_style(11, "muted", 700))
        return label

    # -- lignes ------------------------------------------------------------------------------------------------------

    def _append_row(self, row: SourceRow) -> None:
        widget = _RowWidget(row, self._remove_row)
        self._rows.append(widget)
        self._rows_layout.addWidget(widget)

    def _remove_row(self, widget: _RowWidget) -> None:
        if widget in self._rows and len(self._rows) > 1:
            self._rows.remove(widget)
            widget.setParent(None)
            widget.deleteLater()
            self._addable.append(widget.row)
            self._refresh()

    def _refresh(self) -> None:
        rows = [item.row for item in self._rows]
        reasons = available_methods(rows, from_timeline=self._from_timeline)
        current = self.method()
        for method, button in self._method_buttons.items():
            reason = reasons[method]
            button.setEnabled(not reason)
            button.setToolTip(translate(reason) if reason else translate(f"multicam.method.{method.value}.tip"))
        if current is None or reasons.get(current):
            self._method_buttons[default_method(reasons, from_timeline=self._from_timeline)].setChecked(True)
        self._add_menu.clear()
        for candidate in self._addable:
            action = self._add_menu.addAction(candidate.name)
            action.triggered.connect(lambda _checked=False, item=candidate: self._add_source(item))
        self.add_button.setEnabled(bool(self._addable))
        self.ok_button.setEnabled(len(self._rows) >= 2)
        self.status_label.setText("" if len(self._rows) >= 2 else translate("multicam.message.need_two"))
        height = min(len(self._rows), MAX_VISIBLE_ROWS) * 34 + 6
        self._scroll.setMinimumHeight(height)
        self._scroll.setMaximumHeight(height)

    def _add_source(self, row: SourceRow) -> None:
        if row in self._addable:
            self._addable.remove(row)
            self._append_row(row)
            self._refresh()

    # -- réponse -------------------------------------------------------------------------------------------------------

    def method(self) -> SyncMethod | None:
        for method, button in self._method_buttons.items():
            if button.isChecked():
                return method
        return None

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        if len(self._rows) < 2 or self.method() is None:
            return
        self.accept()

    def choice(self) -> CreationChoice:
        method = self.method() or SyncMethod.START
        return CreationChoice(
            name=self.name_edit.text().strip(),
            method=method,
            sources=tuple((item.row.key, item.angle_name()) for item in self._rows),
        )


@dataclass(frozen=True)
class SummaryRow:
    """Une ligne du résultat de synchronisation."""

    name: str
    status: SyncStatus
    offset: float | None = None
    detail: str = ""
    reference: bool = False


class SyncSummaryDialog(QDialog):
    """Résultat de la synchronisation : fiabilité de chaque angle et, si nécessaire, choix du son."""

    def __init__(
        self,
        rows: list[SummaryRow],
        *,
        audio_choices: list[tuple[str, MulticamAudio]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("multicamSummaryDialog")
        self.setWindowTitle(translate("multicam.summary.title"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self._choices = list(audio_choices or [])
        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(Spacing.sm)
        palette = active_palette()
        for row in rows:
            label = QLabel(self._row_text(row))
            label.setWordWrap(True)
            color = {
                SyncStatus.EXCELLENT: palette.success, SyncStatus.GOOD: palette.success, SyncStatus.MANUAL: palette.text,
                SyncStatus.NONE: palette.muted, SyncStatus.UNCERTAIN: palette.warning, SyncStatus.FAILED: palette.danger,
            }[row.status]
            label.setStyleSheet(f"color: {color};")
            label.setToolTip(row.detail)
            root.addWidget(label)
        if any(row.status in {SyncStatus.UNCERTAIN, SyncStatus.FAILED} for row in rows):
            hint = QLabel(translate("multicam.summary.hint"))
            hint.setWordWrap(True)
            hint.setStyleSheet(label_style(11, "muted", 500))
            root.addWidget(hint)
        self.audio_combo: QComboBox | None = None
        if self._choices:
            form = QFormLayout()
            form.setContentsMargins(0, Spacing.sm, 0, 0)
            self.audio_combo = QComboBox()
            for text, _audio in self._choices:
                self.audio_combo.addItem(text)
            form.addRow(self._caption(translate("multicam.summary.audio")), self.audio_combo)
            root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = buttons.button(QDialogButtonBox.Ok)
        ok.setText(translate("multicam.dialog.create"))
        buttons.button(QDialogButtonBox.Cancel).setText(translate("multicam.dialog.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        set_single_default(self, ok)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(label_style(11, "muted", 700))
        return label

    @staticmethod
    def _row_text(row: SummaryRow) -> str:
        status = translate("multicam.sync.reference") if row.reference else translate(f"multicam.sync.{row.status.value}")
        text = f"{row.name} — {status}"
        if row.offset is not None and not row.reference:
            text += " · " + translate("multicam.summary.offset", seconds=f"{row.offset:.3f}")
        return text

    def audio(self) -> MulticamAudio | None:
        """Politique audio choisie, ``None`` s'il n'y avait rien à choisir."""
        if self.audio_combo is None:
            return None
        return self._choices[self.audio_combo.currentIndex()][1]


__all__ = [
    "METHOD_ORDER",
    "CreationChoice",
    "MulticamCreateDialog",
    "SourceRow",
    "SummaryRow",
    "SyncSummaryDialog",
    "available_methods",
    "default_method",
]
