"""Gestionnaire de proxies média : états, génération FFmpeg annulable, fallback.

Principes
---------

1. **Les proxies ne font jamais partie du projet.** Le fichier ``.kut`` ne
   référence aucun proxy. Les proxies vivent dans le dossier de cache
   utilisateur ; ils sont retrouvés à partir du **chemin du média source**
   et de l'**empreinte du profil**. Un projet ouvert sur une autre machine
   n'a simplement pas de proxy (chemins différents) et se lit sur les
   originaux. Supprimer le cache à la main ne casse rien : l'aperçu
   retombe sur l'original.
2. **L'export utilise toujours l'original.** Seul l'aperçu passe par
   :meth:`ProxyManager.resolve` ; :meth:`ProxyManager.resolve_for_export`
   retourne le média source (sauf ``allow_proxy_for_debug=True``, option
   explicite de débogage).
3. **Un proxy incomplet n'existe pas.** FFmpeg écrit dans un fichier
   ``.partial`` ; le proxy n'est promu (renommé) qu'après une sortie
   réussie, puis un fichier compagnon ``.json`` est écrit **en dernier** :
   sa présence est le marqueur d'achèvement. Un proxy sans marqueur, ou
   dont la taille ne correspond plus à celle du marqueur, n'est jamais
   utilisé.
4. **Rien ne bloque l'interface.** La génération tourne dans des threads
   dédiés (distincts de la file des miniatures, pour qu'un encodage de
   plusieurs minutes ne les affame pas) ; FFmpeg est un processus enfant
   tué proprement à l'annulation et à la fermeture.

États (:class:`ProxyState`) : ``NONE``, ``PENDING``, ``GENERATING``,
``READY``, ``ERROR``, ``STALE`` (la source a changé depuis la génération).
"""

from __future__ import annotations

import errno
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .cache_keys import SignatureMemo, default_signatures, proxy_key
from .platform_paths import user_cache_dir
from .proxy_profiles import (
    DEFAULT_PROFILE_ID,
    ProxyProfile,
    available_profiles,
    get_profile,
)
from .tool_paths import find_media_tool

SIDECAR_VERSION = 1
LAST_USED_REFRESH_SECONDS = 60.0
DISK_STATE_TTL_SECONDS = 2.0
PARTIAL_STALE_SECONDS = 600.0
"""Âge au-delà duquel un fichier partiel est considéré abandonné. FFmpeg qui écrit le met à jour en continu : un
fichier récent peut appartenir à une génération en cours dans **une autre instance** de l'application."""


class ProxyState(str, Enum):
    NONE = "none"
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    ERROR = "error"
    STALE = "stale"


@dataclass(frozen=True)
class ProxyInfo:
    """Tout ce qu'on peut savoir d'un proxy à un instant donné.

    Attributes:
        source_path: média original.
        profile_id: profil concerné.
        state: voir :class:`ProxyState`.
        proxy_path: fichier proxy (si ``READY`` ou ``STALE``).
        progress: avancement 0–100 pendant la génération.
        error: message lisible si ``ERROR``.
        size_bytes: taille du proxy.
        created_at: date de génération (secondes Unix).
        source_signature: signature de la source au moment de la génération.
        source_missing: l'original a disparu (média hors ligne) ; un proxy
            ``READY`` reste utilisable pour l'aperçu.
    """

    source_path: str
    profile_id: str
    state: ProxyState = ProxyState.NONE
    proxy_path: str | None = None
    progress: int = 0
    error: str = ""
    size_bytes: int = 0
    created_at: float | None = None
    source_signature: str | None = None
    source_missing: bool = False

    @property
    def valid(self) -> bool:
        """``True`` si le proxy est complet et correspond à la source et au profil."""
        return self.state is ProxyState.READY


@dataclass
class RunResult:
    returncode: int
    stderr: str = ""
    cancelled: bool = False


Runner = Callable[..., RunResult]


# ---------------------------------------------------------------------------
# Exécution de FFmpeg (processus enfant, annulable)
# ---------------------------------------------------------------------------


