"""Rendu et encodage d'un plan de film : images PNG RGBA de Kut-3D -> vidéo ProRes 4444 avec transparence.

Brique de :mod:`core.film` (la façade ``Film``). Elle vit dans ``core/`` parce que ``kut3d/`` n'a pas le droit d'importer ``core`` ;
``kut3d`` n'est importé qu'au moment du rendu, jamais à l'import de ce module. FFmpeg ne se lance que par le superviseur de
processus (:mod:`core.process_supervisor`), l'exécutable se résout par :func:`core.tool_paths.find_media_tool`.

**Couleurs.** Un fichier sans étiquette est lu par Kut-Studio en BT.709 à partir de 720 lignes, en BT.601 en dessous
(:mod:`core.color_matrix`). L'encodage fixe donc la matrice BT.709 *et* l'écrit dans le fichier : sinon un rendu 1080p serait
décalé en couleur à l'export.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .video_encoders import EncoderUnavailableError, auxiliary_h264_args

MAX_SIDE = 8192
"""Côté maximal d'un film, en pixels (``kut3d.render.limits.MAX_IMAGE_SIDE`` ; un test garde les deux égaux)."""
MAX_FPS = 240.0
"""Cadence maximale (``kut3d.render.limits.MAX_FPS``)."""
MAX_SHOT_FRAMES = 100_000
"""Images d'un plan (``kut3d.render.limits.MAX_SEQUENCE_FRAMES``)."""
SAMPLES_CHOICES = (1, 2, 4)
"""Sur-échantillonnage par axe (``kut3d.render.limits.SAMPLES_CHOICES``)."""
MAX_SHOT_SECONDS = 3600.0
MAX_FILM_SECONDS = 86_400.0
MAX_NAME_LENGTH = 48
MAX_PATH_LENGTH = 1024
MAX_TIME = 1_000_000.0
"""Même borne que les verbes de script (``core.scripting.verbs.MAX_TIME``)."""

FRAME_PATTERN = "frame_%06d.png"
"""Nom des images écrites par ``kut3d.render_sequence`` (``frame_000001.png``...)."""
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"})

_NAME = re.compile(r"[A-Za-z0-9_]+")
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})
_ERROR_TAIL = 800
_COLOR_TAGS = ("-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709")
_TO_BT709 = "scale=out_color_matrix=bt709:out_range=tv"


class FilmError(ValueError):
    """Une opération de film a échoué ou a été refusée (message en français, sans recopier une entrée démesurée)."""


def short(value: object, limit: int = 40) -> str:
    """Représentation bornée d'une valeur pour un message d'erreur."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def check_number(value: object, label: str, *, low: float, high: float, low_open: bool = True) -> float:
    """Un nombre fini dans l'intervalle (``low`` exclu si ``low_open``), sinon :class:`FilmError`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FilmError(f"{label} doit être un nombre (reçu {short(value)}).")
    number = float(value)
    below = number <= low if low_open else number < low
    if not math.isfinite(number) or below or number > high:
        bracket = "]" if low_open else "["
        raise FilmError(f"{label} doit être dans {bracket}{low:g}, {high:g}] (reçu {short(value)}).")
    return number


def check_size(size: object) -> tuple[int, int]:
    """Largeur et hauteur entières, paires (aucun arrondi silencieux), au plus :data:`MAX_SIDE`."""
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        raise FilmError(f"size doit être (largeur, hauteur) (reçu {short(size)}).")
    sides: list[int] = []
    for side in size:
        if isinstance(side, bool) or not isinstance(side, int) or not 2 <= side <= MAX_SIDE:
            raise FilmError(f"Chaque côté de size doit être un entier entre 2 et {MAX_SIDE} (reçu {short(side)}).")
        if side % 2:
            raise FilmError(f"Chaque côté de size doit être pair (reçu {side}) : l'encodage vidéo l'exige.")
        sides.append(side)
    return sides[0], sides[1]


def check_fps(fps: object) -> float:
    return check_number(fps, "fps", low=0.0, high=MAX_FPS)


