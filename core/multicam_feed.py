"""Flux d'images des angles pour le moniteur Multicam (sans Qt : des octets RGB et des instants).

Afficher N angles en même temps ne doit pas multiplier le coût du pipeline : chaque tuile lit **une petite image** (profil
``multicam_grid`` : 480×270 à 160×90 selon le nombre de tuiles), jamais la résolution du média. Un :class:`AngleFeed` est un
FFmpeg supervisé qui décode en continu vers un petit tampon d'images ; l'interface ne fait que **lire la dernière image
disponible** pour l'instant de la tête de lecture (``frame_at``), sans jamais attendre un décodage : si la suivante n'est
pas encore arrivée, la précédente reste affichée plutôt que de bloquer Qt.

* l'horloge est celle de la timeline : les tuiles sont synchronisées par construction, sans dérive entre angles ;
* un saut (déplacement de la tête de lecture) relance le décodage à l'instant voulu ; la lecture normale le laisse tourner ;
* le décodage est freiné (le tube se remplit, FFmpeg attend) dès que le tampon dépasse l'avance voulue : un angle ne
  consomme pas plus de processeur que nécessaire ; un flux inactif s'arrête de lui-même ;
* un :class:`FeedPool` ne garde en vie que les flux des tuiles visibles, l'angle actif d'abord ;
* :class:`TileQualityGovernor` baisse (ou relève) la qualité des tuiles selon le retard **mesuré**, sans règle rigide.

Les images viennent de proxys quand ils existent (l'appelant résout le chemin) ; les flux ne touchent jamais au projet.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .process_supervisor import default_supervisor
from .tool_paths import find_media_tool

LOGGER = logging.getLogger("kut_studio.multicam")

LOOKAHEAD_SECONDS = 1.5
"""Avance décodée au-delà de la tête de lecture ; plus loin, le décodage attend."""
BUFFER_SECONDS = 2.5
"""Durée d'images conservées derrière / devant (borne la mémoire d'un flux)."""
JUMP_SECONDS = 2.0
"""Écart de la cible au-delà duquel on relance le décodage au lieu d'attendre qu'il rattrape."""
IDLE_SECONDS = 6.0
"""Un flux qu'aucun appel n'a sollicité depuis ce délai libère son processus."""


@dataclass(frozen=True)
class TileProfile:
    """Résolution et cadence des images d'une tuile (profil ``multicam_grid``)."""

    name: str
    width: int
    height: int
    fps: float

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3


GRID_PROFILES: tuple[TileProfile, ...] = (
    TileProfile("high", 480, 270, 24.0),
    TileProfile("medium", 320, 180, 15.0),
    TileProfile("low", 240, 136, 10.0),
    TileProfile("minimal", 160, 90, 6.0),
)
"""Du plus fin au plus léger. 4 tuiles : ``high`` ; jusqu'à 9 : ``medium`` ; au-delà : ``low`` (le retard mesuré peut descendre plus bas)."""


def tile_profile(visible: int, *, level: int = 0, active: bool = False) -> TileProfile:
    """Profil d'une tuile selon le nombre de tuiles visibles et le niveau de dégradation (0 = aucune).

    L'angle actif (image du programme) prend un cran de plus de finesse que les autres.
    """
    base = 0 if visible <= 4 else 1 if visible <= 9 else 2
    index = min(len(GRID_PROFILES) - 1, base + max(0, level))
    if active:
        index = max(0, index - 1)
    return GRID_PROFILES[index]


@dataclass(frozen=True)
class Frame:
    """Une image RGB24 décodée (``data`` : ``width × height × 3`` octets) et son instant dans le média."""

    time: float
    width: int
    height: int
    data: bytes


