"""Étalonnage dans le moniteur GPU : la chaîne d'étalonnage de l'export, cuite en LUT 3D par FFmpeg.

L'export étalonne un calque avec ``eq`` → ``colorbalance`` → ``curves`` → ``lut3d``
(:func:`core.export_engine._build_color_grade_filters`). Le moniteur GPU ne réécrit pas ces quatre filtres : FFmpeg
fait passer un **réseau de couleurs** — :data:`LUT_SIZE`\\ ³ codes, multiples de 5, dans l'espace d'entrée du calque —
dans **cette chaîne exacte**, avec les propriétés de couleur du média ; ce qui ressort est une LUT 3D (couleur du
calque → RVB étalonné) que la passe ``grade`` du shader lit avec une interpolation trilinéaire. Même principe que les
mattes de masques, rastérisées par le code de l'export : le moniteur ne peut pas diverger d'une formule qu'il
n'implémente pas.

Mesuré contre l'export réel (mire 4:2:0 aux couleurs saturées, étalonnée : exposition, contraste, saturation,
température, ombres, LUT ``.cube``) : 0,1 à 0,15 niveau d'écart moyen, 3 à 4 au 99ᵉ centile, sur les zones où la chroma
ne change pas brusquement ; le reste est au bord du gamut, où l'interpolation adoucit le coude d'un écrêtage. Aux
bords francs de chroma, l'écart est celui du moniteur sans étalonnage (il traite la chroma à pleine résolution, l'export
après sous-échantillonnage) : la LUT n'y ajoute rien.

La LUT tient dans une texture 2D ordinaire (un **atlas** : :data:`LUT_SIZE` tranches côte à côte), téléversée comme
une matte : aucune texture 3D, valable sur tous les backends (OpenGL 2.1 compris).

Disposition de l'atlas (RVB 8 bits, largeur ``N·N``, hauteur ``N``) : le pixel ``(x = c₂·N + c₁, y = c₀)`` est la
couleur étalonnée du triplet d'entrée ``(c₀, c₁, c₂)`` — ``(Y, U, V)`` pour un calque vidéo YUV, ``(R, V, B)`` pour un
calque RVB (média RVB, calque d'effets).
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)

LUT_SIZE = 52
"""Points par axe : 255 = 51 × 5, chaque nœud tombe sur un code 8 bits entier (les filtres de l'export travaillent en
8 bits : un nœud entre deux codes serait arrondi par FFmpeg, donc faux)."""

CODE_STEP = 255 // (LUT_SIZE - 1)

DOMAIN_YUV = "yuv"
DOMAIN_RGB = "rgb"

_FFMPEG_COLORSPACES = {"bt601": "bt470bg", "bt709": "bt709", "bt2020": "bt2020nc"}
_FFMPEG_RANGES = {"video": "tv", "full": "pc"}

BAKE_TIMEOUT_SECONDS = 20.0


class GradeBakeError(RuntimeError):
    """FFmpeg n'a pas cuit la LUT (absent, LUT illisible, graphe refusé)."""


def grade_filters(grade) -> str:
    """Chaîne d'étalonnage de l'export pour ``grade`` (vide : rien à appliquer)."""
    from .export_engine import _build_color_grade_filters

    return _build_color_grade_filters(grade)


def grade_is_active(grade) -> bool:
    """Le moniteur doit-il étalonner ? Activé et différent de l'identité (comme ce que l'export rend)."""
    if grade is None or not getattr(grade, "enabled", False):
        return False
    checker = getattr(grade, "is_identity", None)
    return not (callable(checker) and checker())


