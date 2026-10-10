"""Détection des changements de plan d'un clip, et découpage du clip à ces changements.

FFmpeg mesure la différence entre images successives (``select='gt(scene, seuil)'``), et ``showinfo`` écrit l'instant
de chaque image retenue. La lecture commence au point de départ du clip par un seek d'entrée, qui ne décode que la
fenêtre du clip : les instants sont alors relatifs à ce point, et on les ramène au temps du média en y ajoutant le
point de départ. Vérifié sur un clip synthétique rouge puis bleu, coupé à 1,0 s : la coupe est trouvée à 1,0 s, y
compris depuis un point de départ de 0,5 s (les tests le refont avec un vrai FFmpeg).
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from .process_supervisor import supervised_popen
from .project_model import Clip, Project
from .timeline_operations import cut_clip, find_clip

SCENE_THRESHOLD = 0.3
"""Seuil par défaut de ``gt(scene, …)`` : assez haut pour ignorer le grain, assez bas pour un vrai changement de plan."""

THRESHOLD_LIMITS = (0.05, 0.95)
MIN_SHOT_SECONDS = 0.2
"""Un plan plus court n'est pas créé : une coupe à moins de 0,2 s d'un bord, ou d'une autre coupe, est écartée."""

_SHOWINFO_TIME = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_PROGRESS_TIME = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def scene_command(
    ffmpeg: str, media: str, *, start: float, duration: float, threshold: float = SCENE_THRESHOLD,
) -> list[str]:
    """Commande FFmpeg qui lit ``duration`` secondes du média à partir de ``start`` et liste les changements de plan."""
    low, high = THRESHOLD_LIMITS
    if not low <= threshold <= high:
        raise ValueError(f"Seuil de plan hors de [{low}, {high}] : {threshold}")
    if duration <= 0:
        raise ValueError("Durée de détection nulle")
    return [
        ffmpeg, "-hide_banner", "-nostdin",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}", "-i", media,
        "-an", "-vf", f"select='gt(scene,{threshold:.3f})',showinfo", "-f", "null", "-",
    ]


def parse_scene_times(text: str, start: float = 0.0) -> list[float]:
    """Instants des images retenues, ramenés au temps du média (``start`` : point de départ du seek)."""
    return [start + float(match.group(1)) for match in _SHOWINFO_TIME.finditer(text)]


def shot_boundaries(times: Sequence[float], low: float, high: float) -> list[float]:
    """Garde les coupes utiles : strictement à l'intérieur de ``[low, high]``, espacées d'au moins ``MIN_SHOT_SECONDS``."""
    kept: list[float] = []
    for time in sorted(times):
        if time - low < MIN_SHOT_SECONDS or high - time < MIN_SHOT_SECONDS:
            continue
        if kept and time - kept[-1] < MIN_SHOT_SECONDS:
            continue
        kept.append(time)
    return kept


@dataclass(frozen=True)
class SceneDetectionSnapshot:
    """État d'une détection, lu par l'interface : ``queued``, ``running``, ``finished``, ``cancelled`` ou ``failed``."""

    state: str
    progress: float
    message: str
    result: tuple[float, ...] | None


class SceneDetectionJob:
    """Détection des plans d'un clip, pour la file d'analyse : progression, annulation, résultat."""

    def __init__(
        self,
        ffmpeg: str,
        media: str,
        *,
        start: float,
        duration: float,
        threshold: float = SCENE_THRESHOLD,
        session_id: str = "",
    ) -> None:
        self.session_id = session_id
        self._start = start
        self._duration = duration
        self._command = scene_command(ffmpeg, media, start=start, duration=duration, threshold=threshold)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._state = "queued"
        self._progress = 0.0
        self._message = ""
        self._result: tuple[float, ...] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._state == "queued":
                self._state = "cancelled"

    def snapshot(self) -> SceneDetectionSnapshot:
        with self._lock:
            return SceneDetectionSnapshot(self._state, self._progress, self._message, self._result)

    def run(self, token: object = None) -> None:
        with self._lock:
            if self._cancel.is_set() or self._state == "cancelled":
                self._state = "cancelled"
                return
            self._state = "running"
        try:
            text, returncode = self._execute()
        except Exception as error:  # noqa: BLE001 - un média illisible ne doit pas bloquer la file d'analyse
            self._finish("failed", message=str(error) or type(error).__name__)
            return
        if self._cancel.is_set():
            self._finish("cancelled")
            return
        if returncode != 0:
            self._finish("failed", message=_last_lines(text))
            return
        times = shot_boundaries(
            parse_scene_times(text, self._start), self._start, self._start + self._duration,
        )
        self._finish("finished", result=tuple(times), progress=1.0)

    def _execute(self) -> tuple[str, int]:
        collected: list[str] = []
        with supervised_popen(
            self._command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        ) as process:
            stream = process.stderr
            if stream is None:  # ``stderr=PIPE`` garantit le tube : ce cas n'arrive pas
                raise RuntimeError("FFmpeg n'a pas de sortie d'erreur lisible")
            for line in stream:
                collected.append(line)
                if self._cancel.is_set():
                    process.kill()
                    break
                match = _PROGRESS_TIME.search(line)
                if match:
                    self._report(_seconds(match) / self._duration)
            returncode = process.wait()
        return "".join(collected), returncode

    def _report(self, fraction: float) -> None:
        with self._lock:
            self._progress = max(self._progress, min(0.99, fraction))

    def _finish(
        self, state: str, *, result: tuple[float, ...] | None = None, message: str = "", progress: float | None = None,
    ) -> None:
        with self._lock:
            self._state = state
            self._result = result
            self._message = message
            if progress is not None:
                self._progress = progress


def _seconds(match: re.Match[str]) -> float:
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _last_lines(text: str, count: int = 3) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-count:]) or "FFmpeg a échoué sans message"


def scene_cut_refusal(clip: Clip) -> str:
    """Raison pour laquelle ``clip`` ne peut pas être découpé au plan, ou chaîne vide si il le peut.

    Un clip retimé, inversé, figé, imbriqué ou à vitesse différente de 1 n'a pas une correspondance linéaire entre temps
    de la timeline et temps du média : on ne sait pas où tomber une coupe.
    """
    if (
        clip.is_time_remapped or clip.has_speed_curve or clip.is_reversed or clip.is_frozen or clip.is_nested
        or clip.is_composition or abs(clip.speed - 1.0) > 1e-9
    ):
        return "Découpage au plan : ce clip est retimé, inversé, figé, imbriqué, composé ou à vitesse différente de 1."
    return ""


def cut_clip_at_scenes(project: Project, clip_id: str, scene_times: Sequence[float]) -> list[str]:
    """Coupe ``clip_id`` à chaque changement de plan (temps du média). Retourne les identifiants des morceaux créés.

    Refusé pour un clip que :func:`scene_cut_refusal` écarte. Les coupes sont appliquées dans l'ordre, chacune sur le
    morceau de droite de la précédente.

    Raises:
        KeyError: si ``clip_id`` n'existe pas.
        ValueError: si le clip n'est pas linéaire, ou si une position de coupe n'est pas valide.
    """
    clip = find_clip(project, clip_id)
    reason = scene_cut_refusal(clip)
    if reason:
        raise ValueError(reason)
    boundaries = shot_boundaries(scene_times, clip.source_in, clip.source_out)
    positions = [clip.timeline_start + (time - clip.source_in) for time in boundaries]
    created: list[str] = []
    current = clip_id
    for position in positions:
        _left, right = cut_clip(project, current, position)
        current = right.id
        created.append(right.id)
    return created
