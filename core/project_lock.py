"""Verrou consultatif d'un projet (ADR-0005, décision 3) : un projet ouvert dans la fenêtre ne se fait pas écraser par un script.

Le verrou est un petit fichier voisin du projet, ``nom.kut.lock`` (comme ``nom.kut.autosave``), créé **atomiquement** (``O_EXCL``) par
celui qui ouvre ou modifie le projet : l'application tant que le projet est ouvert, le CLI le temps d'une écriture ``--in-place`` ou
``--overwrite``. Il dit qui le tient : PID, **heure de début du processus** (un PID réutilisé par un autre processus ne passe pas pour
l'ancien), nom, machine, genre (``app`` ou ``cli``).

« Consultatif » veut dire qu'il n'empêche rien tout seul : c'est un accord entre les programmes de Kut-Studio. Il ne protège pas d'un
éditeur de texte, ni d'une copie qui l'ignore ; il évite la seule vraie catastrophe, un script qui réécrit un projet que la fenêtre
tient ouvert, puis la fenêtre qui réécrit par-dessus (ou l'inverse), sans que personne ne le sache.

Règles :

- **Le doute profite au propriétaire.** Un verrou n'est « périmé » (et repris) que si son propriétaire est *prouvé* disparu : PID absent,
  ou PID réutilisé (heure de début différente), sur la même machine. Autre machine (dossier partagé), identité illisible, fichier
  récent mais abîmé : le verrou tient, l'appelant doit refuser et dire quel fichier supprimer à la main s'il est sûr de son fait.
- **Le processus qui tient le verrou n'est jamais bloqué par lui-même** : le reprendre est permis.
- **On ne retire que son propre verrou** : après une reprise par un autre, ``release`` laisse le fichier en place.
- **Un verrou ne bloque jamais l'ouverture** côté application (elle prévient) ; c'est l'écriture par un script qui est refusée.
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .process_platform import ProbeState, ProcessIdentity, ProcessOps, native_ops, same_process

LOGGER = logging.getLogger("kut_studio.project_lock")

LOCK_VERSION = 1
LOCK_SUFFIX = ".lock"
KINDS = ("app", "cli")
_CORRUPT_GRACE_SECONDS = 0.5
"""Un verrou illisible peut être un verrou en cours d'écriture : on relit un instant avant de conclure."""
_TRANSIENT_READ_SECONDS = 0.3
"""Sous Windows, lire un fichier en cours de suppression ou de remplacement par un autre processus lève ``PermissionError``
(erreurs 5, 32, 33) : transitoire, on relit un court instant. Au-delà, l'état est inconnu et on refuse (``ProjectLockedError``)."""
_CORRUPT_STALE_AFTER = 60.0
"""Un verrou illisible depuis plus de cela (mtime) n'est plus une écriture en cours : c'est un fichier abîmé."""
_MAX_TIMESTAMP = 4_102_444_800.0
"""1er janvier 2100 : au-delà (ou négatif, ou non fini), une heure de début n'est pas celle d'un vrai verrou."""
_GUARD_SUFFIX = ".reclaim"
_GUARD_WAIT_SECONDS = 2.0


@dataclass(frozen=True)
class LockOwner:
    """Qui tient le verrou."""

    pid: int
    token: str
    """Heure de début du processus (format de la plateforme) : ce qui distingue le propriétaire d'un autre processus de même PID."""
    name: str
    host: str
    kind: str
    started: float
    nonce: str
    """Propre à ce verrou : ``release`` ne retire que le fichier qui porte son jeton."""


class LockVerdict(Enum):
    OURS = "ours"
    """Ce processus lui-même."""
    STALE = "stale"
    """Propriétaire prouvé disparu : le verrou peut être repris."""
    HELD = "held"
    """Propriétaire vivant, identité confirmée."""
    UNKNOWN = "unknown"
    """Autre machine, identité illisible : on ne touche à rien."""


class ProjectLockedError(RuntimeError):
    """Le projet est verrouillé par quelqu'un d'autre (ou par un verrou qu'on ne peut pas prouver périmé)."""

    def __init__(self, lock_file: Path, owner: LockOwner | None, verdict: LockVerdict) -> None:
        self.lock_file = lock_file
        self.owner = owner
        self.verdict = verdict
        who = f"{owner.kind} pid {owner.pid} sur {owner.host}" if owner is not None else "un verrou illisible"
        super().__init__(f"Projet verrouillé par {who} ({lock_file}).")


def lock_file_for(project_path: str | os.PathLike[str]) -> Path:
    """Le fichier de verrou d'un projet. Les liens symboliques sont résolus : un lien et son projet partagent **un** verrou."""
    return Path(os.path.realpath(os.fspath(project_path)) + LOCK_SUFFIX)


def _identity_of_this_process(ops: ProcessOps) -> tuple[str, str]:
    probe = ops.probe(os.getpid())
    if probe.state is ProbeState.ALIVE and probe.identity is not None:
        return probe.identity.token, probe.identity.name
    return "", os.path.basename(sys.executable)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON refusée : {name}")