def lut_key(grade, *, domain: str = DOMAIN_YUV, colorspace: str = "", color_range: str = "") -> str:
    """Identité d'une LUT : la chaîne de l'export, le fichier ``.cube`` (taille, date), l'espace d'entrée."""
    chain = grade_filters(grade)
    lut = getattr(grade, "lut", None)
    stamp = ""
    path = (getattr(lut, "source_path", None) or getattr(lut, "path", "")) if lut is not None else ""
    if path:
        try:
            info = os.stat(path)
            stamp = f"{info.st_size}:{info.st_mtime_ns}"
        except OSError:
            stamp = "absent"
    text = "|".join((chain, stamp, domain, colorspace or "", color_range or "", str(LUT_SIZE)))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def lattice(domain: str = DOMAIN_YUV, size: int = LUT_SIZE) -> bytes:
    """Le réseau d'entrée : une image ``N·N × N`` (yuv444p planaire, ou RVBA opaque) dont chaque pixel est un nœud."""
    import numpy as np

    codes = (np.arange(size) * (255 // (size - 1))).astype(np.uint8)
    first = np.repeat(codes[:, None], size * size, axis=1)                    # c₀ : la ligne
    second = np.tile(np.tile(codes, size)[None, :], (size, 1))               # c₁ : dans la tranche
    third = np.tile(np.repeat(codes, size)[None, :], (size, 1))              # c₂ : la tranche
    if domain == DOMAIN_YUV:
        return first.tobytes() + second.tobytes() + third.tobytes()
    opaque = np.full_like(first, 255)
    return np.stack((first, second, third, opaque), axis=-1).tobytes()


def bake_command(ffmpeg: str, chain: str, *, domain: str, colorspace: str = "", color_range: str = "",
                 size: int = LUT_SIZE) -> list[str]:
    """Commande FFmpeg : le réseau (entrée standard) passe dans ``chain``, suivie du ``format=rgba`` qui la suit à
    l'export ; RVB 8 bits sur la sortie standard."""
    if domain == DOMAIN_YUV:
        params = [f"range={_FFMPEG_RANGES.get(color_range, 'tv')}"]
        if colorspace in _FFMPEG_COLORSPACES:
            params.insert(0, f"colorspace={_FFMPEG_COLORSPACES[colorspace]}")
        head, pixel_format = f"setparams={':'.join(params)},", "yuv444p"
    else:
        # Comme la composition RVBA de l'export (calque d'effets) : FFmpeg choisit lui-même les conversions (yuva444p
        # pour ``eq``, puis le RVB de ``colorbalance``). Les imposer changerait le chemin, donc les arrondis : la LUT
        # ne suivrait plus l'export au niveau près.
        head, pixel_format = "", "rgba"
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", pixel_format,
        "-s", f"{size * size}x{size}", "-i", "pipe:0", "-vf", f"{head}{chain},format=rgba",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]


def bake_grade_lut(grade, *, domain: str = DOMAIN_YUV, colorspace: str = "", color_range: str = "",
                   size: int = LUT_SIZE, ffmpeg: str | None = None) -> bytes:
    """Atlas RVB 8 bits (``N·N × N``, :data:`LUT_SIZE`) de l'étalonnage ``grade``, par la chaîne de l'export."""
    from .process_supervisor import supervised_run
    from .tool_paths import find_media_tool

    chain = grade_filters(grade)
    if not chain:
        raise GradeBakeError("Étalonnage inactif : rien à cuire.")
    tool = ffmpeg or find_media_tool("ffmpeg")
    if not tool:
        raise GradeBakeError("FFmpeg introuvable.")
    command = bake_command(tool, chain, domain=domain, colorspace=colorspace, color_range=color_range, size=size)
    try:
        done = supervised_run(command, input=lattice(domain, size), capture_output=True, timeout=BAKE_TIMEOUT_SECONDS)
    except (OSError, ValueError, subprocess.SubprocessError) as error:      # absent, refusé, délai dépassé
        raise GradeBakeError(str(error)) from error
    expected = size * size * size * 3
    if done.returncode != 0 or len(done.stdout) != expected:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise GradeBakeError(detail or f"sortie de {len(done.stdout)} octets au lieu de {expected}")
    return bytes(done.stdout)


def atlas_array(atlas: bytes, size: int = LUT_SIZE):
    """Atlas en tableau ``(N, N·N, 3)`` de valeurs 0..1 (référence numpy)."""
    import numpy as np

    return np.frombuffer(atlas, np.uint8).reshape(size, size * size, 3).astype(np.float64) / 255.0


def sample_atlas(atlas, colors, size: int = LUT_SIZE):
    """Couleurs ``(…, 3)`` (codes 0..1 de l'espace de la LUT) → RVB étalonné : le calcul de la passe ``grade``.

    Interpolation bilinéaire **dans** une tranche (celle du matériel : centres de texels à +0,5, bords recopiés), puis
    linéaire entre les deux tranches qui entourent ``c₂``.
    """
    import numpy as np

    from .gpu_composite import _bilinear

    colors = np.clip(np.asarray(colors, dtype=np.float64), 0.0, 1.0)
    n = float(size)
    z = colors[..., 2] * (n - 1.0)
    z0 = np.floor(z)
    z1 = np.minimum(z0 + 1.0, n - 1.0)

    def slice_at(index):
        x = index * n + colors[..., 1] * (n - 1.0) + 0.5
        y = colors[..., 0] * (n - 1.0) + 0.5
        return _bilinear(np, atlas, x, y)

    t = (z - z0)[..., None]
    return slice_at(z0) * (1.0 - t) + slice_at(z1) * t


class GradeLutCache:
    """LUT cuites, par clé ; la cuisson se fait **hors du thread de l'interface**, la dernière demande d'abord.

    ``lookup`` rend l'atlas s'il est prêt, sinon lance (ou met en file) sa cuisson et rend ``None`` : le moniteur
    affiche le calque sans étalonnage le temps d'une cuisson (≈ 30 ms), puis ``on_ready`` (appelé depuis le fil de
    cuisson) demande une nouvelle image. Pendant un glisser de curseur, seule la dernière valeur attend : les
    intermédiaires ne sont jamais cuites. Un échec est mémorisé (pas de nouvel essai à chaque image).
    """

    def __init__(self, on_ready: Callable[[], None] | None = None, *, capacity: int = 16,
                 bake: Callable[..., bytes] = bake_grade_lut) -> None:
        self._on_ready = on_ready
        self._capacity = max(1, int(capacity))
        self._bake = bake
        self._lock = threading.Lock()
        self._ready: OrderedDict[str, bytes | None] = OrderedDict()
        self._pending: tuple[str, object, dict] | None = None
        self._busy = False

    def lookup(self, grade, *, domain: str = DOMAIN_YUV, colorspace: str = "", color_range: str = ""
               ) -> tuple[str, bytes] | None:
        options = {"domain": domain, "colorspace": colorspace, "color_range": color_range}
        key = lut_key(grade, **options)
        with self._lock:
            if key in self._ready:
                self._ready.move_to_end(key)
                atlas = self._ready[key]
                return (key, atlas) if atlas is not None else None
            self._pending = (key, grade, options)
            if self._busy:
                return None
            self._busy = True
        threading.Thread(target=self._work, name="kut-grade-lut", daemon=True).start()
        return None

    def failed(self, grade, *, domain: str = DOMAIN_YUV, colorspace: str = "", color_range: str = "") -> bool:
        """La cuisson de cette LUT a échoué (le moniteur ne l'affichera pas)."""
        key = lut_key(grade, domain=domain, colorspace=colorspace, color_range=color_range)
        with self._lock:
            return key in self._ready and self._ready[key] is None

    def _work(self) -> None:
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._busy = False
                    return
                if job[0] in self._ready:
                    continue
            key, grade, options = job
            try:
                atlas: bytes | None = self._bake(grade, **options)
            except GradeBakeError as error:
                LOGGER.warning("Étalonnage non montré par le moniteur GPU (LUT non cuite) : %s", error)
                atlas = None
            with self._lock:
                self._ready[key] = atlas
                while len(self._ready) > self._capacity:
                    self._ready.popitem(last=False)
            if self._on_ready is not None:
                try:
                    self._on_ready()
                except RuntimeError:
                    LOGGER.debug("Moniteur détruit avant la fin d'une cuisson de LUT : rien à repeindre", exc_info=True)


__all__ = [
    "CODE_STEP", "DOMAIN_RGB", "DOMAIN_YUV", "LUT_SIZE", "GradeBakeError", "GradeLutCache", "atlas_array",
    "bake_command", "bake_grade_lut", "grade_filters", "grade_is_active", "lattice", "lut_key", "sample_atlas",
]
