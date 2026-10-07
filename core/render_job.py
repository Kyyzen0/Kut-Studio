"""``RenderJob`` : un export décrit de façon autonome et sérialisable.

Un job transporte tout ce qu'il faut pour être rendu **plus tard**, sans
l'interface : réglages de sortie, état, horodatage, résultat. Il ne
contient *pas* le projet : il en référence un instantané sur disque
(:attr:`RenderJob.snapshot_path`) écrit à l'ajout dans la file. Un job
rend donc le montage tel qu'il était quand on l'a ajouté, même si le
projet a été modifié ou fermé depuis, et la file reste légère.

Cycle de vie::

    WAITING ──start──▶ RENDERING ──▶ COMPLETED
       ▲                  ├────────▶ FAILED
       │                  └────────▶ CANCELLED
       └──── retry (depuis FAILED, CANCELLED ou COMPLETED)

Il n'existe pas d'état ``PAUSED`` : FFmpeg ne sait pas reprendre un
encodage interrompu, et une « pause » suspendrait seulement le processus
(impossible de façon fiable avec ``QProcess`` sur les trois plateformes)
ou relancerait tout le rendu. Annuler puis relancer est le comportement
honnête.

Un job qui était ``RENDERING`` quand l'application s'est arrêtée est
relu comme ``FAILED`` avec ``error_kind == "interrupted"`` (jamais
``COMPLETED``) : voir :class:`core.render_queue.RenderQueue`.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .export_engine import ExportRequest
from .render_plan import RenderPlan
from .render_presets import RenderPresetSpec, export_format_for
from .video_encoders import HardwareEncoder, coerce_hardware

SCHEMA_VERSION = 1


class JobStatus(str, Enum):
    """État d'un job. La valeur est ce qui est sérialisé."""

    WAITING = "waiting"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorKind:
    """Nature d'un échec, pour que l'interface puisse l'expliquer."""

    FFMPEG = "ffmpeg"  # FFmpeg a quitté en erreur
    FFMPEG_MISSING = "ffmpeg_missing"
    INVALID = "invalid"  # réglages ou projet inexploitables
    INTERRUPTED = "interrupted"  # application arrêtée pendant le rendu
    SNAPSHOT_MISSING = "snapshot_missing"
    IO = "io"  # écriture ou renommage du fichier final
    ENCODER = "encoder"  # encodeur matériel choisi explicitement indisponible ou en échec


@dataclass(frozen=True)
class RenderResult:
    """Informations utiles sur un rendu terminé.

    Attributes:
        output_bytes: taille du fichier produit.
        render_seconds: durée réelle du rendu.
        timeline_seconds: durée du montage rendu.
        encoder: nom FFmpeg de l'encodeur utilisé.
        hardware_used: famille effectivement utilisée (``"cpu"``…).
        fallback_reason: pourquoi ce n'est pas l'encodeur demandé, s'il y a lieu.
    """

    output_bytes: int = 0
    render_seconds: float = 0.0
    timeline_seconds: float = 0.0
    encoder: str = ""
    hardware_used: str = HardwareEncoder.CPU.value
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_bytes": self.output_bytes,
            "render_seconds": self.render_seconds,
            "timeline_seconds": self.timeline_seconds,
            "encoder": self.encoder,
            "hardware_used": self.hardware_used,
            "fallback_reason": self.fallback_reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> RenderResult | None:
        if not isinstance(data, dict):
            return None
        return cls(
            output_bytes=_int(data.get("output_bytes")),
            render_seconds=_float(data.get("render_seconds")),
            timeline_seconds=_float(data.get("timeline_seconds")),
            encoder=str(data.get("encoder") or ""),
            hardware_used=coerce_hardware(data.get("hardware_used")).value,
            fallback_reason=(
                str(data["fallback_reason"]) if data.get("fallback_reason") else None
            ),
        )


def _codec_family(video_codec: str) -> str:
    return {"libx264": "h264", "prores": "prores_ks"}.get(str(video_codec).lower(), str(video_codec))


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # NaN


