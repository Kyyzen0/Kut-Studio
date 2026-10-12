"""Exécuteur d'export **sans interface** : une fonction, ``run_export``, du plan de rendu au fichier (ADR-0005, étape 1).

Il enchaîne, dans l'ordre où :class:`~core.export_engine.ExportEngine` le fait pour l'interface, les étapes partagées de
``core/export_engine.py`` (vérification, fichier de sous-titres, images intermédiaires, commande), puis lance FFmpeg **avec le
superviseur de processus** (il meurt avec l'appelant, même tué brutalement) et attend la fin. Pas de ``QProcess``, pas de signal, pas de
boucle d'événements : l'appelant est un script, un CLI, un test, ou le fil d'un autre programme. La progression, le statut et
l'encodeur sont rendus par des fonctions de rappel appelées **sur le fil de l'appelant**, jamais depuis un fil interne.

Ce que le moteur de l'interface fait et que cet exécuteur refait à l'identique (mêmes fonctions partagées, pas des copies) :

* progression lue sur ``-progress pipe:1`` (:func:`~core.export_engine.parse_progress_line`), les images intermédiaires occupant
  d'abord 80 % de la barre, qui ne recule jamais ;
* **repli sur le CPU** quand un encodeur matériel choisi par ``auto`` échoue avant la première image
  (:func:`~core.export_engine.hardware_fallback_applies`), un seul par export ; un encodeur explicite qui échoue est signalé
  (``error_kind == "encoder"``), jamais masqué ;
* annulation : FFmpeg est tué, le calcul des images intermédiaires s'interrompt entre deux images ;
* nettoyage des fichiers temporaires (sous-titres, graphe de filtres trop long), quel que soit le résultat.

Ce que l'interface n'a pas et qu'un script veut : **le fichier final est atomique**. FFmpeg écrit dans
``.<nom>.<id>.partial<ext>`` (même dossier, même extension, comme la file de rendu) et le fichier n'est renommé qu'à la fin : un
échec ou une annulation ne laisse jamais un MP4 tronqué sous le nom demandé, ni n'écrase une version précédente.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .export_engine import (
    _PREPARATION_SHARE,
    ExportRequest,
    build_export_command,
    check_export_request,
    cpu_fallback,
    export_needs_preparation,
    hardware_fallback_applies,
    is_explicit_encoder_failure,
    parse_progress_line,
    prepare_export,
    write_subtitle_file,
)
from .flow_cache import FlowCache
from .hardware_encoding import redact_command
from .optical_flow import BackendPreference
from .process_supervisor import supervised_popen
from .retime_prepare import PrepareCancelled, PrepareReport
from .video_encoders import EncoderChoice, EncoderUnavailableError

LOGGER = logging.getLogger("kut_studio.export_runner")

_POLL_SECONDS = 0.1
"""Cadence de contrôle de l'annulation pendant que FFmpeg tourne."""


class ExportStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExportResult:
    """Le bilan d'un export.

    Attributes:
        status: réussi, échoué ou annulé.
        output_path: le fichier demandé (présent seulement si ``status`` est ``OK``).
        message: la raison d'un échec (sortie d'erreur de FFmpeg ou refus lisible), vide sinon.
        encoder: l'encodeur finalement utilisé (le CPU après un repli) ; ``None`` si l'export n'est jamais allé jusque-là.
        fallback_reason: non vide quand un encodeur matériel a échoué et que le CPU a pris le relais.
        error_kind: ``"encoder"`` quand un encodeur choisi explicitement a échoué.
        diagnostics: fin de la sortie d'erreur du dernier essai matériel abandonné (support).
        preparation: bilan des images intermédiaires fabriquées (replis, confiance), ``None`` s'il n'y en avait pas.
    """

    status: ExportStatus
    output_path: str
    message: str = ""
    encoder: EncoderChoice | None = None
    fallback_reason: str = ""
    error_kind: str = ""
    diagnostics: str = ""
    preparation: PrepareReport | None = None

    @property
    def ok(self) -> bool:
        return self.status is ExportStatus.OK


