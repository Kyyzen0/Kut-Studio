"""Saveur de construction : version Store (Mac App Store) ou non (ADR-0013, étape 4).

``STORE`` est **faux par défaut** : sources, ``python build.py``, CI et version Developer ID se comportent exactement
comme avant. Il n'est vrai que dans un bundle construit par ``python build.py --flavor store``, qui écrit le petit
module ``core/_build_flavor_generated.py`` (ignoré par git) avant de lancer PyInstaller, puis le retire.

La valeur ne vient **jamais** d'une variable d'environnement ni d'une option de l'utilisateur : le Store interdit
tout autre mécanisme de mise à jour et tout FFmpeg tiers, et l'utilisateur ne doit pas pouvoir lever ces
interdictions. Ce module n'importe rien d'autre que le module généré (aucune dépendance, importable partout)."""

from __future__ import annotations

GENERATED_MODULE = "_build_flavor_generated"
"""Nom du module généré, à côté de celui-ci (inclus dans le bundle par ``--hidden-import`` de ``build.py``)."""


def _generated_store() -> bool:
    """Valeur du module généré, ou ``False`` s'il n'existe pas (sources, CI, Developer ID).

    Le module n'existe pas dans le dépôt (``build.py --flavor store`` l'écrit puis l'ajoute au bundle par
    ``--hidden-import``) : mypy ne peut pas le résoudre."""
    try:
        from . import _build_flavor_generated as generated  # type: ignore[attr-defined]
    except ImportError:
        return False
    return getattr(generated, "STORE", False) is True


STORE: bool = _generated_store()
"""Vrai seulement dans une construction Store. Lu à l'exécution par :func:`is_store`."""


class StoreRestrictionError(RuntimeError):
    """Fonction refusée parce que la construction est celle du Mac App Store."""


def is_store() -> bool:
    """``True`` dans la construction Mac App Store (lit ``STORE`` à chaque appel : remplaçable en test)."""
    return STORE


def require_not_store(feature: str) -> None:
    """Garde côté modèle : lève :class:`StoreRestrictionError` en construction Store (un signal ou un appel direct
    peut atteindre le service sans passer par l'interface, qui cache déjà la fonction)."""
    if is_store():
        raise StoreRestrictionError(f"{feature} : indisponible dans la version Mac App Store")


__all__ = ["GENERATED_MODULE", "STORE", "StoreRestrictionError", "is_store", "require_not_store"]
