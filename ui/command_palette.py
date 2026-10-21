"""Palette de commandes « Rechercher une fonction » (⌘K / Ctrl+K) : la boîte, sans aucune connaissance de la fenêtre.

Le widget est un **voile** enfant de la fenêtre principale (créé une fois à la construction, jamais reparenté) qui porte une
boîte centrée en haut : champ de recherche, puces de suggestions, liste de résultats (six au plus). Il ne connaît ni les menus ni
les niveaux : la fenêtre lui donne trois fournisseurs (``entries_provider``, ``level_provider``, ``recent_provider``) et reçoit
``entry_chosen(id, switch_level)`` **après** la fermeture (le focus est déjà rendu quand la fonction s'exécute, un dialogue
qu'elle ouvre garde donc son propre focus). La recherche elle-même est dans :mod:`core.command_search` (pure, sans Qt).

Clavier : saisie directe, ↑ ↓ pour choisir, Entrée pour ouvrir, Échap pour fermer ; Tab reste dans la boîte.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from shiboken6 import isValid

from core.command_search import MAX_QUERY_CHARS, MAX_RESULTS, SearchEntry, SearchResult, recent_results, search
from core.usage_level import UsageLevel
from ui import i18n
from ui.icons import IconName, make_icon
from ui.theme import COLORS

BOX_WIDTH = 620
BOX_TOP = 72
ROW_HEIGHT = 54
_CHIP_KEYS = ("tracking", "cut", "captions", "color")


def shortcut_text(sequence: str) -> str:
    """Raccourci dans la forme native de la plateforme (⇧⌘P sous macOS, Ctrl+Maj+P ailleurs)."""
    return QKeySequence(sequence).toString(QKeySequence.NativeText) if sequence else ""


def level_name(level: UsageLevel) -> str:
    return i18n.translate(f"prefs.usage.{level.value}")


class _ResultRow(QWidget):
    """Une ligne de résultat : icône, titre, indication, raccourci et bouton d'action (« Ouvrir » / « Passer en … »)."""

    def __init__(self, result: SearchResult, on_press: Callable[[], None]) -> None:
        super().__init__()
        entry = result.entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)
        faded = not entry.enabled
        icon = QLabel()
        icon.setFixedSize(22, 22)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        if entry.icon:
            try:
                icon.setPixmap(make_icon(IconName(entry.icon)).pixmap(QSize(18, 18)))
            except ValueError:
                pass
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = QLabel(entry.title)
        self.title.setObjectName("paletteTitle")
        self.title.setProperty("faded", faded)
        self.hint = QLabel(self._hint_text(result))
        self.hint.setObjectName("paletteHint")
        for label in (self.title, self.hint):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            label.setTextInteractionFlags(Qt.NoTextInteraction)
            label.setMinimumWidth(0)
        texts.addWidget(self.title)
        texts.addWidget(self.hint)
        layout.addLayout(texts, 1)
        self.shortcut = QLabel(shortcut_text(entry.shortcut))
        self.shortcut.setObjectName("paletteShortcut")
        self.shortcut.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.shortcut)
        self.button: QPushButton | None = None
        if entry.enabled:
            self.button = QPushButton(self._button_text(result))
            self.button.setObjectName("paletteSwitch" if result.locked else "paletteOpen")
            self.button.setCursor(Qt.PointingHandCursor)
            self.button.setFocusPolicy(Qt.NoFocus)             # Entrée agit sur la ligne choisie ; Tab reste sur les contrôles
            self.button.setAccessibleName(f"{self._button_text(result)} : {entry.title}")
            if result.locked:
                self.button.setToolTip(i18n.translate("palette.switch.tooltip", level=level_name(entry.level)))
            self.button.clicked.connect(lambda _checked=False: on_press())
            layout.addWidget(self.button)
        self.setAccessibleName(entry.title)
        self.setAccessibleDescription(self.hint.text())

    @staticmethod
    def _button_text(result: SearchResult) -> str:
        if result.locked:
            return i18n.translate("palette.switch", level=level_name(result.entry.level))
        return i18n.translate("palette.open")

    @staticmethod
    def _hint_text(result: SearchResult) -> str:
        entry = result.entry
        if not entry.enabled:
            reason = entry.reason or i18n.translate("palette.disabled.unknown")
            return i18n.translate("palette.disabled.reason", reason=reason) if entry.reason else reason
        if result.locked:
            return i18n.translate("palette.locked.hint", hint=entry.hint, level=level_name(entry.level)) if entry.hint else (
                level_name(entry.level)
            )
        return entry.hint


