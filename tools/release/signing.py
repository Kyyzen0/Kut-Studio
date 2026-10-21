"""Signature des mises à jour (ADR-0012) : génération de la clé, signature de ``SHA256SUMS.txt``, état pour les notes.

* ``gen-update-key`` est lancé **par le mainteneur, sur sa machine** : la graine secrète est écrite dans un fichier créé
  avec les droits ``600`` (refus d'écraser un fichier existant) et n'est **jamais** affichée, ni sur la sortie standard
  ni sur l'erreur, ni dans un message d'exception. Seule la clé publique et son identifiant sont affichés.
* ``sign`` tourne dans le workflow de release : la graine arrive par la variable d'environnement
  ``UPDATE_SIGNING_KEY`` (secret GitHub, masqué dans les journaux), jamais par un argument de ligne de commande.
  Secret absent : release **non signée** (état ``unsigned`` écrit pour les notes). Secret présent : la signature est
  faite, **vérifiée avec le même code que l'application** et contre la liste de clés du dépôt ; au moindre échec, erreur
  (jamais de release à moitié signée).
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from collections.abc import Sequence
from pathlib import Path

from core import ed25519
from core.release_assets import CHECKSUMS_FILE, SIGNATURE_FILE
from core.update_keys import UPDATE_PUBLIC_KEYS, PublicKey
from core.update_signature import SignatureStatus, sign_checksums, verify_release_signature

from .packaging import ReleaseError

SIGNING_KEY_ENV = "UPDATE_SIGNING_KEY"
KEY_FILE_SUFFIX = ".update-key"
UPDATE_SIGNATURE_STATUSES = ("signed", "unsigned")
"""État de la signature des mises à jour d'une release, écrit dans ``signing-status/update-signature.txt``."""
MAX_SEED_TEXT = 256
"""Longueur maximale lue pour le secret (une graine en base64 en fait 44)."""


def decode_seed(text: str) -> bytes:
    """Graine de 32 octets d'un secret en base64 ; les messages d'erreur ne recopient jamais le secret."""
    text = text.strip()
    if len(text) > MAX_SEED_TEXT:
        raise ReleaseError(f"{SIGNING_KEY_ENV} : valeur démesurée")
    try:
        seed = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReleaseError(f"{SIGNING_KEY_ENV} : base64 invalide") from exc
    if len(seed) != ed25519.SEED_SIZE:
        raise ReleaseError(f"{SIGNING_KEY_ENV} : une graine de {ed25519.SEED_SIZE} octets est attendue")
    return seed


def generate_update_key(destination: Path) -> PublicKey:
    """Crée la graine dans ``destination`` (droits 600, jamais écrasé) et renvoie la clé publique.

    La graine n'est pas renvoyée : l'appelant n'a aucune occasion de l'afficher.
    """
    seed = secrets.token_bytes(ed25519.SEED_SIZE)
    public = PublicKey(ed25519.public_key_from_seed(seed))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise ReleaseError(f"{destination} existe déjà : choisissez un autre fichier (rien n'est écrasé)") from exc
    except OSError as exc:
        raise ReleaseError(f"{destination} : {exc.strerror or 'création impossible'}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(base64.b64encode(seed).decode("ascii") + "\n")
        os.chmod(destination, 0o600)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise ReleaseError(f"{destination} : écriture impossible ({exc.strerror or 'erreur'})") from exc
    return public


def key_instructions(public: PublicKey, destination: Path) -> str:
    """Ce que voit le mainteneur après ``gen-update-key`` : clé publique, identifiant, suite de la procédure."""
    return "\n".join([
        f"Clé de signature créée : {destination}  (droits 600, la graine n'est PAS affichée).",
        "",
        f"Identifiant de la clé : {public.key_id}",
        f"Clé publique (base64)  : {public.base64}",
        "",
        "1. Collez cette ligne dans UPDATE_PUBLIC_KEYS de core/update_keys.py, puis relisez et fusionnez la PR :",
        f'       PublicKey.from_base64("{public.base64}"),  # {public.key_id}',
        "2. Envoyez la graine à GitHub sans la copier à l'écran :",
        f"       gh secret set {SIGNING_KEY_ENV} < {destination}",
        f"3. Gardez {destination} dans un coffre (gestionnaire de mots de passe) ; ne l'ajoutez jamais au dépôt, ne la",
        "   collez dans aucun message ni journal. Perdue, elle impose une rotation (docs/updates.md).",
        "4. Publiez ensuite normalement (tag vX.Y.Z) : les notes de la release indiquent « mises à jour automatiques",
        "   disponibles ».",
        "",
    ])


def sign_release_directory(
    directory: Path,
    environment: dict[str, str] | os._Environ[str] | None = None,
    keys: Sequence[PublicKey] | None = None,
) -> str:
    """Signe ``SHA256SUMS.txt`` du dossier et renvoie l'état (``"signed"`` ou ``"unsigned"``).

    Secret absent ou vide : ``"unsigned"``, rien n'est écrit. Secret présent : écrit ``SHA256SUMS.txt.sig`` et le
    vérifie avec le code de l'application contre ``keys`` (par défaut la liste du dépôt) ; toute anomalie lève
    :class:`ReleaseError` et retire le fichier produit.
    """
    env = os.environ if environment is None else environment
    secret = env.get(SIGNING_KEY_ENV, "").strip()
    if not secret:
        return "unsigned"
    keys = UPDATE_PUBLIC_KEYS if keys is None else keys
    sums_path = directory / CHECKSUMS_FILE
    if not sums_path.is_file():
        raise ReleaseError(f"{CHECKSUMS_FILE} manquant : rien à signer")
    sums = sums_path.read_bytes()
    seed = decode_seed(secret)
    target = directory / SIGNATURE_FILE
    try:
        content = sign_checksums(sums, seed)
        target.write_bytes(content)
        result = verify_release_signature(sums, content, keys)
        if result.status is not SignatureStatus.VERIFIED:
            raise ReleaseError(
                f"la signature produite n'est pas valide pour les clés de core/update_keys.py ({result.detail}) : la clé "
                f"publique de ce secret est-elle bien dans UPDATE_PUBLIC_KEYS ?"
            )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return "signed"


__all__ = [
    "KEY_FILE_SUFFIX",
    "SIGNING_KEY_ENV",
    "UPDATE_SIGNATURE_STATUSES",
    "decode_seed",
    "generate_update_key",
    "key_instructions",
    "sign_release_directory",
]
