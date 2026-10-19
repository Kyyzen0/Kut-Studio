"""Libellé lisible d'un paramètre d'effet animable (« Correction de couleur — Luminosité »), pour l'éditeur de courbes.

Deux effets du même type se distinguent par leur rang (« Correction de couleur n° 2 — Luminosité »). Le rang vient de l'ordre
des effets du clip, qui est aussi l'ordre de leur application.
"""

from __future__ import annotations

from core.effects_model import split_fx_property
from ui import i18n


def effect_property_label(clip, target) -> str:
    """Libellé de ``target`` (identifiant ``fx.<effet>.<paramètre>``) sur ``clip``, ``""`` si ce n'est pas un paramètre d'effet."""
    parts = split_fx_property(target.id)
    if parts is None:
        return ""
    effect_id, _name = parts
    effects = list(getattr(clip, "effects", ()) or ())
    effect = next((e for e in effects if e.id == effect_id), None)
    if effect is None:
        return ""
    param = i18n.translate(target.spec.label_key)
    effect_name = i18n.translate(f"effects.name.{effect.type.value}")
    rank = [e.id for e in effects if e.type is effect.type].index(effect_id) + 1
    if rank > 1:
        return i18n.translate("graph.property.fx_n", effect=effect_name, n=rank, param=param)
    return i18n.translate("graph.property.fx", effect=effect_name, param=param)


__all__ = ["effect_property_label"]