def _parse(raw: bytes) -> LockOwner | None:
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        if not isinstance(data, dict) or data.get("kut_lock") != LOCK_VERSION:
            return None
        pid, started = data["pid"], data["started"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        if isinstance(started, bool) or not isinstance(started, (int, float)) or not math.isfinite(started):
            return None
        if not 0 <= started <= _MAX_TIMESTAMP:                    # ``time.localtime`` lèverait sur une valeur absurde
            return None
        texts = {key: data[key] for key in ("token", "name", "host", "kind", "nonce")}
        if not all(isinstance(value, str) for value in texts.values()) or texts["kind"] not in KINDS or not texts["nonce"]:
            return None
        return LockOwner(pid, texts["token"], texts["name"], texts["host"], texts["kind"], float(started), texts["nonce"])
    except (ValueError, KeyError, UnicodeDecodeError):
        return None


def read_lock(project_path: str | os.PathLike[str]) -> LockOwner | None:
    """Le propriétaire inscrit dans le verrou du projet, ou ``None`` (aucun verrou, ou illisible). Ne juge pas s'il est périmé."""
    try:
        return _parse(lock_file_for(project_path).read_bytes())
    except OSError:
        return None


def judge(owner: LockOwner, *, ops: ProcessOps | None = None, host: str | None = None) -> LockVerdict:
    """Le propriétaire est-il ce processus, disparu, vivant ou invérifiable ? Seul ``STALE`` autorise la reprise."""
    ops = ops or native_ops()
    if owner.host != (host if host is not None else socket.gethostname()):
        return LockVerdict.UNKNOWN                       # un dossier partagé : on ne voit pas ses processus
    if owner.pid == os.getpid():
        token, _name = _identity_of_this_process(ops)
        return LockVerdict.OURS if not owner.token or owner.token == token else LockVerdict.STALE
    probe = ops.probe(owner.pid)
    if probe.state is ProbeState.GONE:
        return LockVerdict.STALE
    if probe.state is ProbeState.ALIVE and probe.identity is not None and owner.token:
        if probe.identity.token != owner.token:
            return LockVerdict.STALE                     # le PID a été repris par un autre processus
        recorded = ProcessIdentity(owner.pid, owner.token, owner.name)
        return LockVerdict.HELD if same_process(recorded, probe.identity) else LockVerdict.UNKNOWN
    return LockVerdict.HELD if probe.state is ProbeState.ALIVE else LockVerdict.UNKNOWN


class ProjectLock:
    """Le verrou d'un projet. ``acquire`` ou ``with`` ; ``release`` ne retire que son propre fichier."""

    def __init__(
        self, project_path: str | os.PathLike[str], *, kind: str = "app", ops: ProcessOps | None = None, host: str | None = None,
    ) -> None:
        if kind not in KINDS:
            raise ValueError(f"Genre de verrou inconnu : {kind!r}.")
        self.path = lock_file_for(project_path)
        self.kind = kind
        self._ops = ops or native_ops()
        self._host = host if host is not None else socket.gethostname()
        self._owner: LockOwner | None = None
        self.replaced_stale: LockOwner | None = None
        """Le propriétaire périmé que ``acquire`` a remplacé, s'il y en avait un (l'appelant peut le signaler)."""

    @property
    def held(self) -> bool:
        return self._owner is not None

    def _new_owner(self) -> LockOwner:
        token, name = _identity_of_this_process(self._ops)
        return LockOwner(os.getpid(), token, name, self._host, self.kind, time.time(), uuid.uuid4().hex)

    def _payload(self, owner: LockOwner) -> bytes:
        return json.dumps({"kut_lock": LOCK_VERSION, **asdict(owner)}, ensure_ascii=False, indent=1).encode("utf-8")

    def _create_settling(self) -> int:
        """Crée le verrou en ``O_EXCL``. Sous Windows, un nom dont la suppression est en attente lève ``PermissionError`` (et son
        ``exists()`` peut mentir) : on retente pendant ``_TRANSIENT_READ_SECONDS`` (la suppression finit, le nom devient libre ou
        ``FileExistsError``) avant de conclure à une vraie erreur de droits."""
        deadline = time.monotonic() + _TRANSIENT_READ_SECONDS
        while True:
            try:
                return os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    @staticmethod
    def _present(path: Path) -> bool:
        try:
            return path.exists()
        except OSError:
            return True                                  # fichier en suppression sous Windows : présent

    def _lock_present(self) -> bool:
        """Le fichier de verrou existe-t-il ? Sous Windows, ``exists()`` lève ``PermissionError`` sur un fichier en suppression : présent."""
        try:
            return self.path.exists()
        except OSError:
            return True

    def _read_settling(self) -> bytes | None:
        """Les octets du verrou ; ``None`` s'il a disparu. Un ``PermissionError`` (suppression ou remplacement en cours sous Windows)
        est retenté pendant ``_TRANSIENT_READ_SECONDS`` ; s'il persiste (vraie erreur de droits), ``ProjectLockedError`` UNKNOWN."""
        deadline = time.monotonic() + _TRANSIENT_READ_SECONDS
        while True:
            try:
                return self.path.read_bytes()
            except FileNotFoundError:
                return None
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise ProjectLockedError(self.path, None, LockVerdict.UNKNOWN) from None
                time.sleep(0.01)

    def _read_with_grace(self) -> tuple[LockOwner | None, bytes | None]:
        """Le verrou existant ; relu un instant s'il est illisible (une écriture peut être en cours). ``(None, None)`` : disparu."""
        deadline = time.monotonic() + _CORRUPT_GRACE_SECONDS
        while True:
            raw = self._read_settling()
            if raw is None:
                return None, None
            owner = _parse(raw)
            if owner is not None or time.monotonic() >= deadline:
                return owner, raw
            time.sleep(0.05)

    def _write_replacing(self, owner: LockOwner) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            temporary.write_bytes(self._payload(owner))
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    @contextmanager
    def _reclaim_guard(self) -> Iterator[None]:
        """Exclusion mutuelle de la **reprise** d'un verrou périmé (``nom.kut.lock.reclaim``, créé en ``O_EXCL``).

        Sans elle, deux processus qui reprennent le même verrou périmé peuvent s'entrecroiser : l'un compare, est suspendu, l'autre retire
        le verrou périmé et pose le sien, puis le premier reprend et retire ce verrou **vivant** ; les deux se croient propriétaires. Sous
        la garde, un seul reprend à la fois et chacun relit avant de décider. La garde ne dure que quelques instants ; si elle traîne
        (plantage pendant la reprise), on refuse et le message désigne le fichier à supprimer, plutôt que de la voler."""
        guard = self.path.with_name(self.path.name + _GUARD_SUFFIX)
        deadline = time.monotonic() + _GUARD_WAIT_SECONDS
        while True:
            try:
                os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                break
            except (FileExistsError, PermissionError) as error:
                # Sous Windows, une garde dont la suppression est en attente lève PermissionError au lieu de FileExistsError : on attend
                # comme pour une garde tenue. Si l'erreur dure et que la garde n'existe pas, c'est une vraie erreur de droits : elle remonte.
                if time.monotonic() >= deadline:
                    if isinstance(error, PermissionError) and not self._present(guard):
                        raise
                    raise ProjectLockedError(guard, None, LockVerdict.UNKNOWN) from None
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                guard.unlink()
            except OSError:
                pass

    def acquire(self) -> LockOwner | None:
        """Prend le verrou. ``ProjectLockedError`` s'il appartient à un autre (vivant, ou qu'on ne peut pas prouver périmé) ;
        ``OSError`` si le dossier du projet n'autorise pas la création du fichier. Rend le propriétaire périmé remplacé, ou ``None``."""
        owner = self._new_owner()
        for _attempt in range(4):
            try:
                descriptor = self._create_settling()
            except (FileExistsError, PermissionError) as error:
                if isinstance(error, PermissionError) and not self._lock_present():
                    raise                                # vraie erreur de droits sur le dossier, pas un verrou en suppression
                existing, raw = self._read_with_grace()
                if raw is None:
                    continue                             # disparu entre-temps : on retente la création
                if existing is None:                     # illisible : récent = peut-être en écriture, ancien = abîmé
                    if time.time() - self._mtime() <= _CORRUPT_STALE_AFTER:
                        raise ProjectLockedError(self.path, None, LockVerdict.UNKNOWN) from None
                    verdict = LockVerdict.STALE
                else:
                    verdict = judge(existing, ops=self._ops, host=self._host)
                if verdict is LockVerdict.OURS:
                    self._write_replacing(owner)
                    self._owner = owner
                    return None
                if verdict is not LockVerdict.STALE:
                    raise ProjectLockedError(self.path, existing, verdict) from None
                with self._reclaim_guard():                # prouvé périmé : repris, un seul à la fois, après relecture sous la garde
                    _again, raw_again = self._read_with_grace()
                    if raw_again != raw:
                        continue                         # quelqu'un l'a remplacé entre-temps : on réévalue depuis le début
                    self._unlink()
                    self.replaced_stale = existing
                continue
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(self._payload(owner))
            except BaseException:
                self._unlink()
                raise
            self._owner = owner
            return self.replaced_stale
        raise ProjectLockedError(self.path, None, LockVerdict.UNKNOWN)

    def _mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return time.time()

    def _unlink(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def release(self) -> None:
        """Retire le verrou **s'il porte encore notre jeton** (après une reprise par un autre, le fichier n'est plus le nôtre)."""
        owner, self._owner = self._owner, None
        if owner is None:
            return
        try:
            raw = self._read_settling()
        except (OSError, ProjectLockedError):
            return
        current = None if raw is None else _parse(raw)
        if current is not None and current.nonce == owner.nonce:
            self._unlink()

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


__all__ = [
    "KINDS", "LOCK_SUFFIX", "LOCK_VERSION", "LockOwner", "LockVerdict", "ProjectLock", "ProjectLockedError", "judge", "lock_file_for",
    "read_lock",
]
