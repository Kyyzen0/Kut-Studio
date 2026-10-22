"""Signature des mises à jour : format du fichier ``SHA256SUMS.txt.sig`` et vérification (ADR-0012).

L'empreinte SHA-256 d'un paquet prouve qu'il est intact, pas *qui* l'a publié : elle vient de la même release. La
signature ajoute l'authenticité : le mainteneur signe ``SHA256SUMS.txt`` (donc, indirectement, chaque paquet et son nom)
avec une clé Ed25519 dont la moitié publique est dans :mod:`core.update_keys`.

**Format du fichier** ``SHA256SUMS.txt.sig`` : une ligne de texte ASCII, ``ed25519 <identifiant de clé> <signature en
base64>`` (64 octets de signature), terminée par un saut de ligne. Texte plutôt que binaire : lisible dans le navigateur,
sans ambiguïté de fin de ligne ni de transfert, et l'identifiant de clé permet de distinguer « clé que cette version ne
connaît pas » (la release a été signée avec une clé plus récente) de « signature fausse ».

**Résultat** (:class:`SignatureResult`) : ``VERIFIED`` (signature valide d'une clé connue), ``ABSENT`` (pas de fichier
``.sig`` : release non signée, l'installation reste assistée) ou ``INVALID`` (fichier présent mais illisible, clé
inconnue ou signature fausse : le paquet est refusé). Aucune exception : un appelant ne peut pas confondre un échec avec
un succès.

Ce module est pur (ni Qt ni réseau) : l'application et l'outil de publication partagent le même code.
"""

from __future__ import annotations

import base64
import binascii
import enum
import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass

from . import ed25519
from .update_keys import UPDATE_PUBLIC_KEYS, PublicKey, key_id_of

MAX_SIGNATURE_BYTES = 1024
"""Taille maximale lue pour ``SHA256SUMS.txt.sig`` (la ligne attendue en fait une centaine)."""
SIGNATURE_ALGORITHM = "ed25519"
_LINE = re.compile(rf"{SIGNATURE_ALGORITHM} (?P<key>[0-9a-f]{{16}}) (?P<signature>[A-Za-z0-9+/]{{86}}==)")


class SignatureStatus(enum.Enum):
    VERIFIED = "verified"
    ABSENT = "absent"
    INVALID = "invalid"


@dataclass(frozen=True)
class SignatureResult:
    status: SignatureStatus
    key_id: str = ""
    """Clé qui a validé la signature (``VERIFIED``) ou clé annoncée par le fichier (``INVALID``), sinon ``""``."""
    detail: str = ""
    """Raison technique (journal, jamais traduite) : courte, ne recopie pas l'entrée."""
    unknown_key: bool = False
    """``INVALID`` parce que la clé annoncée n'est pas dans la liste de cette version de l'application."""

    @property
    def verified(self) -> bool:
        return self.status is SignatureStatus.VERIFIED


class SignatureFormatError(ValueError):
    """Contenu de ``SHA256SUMS.txt.sig`` illisible."""


def format_signature_file(key_id: str, signature: bytes) -> bytes:
    """Contenu du fichier ``.sig`` pour ``signature`` (64 octets) faite par la clé ``key_id``."""
    if len(signature) != ed25519.SIGNATURE_SIZE:
        raise SignatureFormatError(f"signature de {ed25519.SIGNATURE_SIZE} octets attendue")
    text = f"{SIGNATURE_ALGORITHM} {key_id} {base64.b64encode(signature).decode('ascii')}\n"
    if _LINE.fullmatch(text[:-1]) is None:
        raise SignatureFormatError("identifiant de clé invalide")
    return text.encode("ascii")