class CommandPalette(QWidget):
    """Voile + boîte de recherche. Voir le docstring du module."""

    entry_chosen = Signal(str, bool)
    closed = Signal()

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setObjectName("commandPalette")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName(i18n.translate("palette.dialog.name"))
        self.entries_provider: Callable[[], list[SearchEntry]] = lambda: []
        self.level_provider: Callable[[], UsageLevel] = lambda: UsageLevel.ADVANCED
        self.recent_provider: Callable[[], list[str]] = lambda: []
        self._entries: list[SearchEntry] = []
        self._results: list[SearchResult] = []
        self._previous_focus: weakref.ReferenceType[QWidget] | None = None
        self._window = window
        self._focus_hooked = False

        self.box = QFrame(self)
        self.box.setObjectName("paletteBox")
        shadow = QGraphicsDropShadowEffect(self.box)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.box.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self.box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(14, 12, 14, 8)
        header.setSpacing(10)
        self.search_icon = QLabel()
        self.search_icon.setPixmap(make_icon(IconName.SEARCH).pixmap(QSize(18, 18)))
        self.search_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.search_icon)
        self.field = QLineEdit()
        self.field.setObjectName("paletteField")
        self.field.setMaxLength(MAX_QUERY_CHARS)
        self.field.setClearButtonEnabled(True)
        self.field.textChanged.connect(self._refresh)
        self.field.installEventFilter(self)
        header.addWidget(self.field, 1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("paletteClose")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.clicked.connect(self.close_palette)
        header.addWidget(self.close_button)
        outer.addLayout(header)

        chips = QHBoxLayout()
        chips.setContentsMargins(14, 0, 14, 10)
        chips.setSpacing(6)
        self.try_label = QLabel()
        self.try_label.setObjectName("paletteHint")
        chips.addWidget(self.try_label)
        self.chips: list[QPushButton] = []
        for key in _CHIP_KEYS:
            chip = QPushButton()
            chip.setObjectName("paletteChip")
            chip.setProperty("chip_key", key)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, button=chip: self._use_chip(button))
            chips.addWidget(chip)
            self.chips.append(chip)
        chips.addStretch(1)
        outer.addLayout(chips)

        self.separator = QFrame()
        self.separator.setObjectName("paletteSeparator")
        self.separator.setFixedHeight(1)
        outer.addWidget(self.separator)

        self.results = QListWidget()
        self.results.setObjectName("paletteResults")
        self.results.setFrameShape(QFrame.NoFrame)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setSelectionMode(QListWidget.SingleSelection)
        self.results.setUniformItemSizes(True)
        self.results.itemActivated.connect(lambda item: self._activate(self.results.row(item)))
        outer.addWidget(self.results)

        self.message = QLabel()
        self.message.setObjectName("paletteMessage")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.message)

        self.footer = QLabel()
        self.footer.setObjectName("paletteFooter")
        self.footer.setContentsMargins(14, 8, 14, 10)
        outer.addWidget(self.footer)

        QWidget.setTabOrder(self.field, self.results)
        QWidget.setTabOrder(self.results, self.chips[0])
        for first, second in zip(self.chips, self.chips[1:]):
            QWidget.setTabOrder(first, second)
        QWidget.setTabOrder(self.chips[-1], self.close_button)

        window.installEventFilter(self)
        self.hide()
        self.retranslate()

    # -- API ------------------------------------------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.isVisible()

    def open_palette(self, text: str = "") -> None:
        """Ouvre la boîte (saisie vide par défaut), garde le focus d'avant pour le rendre à la fermeture."""
        if not self.isVisible():
            focus = QApplication.focusWidget()
            keep = focus is not None and focus is not self.field and not self.isAncestorOf(focus)
            self._previous_focus = weakref.ref(focus) if keep else None
        self._entries = list(self.entries_provider())
        self.retranslate()
        self._apply_style()
        self.setGeometry(self._window.rect())
        self.show()
        self.raise_()
        self.field.setText(text[:MAX_QUERY_CHARS])
        self._refresh()
        self._layout_box()
        self.field.setFocus(Qt.PopupFocusReason)
        self.field.selectAll()
        app = QApplication.instance()
        if app is not None and not self._focus_hooked:
            app.focusChanged.connect(self._on_focus_changed)
            self._focus_hooked = True

    def close_palette(self) -> None:
        """Ferme la boîte et rend le focus à ce qui l'avait avant."""
        if not self.isVisible():
            return
        app = QApplication.instance()
        if app is not None and self._focus_hooked:
            app.focusChanged.disconnect(self._on_focus_changed)
            self._focus_hooked = False
        self.hide()
        previous = self._previous_focus() if self._previous_focus is not None else None
        self._previous_focus = None
        if previous is not None and isValid(previous) and previous.isVisible():
            previous.setFocus(Qt.OtherFocusReason)
        else:
            self._window.setFocus(Qt.OtherFocusReason)
        self.closed.emit()

    def retranslate(self) -> None:
        """Textes dans la langue courante."""
        self.setAccessibleName(i18n.translate("palette.dialog.name"))
        self.field.setPlaceholderText(i18n.translate("palette.field.placeholder"))
        self.field.setAccessibleName(i18n.translate("palette.field.name"))
        self.try_label.setText(i18n.translate("palette.try"))
        for chip in self.chips:
            chip.setText(i18n.translate(f"palette.chip.{chip.property('chip_key')}"))
            chip.setAccessibleName(f"{i18n.translate('palette.try')} {chip.text()}")
        self.close_button.setText(i18n.translate("palette.close"))
        self.close_button.setToolTip(i18n.translate("palette.close.tooltip"))
        self.close_button.setAccessibleName(i18n.translate("palette.close"))
        self.results.setAccessibleName(i18n.translate("palette.list.name"))
        self.footer.setText(i18n.translate("palette.footer"))

    # -- recherche ---------------------------------------------------------------------------------------------------

    def _refresh(self, _text: str = "") -> None:
        level = self.level_provider()
        query = self.field.text()
        if query.strip():
            self._results = search(self._entries, query, level, MAX_RESULTS)
            empty = i18n.translate("palette.none", query=self._short(query.strip()))
        else:
            self._results = recent_results(self._entries, self.recent_provider(), level, MAX_RESULTS)
            empty = i18n.translate("palette.empty")
        self.results.clear()
        for result in self._results:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            row_index = self.results.count()
            row = _ResultRow(result, lambda index=row_index: self._activate(index))
            self.results.addItem(item)
            self.results.setItemWidget(item, row)
        self.results.setVisible(bool(self._results))
        self.message.setVisible(not self._results)
        if self._results:
            self.results.setFixedHeight(ROW_HEIGHT * len(self._results) + 4)
            self.results.setCurrentRow(0)
        else:
            self.message.setText(empty + ("\n" + i18n.translate("palette.none.hint") if query.strip() else ""))
        self.results.setAccessibleDescription(i18n.translate("palette.list.description", count=len(self._results)))
        self._layout_box()

    @staticmethod
    def _short(text: str, limit: int = 40) -> str:
        """Saisie abrégée pour un message : une très longue saisie n'est jamais recopiée en entier."""
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _use_chip(self, chip: QPushButton) -> None:
        self.field.setText(chip.text())
        self.field.setFocus(Qt.OtherFocusReason)

    def _activate(self, row: int) -> None:
        if not 0 <= row < len(self._results):
            return
        result = self._results[row]
        if not result.entry.enabled:
            return
        entry_id, switch = result.entry.id, result.locked
        self.close_palette()
        self.entry_chosen.emit(entry_id, switch)

    def current_result(self) -> SearchResult | None:
        row = self.results.currentRow()
        return self._results[row] if 0 <= row < len(self._results) else None

    def _move(self, delta: int) -> None:
        count = self.results.count()
        if count:
            self.results.setCurrentRow(max(0, min(count - 1, self.results.currentRow() + delta)))

    # -- événements ---------------------------------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt
        if watched is self._window and event.type() in (QEvent.Resize, QEvent.Show):
            if self.isVisible():
                self.setGeometry(self._window.rect())
                self._layout_box()
            return False
        if watched is self.field and event.type() == QEvent.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if key == Qt.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key_Up:
                self._move(-1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._activate(self.results.currentRow())
                return True
            if key == Qt.Key_Escape:
                self.close_palette()
                return True
        return False

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        if event.key() == Qt.Key_Escape:
            self.close_palette()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        if not self.box.geometry().contains(event.position().toPoint()):
            self.close_palette()
        event.accept()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))                 # voile sombre sur le reste de la fenêtre
        painter.end()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        super().resizeEvent(event)
        self._layout_box()

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        """Tab reste dans la boîte : un focus qui sortirait derrière le voile revient au champ."""
        if self.isVisible() and new is not None and not self.isAncestorOf(new) and new is not self:
            self.field.setFocus(Qt.TabFocusReason)

    def _layout_box(self) -> None:
        width = max(320, min(BOX_WIDTH, self.width() - 32))
        self.box.setFixedWidth(width)
        self.box.adjustSize()
        height = min(self.box.sizeHint().height(), max(120, self.height() - BOX_TOP - 16))
        self.box.setGeometry((self.width() - width) // 2, BOX_TOP, width, height)

    def _apply_style(self) -> None:
        c = COLORS
        self.setStyleSheet(
            f"QFrame#paletteBox {{ background: {c['panel']}; border: 1px solid {c['border_strong']}; border-radius: 12px; }}"
            f" QLineEdit#paletteField {{ background: transparent; border: none; color: {c['text']}; font-size: 16px;"
            f" padding: 6px 2px; }}"
            f" QToolButton#paletteClose {{ background: {c['surface_hover']}; color: {c['text']}; border: 1px solid {c['border']};"
            f" border-radius: 6px; padding: 4px 10px; }}"
            f" QPushButton#paletteChip {{ background: {c['panel_alt']}; color: {c['text']}; border: 1px solid {c['border']};"
            f" border-radius: 11px; padding: 2px 10px; }}"
            f" QPushButton#paletteChip:hover, QPushButton#paletteChip:focus {{ border-color: {c['accent']}; }}"
            f" QFrame#paletteSeparator {{ background: {c['border']}; }}"
            f" QListWidget#paletteResults {{ background: transparent; outline: none; }}"
            f" QListWidget#paletteResults::item:selected {{ background: {c['selection']}; }}"
            f" QLabel#paletteTitle {{ color: {c['text']}; font-size: 13px; font-weight: 600; background: transparent; }}"
            f" QLabel#paletteTitle[faded=\"true\"] {{ color: {c['disabled_text']}; }}"
            f" QLabel#paletteHint, QLabel#paletteShortcut, QLabel#paletteFooter, QLabel#paletteMessage"
            f" {{ color: {c['muted']}; font-size: 11px; background: transparent; }}"
            f" QLabel#paletteMessage {{ font-size: 13px; padding: 22px 16px; }}"
            f" QPushButton#paletteOpen {{ background: {c['accent']}; color: {c['on_accent']}; border: none; border-radius: 6px;"
            f" padding: 5px 12px; font-weight: 600; }}"
            f" QPushButton#paletteSwitch {{ background: transparent; color: {c['accent']}; border: 1px solid {c['accent']};"
            f" border-radius: 6px; padding: 5px 12px; font-weight: 600; }}"
        )


__all__ = ["BOX_WIDTH", "CommandPalette", "level_name", "shortcut_text"]