def partial_output_path(output_path: str) -> str:
    """Fichier de rendu intermédiaire d'un export : même dossier, même extension, nom caché et unique."""
    final = Path(output_path).expanduser()
    return str(final.with_name(f".{final.stem}.{uuid.uuid4().hex[:8]}.partial{final.suffix}"))


def run_export(
    request: ExportRequest,
    *,
    progress: Callable[[int], None] | None = None,
    status: Callable[[str], None] | None = None,
    on_encoder: Callable[[EncoderChoice], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    flow_cache: FlowCache | None = None,
    flow_preference: BackendPreference = BackendPreference.AUTO,
    atomic: bool = True,
) -> ExportResult:
    """Exporte ``request`` et rend un :class:`ExportResult` ; ne lève pas pour un échec d'export (il est dans le résultat).

    Args:
        request: ce qu'il faut exporter (plan, sortie, format, préréglage, fps, matériel).
        progress: appelée avec un entier de 0 à 100, croissant, sur le fil de l'appelant.
        status: appelée avec un message lisible (« Calcul des images intermédiaires… », raison d'un repli CPU…).
        on_encoder: appelée à chaque lancement de FFmpeg avec l'encodeur retenu (deux fois après un repli).
        cancelled: consultée environ dix fois par seconde ; vraie, elle tue FFmpeg ou interrompt la préparation.
        flow_cache: cache des images intermédiaires ; celui de l'application s'il est absent.
        flow_preference: backend de flux optique demandé.
        atomic: écrire dans un fichier ``.partial`` puis le renommer (par défaut) ; ``False`` écrit directement la sortie.
    """

    def report(message: str) -> None:
        if status is not None:
            status(message)

    last_progress = -1

    def advance(value: int) -> None:
        nonlocal last_progress
        value = max(last_progress, min(100, value))      # la barre ne recule jamais
        if value != last_progress:
            last_progress = value
            if progress is not None:
                progress(value)

    final_path = request.output_path
    temporary: list[str] = []
    partial: str | None = None
    preparation: PrepareReport | None = None
    fallback_reason = ""
    diagnostics = ""
    encoder: EncoderChoice | None = None

    def fail(message: str, *, error_kind: str = "") -> ExportResult:
        return ExportResult(
            ExportStatus.FAILED, final_path, message=message, encoder=encoder, fallback_reason=fallback_reason,
            error_kind=error_kind, diagnostics=diagnostics, preparation=preparation,
        )

    try:
        try:
            check_export_request(request)
            subtitles = write_subtitle_file(request.render_plan)
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            return fail(str(error))
        if subtitles:
            temporary.append(subtitles)

        prepared = None
        floor = 0
        if export_needs_preparation(request, flow_preference):
            report("Calcul des images intermédiaires...")
            advance(0)

            def preparation_progress(done: int, total: int) -> None:
                if total > 0:
                    advance(_PREPARATION_SHARE * max(0, done) // total)

            try:
                outcome = prepare_export(
                    request, cache=flow_cache, preference=flow_preference, progress=preparation_progress, cancelled=cancelled,
                )
            except PrepareCancelled:
                return ExportResult(ExportStatus.CANCELLED, final_path)
            except Exception as error:  # noqa: BLE001 - tout échec de préparation est rendu à l'appelant, jamais perdu
                LOGGER.error("Images intermédiaires : %s", error)
                return fail(str(error) or type(error).__name__)
            prepared, preparation, floor = outcome.streams, outcome.report, _PREPARATION_SHARE

        attempt = request
        if atomic:
            partial = partial_output_path(final_path)
            attempt = replace(request, output_path=partial)
        fallback_used = False
        report("Export en cours...")
        advance(floor)
        while True:
            try:
                command = build_export_command(attempt, prepared=prepared, subtitle_path=subtitles, temporary_files=temporary)
            except EncoderUnavailableError as error:
                LOGGER.warning("Export refusé : %s", error)
                return fail(str(error), error_kind="encoder")
            except (ImportError, OSError, ValueError, RuntimeError) as error:
                return fail(str(error))
            encoder = command.encoder if not fallback_used else encoder
            if on_encoder is not None and encoder is not None:
                on_encoder(encoder)
            LOGGER.info("Commande FFmpeg : %s", redact_command(command.arguments))
            try:
                outcome_run = _run_ffmpeg(command.arguments, request.render_plan.duration, floor, advance, cancelled)
            except OSError as error:
                LOGGER.error("Export : FFmpeg n'a pas pu démarrer (%s)", error)
                return fail(f"Impossible de démarrer ffmpeg : {error}")
            if outcome_run.cancelled:
                return ExportResult(ExportStatus.CANCELLED, final_path, encoder=encoder, preparation=preparation)
            if outcome_run.exit_code == 0:
                break
            if encoder is not None and hardware_fallback_applies(
                encoder, progress_seen=outcome_run.progress_seen, fallback_used=fallback_used
            ):
                attempt, cpu, fallback_reason = cpu_fallback(attempt, encoder, outcome_run.error_output)
                diagnostics = outcome_run.error_output[-800:]
                fallback_used = True
                encoder = cpu
                LOGGER.warning("Repli CPU : %s", fallback_reason)
                report(fallback_reason)
                continue
            kind = "encoder" if is_explicit_encoder_failure(encoder, outcome_run.error_output) else ""
            if kind:
                diagnostics = outcome_run.error_output[-800:]
            message = outcome_run.error_output or "L'export ffmpeg a échoué."
            LOGGER.error("Export échoué (code %s) : %s", outcome_run.exit_code, message.splitlines()[-1] if message else "")
            return fail(message, error_kind=kind)

        if partial is not None:
            os.replace(partial, final_path)
            partial = None
        advance(100)
        report("Export terminé")
        return ExportResult(
            ExportStatus.OK, final_path, encoder=encoder, fallback_reason=fallback_reason, diagnostics=diagnostics,
            preparation=preparation,
        )
    except OSError as error:                      # renommage final, fichier verrouillé : un échec d'export, pas un plantage
        return fail(str(error))
    finally:
        for path in temporary:
            _remove(path)
        if partial is not None:
            _remove(partial)


@dataclass(frozen=True)
class _Run:
    exit_code: int
    error_output: str
    progress_seen: bool
    cancelled: bool


def _run_ffmpeg(
    arguments: tuple[str, ...],
    duration: float,
    floor: int,
    advance: Callable[[int], None],
    cancelled: Callable[[], bool] | None,
) -> _Run:
    """Lance FFmpeg sous le superviseur, relaie la progression sur le fil appelant, et obéit à l'annulation.

    Deux fils lisent les tuyaux (sortie standard : progression ; sortie d'erreur : diagnostics) : sans eux FFmpeg se bloquerait dès
    que l'un se remplit. La progression passe par une file, lue ici, pour que les rappels restent sur le fil de l'appelant.
    """
    lines: queue.Queue[str | None] = queue.Queue()
    error_chunks: list[str] = []
    seen = False
    was_cancelled = False
    with supervised_popen(
        list(arguments), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    ) as process:

        def read_progress() -> None:
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        lines.put(line.strip())
            finally:
                lines.put(None)

        def read_errors() -> None:
            if process.stderr is not None:
                error_chunks.append(process.stderr.read())

        readers = [
            threading.Thread(target=read_progress, name="kut-export-progress", daemon=True),
            threading.Thread(target=read_errors, name="kut-export-stderr", daemon=True),
        ]
        for reader in readers:
            reader.start()
        while True:
            try:
                line = lines.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                value = parse_progress_line(line, duration)
                if value is not None:
                    seen = seen or value > 0
                    advance(min(99, floor + value * (100 - floor) // 100))
            if cancelled is not None and cancelled():
                was_cancelled = True
                process.kill()
                break
        process.wait()
        for reader in readers:
            reader.join(timeout=5.0)
    return _Run(
        exit_code=int(process.returncode), error_output="".join(error_chunks).strip(), progress_seen=seen, cancelled=was_cancelled,
    )


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


__all__ = ["ExportResult", "ExportStatus", "partial_output_path", "run_export"]