def parse_signature_file(content: bytes) -> tuple[str, bytes]:
    """``(identifiant de clé, signature de 64 octets)`` ; refuse tout ce qui n'est pas exactement une ligne conforme."""
    if len(content) > MAX_SIGNATURE_BYTES:
        raise SignatureFormatError(f"fichier de signature trop grand (plus de {MAX_SIGNATURE_BYTES} octets)")
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SignatureFormatError("fichier de signature non ASCII") from exc
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    match = _LINE.fullmatch(text)
    if match is None:
        raise SignatureFormatError("format de signature inattendu")
    try:
        raw = base64.b64decode(match.group("signature"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureFormatError("base64 invalide") from exc
    if len(raw) != ed25519.SIGNATURE_SIZE:
        raise SignatureFormatError("longueur de signature fausse")
    return match.group("key"), raw


def sign_checksums(sums: bytes, seed: bytes) -> bytes:
    """Contenu du fichier ``.sig`` pour ``sums``, signé par la graine ``seed`` (outil de publication uniquement)."""
    public = ed25519.public_key_from_seed(seed)
    return format_signature_file(key_id_of(public), ed25519.sign(seed, sums))


def verify_release_signature(
    sums: bytes,
    signature: bytes | None,
    keys: Sequence[PublicKey] = UPDATE_PUBLIC_KEYS,
) -> SignatureResult:
    """Vérifie la signature de ``SHA256SUMS.txt`` (``sums``, octets exacts reçus) par l'une des ``keys``.

    ``signature`` est le contenu du fichier ``.sig``, ou ``None`` s'il n'existe pas dans la release (``ABSENT``).
    """
    if signature is None:
        return SignatureResult(SignatureStatus.ABSENT, detail="aucun fichier de signature publié")
    try:
        announced, raw = parse_signature_file(bytes(signature))
    except SignatureFormatError as exc:
        return SignatureResult(SignatureStatus.INVALID, detail=str(exc))
    for key in keys:
        if hmac.compare_digest(key.key_id.encode("ascii"), announced.encode("ascii")):
            if ed25519.verify(key.public, sums, raw):
                return SignatureResult(SignatureStatus.VERIFIED, key_id=key.key_id)
            return SignatureResult(SignatureStatus.INVALID, key_id=key.key_id,
                                   detail="la signature ne correspond pas au contenu de SHA256SUMS.txt")
    return SignatureResult(SignatureStatus.INVALID, key_id=announced, unknown_key=True,
                           detail=f"clé {announced} inconnue de cette version de l'application")


# Vecteur de démonstration (RFC 8032 § 7.1, TEST 2) : le smoke test de l'application construite le vérifie, ce qui
# prouve que l'arithmétique Ed25519 fonctionne dans l'exécutable gelé, indépendamment de la liste de clés réelle.
_DEMO_PUBLIC = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
_DEMO_MESSAGE = bytes.fromhex("72")
_DEMO_SIGNATURE = bytes.fromhex(
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
    "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
)


def self_check() -> str | None:
    """``None`` si l'application sait vérifier une signature, sinon la raison (smoke test de ``main.py``)."""
    key = PublicKey(_DEMO_PUBLIC)
    good = format_signature_file(key.key_id, _DEMO_SIGNATURE)
    result = verify_release_signature(_DEMO_MESSAGE, good, (key,))
    if result.status is not SignatureStatus.VERIFIED:
        return f"signature de démonstration non vérifiée ({result.status.value} : {result.detail})"
    if verify_release_signature(_DEMO_MESSAGE + b"x", good, (key,)).status is not SignatureStatus.INVALID:
        return "un message altéré a été accepté"
    if verify_release_signature(_DEMO_MESSAGE, None, (key,)).status is not SignatureStatus.ABSENT:
        return "l'absence de signature n'est pas reconnue"
    return None


__all__ = [
    "MAX_SIGNATURE_BYTES",
    "SIGNATURE_ALGORITHM",
    "SignatureFormatError",
    "SignatureResult",
    "SignatureStatus",
    "format_signature_file",
    "parse_signature_file",
    "self_check",
    "sign_checksums",
    "verify_release_signature",
]
