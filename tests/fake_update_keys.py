"""Clé de signature FACTICE pour les tests (ADR-0012) : permet de simuler une release signée sans le vrai secret.

La graine ci-dessous est une constante publique de test, sans aucun rapport avec la vraie clé du mainteneur (qui n'est
jamais dans le dépôt). Le lot « installation automatique » l'utilise pour obtenir ``VERIFIED``.
"""

from __future__ import annotations

from core import ed25519
from core.update_keys import PublicKey
from core.update_signature import sign_checksums

FAKE_SEED = bytes(range(1, 33))
OTHER_SEED = bytes(range(101, 133))
FAKE_KEY = PublicKey(ed25519.public_key_from_seed(FAKE_SEED))
OTHER_KEY = PublicKey(ed25519.public_key_from_seed(OTHER_SEED))
FAKE_KEYS = (FAKE_KEY,)


def sign_with_fake_key(sums: bytes, seed: bytes = FAKE_SEED) -> bytes:
    """Contenu d'un ``SHA256SUMS.txt.sig`` valide (pour ``FAKE_KEYS``) du texte ``sums``."""
    return sign_checksums(sums, seed)
