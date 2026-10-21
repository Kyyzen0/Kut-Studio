"""Clés publiques qui authentifient les mises à jour (ADR-0012).

La liste est **dans le dépôt** : quiconque peut la relire dans le code source et, depuis « À propos », comparer
l'identifiant de la clé installée avec celui publié par le mainteneur. Un attaquant qui contrôle le dépôt peut la changer
(voir la section « Ce que la signature ne protège pas » de l'ADR) : l'identifiant affiché sert à le repérer.

* **Rotation** : deux clés au plus en service, la courante et la suivante (procédure en deux versions, ``docs/updates.md``).
* **Liste vide** : tant que le mainteneur n'a pas lancé ``python -m tools.release gen-update-key`` et collé la clé
  publique ci-dessous, aucune release n'est signée et l'application se comporte comme avant (installation assistée) ;
  une signature présente mais dont la clé est inconnue est refusée.
* **Jamais de clé privée ici** : la graine secrète n'existe que dans le secret GitHub ``UPDATE_SIGNING_KEY`` et dans le
  fichier que le mainteneur a choisi (``*.update-key``, ignoré par git).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass

from .ed25519 import PUBLIC_KEY_SIZE

KEY_ID_LENGTH = 16
"""Caractères hexadécimaux de l'identifiant : 64 premiers bits du SHA-256 de la clé publique."""


@dataclass(frozen=True)
class PublicKey:
    """Clé publique Ed25519 de signature des mises à jour."""

    public: bytes

    def __post_init__(self) -> None:
        if len(self.public) != PUBLIC_KEY_SIZE:
            raise ValueError(f"clé publique de {PUBLIC_KEY_SIZE} octets attendue")

    @property
    def key_id(self) -> str:
        """Identifiant court et stable, affiché dans « À propos » et écrit dans le fichier de signature."""
        return key_id_of(self.public)

    @property
    def base64(self) -> str:
        return base64.b64encode(self.public).decode("ascii")

    @classmethod
    def from_base64(cls, text: str) -> PublicKey:
        try:
            raw = base64.b64decode(text.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("clé publique : base64 invalide") from exc
        return cls(raw)


def key_id_of(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:KEY_ID_LENGTH]


UPDATE_PUBLIC_KEYS: tuple[PublicKey, ...] = (
    # Collez ici la sortie de « python -m tools.release gen-update-key » (une ligne PublicKey.from_base64("…")).
)
"""Clés acceptées, la courante en premier. Vide tant que la clé n'a pas été générée."""


def key_ids(keys: tuple[PublicKey, ...] | None = None) -> list[str]:
    return [key.key_id for key in (UPDATE_PUBLIC_KEYS if keys is None else keys)]


__all__ = ["KEY_ID_LENGTH", "PublicKey", "UPDATE_PUBLIC_KEYS", "key_id_of", "key_ids"]
