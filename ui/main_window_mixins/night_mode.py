"""Méthodes de ``MainWindow`` : mode nuit de l'interface (bascule, intensité, horaire automatique).

Le mode nuit ne change que les couleurs de l'interface (:mod:`ui.night_palette`) ; il ne touche ni au moniteur, ni à l'export,
ni aux scopes (voir ``docs/ui-layout.md``). L'état est un :class:`core.night_mode.NightModeState` ; l'heure vient de
``self._night_clock`` (injectable : les tests la fixent). Un ``QTimer`` de 60 s relit l'horaire et n'applique la palette que
lorsque l'état change (jamais de repolish inutile) ; il est arrêté à la fermeture.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.night_mode import CHECK_INTERVAL_MS, NightModeState, coerce_intensity, coerce_time, DEFAULT_END, DEFAULT_START
from core.user_settings import UserSettings
from ui.theming import retheme_application

LOGGER = logging.getLogger(__name__)


def _main_window():
    from ui import main_window

    return main_window


class NightModeMixin:
    """Mixin de ``MainWindow`` (mode nuit)."""

    _night_clock: Callable[[], datetime]
    _night_state: NightModeState

    def _init_night_mode(self, settings: UserSettings, clock: Callable[[], datetime] | None = None) -> None:
        """Lit les réglages et règle la palette AVANT la construction des widgets (ils se peignent déjà en mode nuit)."""
        self._night_clock = clock if clock is not None else datetime.now        # seule lecture de l'heure système : l'injection l'écrase
        self._night_state = NightModeState()
        self._night_load(settings)
        self.theme_manager.set_night(self._night_state.level(self._night_clock()))   # type: ignore[attr-defined]
        self._night_timer: QTimer | None = None

    def _night_load(self, settings: UserSettings) -> None:
        """Reprend les réglages ; l'« éteindre » manuel de la plage horaire ne survit qu'à des réglages inchangés."""
        state = self._night_state
        before = (state.enabled, state.intensity, state.auto, state.start, state.end)
        state.enabled = bool(settings.night_mode_enabled)
        state.intensity = coerce_intensity(settings.night_intensity)
        state.auto = bool(settings.night_auto)
        state.start = coerce_time(settings.night_start, DEFAULT_START)
        state.end = coerce_time(settings.night_end, DEFAULT_END)
        if before != (state.enabled, state.intensity, state.auto, state.start, state.end):
            state.reset_override(self._night_clock())

    def _night_settings_fields(self) -> dict:
        state = self._night_state
        return {
            "night_mode_enabled": state.enabled, "night_intensity": state.intensity, "night_auto": state.auto,
            "night_start": state.start, "night_end": state.end,
        }

    # -- état courant ------------------------------------------------------------------------------------------------------

    @property
    def night_mode_active(self) -> bool:
        return self._night_state.active(self._night_clock())

    def toggle_night_mode(self) -> bool:
        """Commande « Basculer le mode nuit » (menu, palette de commandes, raccourci, pastille) ; renvoie le nouvel état."""
        active = self._night_state.toggle(self._night_clock())
        self._night_apply(save=True)
        return active

    def set_night_settings(self, enabled: bool, intensity: int, auto: bool, start: str, end: str) -> None:
        """Réglages venus des Préférences (ou d'un script) : validés comme au chargement, enregistrés, appliqués d'un seul passage."""
        settings = replace(
            UserSettings(), night_mode_enabled=bool(enabled), night_intensity=intensity, night_auto=bool(auto),
            night_start=start, night_end=end,
        )
        self._night_load(settings)
        self._night_apply(save=True)

    # -- application -------------------------------------------------------------------------------------------------------

    def _night_apply(self, *, save: bool = False) -> bool:
        """Applique le niveau courant : une feuille globale, une re-teinte, une seule fois ; renvoie ``True`` si la palette a changé."""
        manager = self.theme_manager                                              # type: ignore[attr-defined]
        previous, previous_base = manager.effective_palette, manager.base_palette
        manager.set_night(self._night_state.level(self._night_clock()))
        manager.apply_to(QApplication.instance())
        changed = manager.effective_palette is not previous
        if changed:
            retheme_application(previous, manager.effective_palette, previous_base, manager.base_palette)
        self._night_refresh_controls()
        if save:
            try:
                _main_window().save_user_settings(self._settings_snapshot())      # type: ignore[attr-defined]
            except OSError:
                LOGGER.warning("Mode nuit : préférences non enregistrées", exc_info=True)
        return changed

    def _night_tick(self) -> None:
        """Vérification périodique de l'horaire : n'agit que si le niveau à appliquer a changé."""
        now = self._night_clock()
        self._night_state.sync(now)
        if self._night_state.level(now) != self.theme_manager.night_intensity:    # type: ignore[attr-defined]
            self._night_apply()

    def _start_night_timer(self) -> None:
        timer = QTimer(self)                                                      # type: ignore[call-overload]
        timer.setInterval(CHECK_INTERVAL_MS)
        timer.timeout.connect(self._night_tick)
        timer.start()
        self._night_timer = timer

    def _stop_night_timer(self) -> None:
        if getattr(self, "_night_timer", None) is not None:
            self._night_timer.stop()

    # -- contrôles ---------------------------------------------------------------------------------------------------------

    def _install_night_controls(self) -> None:
        """Démarre la vérification de l'horaire. L'action de menu est créée avec les menus (``night_action``).

        Pas de pastille dans la barre d'état : ``statusBar()`` la crée à la première demande et lui donne ~23 px de haut sur toutes les
        pages, ce qui reprendrait la hauteur que les gardes de ``tests/test_ui_legibility.py`` (4 pistes de 60 px…) comptent sur
        l'absence de cette barre. La bascule est dans le menu Fenêtre, la palette de commandes, le raccourci et les Préférences.
        """
        self._start_night_timer()

    def _night_refresh_controls(self) -> None:
        active = self.night_mode_active
        action = getattr(self, "night_action", None)
        if action is not None and action.isChecked() != active:
            action.blockSignals(True)
            action.setChecked(active)
            action.blockSignals(False)
        dialog = getattr(self, "_preferences_dialog", None)
        group = getattr(dialog, "night_box", None) if dialog is not None else None
        if group is not None:
            group.sync(self._night_state)


__all__ = ["NightModeMixin"]