class AngleFeed:
    """Décodage continu d'un média vers un petit tampon d'images, lu sans attente par l'interface."""

    def __init__(
        self,
        path: str,
        profile: TileProfile,
        *,
        command: Callable[[str, TileProfile, float], list[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.profile = profile
        self._command = command or _ffmpeg_command
        self._clock = clock
        self._lock = threading.Lock()
        self._frames: deque[Frame] = deque()
        self._max_frames = max(4, int(profile.fps * BUFFER_SECONDS))
        self._target = 0.0
        self._playing = False
        self._touched = clock()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._stream_start = 0.0
        self._eof = False
        self._held: Frame | None = None
        self.error = ""
        self.last_lag = 0.0

    # -- appelé par l'interface (rapide, sans attente) ------------------------------------------------------------

    def update(self, media_time: float, playing: bool) -> None:
        """Indique l'instant voulu ; relance le décodage s'il faut sauter, sinon le laisse tourner."""
        restart = False
        with self._lock:
            self._target = float(media_time)
            self._playing = bool(playing)
            self._touched = self._clock()
            alive = self._thread is not None and self._thread.is_alive()
            if self._frames:
                low, high = self._frames[0].time, self._frames[-1].time
            else:
                low = high = self._stream_start
            if self.error:
                return
            if not alive:
                restart = not (self._eof and media_time >= high)
                if self._eof and media_time < low - 0.05:
                    restart = True
            elif media_time < low - 0.05 or media_time > high + JUMP_SECONDS:
                restart = True
        if restart:
            self._start(media_time)

    def frame_at(self, media_time: float) -> Frame | None:
        """Dernière image décodée au plus tard à ``media_time`` ; à défaut, la plus proche, sinon l'image tenue."""
        with self._lock:
            chosen = None
            half = 0.5 / self.profile.fps
            for frame in self._frames:
                if frame.time <= media_time + half:
                    chosen = frame
                else:
                    break
            if chosen is None and self._frames and self._frames[0].time - media_time < 0.5:
                chosen = self._frames[0]
            if chosen is None:
                return self._held
            self._held = chosen
            self.last_lag = max(0.0, media_time - chosen.time)
            return chosen

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def close(self) -> None:
        """Arrête le décodage et libère le processus (appelé à la fermeture, aucun FFmpeg ne survit)."""
        with self._lock:
            self._generation += 1
            process, thread = self._process, self._thread
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    # -- décodage -----------------------------------------------------------------------------------------------------------

    def _start(self, media_time: float) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            previous = self._process
            self._stream_start = max(0.0, media_time)
            self._eof = False
            self._frames.clear()
            thread = threading.Thread(
                target=self._run, args=(generation, max(0.0, media_time)), name="kut-multicam-feed", daemon=True
            )
            self._thread = thread
        if previous is not None and previous.poll() is None:
            try:
                previous.kill()
            except OSError:
                pass
        thread.start()

    def _run(self, generation: int, start: float) -> None:
        supervisor = default_supervisor()
        profile = self.profile
        try:
            command = self._command(self.path, profile, start)
        except OSError as error:
            self._fail(generation, str(error))
            return
        try:
            process = supervisor.popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            self._fail(generation, str(error))
            return
        stdout, stderr = process.stdout, process.stderr
        if stdout is None or stderr is None:  # jamais : ``PIPE`` demandé ci-dessus
            supervisor.finish(process)
            return
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(stderr.read()), daemon=True)
        drain.start()
        with self._lock:
            if generation != self._generation:
                stale = True
            else:
                stale = False
                self._process = process
        produced = 0
        try:
            if stale:
                return
            index = 0
            while True:
                data = _read_exactly(stdout, profile.frame_bytes)
                if data is None:
                    break
                with self._lock:
                    if generation != self._generation:
                        return
                    self._frames.append(Frame(start + index / profile.fps, profile.width, profile.height, data))
                    while len(self._frames) > self._max_frames:
                        self._frames.popleft()
                    newest = self._frames[-1].time
                index += 1
                produced += 1
                while self._throttled(generation, newest):
                    time.sleep(0.02)
        finally:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                stdout.close()
            except OSError:
                pass
            supervisor.finish(process)
            drain.join(timeout=2.0)
            with self._lock:
                if self._process is process:
                    self._process = None
                if generation == self._generation:
                    self._eof = produced > 0
        if produced == 0 and generation == self._generation:
            message = b"".join(errors).decode("utf-8", "replace").strip().splitlines()
            self._fail(generation, message[-1] if message else "aucune image décodée")

    def _throttled(self, generation: int, newest: float) -> bool:
        with self._lock:
            if generation != self._generation:
                return False
            if self._clock() - self._touched > IDLE_SECONDS:
                self._generation += 1      # flux inactif : on libère le processus
                return False
            return newest > self._target + LOOKAHEAD_SECONDS

    def _fail(self, generation: int, reason: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            first = not self.error
            self.error = reason
        if first:
            LOGGER.warning("Multicam : flux de %s impossible (%s)", os.path.basename(self.path), reason)


def _ffmpeg_command(path: str, profile: TileProfile, start: float) -> list[str]:
    ffmpeg = find_media_tool("ffmpeg")
    if ffmpeg is None:
        raise OSError("FFmpeg est introuvable")
    command = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error"]
    if start > 0:
        command += ["-ss", f"{start:.6f}"]
    command += [
        "-i", path, "-an", "-sn",
        "-vf", (
            f"fps={profile.fps:g},scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease,"
            f"pad={profile.width}:{profile.height}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]
    return command


def _read_exactly(stream, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        data = stream.read(remaining)
        if not data:
            return None
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


class FeedPool:
    """Les flux en cours, un par (média, profil) : ne garde en vie que ce que les tuiles visibles demandent."""

    def __init__(self, factory: Callable[[str, TileProfile], AngleFeed] = AngleFeed) -> None:
        self._factory = factory
        self._feeds: dict[tuple[str, TileProfile], AngleFeed] = {}

    def feed(self, path: str, profile: TileProfile) -> AngleFeed:
        key = (path, profile)
        feed = self._feeds.get(key)
        if feed is None:
            feed = self._feeds[key] = self._factory(path, profile)
        return feed

    def retain(self, wanted: set[tuple[str, TileProfile]]) -> None:
        """Arrête les flux qui ne servent plus (tuile masquée, angle changé de profil, projet fermé)."""
        for key in [key for key in self._feeds if key not in wanted]:
            self._feeds.pop(key).close()

    def close_all(self) -> None:
        self.retain(set())

    def __len__(self) -> int:
        return len(self._feeds)



def lag_ratio(shown: list[tuple[AngleFeed, float]]) -> float:
    """Part des flux dont l'image affichée a plus de quatre périodes de retard sur la tête de lecture."""
    if not shown:
        return 0.0
    return sum(1 for feed, lag in shown if lag > 4.0 / feed.profile.fps) / len(shown)


class TileQualityGovernor:
    """Choisit le niveau de dégradation des tuiles (0 = pleine qualité) d'après le retard **mesuré**.

    Pas de règle rigide : si le matériel suit (retard rare pendant plusieurs secondes), la qualité remonte d'un cran ;
    si le retard persiste, elle baisse d'un cran, avec un délai entre deux changements (pas de va-et-vient).
    """

    def __init__(
        self, *, max_level: int = 3, degrade_after: float = 1.5, recover_after: float = 8.0, cooldown: float = 3.0,
        late: float = 0.35, calm: float = 0.05,
    ) -> None:
        self.max_level = max_level
        self.level = 0
        self._degrade_after, self._recover_after, self._cooldown = degrade_after, recover_after, cooldown
        self._late, self._calm = late, calm
        self._since_late: float | None = None
        self._since_calm: float | None = None
        self._changed_at = -1e9

    def observe(self, late_ratio: float, now: float) -> int:
        """Prend une mesure (part de flux en retard) à l'instant ``now`` ; retourne le niveau courant."""
        if late_ratio >= self._late:
            self._since_calm = None
            self._since_late = now if self._since_late is None else self._since_late
            if now - self._since_late >= self._degrade_after and self._can_change(now) and self.level < self.max_level:
                self.level += 1
                self._changed_at = now
                self._since_late = None
        elif late_ratio <= self._calm:
            self._since_late = None
            self._since_calm = now if self._since_calm is None else self._since_calm
            if now - self._since_calm >= self._recover_after and self._can_change(now) and self.level > 0:
                self.level -= 1
                self._changed_at = now
                self._since_calm = None
        else:
            self._since_late = self._since_calm = None
        return self.level

    def _can_change(self, now: float) -> bool:
        return now - self._changed_at >= self._cooldown

    def reset(self) -> None:
        self.level = 0
        self._since_late = self._since_calm = None


__all__ = [
    "BUFFER_SECONDS",
    "GRID_PROFILES",
    "IDLE_SECONDS",
    "JUMP_SECONDS",
    "LOOKAHEAD_SECONDS",
    "AngleFeed",
    "FeedPool",
    "Frame",
    "TileProfile",
    "TileQualityGovernor",
    "lag_ratio",
    "tile_profile",
]
