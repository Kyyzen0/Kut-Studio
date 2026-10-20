"""Façade Python d'un film (ADR-0009, phase 3, tranche A) : script -> plans 3D -> projet Kut -> MP4, sans fenêtre.

Un script construit une ``kut3d.Scene``, la rend en vidéo AVEC TRANSPARENCE (ProRes 4444), la pose au-dessus d'un fond dans un
projet ordinaire, ajoute de l'audio, sauve le ``.kut`` et exporte un MP4 ::

    film = Film("out/film.kut", size=(1280, 720), fps=24)
    film.background((0.05, 0.06, 0.1))
    shot = film.render_shot(scene, duration=4.0, name="plan1")
    film.place(shot, start=0.0)
    film.save()
    film.export("out/film.mp4")

Rien ne change dans le format ``.kut`` : le projet écrit est celui qu'écrirait la même construction à la main (un plan est un média
vidéo ordinaire, le fond un autre). ``import core.film`` n'importe ni ``kut3d`` ni Qt ni le moteur d'export : tout se charge à
l'usage. Les modèles de rendu et d'encodage sont dans :mod:`core.film_render`.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .film_render import (
    MAX_FILM_SECONDS,
    MAX_TIME,
    BackgroundSpec,
    FilmError,
    check_fps,
    check_name,
    check_number,
    check_path_text,
    check_samples,
    check_size,
    clear_frames,
    encode_alpha_video,
    ffmpeg_tool,
    make_background_video,
    parse_background,
    render_frames,
    shot_frame_count,
    short,
)
from .film_scene import ScenePlacement, write_scene

if TYPE_CHECKING:
    from .project_model import MediaAsset, Project

MAX_VIDEO_TRACKS = 16
MAX_AUDIO_TRACKS = 16
MAX_PLACEMENTS = 2000
_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Shot:
    """Un plan rendu : une vidéo ProRes 4444 avec canal alpha. Immuable ; ``duration`` = ``frames / fps`` exactement."""

    name: str
    path: Path
    duration: float
    frames: int
    size: tuple[int, int]
    fps: float


@dataclass(frozen=True)
class _Placement:
    shot: Shot
    start: float
    track: int


@dataclass(frozen=True)
class _AudioPlacement:
    asset: "MediaAsset"
    start: float
    track: int
    gain_db: float
    length: float


def _start_time(value: object, label: str = "start") -> float:
    return check_number(value, label, low=0.0, high=MAX_TIME, low_open=False)


def _track_number(value: object, maximum: int, label: str = "track") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise FilmError(f"{label} doit être un entier entre 1 et {maximum} (reçu {short(value)}).")
    return value


def _check_video_background(path: Path) -> None:
    """Refuse tout de suite un fond « vidéo » illisible, audio seul ou sans flux vidéo (sinon l'échec viendrait à project())."""
    from .media_probe import MediaProbeError, probe_media

    try:
        asset = probe_media(str(path))
    except MediaProbeError as error:
        raise FilmError(f"Fond illisible : {short(str(error), 300)}") from error
    if asset.media_type != "video":
        raise FilmError(f"Le fond {short(path.name, 80)} n'est pas une vidéo ni une image (type {asset.media_type!r}).")


class Film:
    """Un film en construction. Le projet est reconstruit à chaque :meth:`save` / :meth:`export` à partir de ce qui a été posé."""

    def __init__(self, path: str | os.PathLike[str], *, size: tuple[int, int] = (1920, 1080), fps: float = 24.0) -> None:
        text = check_path_text(path, "path")
        if not text.lower().endswith(".kut") or Path(text).name.lower() == ".kut":
            raise FilmError(f"Le projet doit se terminer par « .kut » (reçu {short(text, 80)}).")
        self.path = Path(text).expanduser()
        self.size = check_size(size)
        self.fps = check_fps(fps)
        self.assets_dir = self.path.with_name(f"{self.path.stem}_assets")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise FilmError(f"Impossible de créer le dossier de sortie : {short(str(error), 200)}") from error
        self._background: BackgroundSpec | None = None
        self._shots: dict[str, Shot] = {}
        self._placements: list[_Placement] = []
        self._scenes: list[ScenePlacement] = []
        self._audio: list[_AudioPlacement] = []
        self._assets: dict[str, MediaAsset] = {}
        self._built_background: tuple[str, Path] | None = None

    # --- fond ------------------------------------------------------------------------------------------------------------

    def background(self, value: Any) -> None:
        """Fixe le fond (piste V1) : une couleur RVB(A) 0..1, ou le chemin d'une image ou d'une vidéo. Remplace le précédent."""
        spec = parse_background(value)
        if spec.path is not None and not spec.is_image:
            _check_video_background(spec.path)
        if any(item.track == 1 for item in self._placements):
            raise FilmError("La piste 1 porte déjà un plan : le fond l'occupe, posez les plans à partir de la piste 2.")
        self._background = spec

    # --- plans -----------------------------------------------------------------------------------------------------------

    def render_shot(
        self,
        scene_or_fn: Any,
        *,
        duration: float,
        name: str,
        samples: int = 1,
        backend: str = "auto",
        keep_frames: bool = False,
        overwrite: bool = False,
    ) -> Shot:
        """Rend un plan : ``scene_or_fn`` est une ``Scene`` (ses animations avancent avec le temps) ou une fonction ``t -> Scene``.

        Le plan a la taille et la cadence du film. Les images PNG intermédiaires vont dans ``<projet>_assets/<name>/`` et sont
        supprimées après l'encodage (sauf ``keep_frames``). Un nom déjà pris (dans ce film ou sur le disque) est refusé sans
        ``overwrite=True``. En cas d'erreur ou d'interruption, aucun fichier partiel ne reste.
        """
        label = check_name(name)
        count = shot_frame_count(duration, self.fps)
        samples = check_samples(samples)
        if not isinstance(keep_frames, bool) or not isinstance(overwrite, bool):
            raise FilmError("keep_frames et overwrite sont des booléens.")
        _check_scene_source(scene_or_fn)
        ffmpeg_tool()                                           # FFmpeg absent : on le dit AVANT de rendre la moindre image
        output = self.assets_dir / f"{label}.mov"
        key = label.lower()                                     # macOS et Windows ignorent la casse des noms de fichiers
        if not overwrite and (key in self._shots or output.exists()):
            raise FilmError(f"Le plan {label!r} existe déjà : choisissez un autre nom ou passez overwrite=True.")
        if any(item.name.lower() == key for item in self._scenes):
            raise FilmError(f"Le nom {label!r} est déjà celui d'une scène posée dans ce film.")
        if any(item.shot.name.lower() == key for item in self._placements):
            raise FilmError(f"Le plan {label!r} est déjà posé dans ce film : il ne peut plus être remplacé.")
        folder = self.assets_dir / label
        succeeded = False
        try:
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            clear_frames(folder)                                # des images d'un essai précédent ne se mêlent jamais aux nouvelles
            folder.mkdir(parents=True, exist_ok=True)
            frames = render_frames(
                scene_or_fn, fps=self.fps, duration=float(duration), size=self.size, folder=folder, backend=backend,
                samples=samples,
            )
            if frames != count:                                 # ne devrait pas arriver : on ne code pas une durée fausse
                raise FilmError(f"Le rendu a produit {frames} images au lieu de {count}.")
            encode_alpha_video(folder, frames, self.fps, output)
            succeeded = True
        except OSError as error:
            raise FilmError(f"Écriture impossible : {short(str(error), 200)}") from error
        finally:
            if not (succeeded and keep_frames):
                clear_frames(folder)
        shot = Shot(label, output.resolve(), frames / self.fps, frames, self.size, self.fps)
        self._shots[key] = shot
        return shot

    def place(self, shot: Shot, *, start: float = 0.0, track: int = 2) -> str:
        """Pose ``shot`` sur la piste vidéo ``track`` (V1 est le fond) à ``start`` secondes ; retourne l'identifiant du clip."""
        if not isinstance(shot, Shot):
            raise FilmError("place() attend un Shot rendu par render_shot().")
        at = _start_time(start)
        number = _track_number(track, MAX_VIDEO_TRACKS)
        if shot.size != self.size or not math.isclose(shot.fps, self.fps):
            raise FilmError(f"Le plan {shot.name!r} ne correspond pas au film ({shot.size} à {shot.fps:g} i/s).")
        if not shot.path.is_file():
            raise FilmError(f"Le fichier du plan {shot.name!r} a disparu : {short(str(shot.path), 120)}.")
        if number == 1 and self._background is not None:
            raise FilmError("La piste 1 est occupée par le fond : posez les plans à partir de la piste 2.")
        if len(self._placements) >= MAX_PLACEMENTS:
            raise FilmError(f"Trop de plans posés (maximum {MAX_PLACEMENTS}).")
        if at + shot.duration > MAX_FILM_SECONDS:
            raise FilmError(f"Le film dépasserait {MAX_FILM_SECONDS:g} s.")
        for other in self._placements:
            if other.track == number and at < other.start + other.shot.duration - _TOLERANCE and other.start < at + shot.duration - _TOLERANCE:
                raise FilmError(f"Le plan {shot.name!r} chevauche {other.shot.name!r} sur la piste {number}.")
        known = self._shots.get(shot.name.lower())
        if known is not None and known.path != shot.path:
            raise FilmError(f"Un autre plan porte déjà le nom {shot.name!r} dans ce film.")
        self._placements.append(_Placement(shot, at, number))
        return _clip_id(shot.name, len(self._placements))

    def place_scene(
        self, scene: Any, *, duration: float, start: float = 0.0, track: int = 1, name: str, overwrite: bool = False,
    ) -> str:
        """Pose ``scene`` comme un **vrai clip « scène 3D »** (calque graphique, éditable dans l'application, rendu fidèle) ; retourne l'id du clip.

        ``scene`` est une ``kut3d.Scene`` (une fonction ``t -> Scene`` ne s'écrit pas dans un fichier : utilisez :meth:`render_shot`).
        Elle est écrite dans ``<projet>_assets/<name>.kut3d`` ; ses modèles et leurs dépendances sont **copiés** dans
        ``<projet>_assets/<name>_models/`` (voir :mod:`core.film_scene`). ``track`` est la piste de graphiques (G1, G2...) :
        un calque graphique passe au-dessus de toute la vidéo. La durée est arrondie à l'image (``frames / fps``). Fond transparent,
        taille du projet, vitesse fixe. Le projet s'enregistre alors au format ``.kut`` 22. Un nom déjà pris est refusé sans ``overwrite=True``.
        """
        import kut3d

        if callable(scene) or not isinstance(scene, kut3d.Scene):
            raise FilmError("place_scene() attend une Scene, pas une fonction t -> Scene (non sérialisable) : utilisez render_shot() pour une fonction.")
        label = check_name(name, "scène")
        count = shot_frame_count(duration, self.fps)
        length = count / self.fps
        at = _start_time(start)
        number = _track_number(track, MAX_VIDEO_TRACKS)
        if not isinstance(overwrite, bool):
            raise FilmError("overwrite est un booléen.")
        key = label.lower()
        if len(self._placements) + len(self._scenes) >= MAX_PLACEMENTS:
            raise FilmError(f"Trop de plans posés (maximum {MAX_PLACEMENTS}).")
        if at + length > MAX_FILM_SECONDS:
            raise FilmError(f"Le film dépasserait {MAX_FILM_SECONDS:g} s.")
        if key in self._shots or any(item.shot.name.lower() == key for item in self._placements) or any(item.name.lower() == key for item in self._scenes):
            raise FilmError(f"Le nom {label!r} est déjà pris dans ce film : choisissez un autre nom.")
        for other in self._scenes:
            if other.track == number and at < other.start + other.duration - _TOLERANCE and other.start < at + length - _TOLERANCE:
                raise FilmError(f"La scène {label!r} chevauche {other.name!r} sur la piste de graphiques {number}.")
        path = write_scene(scene, self.assets_dir, label, overwrite=overwrite)
        self._scenes.append(ScenePlacement(label, path, at, length, number))
        return _scene_clip_id(label, len(self._scenes))

    # --- audio -----------------------------------------------------------------------------------------------------------

    def audio(
        self, path: str | os.PathLike[str], *, start: float = 0.0, track: int = 1, gain_db: float = 0.0,
        duration: float | None = None,
    ) -> str:
        """Pose un fichier audio sur la piste audio ``track`` à ``start`` ; retourne l'identifiant du clip.

        La musique n'allonge jamais le film : elle est coupée à la fin des plans (à l'enregistrement du projet).
        ``duration`` raccourcit le son posé ; ``gain_db`` est refusé hors de ±60 dB et +12 dB, jamais ramené.
        """
        from .media_probe import MediaProbeError, probe_media
        from .project_model import MAX_GAIN_DB, MIN_GAIN_DB

        text = check_path_text(path, "path")
        file = Path(text).expanduser()
        if not file.is_file():
            raise FilmError(f"Fichier audio introuvable : {short(text, 120)}.")
        at = _start_time(start)
        number = _track_number(track, MAX_AUDIO_TRACKS)
        gain = check_number(gain_db, "gain_db", low=MIN_GAIN_DB, high=MAX_GAIN_DB, low_open=False)
        if len(self._audio) >= MAX_PLACEMENTS:
            raise FilmError(f"Trop de sons posés (maximum {MAX_PLACEMENTS}).")
        try:
            asset = probe_media(str(file.resolve()))
        except MediaProbeError as error:
            raise FilmError(short(str(error), 300)) from error
        if asset.media_type != "audio":
            raise FilmError(f"{short(text, 80)} n'est pas un fichier audio seul (type {asset.media_type!r}).")
        length = asset.duration if duration is None else check_number(duration, "duration", low=0.0, high=asset.duration)
        for other in self._audio:
            if other.track == number and at < other.start + other.length - _TOLERANCE and other.start < at + length - _TOLERANCE:
                raise FilmError(f"Ce son chevauche un autre son de la piste audio {number}.")
        self._audio.append(_AudioPlacement(asset, at, number, gain, length))
        return f"audio_{len(self._audio)}"

    # --- projet ----------------------------------------------------------------------------------------------------------

    @property
    def duration(self) -> float:
        """La durée du film : la fin du dernier plan posé (0.0 s'il n'y en a pas)."""
        return max(
            [item.start + item.shot.duration for item in self._placements] + [item.start + item.duration for item in self._scenes],
            default=0.0,
        )

    def project(self) -> "Project":
        """Construit le projet ordinaire (médias, pistes V1.. et A1.., clips) ; fabrique le fond au besoin."""
        from .media_probe import MediaProbeError, probe_media
        from .graphics import GraphicType, add_graphic_clip
        from .project_model import Project, Track
        from .scene3d import Scene3DError
        from .timeline_operations import add_clip_to_track

        if not self._placements and not self._scenes:
            raise FilmError("Aucun plan posé : appelez place() ou place_scene() avant save() ou export().")
        total = self.duration
        video_count = max([item.track for item in self._placements] + [1])
        audio_count = max([item.track for item in self._audio], default=0)
        graphics_count = max([item.track for item in self._scenes], default=0)
        project = Project(
            name=self.path.stem, width=self.size[0], height=self.size[1], fps=self.fps,
            tracks=[
                *(Track(id=f"V{i}", name=f"V{i}", type="video") for i in range(1, video_count + 1)),
                *(Track(id=f"A{i}", name=f"A{i}", type="audio") for i in range(1, audio_count + 1)),
                *(Track(id=f"G{i}", name=f"G{i}", type="graphics") for i in range(1, graphics_count + 1)),
            ],
        )
        tracks = {track.id: track for track in project.tracks}

        def add_asset(asset_id: str, source: MediaAsset) -> MediaAsset:
            fresh = copy.copy(source)
            fresh.id = asset_id
            project.media_assets.append(fresh)
            return fresh

        if self._background is not None:
            background = self._background_asset(total)
            try:
                probed = probe_media(str(background))
            except MediaProbeError as error:
                raise FilmError(short(str(error), 300)) from error
            if probed.duration < total - _TOLERANCE:         # aucune tolérance : un clip plus court laisserait un trou sur V1
                raise FilmError(f"Le fond dure {probed.duration:g} s, moins que le film ({total:g} s).")
            asset = add_asset("background", probed)
            asset.name = "Fond"
            clip = add_clip_to_track(project, asset.id, "V1", 0.0, clip_id="background")
            clip.source_out = min(total, asset.duration)
            clip.label = "Fond"
        for index, placed in enumerate(self._placements, start=1):
            shot_asset_id = f"shot_{placed.shot.name}"
            if all(item.id != shot_asset_id for item in project.media_assets):
                if shot_asset_id not in self._assets:
                    try:
                        self._assets[shot_asset_id] = probe_media(str(placed.shot.path))
                    except MediaProbeError as error:
                        raise FilmError(short(str(error), 300)) from error
                add_asset(shot_asset_id, self._assets[shot_asset_id]).name = placed.shot.name
            clip = add_clip_to_track(project, shot_asset_id, f"V{placed.track}", round(placed.start, 4), clip_id=_clip_id(placed.shot.name, index))
            clip.label = placed.shot.name
        for index, placed_scene in enumerate(self._scenes, start=1):
            try:
                add_graphic_clip(
                    project, GraphicType.SCENE3D, timeline_start=round(placed_scene.start, 4), duration=placed_scene.duration,
                    source_path=str(placed_scene.path), track=tracks[f"G{placed_scene.track}"], clip_id=_scene_clip_id(placed_scene.name, index),
                )
            except (Scene3DError, OSError) as error:
                raise FilmError(f"Scène 3D {placed_scene.name!r} illisible : {short(str(error), 300)}") from error
        for index, sound in enumerate(self._audio, start=1):
            if sound.start >= total - _TOLERANCE:
                raise FilmError(f"Le son {index} commence à {sound.start:g} s, après la fin du film ({total:g} s).")
            asset = add_asset(f"audio_{index}", sound.asset)
            clip = add_clip_to_track(project, asset.id, f"A{sound.track}", round(sound.start, 4), clip_id=f"audio_{index}")
            clip.source_out = min(sound.length, total - sound.start)
            clip.gain_db = sound.gain_db
        for track in tracks.values():
            track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        return project

    def _background_asset(self, total: float) -> Path:
        """Le fichier vidéo du fond : le fichier donné s'il est une vidéo, sinon une vidéo fabriquée de la durée du film."""
        spec = self._background
        if spec is None:
            raise FilmError("Aucun fond.")
        if spec.path is not None and not spec.is_image:
            return spec.path
        count = max(1, math.ceil(total * self.fps - _TOLERANCE))
        key = f"{spec.key()}|{self.size}|{self.fps}|{count}"
        output = self.assets_dir / "background.mp4"
        if self._built_background == (key, output) and output.is_file():
            return output
        try:
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            make_background_video(spec, self.size, self.fps, count, output)
        except OSError as error:
            raise FilmError(f"Écriture impossible : {short(str(error), 200)}") from error
        self._built_background = (key, output)
        return output

    def save(self) -> Path:
        """Écrit le ``.kut`` (le format ordinaire du moment) et retourne son chemin."""
        from .project_io import save_project

        project = self.project()
        try:
            save_project(project, str(self.path))
        except OSError as error:
            raise FilmError(f"Écriture du projet impossible : {short(str(error), 200)}") from error
        return self.path

    def export(self, output: str | os.PathLike[str], *, preset: str | None = None) -> Path:
        """Exporte le film. Par défaut un MP4 H.264 à la taille et la cadence du film ; ``preset`` : identifiant d'un préréglage de Kut.

        Sans fenêtre : le moteur d'export est celui de l'application. Le fichier est écrit dans un temporaire puis renommé. Une
        interruption (Ctrl-C) arrête FFmpeg et ne laisse aucun fichier partiel.
        """
        from .export_engine import ExportFormat, ExportPreset, ExportRequest
        from .export_runner import ExportStatus, run_export
        from .render_plan import build_render_plan

        text = check_path_text(output, "output")
        target = Path(text).expanduser()
        if preset is None:
            export_format, export_preset, fps = ExportFormat.MP4_H264, ExportPreset("Film", self.size, 18, "192k"), self.fps
            container, loudness = "mp4", None
        else:
            from .render_presets import get_preset

            spec = get_preset(preset) if isinstance(preset, str) else None
            if spec is None:
                raise FilmError(f"Préréglage inconnu : {short(preset, 60)}.")
            export_format, export_preset, fps = spec.export_parts(self.fps)
            container, loudness = spec.container, spec.loudness_lufs
        if target.suffix.lower() != f".{container}":
            raise FilmError(f"Ce format s'écrit en « .{container} » (reçu {short(target.name, 60)}).")
        project = self.project()
        sources = {Path(asset.path).resolve() for asset in project.media_assets}
        if target.resolve() in sources:
            raise FilmError("La sortie écraserait un média du film.")
        plan = build_render_plan(project)
        if plan.missing_media:
            raise FilmError(f"Médias introuvables : {short(plan.missing_media, 200)}.")
        if loudness is not None and plan.audio_layers:
            from .loudness import LoudnessError, normalized_plan

            try:
                plan, _measure = normalized_plan(plan, int(round(fps)), float(loudness))
            except LoudnessError as error:
                raise FilmError(short(str(error), 300)) from error
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise FilmError(f"Impossible de créer le dossier de sortie : {short(str(error), 200)}") from error
        request = ExportRequest(render_plan=plan, output_path=str(target), format=export_format, preset=export_preset, fps=fps)
        result = run_export(request)
        if result.status is not ExportStatus.OK:
            raise FilmError(f"Export impossible ({result.status.value}) : {result.message[-800:]}")
        return Path(result.output_path)


def _clip_id(shot_name: str, index: int) -> str:
    return f"shot_{shot_name}_{index}"


def _scene_clip_id(scene_name: str, index: int) -> str:
    return f"scene_{scene_name}_{index}"


def _check_scene_source(source: object) -> None:
    if callable(source):
        return
    import kut3d

    if not isinstance(source, kut3d.Scene):
        raise FilmError("Le premier argument doit être une Scene ou une fonction t -> Scene.")


__all__ = ["Film", "FilmError", "Shot"]
