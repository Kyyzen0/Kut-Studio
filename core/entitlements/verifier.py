"""Signature de l'état de droits : l'interface qu'implémente ``StoreKitVerifier`` (étape 13).

L'état persisté dans le conteneur est une **enveloppe signée** : ``{"v": 1, "payload": <base64>, "signature": <base64>}``.
Le module de droits ne calcule **jamais** un état à partir d'un fichier librement modifiable : sans signature valide,
l'état est ``UNKNOWN`` (jamais un essai neuf).

:class:`EntitlementVerifier` est abstraite. L'étape 13 la réalise avec ``StoreKitVerifier`` (``storekit_verifier.py`` : clé
aléatoire du conteneur ; le trousseau reste un durcissement possible) ; :class:`HmacVerifier` ne sert qu'aux tests et au développement
(clé symétrique : qui lit l'application lit la clé). Ce module ne choisit pas d'algorithme pour le Store.
"""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod


class EntitlementVerifier(ABC):
    """Signe et vérifie la charge utile de l'état persisté."""

    @abstractmethod
    def sign(self, payload: bytes) -> bytes:
        """La signature de ``payload`` (appelée quand le pont publie un état vérifié, ou quand la plus grande date vue avance)."""

    @abstractmethod
    def verify(self, payload: bytes, signature: bytes) -> bool:
        """``True`` si ``signature`` est bien celle de ``payload``. Ne lève pas : une signature mal formée est fausse."""


class HmacVerifier(EntitlementVerifier):
    """HMAC-SHA256 à clé symétrique. **Tests et développement seulement** (voir le docstring du module)."""

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("La clé de signature est vide.")
        self._key = bytes(key)

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def verify(self, payload: bytes, signature: bytes) -> bool:
        try:
            return hmac.compare_digest(self.sign(payload), bytes(signature))
        except (TypeError, ValueError):
            return False


__all__ = ["EntitlementVerifier", "HmacVerifier"]
