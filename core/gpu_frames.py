"""Formats d'images décodées et conversion couleur pour l'aperçu GPU.

Le décodeur (matériel ou logiciel) livre des images **YUV** dans des formats
variés ; l'aperçu GPU les envoie **telles quelles** à la carte (un plan par
texture) et fait la conversion YUV → RVB dans le shader. On évite ainsi la
conversion CPU YUV → RVBA (4 octets par pixel au lieu de 1,5 pour NV12) et
toute relecture GPU → CPU.

Où les conversions ont lieu
---------------------------

==========================  ==========================  ==========================
Étape                       Chemin GPU                   Chemin CPU (repli)
==========================  ==========================  ==========================
Décodage                    VideoToolbox / NVDEC / … ou logiciel (Qt FFmpeg)
Mémoire système             NV12 / P010 / YUV420P(10) mappés (copie zéro côté Qt)
Envoi                       1 copie par plan → texture  —
YUV → RVB                   shader (matrice ci-dessous) Qt (``QVideoFrame``)
Effets, masque, fusion      shader                      segments FFmpeg (export)
Affichage                   ``QRhiWidget`` (Metal,      ``QGraphicsVideoItem``
                            D3D11, Vulkan, OpenGL)      (raster)
==========================  ==========================  ==========================

Matrice couleur : celle de l'étiquette du flux (BT.601, BT.709, BT.2020) ; un
flux **non étiqueté** est lu en **BT.601 plage limitée**, exactement comme
``swscale`` à l'export (vérifié : même RVB au bit près sur des couleurs pures).

Module pur (aucun Qt) : les formats Qt sont désignés par leur nom
(``"Format_NV12"``), ce qui le rend testable sans GPU.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Formats -------------------------------------------------------------------------------------------

LAYOUT_PACKED = 0
"""Une texture RVBA (ou BGRA) : pas de conversion YUV."""
LAYOUT_SEMIPLANAR = 1
"""Y + UV entrelacés (NV12, P010)."""
LAYOUT_PLANAR = 2
"""Y, U et V séparés (YUV420P, YUV422P, YUV444P…)."""


@dataclass(frozen=True)
class PlaneSpec:
    """Une texture d'un plan.

    Attributes:
        texture_format: format QRhi (``R8``, ``RG8``, ``R16``, ``RG16``, ``RGBA8``, ``BGRA8``).
        width_divisor / height_divisor: sous-échantillonnage du plan.
        bytes_per_texel: pour déduire la largeur de texture du pas de ligne.
    """

    texture_format: str
    width_divisor: int = 1
    height_divisor: int = 1
    bytes_per_texel: int = 1


@dataclass(frozen=True)
class FrameLayout:
    """Comment un format décodé devient des textures et un mode de shader.

    ``value_scale`` corrige la normalisation des formats 10 bits rangés dans
    16 bits : P010 aligne les bits **en haut** (valeur/65535 ≈ code/1023 × 0,99902),
    YUV420P10 **en bas** (valeur/65535 = code/65535, à multiplier par 65535/1023).
    """

    name: str
    planes: tuple[PlaneSpec, ...]
    mode: int
    bit_depth: int = 8
    value_scale: float = 1.0
    swizzle_bgra: bool = False

    @property
    def is_yuv(self) -> bool:
        return self.mode != LAYOUT_PACKED

    def plane_size(self, index: int, width: int, height: int) -> tuple[int, int]:
        spec = self.planes[index]
        return (max(1, -(-int(width) // spec.width_divisor)), max(1, -(-int(height) // spec.height_divisor)))

    def bytes_per_frame(self, width: int, height: int) -> int:
        total = 0
        for index, spec in enumerate(self.planes):
            w, h = self.plane_size(index, width, height)
            total += w * h * spec.bytes_per_texel
        return total


_P010_SCALE = 65535.0 / 65472.0
_LOW10_SCALE = 65535.0 / 1023.0

LAYOUTS: dict[str, FrameLayout] = {
    "nv12": FrameLayout("nv12", (PlaneSpec("R8"), PlaneSpec("RG8", 2, 2, 2)), LAYOUT_SEMIPLANAR),
    "p010": FrameLayout("p010", (PlaneSpec("R16", 1, 1, 2), PlaneSpec("RG16", 2, 2, 4)),
                        LAYOUT_SEMIPLANAR, 10, _P010_SCALE),
    "p016": FrameLayout("p016", (PlaneSpec("R16", 1, 1, 2), PlaneSpec("RG16", 2, 2, 4)),
                        LAYOUT_SEMIPLANAR, 16),
    "yuv420p": FrameLayout("yuv420p", (PlaneSpec("R8"), PlaneSpec("R8", 2, 2), PlaneSpec("R8", 2, 2)),
                           LAYOUT_PLANAR),
    "yuv420p10": FrameLayout("yuv420p10", (PlaneSpec("R16", 1, 1, 2), PlaneSpec("R16", 2, 2, 2),
                                           PlaneSpec("R16", 2, 2, 2)), LAYOUT_PLANAR, 10, _LOW10_SCALE),
    "yuv422p": FrameLayout("yuv422p", (PlaneSpec("R8"), PlaneSpec("R8", 2, 1), PlaneSpec("R8", 2, 1)),
                           LAYOUT_PLANAR),
    "yuv422p10": FrameLayout("yuv422p10", (PlaneSpec("R16", 1, 1, 2), PlaneSpec("R16", 2, 1, 2),
                                           PlaneSpec("R16", 2, 1, 2)), LAYOUT_PLANAR, 10, _LOW10_SCALE),
    "yuv444p": FrameLayout("yuv444p", (PlaneSpec("R8"), PlaneSpec("R8"), PlaneSpec("R8")), LAYOUT_PLANAR),
    "rgba": FrameLayout("rgba", (PlaneSpec("RGBA8", 1, 1, 4),), LAYOUT_PACKED),
    "bgra": FrameLayout("bgra", (PlaneSpec("RGBA8", 1, 1, 4),), LAYOUT_PACKED, swizzle_bgra=True),
}
"""Formats envoyés tels quels. Tout autre format passe par la conversion Qt (repli)."""

QT_PIXEL_FORMATS: dict[str, str] = {
    "Format_NV12": "nv12",
    "Format_P010": "p010",
    "Format_P016": "p016",
    "Format_YUV420P": "yuv420p",
    "Format_YUV420P10": "yuv420p10",
    "Format_YUV422P": "yuv422p",
    "Format_RGBA8888": "rgba",
    "Format_RGBX8888": "rgba",
    "Format_BGRA8888": "bgra",
    "Format_BGRX8888": "bgra",
}
"""Nom de ``QVideoFrameFormat.PixelFormat`` → disposition. Le reste : ``toImage()`` (copie CPU)."""


def layout_for(qt_format_name: str) -> FrameLayout | None:
    """Disposition d'un format Qt, ``None`` s'il faut passer par la conversion de Qt."""
    key = QT_PIXEL_FORMATS.get(str(qt_format_name))
    return LAYOUTS.get(key) if key else None


# --- Matrices couleur ------------------------------------------------------------------------------------

_KR_KB = {"bt601": (0.299, 0.114), "bt709": (0.2126, 0.0722), "bt2020": (0.2627, 0.0593)}

QT_COLOR_SPACES = {
    "ColorSpace_BT601": "bt601",
    "ColorSpace_BT709": "bt709",
    "ColorSpace_BT2020": "bt2020",
    "ColorSpace_AdobeRgb": "bt709",
    "ColorSpace_Undefined": "",
}
QT_COLOR_RANGES = {"ColorRange_Video": "video", "ColorRange_Full": "full", "ColorRange_Unknown": ""}


def resolve_color(colorspace: str = "", color_range: str = "") -> tuple[str, str]:
    """Espace et plage effectifs ; l'inconnu suit ``swscale`` (BT.601, plage limitée)."""
    space = colorspace if colorspace in _KR_KB else "bt601"
    rng = color_range if color_range in ("video", "full") else "video"
    return space, rng