def check_name(name: object, kind: str = "plan") -> str:
    """Un nom de plan sûr comme nom de fichier ET comme identifiant (lettres, chiffres, « _ »), jamais réservé sous Windows."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or len(name) > MAX_NAME_LENGTH:
        raise FilmError(
            f"Nom de {kind} invalide ({short(name)}) : lettres, chiffres et « _ » seulement, {MAX_NAME_LENGTH} caractères au plus."
        )
    if name.lower() in _WINDOWS_RESERVED:
        raise FilmError(f"Nom de {kind} réservé par Windows : {name!r}.")
    return name


def check_path_text(value: object, label: str) -> str:
    """Un chemin donné comme texte ou ``Path``, de taille raisonnable et sans octet nul."""
    if not isinstance(value, (str, os.PathLike)):
        raise FilmError(f"{label} doit être un chemin (reçu {short(value)}).")
    text = os.fspath(value)
    if not isinstance(text, str) or not text or len(text) > MAX_PATH_LENGTH or "\0" in text:
        raise FilmError(f"{label} n'est pas un chemin valable (reçu {short(text)}).")
    return text


def shot_frame_count(duration: object, fps: float) -> int:
    """Nombre d'images d'un plan (``round(durée x fps)``, au moins une), vérifié AVANT tout rendu."""
    seconds = check_number(duration, "duration", low=0.0, high=MAX_SHOT_SECONDS)
    count = max(1, round(seconds * fps))
    if count > MAX_SHOT_FRAMES:
        raise FilmError(f"Trop d'images pour un plan ({count}, maximum {MAX_SHOT_FRAMES}).")
    return count


def check_samples(samples: object) -> int:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples not in SAMPLES_CHOICES:
        raise FilmError(f"samples doit valoir 1, 2 ou 4 (reçu {short(samples)}).")
    return samples


def ffmpeg_tool() -> str:
    from .tool_paths import find_media_tool

    found = find_media_tool("ffmpeg")
    if found is None:
        raise FilmError("FFmpeg est introuvable : installez-le (ou ajoutez-le au PATH) pour encoder un film.")
    return found


def run_ffmpeg(arguments: list[str], what: str, *, cwd: Path | None = None) -> None:
    """Lance FFmpeg sous le superviseur ; un échec devient une :class:`FilmError` avec la fin de sa sortie d'erreur."""
    from .process_supervisor import supervised_run

    command = [ffmpeg_tool(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *arguments]
    try:
        done = supervised_run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd) if cwd is not None else None,
        )
    except OSError as error:
        raise FilmError(f"Impossible de lancer FFmpeg ({what}) : {short(str(error), 200)}") from error
    if done.returncode != 0:
        tail = (done.stderr or "").strip()[-_ERROR_TAIL:]
        raise FilmError(f"FFmpeg a échoué ({what}, code {done.returncode}) : {tail}")


def fps_text(fps: float) -> str:
    return f"{fps:.9g}"


def encode_alpha_video(frames_dir: Path, count: int, fps: float, output: Path) -> None:
    """Encode ``frame_000001.png``... de ``frames_dir`` en ProRes 4444 avec canal alpha dans ``output`` (un ``.mov``).

    Écrit d'abord un fichier caché voisin puis le renomme : jamais de ``.mov`` partiel sous le nom final, même interrompu.
    """
    partial = output.with_name(f".{output.stem}.partial{output.suffix}")
    try:
        run_ffmpeg(
            [
                "-framerate", fps_text(fps), "-start_number", "1", "-i", FRAME_PATTERN, "-frames:v", str(count),
                "-vf", f"{_TO_BT709},format=yuva444p10le", "-c:v", "prores_ks", "-profile:v", "4444",
                "-pix_fmt", "yuva444p10le", "-vendor", "apl0", *_COLOR_TAGS, "-an", str(partial),
            ],
            "encodage du plan",
            cwd=frames_dir,       # le motif d'images reste relatif : un « % » dans un chemin ne le casse pas
        )
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)


@dataclass(frozen=True)
class BackgroundSpec:
    """Le fond d'un film : une couleur unie (RVB 0..1) OU un fichier image/vidéo."""

    color: tuple[float, float, float] | None = None
    path: Path | None = None

    @property
    def is_image(self) -> bool:
        return self.path is not None and self.path.suffix.lower() in IMAGE_SUFFIXES

    def key(self) -> str:
        return f"{self.color}|{self.path}"