def _optional_float(value: object) -> float | None:
    return None if value is None else _float(value)


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def is_safe_job_id(value: object) -> bool:
    """``True`` si ``value`` peut servir de nom de dossier sans sortir de la file.

    Les identifiants générés (12 caractères hexadécimaux) le respectent ;
    un fichier de file abîmé ou fabriqué ne doit jamais pouvoir désigner
    un chemin absolu ou remonter l'arborescence.
    """
    return isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class RenderJob:
    """Un export dans la file de rendu (mutable : son état évolue).

    Voir le module pour le cycle de vie. Les dates sont des horodatages
    Unix (secondes, UTC) ; ``None`` tant que l'événement n'a pas eu lieu.
    """

    id: str
    name: str
    project_name: str
    snapshot_path: str
    output_path: str
    container: str
    video_codec: str
    audio_codec: str
    width: int
    height: int
    fps: float
    quality: int
    audio_bitrate: str
    hardware: str = HardwareEncoder.CPU.value
    preset_id: str = "custom"
    master_gain_db: float = 0.0
    master_muted: bool = False
    duration_seconds: float = 0.0
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    progress: int = 0
    status: JobStatus = JobStatus.WAITING
    error_message: str = ""
    error_kind: str = ""
    result: RenderResult | None = None
    encoder: str = ""
    """Encodeur FFmpeg réellement lancé (``h264_videotoolbox``…), connu dès le démarrage."""
    hardware_used: str = ""
    """Famille réellement utilisée (``cpu``, ``videotoolbox``…) ; vide avant le lancement."""
    fallback_reason: str = ""
    """Raison d'un repli automatique sur le CPU, s'il a eu lieu."""
    diagnostics: str = ""
    """Fin de la sortie d'erreur FFmpeg d'un essai matériel échoué (support)."""
    sequence_id: str = ""
    """Séquence rendue (vide : séquence active de l'instantané, anciens jobs)."""
    sequence_name: str = ""
    """Nom de la séquence au moment de l'ajout (affichage)."""
    loudness_lufs: float | None = None
    """Loudness visée (normalisation du mixage) ; ``None`` : le mixage tel quel."""
    measured_lufs: float | None = None
    """Loudness du mixage mesurée avant normalisation (première passe), pour l'affichage."""
    preview_copy: bool = False
    """Écrire une copie d'aperçu légère après le rendu."""
    cover: bool = False
    """Écrire l'image de couverture après le rendu."""
    cover_seconds: float = 0.0
    """Instant de la couverture (secondes du plan), fixé à l'ajout : marqueur ``cover`` ou tête de lecture."""
    extras: list[str] = field(default_factory=list)
    """Fichiers livrés en plus de la vidéo (couverture, copie d'aperçu)."""
    extras_error: str = ""
    """Pourquoi un fichier livré manque (la vidéo, elle, est terminée)."""

    # -- Création -------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        spec: RenderPresetSpec,
        snapshot_path: str,
        output_path: str,
        project_name: str = "",
        master_gain_db: float = 0.0,
        master_muted: bool = False,
        duration_seconds: float = 0.0,
        name: str | None = None,
        now: float | None = None,
        sequence_id: str = "",
        sequence_name: str = "",
        cover_seconds: float = 0.0,
        project_fps: float | None = None,
    ) -> RenderJob:
        """Crée un job ``WAITING`` à partir d'un preset.

        ``project_fps`` est la cadence de la séquence rendue : un preset qui la suit (``spec.fps is None``) en a besoin,
        le job garde la cadence résolue (l'instantané ne change plus).

        Raises:
            ValueError: le preset suit la séquence et ``project_fps`` manque ou est invalide.
        """
        if spec.follows_project_fps and project_fps is None:
            raise ValueError(f"Le preset « {spec.name} » suit la cadence de la séquence : project_fps est requis.")
        fps = spec.output_fps(project_fps if project_fps is not None else 0.0)
        stamp = time.time() if now is None else now
        return cls(
            id=new_job_id(),
            name=name or f"{project_name or 'Projet'} · {spec.name}",
            project_name=project_name,
            snapshot_path=str(snapshot_path),
            output_path=str(output_path),
            container=spec.container,
            video_codec=spec.video_codec,
            audio_codec=spec.audio_codec,
            width=spec.width,
            height=spec.height,
            fps=fps,
            quality=spec.quality,
            audio_bitrate=spec.audio_bitrate,
            hardware=spec.hardware,
            preset_id=spec.id,
            master_gain_db=float(master_gain_db),
            master_muted=bool(master_muted),
            duration_seconds=float(duration_seconds),
            created_at=stamp,
            sequence_id=str(sequence_id or ""),
            sequence_name=str(sequence_name or ""),
            loudness_lufs=getattr(spec, "loudness_lufs", None),
            preview_copy=bool(getattr(spec, "preview_copy", False)),
            cover=bool(getattr(spec, "cover", False)),
            cover_seconds=max(0.0, float(cover_seconds)),
        )

    # -- Lecture ----------------------------------------------------------------------

    @property
    def file_name(self) -> str:
        return Path(self.output_path).name

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def is_finished(self) -> bool:
        """``True`` pour un état terminal (terminé, échoué, annulé)."""
        return self.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def can_start(self) -> bool:
        return self.status is JobStatus.WAITING

    @property
    def can_retry(self) -> bool:
        return self.is_finished

    @property
    def encoder_label(self) -> str:
        """Libellé discret de l'encodeur : ``H.264 · VideoToolbox``, ``H.264 · CPU``.

        Vide tant que le job n'a jamais été lancé (aucun encodeur n'est encore
        connu : afficher celui *demandé* laisserait croire qu'il est utilisé).
        """
        from .video_encoders import encoder_label

        used = self.hardware_used or (self.result.hardware_used if self.result else "")
        if not used:
            return ""
        return encoder_label(_codec_family(self.video_codec), coerce_hardware(used))

    @property
    def can_retry_on_cpu(self) -> bool:
        """Échec d'un encodeur explicite : relancer en CPU est proposé."""
        return (
            self.status is JobStatus.FAILED
            and self.error_kind == ErrorKind.ENCODER
            and coerce_hardware(self.hardware) is not HardwareEncoder.CPU
        )

    @property
    def elapsed_seconds(self) -> float | None:
        """Durée de rendu connue (en cours ou terminé), ``None`` avant le début."""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def to_request(self, plan: RenderPlan, output_path: str | None = None) -> ExportRequest:
        """Construit la requête du moteur d'export existant pour ``plan``.

        ``output_path`` permet de rendre vers un fichier temporaire ; par
        défaut le chemin final du job.
        """
        from .export_engine import ExportPreset

        return ExportRequest(
            render_plan=plan,
            output_path=output_path or self.output_path,
            format=export_format_for(self.container, self.video_codec),
            preset=ExportPreset(
                name=self.preset_id,
                resolution=self.resolution,
                crf=self.quality,
                audio_bitrate=self.audio_bitrate,
            ),
            fps=self.fps,
            hardware=self.hardware,
        )

    # -- Transitions ---------------------------------------------------------------------

    def mark_waiting(self) -> None:
        """Remet le job en attente (relance) : efface l'historique du rendu."""
        self.status = JobStatus.WAITING
        self.progress = 0
        self.started_at = None
        self.finished_at = None
        self.error_message = ""
        self.error_kind = ""
        self.result = None
        self._clear_run_info()

    def _clear_run_info(self) -> None:
        self._clear_encoder_info()
        self.extras = []
        self.extras_error = ""

    def _clear_encoder_info(self) -> None:
        self.encoder = ""
        self.hardware_used = ""
        self.fallback_reason = ""
        self.diagnostics = ""

    def mark_rendering(self, now: float | None = None) -> None:
        self.status = JobStatus.RENDERING
        self.progress = 0
        self.started_at = time.time() if now is None else now
        self.finished_at = None
        self.error_message = ""
        self.error_kind = ""
        self.result = None
        self._clear_run_info()

    def mark_completed(self, result: RenderResult, now: float | None = None) -> None:
        self.status = JobStatus.COMPLETED
        self.progress = 100
        self.finished_at = time.time() if now is None else now
        self.result = result

    def mark_failed(self, message: str, kind: str = ErrorKind.FFMPEG, now: float | None = None) -> None:
        self.status = JobStatus.FAILED
        self.error_message = message
        self.error_kind = kind
        self.finished_at = time.time() if now is None else now
        self.result = None

    def mark_cancelled(self, message: str = "", now: float | None = None) -> None:
        self.status = JobStatus.CANCELLED
        self.error_message = message
        self.error_kind = ""
        self.finished_at = time.time() if now is None else now
        self.result = None

    # -- Sérialisation ----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "project_name": self.project_name,
            "snapshot_path": self.snapshot_path,
            "output_path": self.output_path,
            "container": self.container,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "quality": self.quality,
            "audio_bitrate": self.audio_bitrate,
            "hardware": self.hardware,
            "preset_id": self.preset_id,
            "master_gain_db": self.master_gain_db,
            "master_muted": self.master_muted,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "status": self.status.value,
            "error_message": self.error_message,
            "error_kind": self.error_kind,
            "result": self.result.to_dict() if self.result else None,
            "encoder": self.encoder,
            "hardware_used": self.hardware_used,
            "fallback_reason": self.fallback_reason,
            "diagnostics": self.diagnostics,
            "sequence_id": self.sequence_id,
            "sequence_name": self.sequence_name,
            "loudness_lufs": self.loudness_lufs,
            "measured_lufs": self.measured_lufs,
            "preview_copy": self.preview_copy,
            "cover": self.cover,
            "cover_seconds": self.cover_seconds,
            "extras": list(self.extras),
            "extras_error": self.extras_error,
        }

    @classmethod
    def from_dict(cls, data: object) -> RenderJob:
        """Relit un job stocké ; lève ``ValueError`` si l'essentiel manque.

        Les champs optionnels corrompus retombent sur leur défaut : un
        fichier abîmé ne doit pas faire perdre toute la file.
        """
        if not isinstance(data, dict):
            raise ValueError("Job de rendu invalide : objet attendu.")
        job_id = str(data.get("id") or "").strip()
        snapshot = str(data.get("snapshot_path") or "").strip()
        output = str(data.get("output_path") or "").strip()
        if not job_id or not snapshot or not output:
            raise ValueError("Job de rendu invalide : id, instantané ou sortie manquant.")
        if not is_safe_job_id(job_id):
            raise ValueError("Job de rendu invalide : identifiant non sûr.")
        try:
            status = JobStatus(str(data.get("status")))
        except ValueError:
            status = JobStatus.WAITING
        container = str(data.get("container") or "mp4")
        video_codec = str(data.get("video_codec") or "h264")
        export_format_for(container, video_codec)  # lève ValueError si inexploitable
        width = _int(data.get("width"), 1920)
        height = _int(data.get("height"), 1080)
        fps = _float(data.get("fps"), 30.0)   # entier dans les files écrites avant les cadences NTSC (29,97…)
        if width <= 0 or height <= 0 or not 0.0 < fps < float("inf"):
            raise ValueError("Job de rendu invalide : résolution ou fréquence incorrecte.")
        return cls(
            id=job_id,
            name=str(data.get("name") or job_id),
            project_name=str(data.get("project_name") or ""),
            snapshot_path=snapshot,
            output_path=output,
            container=container,
            video_codec=video_codec,
            audio_codec=str(data.get("audio_codec") or "aac"),
            width=width,
            height=height,
            fps=fps,
            quality=_int(data.get("quality"), 20),
            audio_bitrate=str(data.get("audio_bitrate") or "192k"),
            hardware=coerce_hardware(data.get("hardware")).value,
            preset_id=str(data.get("preset_id") or "custom"),
            master_gain_db=_float(data.get("master_gain_db")),
            master_muted=bool(data.get("master_muted", False)),
            duration_seconds=max(0.0, _float(data.get("duration_seconds"))),
            created_at=_float(data.get("created_at")),
            started_at=_optional_float(data.get("started_at")),
            finished_at=_optional_float(data.get("finished_at")),
            progress=max(0, min(100, _int(data.get("progress")))),
            status=status,
            error_message=str(data.get("error_message") or ""),
            error_kind=str(data.get("error_kind") or ""),
            result=RenderResult.from_dict(data.get("result")),
            encoder=str(data.get("encoder") or ""),
            hardware_used=(
                coerce_hardware(data["hardware_used"]).value if data.get("hardware_used") else ""
            ),
            fallback_reason=str(data.get("fallback_reason") or ""),
            diagnostics=str(data.get("diagnostics") or "")[-800:],
            sequence_id=str(data.get("sequence_id") or ""),
            sequence_name=str(data.get("sequence_name") or ""),
            loudness_lufs=_optional_float(data.get("loudness_lufs")),
            measured_lufs=_optional_float(data.get("measured_lufs")),
            preview_copy=data.get("preview_copy") is True,
            cover=data.get("cover") is True,
            cover_seconds=max(0.0, _float(data.get("cover_seconds"))),
            extras=[str(path) for path in data.get("extras") or () if isinstance(path, str)]
            if isinstance(data.get("extras"), list) else [],
            extras_error=str(data.get("extras_error") or ""),
        )


__all__ = [
    "SCHEMA_VERSION",
    "ErrorKind",
    "JobStatus",
    "RenderJob",
    "RenderResult",
    "is_safe_job_id",
    "new_job_id",
]