Matrix4 = tuple[tuple[float, float, float, float], ...]


def yuv_to_rgb_matrix(colorspace: str = "", color_range: str = "") -> Matrix4:
    """Matrice 4×4 (lignes) : ``(Y, U, V, 1)`` en codes normalisés 0..1 → ``(R, V, B, 1)``.

    Les codes sont ceux du plan divisés par leur maximum (255 en 8 bits) : un Y
    de 16 vaut 16/255. Plage limitée : Y 16..235, UV 16..240 (centre 128).
    """
    space, rng = resolve_color(colorspace, color_range)
    kr, kb = _KR_KB[space]
    kg = 1.0 - kr - kb
    if rng == "full":
        ys, yo, cs = 1.0, 0.0, 1.0
    else:
        ys, yo, cs = 255.0 / 219.0, 16.0 / 255.0, 255.0 / 224.0
    co = 128.0 / 255.0
    rv = 2.0 * (1.0 - kr) * cs
    bu = 2.0 * (1.0 - kb) * cs
    gu = -bu * kb / kg
    gv = -rv * kr / kg
    rows = (
        (ys, 0.0, rv, -ys * yo - rv * co),
        (ys, gu, gv, -ys * yo - (gu + gv) * co),
        (ys, bu, 0.0, -ys * yo - bu * co),
        (0.0, 0.0, 0.0, 1.0),
    )
    return rows


def invert4(m: Matrix4) -> Matrix4:
    """Inverse d'une matrice affine 4×4 (dernière ligne 0,0,0,1)."""
    a = [list(row[:3]) for row in m[:3]]
    t = [m[0][3], m[1][3], m[2][3]]
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    if abs(det) < 1e-12:
        raise ValueError("matrice non inversible")
    inv = [[0.0] * 3 for _ in range(3)]
    inv[0][0] = (a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det
    inv[0][1] = (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det
    inv[0][2] = (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det
    inv[1][0] = (a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det
    inv[1][1] = (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det
    inv[1][2] = (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det
    inv[2][0] = (a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det
    inv[2][1] = (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det
    inv[2][2] = (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det
    offset = [-(inv[r][0] * t[0] + inv[r][1] * t[1] + inv[r][2] * t[2]) for r in range(3)]
    rows = [(inv[r][0], inv[r][1], inv[r][2], offset[r]) for r in range(3)]
    return (rows[0], rows[1], rows[2], (0.0, 0.0, 0.0, 1.0))


def column_major(m: Matrix4) -> tuple[float, ...]:
    """Ordre mémoire d'un ``mat4`` GLSL (std140)."""
    return tuple(m[r][c] for c in range(4) for r in range(4))


__all__ = [
    "LAYOUTS",
    "LAYOUT_PACKED",
    "LAYOUT_PLANAR",
    "LAYOUT_SEMIPLANAR",
    "QT_COLOR_RANGES",
    "QT_COLOR_SPACES",
    "QT_PIXEL_FORMATS",
    "FrameLayout",
    "PlaneSpec",
    "column_major",
    "invert4",
    "layout_for",
    "resolve_color",
    "yuv_to_rgb_matrix",
]
