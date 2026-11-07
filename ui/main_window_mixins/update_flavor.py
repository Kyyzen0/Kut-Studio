"""Choisit le mixin des mises à jour selon la saveur de construction (ADR-0013, étape 4).

La version Store n'importe aucun module de mise à jour : le mixin complet (qui importe ``core.update_service``…) n'est
chargé que hors Store. Pour mypy et les outils, le nom ``UpdatesMixin`` est celui de la version complète."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.build_flavor import is_store

if TYPE_CHECKING:
    from ui.main_window_mixins.updates import UpdatesMixin
elif is_store():
    from ui.main_window_mixins.updates_store import UpdatesMixin
else:
    from ui.main_window_mixins.updates import UpdatesMixin

__all__ = ["UpdatesMixin"]