def run_ffmpeg(
    command: list[str],
    cancel: threading.Event,
    on_progress: Callable[[float], None],
    on_start: Callable[[int], None] | None = None,
) -> RunResult:
    """Lance ``command`` et le tue dès que ``cancel`` est posé.

    La progression (``out_time_us=`` de ``-progress pipe:1``) est rapportée
    en secondes de média encodées. Les deux tubes sont lus par des threads :
    un FFmpeg bavard ne peut pas se bloquer sur un tube plein.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if on_start is not None:
        on_start(process.pid)
    lines: queue.Queue[bytes] = queue.Queue()
    stderr_chunks: list[bytes] = []

    def pump_stdout() -> None:
        assert process.stdout is not None
        for raw in iter(process.stdout.readline, b""):
            lines.put(raw)

    def pump_stderr() -> None:
        assert process.stderr is not None
        for raw in iter(process.stderr.readline, b""):
            if sum(len(chunk) for chunk in stderr_chunks) < 20_000:
                stderr_chunks.append(raw)

    readers = [threading.Thread(target=pump_stdout, daemon=True),
               threading.Thread(target=pump_stderr, daemon=True)]
    for reader in readers:
        reader.start()
    cancelled = False
    while True:
        try:
            raw = lines.get(timeout=0.05)
        except queue.Empty:
            raw = b""
        if raw.startswith((b"out_time_us=", b"out_time_ms=")):
            try:
                on_progress(int(raw.split(b"=", 1)[1]) / 1_000_000)
            except ValueError:
                pass
        if cancel.is_set() and process.poll() is None:
            cancelled = True
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.poll() is not None and lines.empty():
            break
    for reader in readers:
        reader.join(timeout=2)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    return RunResult(
        returncode=process.returncode,
        stderr=b"".join(stderr_chunks).decode("utf-8", "replace"),
        cancelled=cancelled,
    )


def _default_ffmpeg_command() -> list[str]:
    path = find_media_tool("ffmpeg")
    if not path:
        raise FileNotFoundError("FFmpeg est introuvable : génération de proxies impossible.")
    return [str(path)]


# ---------------------------------------------------------------------------
# Gestionnaire
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    source: str
    profile: ProxyProfile
    duration: float
    cancel: threading.Event
    progress: int = 0
    pid: int = 0


class ProxyManager:
    """Proxies d'un ensemble de médias, pour un dossier de cache donné."""

    def __init__(
        self,
        directory: str | os.PathLike[str] | None = None,
        *,
        profile_id: str = DEFAULT_PROFILE_ID,
        enabled: bool = True,
        ffmpeg_command: Callable[[], list[str]] | None = None,
        runner: Runner | None = None,
        max_parallel: int = 1,
        memo: SignatureMemo | None = None,
        clock: Callable[[], float] = time.time,
        disk_ttl: float = DISK_STATE_TTL_SECONDS,
    ) -> None:
        self.directory = Path(directory) if directory is not None else user_cache_dir() / "proxies"
        self._profile_id = get_profile(profile_id).id
        self._enabled = bool(enabled)
        self._ffmpeg_command = ffmpeg_command or _default_ffmpeg_command
        self._runner = runner or run_ffmpeg
        self._max_parallel = max(1, int(max_parallel))
        self._memo = memo or default_signatures
        self._clock = clock
        self._disk_ttl = float(disk_ttl)
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._pending: OrderedDict[tuple[str, str], _Job] = OrderedDict()
        self._active: dict[tuple[str, str], _Job] = {}
        self._errors: dict[tuple[str, str], str] = {}
        self._disk_cache: dict[tuple[str, str], tuple[float, ProxyInfo]] = {}
        self._touched: dict[str, float] = {}
        self._listeners: list[Callable[[str, ProxyInfo], None]] = []
        self._threads: list[threading.Thread] = []
        self._stopping = False

    # ------------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Active ou coupe l'usage des proxies pour l'aperçu (les fichiers restent)."""
        self._enabled = bool(enabled)

    @property
    def profile(self) -> ProxyProfile:
        return get_profile(self._profile_id)

    def set_profile(self, profile_id: str) -> None:
        self._profile_id = get_profile(profile_id).id

    def subscribe(self, callback: Callable[[str, ProxyInfo], None]) -> None:
        """Rappel ``callback(source_path, info)`` à chaque changement d'état.

        Appelé depuis le thread de génération : une interface Qt doit
        relayer par un signal (connexion en file) plutôt que toucher ses
        widgets directement.
        """
        self._listeners.append(callback)

    def _notify(self, source: str, profile_id: str) -> None:
        info = self.info(source, profile_id)
        for callback in list(self._listeners):
            try:
                callback(source, info)
            except Exception:  # un abonné fautif ne doit pas casser la génération
                pass

    # ------------------------------------------------------------------
    # Emplacements
    # ------------------------------------------------------------------

    def _paths(self, source: str, profile: ProxyProfile) -> tuple[Path, Path, Path]:
        base = proxy_key(source, profile.fingerprint())
        ext = profile.extension
        return (
            self.directory / f"{base}.{ext}",
            self.directory / f"{base}.partial.{ext}",
            self.directory / f"{base}.json",
        )

    # ------------------------------------------------------------------
    # État
    # ------------------------------------------------------------------

    def info(self, source_path: str, profile_id: str | None = None) -> ProxyInfo:
        """État courant du proxy de ``source_path`` pour ce profil (par défaut : le courant)."""
        profile = get_profile(profile_id or self._profile_id)
        source = os.path.abspath(source_path)
        key = (source, profile.id)
        with self._lock:
            job = self._active.get(key)
            if job is not None:
                return ProxyInfo(source, profile.id, ProxyState.GENERATING, progress=job.progress)
            if key in self._pending:
                return ProxyInfo(source, profile.id, ProxyState.PENDING)
            error = self._errors.get(key)
            if error is not None:
                return ProxyInfo(source, profile.id, ProxyState.ERROR, error=error)
            cached = self._disk_cache.get(key)
            if cached is not None and self._clock() - cached[0] < self._disk_ttl:
                return cached[1]
        info = self._read_disk(source, profile)
        with self._lock:
            self._disk_cache[key] = (self._clock(), info)
        return info

    def _read_disk(self, source: str, profile: ProxyProfile) -> ProxyInfo:
        final, _partial, sidecar = self._paths(source, profile)
        try:
            marker = json.loads(sidecar.read_text(encoding="utf-8"))
            recorded = str(marker["signature"])
            size = int(marker["size_bytes"])
            created = float(marker.get("created_at", 0.0))
            if (
                int(marker.get("version", 0)) != SIDECAR_VERSION
                or marker.get("fingerprint") != profile.fingerprint()
            ):
                raise ValueError("marqueur incompatible")
            actual = final.stat().st_size
        except (OSError, ValueError, KeyError, TypeError):
            return ProxyInfo(source, profile.id, ProxyState.NONE)
        if actual != size or actual <= 0:
            # Proxy tronqué ou remplacé : jamais utilisable.
            return ProxyInfo(source, profile.id, ProxyState.NONE)
        signature = self._memo.get(source)
        if signature is None:
            return ProxyInfo(
                source, profile.id, ProxyState.READY, str(final), progress=100,
                size_bytes=actual, created_at=created, source_signature=recorded,
                source_missing=True,
            )
        state = ProxyState.READY if signature.token == recorded else ProxyState.STALE
        return ProxyInfo(
            source, profile.id, state, str(final),
            progress=100 if state is ProxyState.READY else 0, size_bytes=actual,
            created_at=created, source_signature=recorded,
        )

    def _invalidate_disk_state(self, source: str, profile_id: str | None = None) -> None:
        with self._lock:
            for key in [k for k in self._disk_cache if k[0] == source and (profile_id is None or k[1] == profile_id)]:
                del self._disk_cache[key]
        self._memo.invalidate(source)

    # ------------------------------------------------------------------
    # Résolution (aperçu / export)
    # ------------------------------------------------------------------

    def resolve(self, path: str, *, need_audio: bool = False, divisor: int = 1) -> str:
        """Chemin à lire pour l'**aperçu** : proxy valide si possible, sinon ``path``.

        Retombe sur l'original quand les proxies sont coupés, absents,
        obsolètes, incomplets, supprimés à la main, ou — avec
        ``need_audio`` — quand le profil n'embarque pas l'audio.
        ``divisor`` (qualité d'aperçu réduite) préfère un profil plus
        léger **déjà prêt** ; il ne déclenche jamais de génération.
        """
        if not self._enabled or not path:
            return path
        for profile in self._candidate_profiles(divisor):
            if need_audio and not profile.keeps_audio:
                continue
            info = self.info(path, profile.id)
            if not info.valid or not info.proxy_path:
                continue
            # Le fichier a pu être supprimé depuis la dernière lecture du disque.
            if not self._memo.exists(info.proxy_path):
                self._invalidate_disk_state(info.source_path, profile.id)
                continue
            self._touch_last_used(info)
            return info.proxy_path
        return path

    def _candidate_profiles(self, divisor: int) -> list[ProxyProfile]:
        current = self.profile
        if int(divisor) <= 1:
            return [current]
        target = max(2, current.max_height // int(divisor))
        # Du plus léger au plus lourd : le premier proxy **prêt** assez net pour la
        # résolution réduite est retenu ; sinon le profil courant.
        lighter = [p for p in available_profiles() if target <= p.max_height < current.max_height]
        return [*lighter, current]

    def lighter_profile(self, divisor: int) -> ProxyProfile | None:
        """Profil le plus léger encore assez net pour ``divisor``, ``None`` s'il n'y en a pas."""
        if int(divisor) <= 1:
            return None
        current = self.profile
        target = max(2, current.max_height // int(divisor))
        lighter = [p for p in available_profiles() if target <= p.max_height < current.max_height]
        return lighter[0] if lighter else None

    def request_lighter(self, path: str, divisor: int, *, duration: float = 0.0) -> ProxyState | None:
        """Planifie (sans bloquer) le proxy léger adapté à une qualité réduite.

        Sans effet (``None``) si les proxies sont coupés ou s'il n'existe pas de
        profil plus léger ; un proxy léger déjà prêt n'est pas régénéré.
        """
        profile = self.lighter_profile(divisor)
        if not self._enabled or profile is None or not path:
            return None
        return self.request(path, profile_id=profile.id, duration=duration)

    def _touch_last_used(self, info: ProxyInfo) -> None:
        now = self._clock()
        if now - self._touched.get(info.source_path, 0.0) < LAST_USED_REFRESH_SECONDS:
            return
        self._touched[info.source_path] = now
        _final, _partial, sidecar = self._paths(info.source_path, get_profile(info.profile_id))
        try:
            os.utime(sidecar, (now, now))
        except OSError:
            pass

    def preview_resolver(self) -> Callable[..., str]:
        """Fonction ``(path, need_audio=False) -> path`` pour l'aperçu fidèle."""
        return lambda path, need_audio=False: self.resolve(path, need_audio=need_audio)

    def resolve_for_export(self, path: str, *, allow_proxy_for_debug: bool = False) -> str:
        """Chemin à utiliser pour l'**export** : toujours l'original.

        ``allow_proxy_for_debug=True`` est une option de débogage
        explicite ; elle n'est jamais activée par l'interface.
        """
        if allow_proxy_for_debug:
            return self.resolve(path)
        return path

    # ------------------------------------------------------------------
    # Génération
    # ------------------------------------------------------------------

    def request(
        self,
        path: str,
        *,
        profile_id: str | None = None,
        duration: float = 0.0,
        force: bool = False,
    ) -> ProxyState:
        """Planifie la génération du proxy de ``path`` (sans bloquer).

        Sans effet si un proxy valide existe déjà (sauf ``force``) ou si
        une génération est déjà planifiée. Retourne l'état résultant.
        """
        profile = get_profile(profile_id or self._profile_id)
        source = os.path.abspath(path)
        key = (source, profile.id)
        if self._memo.get(source) is None:
            with self._lock:
                self._errors[key] = "Média original introuvable."
            self._notify(source, profile.id)
            return ProxyState.ERROR
        with self._lock:
            if self._stopping:
                return ProxyState.NONE
            if key in self._active:
                return ProxyState.GENERATING
            if key in self._pending:
                return ProxyState.PENDING
        if not force and self.info(source, profile.id).valid:
            return ProxyState.READY
        with self._lock:
            self._errors.pop(key, None)
            self._pending[key] = _Job(source, profile, float(duration), threading.Event())
            self._ensure_workers()
            self._wake.notify()
        self._invalidate_disk_state(source, profile.id)
        self._notify(source, profile.id)
        return ProxyState.PENDING

    def request_many(self, items: Iterable[tuple[str, float]], **kwargs) -> dict[str, ProxyState]:
        """Planifie plusieurs médias ``(chemin, durée)`` ; retourne l'état de chacun."""
        return {path: self.request(path, duration=duration, **kwargs) for path, duration in items}

    def regenerate(self, path: str, *, profile_id: str | None = None, duration: float = 0.0) -> ProxyState:
        """Supprime le proxy puis le génère de nouveau."""
        self.delete(path, profile_id=profile_id)
        return self.request(path, profile_id=profile_id, duration=duration, force=True)

    def cancel(self, path: str, *, profile_id: str | None = None) -> bool:
        """Annule la génération (planifiée ou en cours). ``False`` s'il n'y en avait pas."""
        profile = get_profile(profile_id or self._profile_id)
        source = os.path.abspath(path)
        key = (source, profile.id)
        with self._lock:
            pending = self._pending.pop(key, None)
            active = self._active.get(key)
            if active is not None:
                active.cancel.set()
        if pending is None and active is None:
            return False
        if pending is not None:
            self._notify(source, profile.id)
        return True

    def cancel_all(self) -> int:
        """Annule tout ; retourne le nombre de générations concernées."""
        with self._lock:
            keys = list(self._pending) + list(self._active)
            self._pending.clear()
            for job in self._active.values():
                job.cancel.set()
        for source, profile_id in keys:
            self._notify(source, profile_id)
        return len(keys)

    def _ensure_workers(self) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        while len(self._threads) < self._max_parallel:
            thread = threading.Thread(target=self._worker, name="kut-proxy", daemon=True)
            self._threads.append(thread)
            thread.start()

    def _worker(self) -> None:
        while True:
            with self._wake:
                while not self._pending and not self._stopping:
                    self._wake.wait(timeout=0.5)
                if self._stopping:
                    return
                key, job = self._pending.popitem(last=False)
                self._active[key] = job
            self._notify(*key)
            try:
                self._generate(key, job)
            finally:
                with self._lock:
                    self._active.pop(key, None)
                self._invalidate_disk_state(*key)
                self._notify(*key)

    def _generate(self, key: tuple[str, str], job: _Job) -> None:
        source, profile = job.source, job.profile
        final, partial, sidecar = self._paths(source, profile)
        # Fichier partiel propre à CETTE génération : deux instances de l'application (ou un FFmpeg survivant d'un
        # plantage) qui produisent le même proxy ne doivent pas écrire dans le même fichier.
        partial = partial.with_name(
            partial.name.replace(".partial.", f".partial.{os.getpid()}-{uuid.uuid4().hex[:8]}.", 1)
        )
        error = ""
        promoted = False        # ce proxy-ci est-il passé de partiel à final ? (seul ce cas autorise à nettoyer final)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            signature = self._memo.get(source)
            if signature is None:
                raise FileNotFoundError("Média original introuvable.")
            def build(args_for) -> list[str]:
                return [
                    *self._ffmpeg_command(),
                    "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-progress", "pipe:1", "-nostats",
                    # Décodage matériel de la source (4K, HEVC 10 bits…) si validé et
                    # utile ; relancé en CPU s'il échoue (voir core.decode_policy).
                    *args_for(source),
                    "-i", source,
                    *profile.ffmpeg_output_args(),
                    str(partial),
                ]

            def on_progress(seconds: float) -> None:
                if job.duration > 0:
                    value = max(0, min(99, int(seconds / job.duration * 100)))
                    if value != job.progress:
                        job.progress = value
                        self._notify(*key)

            def on_start(pid: int) -> None:
                job.pid = pid

            runs: list[RunResult] = []

            def run(command: list[str]) -> tuple[int, str]:
                result = self._runner(command, job.cancel, on_progress, on_start)
                runs.append(result)
                if result.cancelled or job.cancel.is_set():
                    return 0, ""  # une annulation n'est pas un échec du décodeur
                return result.returncode, result.stderr

            from .decode_policy import DecodePurpose, run_with_decode_fallback

            run_with_decode_fallback(build, run, paths=[source], purpose=DecodePurpose.PROXY)
            result = runs[-1]
            if result.cancelled or job.cancel.is_set():
                self._remove(partial)
                return
            if result.returncode != 0:
                error = _explain_ffmpeg_error(result.stderr)
            elif not partial.is_file() or partial.stat().st_size <= 0:
                error = "FFmpeg n'a produit aucun fichier."
            else:
                size = partial.stat().st_size
                os.replace(partial, final)
                promoted = True
                self._write_sidecar(sidecar, source, signature.token, profile, size)
        except FileNotFoundError as exc:
            error = str(exc)
        except OSError as exc:
            error = _explain_os_error(exc)
        except Exception as exc:  # un échec de proxy ne doit jamais remonter plus haut
            error = f"Génération du proxy impossible : {exc}"
        if error:
            self._remove(partial)
            if promoted:
                # Demi-promotion (le marqueur n'a pas pu être écrit) : on ne laisse pas un proxy sans marqueur.
                # Sans promotion, ``final`` et le marqueur sont peut-être ceux d'une AUTRE instance qui vient de
                # réussir : on n'y touche pas.
                self._remove(final)
                self._remove(sidecar)
            with self._lock:
                self._errors[key] = error

    def _write_sidecar(
        self, sidecar: Path, source: str, signature: str, profile: ProxyProfile, size: int
    ) -> None:
        payload = {
            "version": SIDECAR_VERSION,
            "source": source,
            "signature": signature,
            "profile": profile.id,
            "fingerprint": profile.fingerprint(),
            "created_at": self._clock(),
            "size_bytes": size,
        }
        # Temporaire propre à cet appel : deux instances qui terminent en même temps ne se disputent pas le même
        # fichier (sous Windows le second remplacement échouait, et l'erreur supprimait le proxy tout juste produit).
        temporary = sidecar.with_name(f"{sidecar.stem}.{os.getpid()}-{uuid.uuid4().hex[:8]}.json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, sidecar)

    @staticmethod
    def _remove(path: Path) -> bool:
        """Supprime un fichier ; ``False`` s'il est toujours là (tenu ouvert, droits)."""
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    # ------------------------------------------------------------------
    # Suppression, inventaire
    # ------------------------------------------------------------------

    def delete(self, path: str, *, profile_id: str | None = None) -> bool:
        """Supprime le proxy (et annule sa génération). ``False`` s'il n'y avait rien."""
        profile = get_profile(profile_id or self._profile_id)
        source = os.path.abspath(path)
        self.cancel(source, profile_id=profile.id)
        # Une génération en cours doit être terminée avant de retirer ses fichiers.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with self._lock:
                if (source, profile.id) not in self._active:
                    break
            time.sleep(0.01)
        removed = False
        for target in self._paths(source, profile):
            if target.exists():
                self._remove(target)
                removed = True
        with self._lock:
            self._errors.pop((source, profile.id), None)
        self._invalidate_disk_state(source, profile.id)
        self._notify(source, profile.id)
        return removed

    def delete_all(self) -> int:
        """Supprime tous les proxies du dossier. Retourne le nombre de fichiers retirés."""
        self.cancel_all()
        self._wait_idle()
        removed = 0
        if self.directory.is_dir():
            for entry in list(self.directory.iterdir()):
                if entry.name.startswith("proxy-") and entry.is_file():
                    self._remove(entry)
                    removed += 1
        with self._lock:
            self._disk_cache.clear()
            self._errors.clear()
        self._memo.invalidate()
        return removed

    def entries(self) -> list[tuple[Path, int, float]]:
        """Proxies complets du dossier : ``(chemin, taille, dernier usage)``.

        Le dernier usage est la date du fichier compagnon (rafraîchie à la
        lecture). Sert à l'éviction LRU du gestionnaire de cache global.
        """
        result: list[tuple[Path, int, float]] = []
        if not self.directory.is_dir():
            return result
        for sidecar in self.directory.glob("proxy-*.json"):
            base = sidecar.with_suffix("")
            for candidate in (base.with_suffix(".mp4"), base.with_suffix(".mov")):
                if candidate.is_file():
                    try:
                        result.append((candidate, candidate.stat().st_size, sidecar.stat().st_mtime))
                    except OSError:
                        pass
        return result

    def usage_bytes(self) -> int:
        """Octets occupés par les proxies (fichiers partiels compris)."""
        total = 0
        if self.directory.is_dir():
            for entry in self.directory.iterdir():
                if entry.name.startswith("proxy-") and entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        return total

    def evict_path(self, proxy_file: Path) -> bool:
        """Retire un proxy (et son marqueur) sur demande du gestionnaire de cache.

        ``False`` si le proxy est toujours sur le disque : l'appelant ne doit pas le compter comme libéré.
        """
        # Le marqueur d'abord : sans lui, un proxy qui resterait n'est plus jamais servi (il est « sans marqueur »).
        self._remove(proxy_file.with_suffix(".json"))
        removed = self._remove(proxy_file)
        with self._lock:
            self._disk_cache.clear()
        return removed

    def cleanup_orphans(self) -> int:
        """Supprime fichiers partiels **abandonnés** et proxies sans marqueur (reste d'un arrêt brutal).

        Un fichier partiel récent est laissé : il peut appartenir à une génération en cours dans une autre
        instance de l'application (voir :data:`PARTIAL_STALE_SECONDS`).
        """
        removed = 0
        if not self.directory.is_dir():
            return 0
        with self._lock:
            busy = bool(self._active)
        if busy:
            return 0
        for entry in list(self.directory.iterdir()):
            if not entry.name.startswith("proxy-") or not entry.is_file():
                continue
            incomplete = (".partial." in entry.name or entry.name.endswith(".tmp")) and self._is_stale(entry)
            orphan = (
                entry.suffix in (".mp4", ".mov")
                and ".partial." not in entry.name
                and not entry.with_suffix(".json").is_file()
            )
            if incomplete or orphan:
                self._remove(entry)
                removed += 1
        return removed

    def _is_stale(self, entry: Path) -> bool:
        """Fichier non modifié depuis longtemps (ou illisible) : abandonné, sans propriétaire vivant."""
        try:
            return time.time() - entry.stat().st_mtime > PARTIAL_STALE_SECONDS
        except OSError:
            return True

    def active_process_ids(self) -> list[int]:
        """PID des FFmpeg de génération en cours (diagnostic, tests)."""
        with self._lock:
            return [job.pid for job in self._active.values() if job.pid]

    # ------------------------------------------------------------------
    # Fermeture
    # ------------------------------------------------------------------

    def _wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._active and not self._pending:
                    return True
            time.sleep(0.01)
        return False

    def shutdown(self, timeout: float = 5.0) -> bool:
        """Annule tout, tue les FFmpeg en cours et attend les threads.

        Retourne ``True`` si plus aucun processus de génération ne survit.
        """
        with self._wake:
            self._stopping = True
            self._pending.clear()
            for job in self._active.values():
                job.cancel.set()
            self._wake.notify_all()
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self._threads)


def _explain_ffmpeg_error(stderr: str) -> str:
    text = (stderr or "").strip()
    lowered = text.lower()
    if "no space left" in lowered:
        return "Espace disque insuffisant pour générer le proxy."
    if "permission denied" in lowered:
        return "Accès refusé au dossier de cache des proxies."
    return (text[-400:] if text else "FFmpeg a échoué sans message.")


def _explain_os_error(exc: OSError) -> str:
    if exc.errno == errno.ENOSPC:
        return "Espace disque insuffisant pour générer le proxy."
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "Dossier de cache des proxies inaccessible en écriture."
    return f"Erreur disque : {exc}"


__all__ = [
    "ProxyInfo",
    "ProxyManager",
    "ProxyState",
    "RunResult",
    "run_ffmpeg",
]