def parse_background(value: Any) -> BackgroundSpec:
    """Une couleur ``(r, g, b)`` ou ``(r, g, b, 1)`` dans [0, 1], ou le chemin d'un fichier existant."""
    if isinstance(value, (tuple, list)):
        if len(value) not in (3, 4):
            raise FilmError(f"Une couleur de fond a 3 ou 4 composantes (reçu {len(value)}).")
        parts = [check_number(v, "composante du fond", low=0.0, high=1.0, low_open=False) for v in value]
        if len(parts) == 4 and parts[3] != 1.0:
            raise FilmError("Un fond translucide n'est pas pris en charge : l'alpha doit valoir 1.")
        return BackgroundSpec(color=(parts[0], parts[1], parts[2]))
    text = check_path_text(value, "Le fond")
    path = Path(text).expanduser()
    if not path.is_file():
        raise FilmError(f"Fond introuvable : {short(text, 120)}.")
    if path.suffix.lower() in IMAGE_SUFFIXES and "%" in path.name:
        raise FilmError("Le nom d'une image de fond ne peut pas contenir « % » (FFmpeg y lirait un motif de séquence).")
    return BackgroundSpec(path=path.resolve())


def _color_hex(color: tuple[float, float, float]) -> str:
    return "0x" + "".join(f"{round(c * 255):02X}" for c in color)


def make_background_video(spec: BackgroundSpec, size: tuple[int, int], fps: float, count: int, output: Path) -> None:
    """Fabrique le fond (``count`` images) en H.264 : une couleur unie, ou une image recadrée « remplir le cadre »."""
    width, height = size
    try:
        # libx264 (FFmpeg complet) ; sans lui (construction LGPL du Mac App Store), VideoToolbox au débit de la qualité « master ».
        encoder = auxiliary_h264_args(speed_preset="veryfast", crf=10, width=width, height=height, fps=fps)
    except EncoderUnavailableError as error:
        raise FilmError(str(error)) from error
    tail = [
        "-frames:v", str(count), *encoder, "-pix_fmt", "yuv420p",
        *_COLOR_TAGS, "-an", "-movflags", "+faststart",
    ]
    partial = output.with_name(f".{output.stem}.partial{output.suffix}")
    try:
        if spec.color is not None:
            source = ["-f", "lavfi", "-i", f"color=c={_color_hex(spec.color)}:s={width}x{height}:r={fps_text(fps)}"]
            graph = f"{_TO_BT709},format=yuv420p"
        elif spec.path is not None:
            source = ["-loop", "1", "-framerate", fps_text(fps), "-i", str(spec.path)]
            graph = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                f"{_TO_BT709},format=yuv420p"
            )
        else:
            raise FilmError("Aucun fond à fabriquer.")
        run_ffmpeg([*source, "-vf", graph, *tail, str(partial)], "fabrication du fond")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)


def clear_frames(folder: Path) -> None:
    """Supprime les images ``frame_*.png`` et les temporaires de ``kut3d`` du dossier, puis le dossier s'il est vide.

    Ne touche à rien d'autre : un fichier de l'utilisateur rangé là reste, et le dossier avec lui.
    """
    if not folder.is_dir():
        return
    for item in folder.iterdir():
        if (item.name.startswith("frame_") and item.suffix == ".png") or (
            item.name.startswith(".kut3d-") and item.suffix == ".tmp"
        ):
            item.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def render_frames(scene: Any, *, fps: float, duration: float, size: tuple[int, int], folder: Path, backend: str, samples: int) -> int:
    """Rend les images PNG RGBA dans ``folder`` avec ``kut3d.render_sequence`` et retourne leur nombre."""
    try:
        import kut3d
    except ImportError as error:        # pragma: no cover - kut3d fait partie du dépôt
        raise FilmError("Le paquet kut3d est introuvable.") from error
    if not isinstance(backend, str) or not backend:
        raise FilmError(f"backend doit être un nom de moteur (reçu {short(backend)}).")
    try:
        paths = kut3d.render_sequence(
            scene, fps=fps, duration=duration, size=size, out_dir=folder, backend=backend, samples=samples,
        )
    except kut3d.RenderError as error:
        raise FilmError(f"Rendu impossible : {short(str(error), 300)}") from error
    return len(paths)


__all__ = [
    "BackgroundSpec", "FilmError", "check_fps", "check_name", "check_number", "check_path_text", "check_samples", "check_size",
    "clear_frames", "encode_alpha_video", "make_background_video", "parse_background", "render_frames", "shot_frame_count",
]
