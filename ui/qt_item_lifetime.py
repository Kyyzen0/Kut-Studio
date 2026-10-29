"""Durée de vie des éléments de liste / d'arbre créés en Python (``QListWidgetItem``, ``QTreeWidgetItem``…).

**Le défaut.** Un élément créé par ``QListWidgetItem(...)`` est un objet C++ possédé par la liste, avec un *wrapper*
C++ (``QListWidgetItemWrapper``) qui pointe vers son objet Python. Quand Qt détruit l'élément (``clear()``, destruction
de la fenêtre), le destructeur du wrapper appelle ``Shiboken::Object::destroy(objet_python, ptr_c++)``. Si, à cet
instant, la **dernière** référence Python vient de tomber (la liste ne garde que celle-là), l'objet Python est libéré
*dans* ``destroy`` ; la fin de ``destroy`` relit alors l'objet libéré (son type, ses pointeurs C++) : lecture après
libération. Le plus souvent la mémoire libérée est intacte et rien ne se voit ; mais selon l'état du ramasse-miettes et
de l'allocateur, le bloc est déjà réutilisé et le processus s'arrête en segfault, sans trace Python (« worker 'gwN'
crashed » sous pytest-xdist). Mesuré avec ``PYTHONMALLOC=debug`` (la mémoire libérée est remplie de ``0xDD``) : le
plantage est toujours dans ``~QListWidgetItemWrapper`` -> ``Shiboken::Object::destroy``, sur un objet libéré pendant
cet appel même (l'adresse figure dans le journal des ``__del__`` du test de diagnostic).

**Le correctif.** :func:`retain` ajoute une référence Python à chaque élément, tenue jusqu'à ce que l'objet C++ soit
détruit (``shiboken6.isValid`` devient faux) : ``destroy`` ne libère plus jamais l'objet qu'il relit. La référence
supplémentaire est lâchée plus tard, hors de tout destructeur C++, quand l'élément est déjà invalide (libération sans
danger). Le nettoyage est amorti : une passe quand le registre double de taille.

Règle : tout élément d'un widget d'éléments créé dans ``ui/`` passe par ``retain(...)`` (garde statique :
``tests/test_qt_item_lifetime.py``).
"""

from __future__ import annotations

from typing import TypeVar

import shiboken6

T = TypeVar("T")

_MIN_PRUNE = 256                      # en dessous, le registre ne se nettoie pas (quelques centaines d'éléments au plus)
_retained: list[object] = []
_prune_at = _MIN_PRUNE


def retain(item: T) -> T:
    """Garde ``item`` vivant côté Python jusqu'à la destruction de son objet C++ ; renvoie ``item`` (à écrire en ligne)."""
    global _prune_at
    _retained.append(item)
    if len(_retained) >= _prune_at:
        prune()
        _prune_at = max(_MIN_PRUNE, 2 * len(_retained))
    return item


def prune() -> int:
    """Lâche les éléments dont l'objet C++ est déjà détruit ; renvoie combien ont été lâchés."""
    before = len(_retained)
    _retained[:] = [item for item in _retained if shiboken6.isValid(item)]
    return before - len(_retained)


def retained_count() -> int:
    """Nombre d'éléments actuellement retenus (diagnostic et tests)."""
    return len(_retained)
