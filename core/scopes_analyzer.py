"""Analyse d'image pour les scopes, hors du thread Qt (tâche 31).

Ce module orchestre l'extraction d'une **frame réellement composée**
(via FFmpeg, avec le même graphe de filtres que l'export : effets,
étalonnage, LUT, courbes) puis son analyse par :mod:`core.scopes`.

Trois responsabilités :

1. :func:`extract_frame_png` — exécuter une commande ``ffmpeg``
   produisant **une** frame composée sur stdout, en réutilisant le
   graphe de filtres déjà implémenté par :mod:`core.export_engine` ;
2. :class:`ScopeAnalyzer` — coordinateur d'analyse : thread de
   travail, file « le dernier gagne », limitation de fréquence,
   annulation des analyses obsolètes ;
3. :class:`ScopeRequest` / :class:`ScopeAnalysis` — descripteurs
   immuables et résultats.

Le module ne dépend **ni de Qt ni de PySide6** : le panneau Qt
pilote l'analyseur et reçoit les résultats via un callback appelé
depuis le thread de travail. L'annulation repose sur les
identifiants monotones des demandes : tout résultat dont l'identifiant
a été dépassé pendant l'analyse est marqué ``stale`` et n'est jamais
affiché.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .scopes import (
    ColorSpace,
    ScopeFrame,
    ScopeResult,
    VideoLevels,
    analyze_frame,
)


# ---------------------------------------------------------------------------
# Extraction de frame
# ---------------------------------------------------------------------------


class ScopeExtractionError(RuntimeError):
    """L'extraction de la frame d'analyse a échoué."""


def extract_frame_png(
    ffmpeg_command: list[str],
    *,
    timeout: float = 20.0,
) -> bytes:
    """Exécute une commande ``ffmpeg`` et renvoie les octets PNG.

    La commande doit produire une image PNG **sur stdout** (donc
    ``-f image2pipe -vcodec png`` ou équivalent). Cette fonction
    encapsule l'appel processus, la capture de stdout et la gestion
    d'erreur.

    Raises:
        ScopeExtractionError: si ``ffmpeg`` retourne un code != 0 ou
            si la sortie est vide.
    """
    try:
        completed = subprocess.run(
            ffmpeg_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ScopeExtractionError(
            "FFmpeg introuvable sur le PATH : impossible d'analyser "
            "l'image pour les scopes."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ScopeExtractionError(
            f"Extraction de la frame d'analyse expirée "
            f"(>{timeout:.0f}s)."
        ) from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise ScopeExtractionError(
            f"FFmpeg a échoué (code {completed.returncode}) : {stderr}"
        )
    if not completed.stdout:
        raise ScopeExtractionError(
            "FFmpeg n'a produit aucune image pour l'analyse."
        )
    return completed.stdout


def png_to_scope_frame(
    png_bytes: bytes,
    *,
    width: int | None = None,
    height: int | None = None,
) -> ScopeFrame:
    """Convertit des octets PNG en :class:`ScopeFrame` (sans Qt).

    On évite une dépendance à PySide6 ici : le décodage PNG pur
    Python n'est pas raisonnable pour une image 1080p, donc on
    délègue au décodeur Qt si disponible, sinon on raise.

    Raises:
        ScopeExtractionError: si aucune bibliothèque de décodage
            n'est disponible.
    """
    try:
        from PySide6.QtGui import QImage
    except ImportError as exc:  # pragma: no cover - PySide6 toujours là
        raise ScopeExtractionError(
            "Le décodage PNG nécessite PySide6 (QtGui.QImage)."
        ) from exc

    image = QImage()
    # Les octets, pas un QBuffer : PySide6 refuse (TypeError) un QBuffer ici, et toute analyse échouait.
    if not image.loadFromData(png_bytes, "PNG"):
        raise ScopeExtractionError("Impossible de décoder la frame PNG.")
    if width is None or height is None:
        width = image.width()
        height = image.height()
    rgb = image.convertToFormat(QImage.Format_RGB888)
    # En Qt 6, ``bits()`` renvoie un memoryview ; on obtient les
    # octets via ``bytes(rgb.constBits())`` pour éviter toute copie
    # supplémentaire si possible.
    raw = bytes(rgb.constBits())[: rgb.sizeInBytes()]
    return ScopeFrame.from_rgb_bytes(raw, rgb.width(), rgb.height())


# ---------------------------------------------------------------------------
# Descripteurs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeRequest:
    """Une demande d'analyse, identifiée de façon unique.

    L'identifiant est composé de la position sur la timeline et d'un
    compteur monotone. C'est ce qui permet d'annuler les analyses
    devenues obsolètes : un nouveau déplacement de la tête de lecture
    produit un identifiant plus élevé, et l'analyseur abandonne toute
    demande plus ancienne.

    Attributes:
        request_id: Identifiant monotone, unique et croissant.
        playhead: Position sur la timeline (secondes).
        ffmpeg_command: Commande ``ffmpeg`` à exécuter pour extraire
            la frame composée.
        color_space: Espace colorimétrique utilisé pour la luminance.
        levels: Mode de niveaux (``"video"`` / ``"full"``).
        columns: Nombre de colonnes des scopes.
        vectorscope_bins: Résolution du vectorscope.
        clip_tolerance: Tolérance d'écrêtage (niveaux 0‑255).
        source: Étiquette du clip source (diagnostic).
        temporary_paths: Fichiers créés exclusivement pour cette demande,
            supprimés dès que l'analyse se termine ou est abandonnée.
    """

    request_id: int
    playhead: float
    ffmpeg_command: tuple[str, ...]
    color_space: ColorSpace = ColorSpace.REC709
    levels: VideoLevels = VideoLevels.VIDEO
    columns: int = 320
    vectorscope_bins: int = 128
    clip_tolerance: int = 0
    source: str = ""
    temporary_paths: tuple[str, ...] = ()


def cleanup_temporary_paths(paths: tuple[str, ...] | list[str]) -> None:
    """Supprime au mieux les fichiers possédés par une demande de scopes."""
    for path in paths:
        try:
            os.remove(str(path))
        except OSError:
            pass


@dataclass
class ScopeAnalysis:
    """Résultat d'une analyse réussie.

    Attributes:
        request: La demande d'origine.
        result: Les quatre scopes calculés.
        elapsed_seconds: Durée totale (extraction + calcul).
        stale: ``True`` si le résultat a été produit pour une
            demande devenue obsolète (il ne doit alors pas être
            affiché).
    """

    request: ScopeRequest
    result: ScopeResult
    elapsed_seconds: float
    stale: bool = False


# ---------------------------------------------------------------------------
# Analyseur
# ---------------------------------------------------------------------------


class ScopeAnalyzer:
    """Orchestrateur d'analyse de scopes, sans dépendance Qt.

    L'analyseur maintient :

    - un compteur monotone ``_next_id`` pour numéroter les demandes ;
    - un verrou (``_lock``) protégeant l'état partagé ;
    - l'identifiant de la demande **en cours** ; toute demande
      d'un identifiant inférieur est considérée obsolète et
      immédiatement annulée ;
    - une limitation de fréquence (``_min_interval``) pour éviter de
      lancer une analyse à chaque frame pendant la lecture.

    **Asynchronisme.** Tout le traitement lourd — appel FFmpeg,
    décodage PNG, calcul des quatre scopes — s'exécute sur un unique
    *thread de travail* daemon, jamais dans le thread appelant. Le
    thread GUI ne fait donc que déposer une demande et recevoir un
    callback : l'interface Qt reste fluide pendant la lecture.

    La file d'attente est à **une seule place, « le dernier gagne »** :
    si une nouvelle demande arrive pendant l'analyse en cours, la
    précédente est marquée obsolète et remplacée. C'est exactement le
    comportement souhaité quand l'utilisateur déplace rapidement la
    tête de lecture : on n'analyse pas des images qui ne sont plus
    affichées.

    Le callback ``on_result`` est appelé depuis le thread de travail ;
    l'appelant (le panneau Qt) est responsable de repasser dans le
    thread GUI via un signal Qt ou ``QTimer.singleShot(0, ...)``.
    """

    def __init__(
        self,
        *,
        on_result: Optional[Callable[[ScopeAnalysis], None]] = None,
        on_error: Optional[Callable[[ScopeRequest, BaseException], None]] = None,
        min_interval: float = 0.1,
        extractor: Callable[[ScopeRequest], ScopeFrame] | None = None,
    ) -> None:
        self._on_result = on_result
        self._on_error = on_error
        # ``min_interval`` borne la fréquence de lancement : pendant
        # la lecture, on ne veut pas analyser plus de 10 images / s.
        self._min_interval = max(0.0, float(min_interval))
        # Extraction.injectable pour les tests (évite FFmpeg).
        self._extractor = extractor
        self._lock = threading.Lock()
        self._next_id = 1
        # Identifiant de la dernière demande acceptée (la plus
        # récente). Tout ce qui est antérieur est obsolète.
        self._latest_id = 0
        # Identifiant de la demande en cours de traitement.
        self._current_id = 0
        self._last_launch_monotonic = 0.0
        # Statistiques (utile pour les tests de performance).
        self._launched = 0
        self._cancelled = 0
        self._completed = 0
        # File d'attente à une seule place + réveil du thread de
        # travail. ``_pending`` contient la demande la plus récente
        # non encore démarrée ; une demande plus récente la
        # remplace purement et simplement.
        self._pending: Optional[ScopeRequest] = None
        self._pending_frame: Optional[ScopeFrame] = None
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._closed = False
        self._thread: Optional[threading.Thread] = None
        self._ensure_thread()

    # ----- Cycle de vie ---------------------------------------------------

    def _ensure_thread(self) -> None:
        """Démarre le thread de travail au premier besoin (lazy)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="kut-scopes-analyzer",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Arrête le thread de travail (fermeture de l'application)."""
        with self._lock:
            self._closed = True
            pending = self._pending
            self._pending = None
            self._pending_frame = None
        if pending is not None:
            cleanup_temporary_paths(pending.temporary_paths)
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Attend que la file soit vide et qu'aucune analyse ne tourne.

        Réservé aux tests et à la fermeture propre : l'interface Qt
        n'a jamais à l'appeler, puisque tout passe par les callbacks.

        Returns:
            ``True`` si l'analyseur est devenu inactif dans le délai.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._lock:
                idle = self._pending is None and self._current_id == 0
            if idle and self._idle.is_set():
                return True
            time.sleep(0.005)
        with self._lock:
            return self._pending is None and self._current_id == 0

    # ----- API publique ---------------------------------------------------

    def submit(
        self,
        *,
        playhead: float,
        ffmpeg_command: list[str] | tuple[str, ...],
        color_space: ColorSpace | str = ColorSpace.REC709,
        levels: VideoLevels | str = VideoLevels.VIDEO,
        columns: int = 320,
        vectorscope_bins: int = 128,
        clip_tolerance: int = 0,
        source: str = "",
        temporary_paths: tuple[str, ...] | list[str] = (),
        force: bool = False,
    ) -> Optional[ScopeRequest]:
        """Soumet une demande d'analyse.

        Args:
            playhead: Position sur la timeline.
            ffmpeg_command: Commande d'extraction de la frame.
            force: Si ``True``, ignore la limitation de fréquence
                (utile quand la lecture est en pause et qu'un
                réglage vient de changer : on veut un refresh
                immédiat).

        Returns:
            La demande acceptée, ou ``None`` si elle a été rejetée
            (limitation de fréquence). La méthode est **non
            bloquante** : l'analyse a lieu sur le thread de travail.
        """
        now = time.monotonic()
        with self._lock:
            if self._closed:
                return None
            # Limitation de fréquence (sauf refresh forcé).
            if not force and self._min_interval > 0.0:
                elapsed = now - self._last_launch_monotonic
                if elapsed < self._min_interval:
                    return None
            request = ScopeRequest(
                request_id=self._next_id,
                playhead=float(playhead),
                ffmpeg_command=tuple(str(c) for c in ffmpeg_command),
                color_space=ColorSpace(color_space),
                levels=VideoLevels(levels),
                columns=int(columns),
                vectorscope_bins=int(vectorscope_bins),
                clip_tolerance=int(clip_tolerance),
                source=str(source),
                temporary_paths=tuple(str(path) for path in temporary_paths),
            )
            self._next_id += 1
            # La nouvelle demande invalide tout ce qui est en cours.
            self._latest_id = request.request_id
            self._last_launch_monotonic = now
            self._launched += 1
            self._enqueue(request)
        return request

    def submit_frame(
        self,
        frame: ScopeFrame,
        *,
        playhead: float = 0.0,
        color_space: ColorSpace | str = ColorSpace.REC709,
        levels: VideoLevels | str = VideoLevels.VIDEO,
        columns: int = 320,
        vectorscope_bins: int = 128,
        clip_tolerance: int = 0,
        force: bool = True,
        source: str = "",
    ) -> Optional[ScopeRequest]:
        """Variante de :meth:`submit` acceptant une image déjà extraite.

        Utile pour les tests et pour réutiliser une frame déjà
        décodée par le viewer (évite un second passage FFmpeg).
        Comme :meth:`submit`, elle est non bloquante.
        """
        now = time.monotonic()
        with self._lock:
            if self._closed:
                return None
            if not force and self._min_interval > 0.0:
                elapsed = now - self._last_launch_monotonic
                if elapsed < self._min_interval:
                    return None
            request = ScopeRequest(
                request_id=self._next_id,
                playhead=float(playhead),
                ffmpeg_command=(),
                color_space=ColorSpace(color_space),
                levels=VideoLevels(levels),
                columns=int(columns),
                vectorscope_bins=int(vectorscope_bins),
                clip_tolerance=int(clip_tolerance),
                source=str(source),
            )
            self._next_id += 1
            self._latest_id = request.request_id
            self._last_launch_monotonic = now
            self._launched += 1
            self._enqueue(request, frame=frame)
        return request

    def cancel_all(self) -> None:
        """Annule toute analyse en cours ou en attente."""
        with self._lock:
            self._cancelled += 1
            pending = self._pending
            self._pending = None
            self._pending_frame = None
            self._current_id = 0
        if pending is not None:
            cleanup_temporary_paths(pending.temporary_paths)

    def reset_rate_limit(self) -> None:
        """Réinitialise l'horloge de limitation de fréquence."""
        with self._lock:
            self._last_launch_monotonic = 0.0

    def stats(self) -> dict[str, int]:
        """Statistiques (tests de performance / diagnostic)."""
        with self._lock:
            return {
                "launched": self._launched,
                "cancelled": self._cancelled,
                "completed": self._completed,
            }

    # ----- Interne --------------------------------------------------------

    def _enqueue(
        self, request: ScopeRequest, frame: ScopeFrame | None = None,
    ) -> None:
        """Place la demande en file (verrou ``_lock`` déjà pris).

        Une demande en attente est écrasée : seule la plus récente
        nous intéresse, les précédentes sont sans objet puisque la
        tête de lecture a bougé.
        """
        if self._pending is not None:
            self._cancelled += 1
            cleanup_temporary_paths(self._pending.temporary_paths)
        self._pending = request
        self._pending_frame = frame
        self._idle.clear()
        self._wake.set()

    def _worker_loop(self) -> None:
        """Boucle du thread de travail.

        Elle ne fait rien d'autre que : attendre une demande, la
        traiter, recommencer. Toute exception est confinée à la
        demande courante pour ne jamais tuer le thread.
        """
        while True:
            # Pas de timeout : le ``clear`` est pris sous le même
            # verrou que le ``set`` de ``_enqueue``, donc aucun réveil
            # ne peut être perdu. ``close()`` réveille le thread pour
            # le terminer.
            self._wake.wait()
            # Le ``clear`` se fait sous le même verrou que
            # ``_enqueue`` : pas de réveil perdu entre le ``wait``
            # qui retourne et la lecture de la file.
            with self._lock:
                self._wake.clear()
                if self._closed:
                    return
                request = self._pending
                frame = self._pending_frame
                self._pending = None
                self._pending_frame = None
            if request is None:
                with self._lock:
                    if self._pending is None and self._current_id == 0:
                        self._idle.set()
                continue
            self._process(request, frame)

    def _process(
        self, request: ScopeRequest, frame: ScopeFrame | None,
    ) -> None:
        """Traite une demande : extraction puis calcul des scopes."""
        with self._lock:
            self._current_id = request.request_id
        try:
            if frame is not None:
                self._emit(request, frame)
            else:
                if self._extractor is not None:
                    extracted = self._extractor(request)
                else:
                    if not request.ffmpeg_command:
                        raise ScopeExtractionError(
                            "Aucune commande FFmpeg fournie pour l'analyse."
                        )
                    png = extract_frame_png(list(request.ffmpeg_command))
                    extracted = png_to_scope_frame(png)
                self._emit(request, extracted)
        except BaseException as exc:  # noqa: BLE001 - le thread survit
            if self._on_error is not None:
                self._on_error(request, exc)
        finally:
            cleanup_temporary_paths(request.temporary_paths)
            with self._lock:
                self._current_id = 0
                if self._pending is None:
                    self._idle.set()

    def _emit(self, request: ScopeRequest, frame: ScopeFrame) -> None:
        """Calcule les scopes et notifie le callback.

        Le résultat est toujours notifié, mais porte le drapeau
        ``stale`` lorsqu'une demande plus récente est arrivée pendant
        l'extraction : l'appelant (le panneau Qt) ignore alors le
        résultat au lieu d'afficher une image qui n'est plus visible
        dans le moniteur.
        """
        start = time.monotonic()
        with self._lock:
            already_stale = request.request_id < self._latest_id
        if already_stale:
            # La tête de lecture a déjà bougé : inutile de brûler
            # du CPU sur une image qui ne sera pas affichée.
            with self._lock:
                self._cancelled += 1
            if self._on_result is not None:
                self._on_result(
                    ScopeAnalysis(
                        request=request,
                        result=ScopeResult.empty(
                            columns=request.columns,
                            vectorscope_bins=request.vectorscope_bins,
                            color_space=request.color_space,
                            levels=request.levels,
                            pixel_count=len(frame.pixels),
                        ),
                        elapsed_seconds=time.monotonic() - start,
                        stale=True,
                    )
                )
            return
        result = analyze_frame(
            frame,
            columns=request.columns,
            vectorscope_bins=request.vectorscope_bins,
            color_space=request.color_space,
            levels=request.levels,
            clip_tolerance=request.clip_tolerance,
        )
        elapsed = time.monotonic() - start
        with self._lock:
            stale = request.request_id < self._latest_id
            if not stale:
                self._completed += 1
        analysis = ScopeAnalysis(
            request=request,
            result=result,
            elapsed_seconds=elapsed,
            stale=stale,
        )
        if self._on_result is not None:
            self._on_result(analysis)


__all__ = [
    "ScopeAnalysis",
    "ScopeAnalyzer",
    "ScopeExtractionError",
    "ScopeRequest",
    "extract_frame_png",
    "png_to_scope_frame",
]
