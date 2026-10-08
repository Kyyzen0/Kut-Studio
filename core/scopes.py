"""Calculs de scopes vidéo (tâche 31).

Ce module contient toute la logique **pure** de mesure couleur, sans
aucune dépendance à Qt ni à FFmpeg. Il est directement testable et
réutilisable (scripts CLI, tests, futures vues).

Contenu :

- :class:`ScopeLevels` — gestion des niveaux « video » (16‑235) et
  « full » (0‑255) ainsi que de l'espace Rec.709 ;
- :class:`ScopeFrame` — une image échantillonnée (tableau plat de
  valeurs 0‑255 RGB), prête à être analysée ;
- :func:`sample_luminance` — luminance Rec.709 (0‑255) ;
- :func:`compute_histogram` — histogrammes luminance + R/G/B ;
- :func:`compute_waveform` — waveform luminance (colonnes x niveaux) ;
- :func:`compute_parade` — parade RGB (colonnes x canaux) ;
- :func:`compute_vectorscope` — distribution de teinte / saturation ;
- :class:`ScopeAlerts` — détection d'écrêtage noirs / hautes lumières ;
- :class:`ScopeResult` — résultat agrégé des quatre scopes.

Conventions de normalisation : toutes les fonctions de mesure
travaillent sur des valeurs **0‑255** (entiers ou flottants). La
conversion vers des canaux 0‑1 est faite uniquement au moment du
rendu (widget Qt), ce qui évite les arrondis répétés.

Le vectoscope utilise la convention **YUV** de Broadcast (U/V en
amplitude ±0.5 autour de 128) plutôt que l'axe polaire I/Q, parce que
c'est ce que les étalonnages professionnels attendent : la ligne de
teinte de peau se situe alors à ~+123° sur le cercle des teintes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


# ---------------------------------------------------------------------------
# Niveaux vidéo
# ---------------------------------------------------------------------------


class VideoLevels(str, Enum):
    """Plage de niveaux mesurée par les scopes.

    ``VIDEO`` correspond aux niveaux **limités** de la Broadcasting
    (noirs à 16, blancs à 235) : c'est le standard de diffusion. Les
    valeurs hors de cette plage sont considérées comme du « super
    blanc » / « super noir » et sont signalées par les alertes.

    ``FULL`` correspond à la plage **complète** (0‑255), utilisée en
    travail numérique (raw, ProRes non calibré, étalonnage sur image
    non broadcast). Dans ce mode aucune valeur n'est « hors gamme ».
    """

    VIDEO = "video"
    FULL = "full"


VIDEO_BLACK_POINT = 16.0
"""Niveau du noir Legal (0‑255)."""

VIDEO_WHITE_POINT = 235.0
"""Niveau du blanc Legal (0‑255)."""


def levels_bounds(levels: VideoLevels | str) -> tuple[float, float]:
    """Retourne ``(noir, blanc)`` correspondant au mode de niveaux.

    Args:
        levels: ``"video"`` (16‑235) ou ``"full"`` (0‑255). Toute
            valeur inconnue retombe sur ``VIDEO`` : c'est le mode le
            plus prudent pour un affichage de contrôle.
    """
    try:
        mode = VideoLevels(levels)
    except ValueError:
        mode = VideoLevels.VIDEO
    if mode is VideoLevels.FULL:
        return (0.0, 255.0)
    return (VIDEO_BLACK_POINT, VIDEO_WHITE_POINT)


# ---------------------------------------------------------------------------
# Espace colorimétrique
# ---------------------------------------------------------------------------


REC709_LUMA_R = 0.2126
REC709_LUMA_G = 0.7152
REC709_LUMA_B = 0.0722

REC601_LUMA_R = 0.299
REC601_LUMA_G = 0.587
REC601_LUMA_B = 0.114


class ColorSpace(str, Enum):
    """Espace colorimétrique utilisé pour le calcul de luminance."""

    REC709 = "rec709"
    REC601 = "rec601"

    def luma_coefficients(self) -> tuple[float, float, float]:
        if self is ColorSpace.REC601:
            return (REC601_LUMA_R, REC601_LUMA_G, REC601_LUMA_B)
        return (REC709_LUMA_R, REC709_LUMA_G, REC709_LUMA_B)


#: Coefficients de luminance indexés par espace : la boucle d'accumulation
#: les résout une seule fois, hors du parcours des pixels.
_LUMA_COEFFICIENTS: dict[ColorSpace, tuple[float, float, float]] = {
    ColorSpace.REC709: (REC709_LUMA_R, REC709_LUMA_G, REC709_LUMA_B),
    ColorSpace.REC601: (REC601_LUMA_R, REC601_LUMA_G, REC601_LUMA_B),
}


def sample_luminance(
    r: float, g: float, b: float, color_space: ColorSpace | str = ColorSpace.REC709,
) -> float:
    """Luminance Rec.709 (ou Rec.601) d'un triplet 0‑255.

    Le choix par défaut est **Rec.709** car c'est l'espace de travail
    de tous les moniteurs contemporary et le standard UHD. Les
    produits broadcasts SD utilisent encore Rec.601.
    """
    try:
        space = ColorSpace(color_space)
    except ValueError:
        space = ColorSpace.REC709
    kr, kg, kb = space.luma_coefficients()
    return kr * float(r) + kg * float(g) + kb * float(b)


# ---------------------------------------------------------------------------
# Données d'entrée
# ---------------------------------------------------------------------------


@dataclass
class ScopeFrame:
    """Une image échantillonnée pour analyse.

    On stocke un **tableau plat** de triplets ``(r, g, b)`` en 0‑255
    plutôt qu'une matrice ``width × height × 3`` : l'itération est
    plus rapide en Python pur et la structure est triviale à
    sérialiser en test.

    Attributes:
        width: Largeur en pixels.
        height: Hauteur en pixels.
        pixels: Triplet de tuples ``(r, g, b)`` en 0‑255, de longueur
            ``width * height``.
    """

    width: int
    height: int
    pixels: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"Dimensions invalides : {self.width}×{self.height}."
            )
        expected = self.width * self.height
        if len(self.pixels) != expected:
            raise ValueError(
                f"Nombre de pixels incohérent : attendu {expected}, "
                f"reçu {len(self.pixels)}."
            )
        object.__setattr__(self, "pixels", tuple(self.pixels))

    @classmethod
    def from_rgb_bytes(
        cls, data: bytes | bytearray | Sequence[int], width: int, height: int,
    ) -> "ScopeFrame":
        """Construit une :class:`ScopeFrame` depuis un buffer RGB(A).

        Accepte un buffer RGB (3 octets / pixel) ou RGBA (4 octets) ;
        le canal alpha est ignoré. C'est le format produit par
        ``QImage.convertToFormat(QImage.Format_RGB888)``.
        """
        if width <= 0 or height <= 0:
            raise ValueError(
                f"Dimensions invalides : {width}×{height}."
            )
        expected = width * height
        raw = bytes(data)
        if len(raw) == expected * 4:
            stride = 4
        elif len(raw) == expected * 3:
            stride = 3
        else:
            raise ValueError(
                f"Taille de buffer incohérente : attendu {expected * 3} "
                f"(RGB) ou {expected * 4} (RGBA), reçu {len(raw)}."
            )
        pixels: list[tuple[int, int, int]] = []
        for index in range(expected):
            base = index * stride
            pixels.append((raw[base], raw[base + 1], raw[base + 2]))
        return cls(width=width, height=height, pixels=tuple(pixels))


# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------


@dataclass
class ScopeAlerts:
    """Alertes de dépassement de niveau.

    Attributes:
        black_clipping: Fraction (0‑1) des pixels sous le point noir.
        highlight_clipping: Fraction (0‑1) des pixels au‑dessus du
            point blanc.
    """

    black_clipping: float = 0.0
    highlight_clipping: float = 0.0

    @property
    def has_any(self) -> bool:
        return self.black_clipping > 0.0 or self.highlight_clipping > 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "black_clipping": float(self.black_clipping),
            "highlight_clipping": float(self.highlight_clipping),
        }


@dataclass
class ScopeResult:
    """Résultat agrégé des quatre scopes pour une image.

    Attributes:
        histogram_luma: ``(256,)`` — comptage par niveau de luminance.
        histogram_r/g/b: ``(256,)`` — comptage par canal.
        waveform: ``(columns, 256)`` — densité par colonne / niveau
            de luminance, normalisée à 1.0.
        parade: ``(columns, 256, 3)`` — idem pour R, V, B.
        vectorscope: ``(bins, bins)`` — densité de teinte / saturation
            normalisée.
        alerts: Alertes d'écrêtage.
        pixel_count: Nombre de pixels analysés.
    """

    histogram_luma: tuple[int, ...]
    histogram_r: tuple[int, ...]
    histogram_g: tuple[int, ...]
    histogram_b: tuple[int, ...]
    waveform: tuple[tuple[float, ...], ...]
    parade: tuple[tuple[tuple[float, ...], ...], ...]
    vectorscope: tuple[tuple[float, ...], ...]
    alerts: ScopeAlerts
    pixel_count: int
    columns: int
    vectorscope_bins: int
    color_space: ColorSpace = ColorSpace.REC709
    levels: VideoLevels = VideoLevels.VIDEO

    @property
    def histogram_rgb(self) -> tuple[tuple[int, ...], ...]:
        """Les trois histogrammes de canaux empilés (R, G, B)."""
        return (self.histogram_r, self.histogram_g, self.histogram_b)

    @classmethod
    def empty(
        cls,
        *,
        columns: int = 320,
        vectorscope_bins: int = 128,
        color_space: ColorSpace = ColorSpace.REC709,
        levels: VideoLevels = VideoLevels.VIDEO,
        pixel_count: int = 0,
    ) -> "ScopeResult":
        """Construit un résultat neutre (toutes grilles à zéro).

        Utilisé quand une analyse devient obsolète : l'appelant reçoit
        un :class:`ScopeAnalysis` marqué ``stale`` qu'il ignorera, sans
        avoir à payer le calcul des quatre scopes.
        """
        bins = _coerce_bins(vectorscope_bins)
        cols = _coerce_columns(columns)
        return cls(
            histogram_luma=(0,) * DEFAULT_HISTOGRAM_BINS,
            histogram_r=(0,) * DEFAULT_HISTOGRAM_BINS,
            histogram_g=(0,) * DEFAULT_HISTOGRAM_BINS,
            histogram_b=(0,) * DEFAULT_HISTOGRAM_BINS,
            waveform=tuple(
                (0.0,) * DEFAULT_HISTOGRAM_BINS for _ in range(cols)
            ),
            parade=tuple(
                tuple(
                    (0.0,) * DEFAULT_HISTOGRAM_BINS for _ in range(cols)
                )
                for _ in range(3)
            ),
            vectorscope=tuple(
                (0.0,) * bins for _ in range(bins)
            ),
            alerts=ScopeAlerts(0.0, 0.0),
            pixel_count=int(pixel_count),
            columns=cols,
            vectorscope_bins=bins,
            color_space=color_space,
            levels=levels,
        )


# ---------------------------------------------------------------------------
# Paramètres de mesure
# ---------------------------------------------------------------------------


DEFAULT_HISTOGRAM_BINS = 256
DEFAULT_SCOPE_COLUMNS = 320
DEFAULT_VECTORSCOPE_BINS = 128

# Nombre maximal de colonnes / bins : au‑delà, le coût CPU croît
# linéairement sans gain de lisibilité (les scopes sont affichés dans
# des widgets de quelques centaines de pixels).
MAX_SCOPE_COLUMNS = 2048
MAX_VECTORSCOPE_BINS = 512

# Budget d'échantillons par analyse. Au-delà, l'image est
# sous-échantillonnée régulièrement : un scope n'affiche que
# quelques centaines de points, et 98 304 échantillons restent très
# représentatifs d'une image 1080p (≈ 307 échantillons par colonne de
# waveform), pour un coût CPU divisé par plus de vingt. Ce plafond laisse
# une marge réelle sur les machines portables et les runners CI, où une
# analyse Python pure à 131 072 échantillons pouvait dépasser le budget
# d'interactivité. Le
# vecteur du vectorscope utilise un quart de ce budget. Une image
# HD reste ainsi confortablement sous le budget de 250 ms en Python pur,
# ce qui tient la
# cadence de 10 analyses / s visée pendant la lecture.
MAX_SCOPE_SAMPLES = 98_304


def scope_frame_size(width: int, height: int, *, samples: int = 2 * MAX_SCOPE_SAMPLES) -> tuple[int, int]:
    """Taille à laquelle composer l'image d'une analyse : au plus ``samples`` pixels, proportions gardées.

    L'analyse ne lit de toute façon pas plus de :data:`MAX_SCOPE_SAMPLES` pixels ; composer l'image en 1080×1920
    (2 millions de pixels, calques rastérisés compris) pour l'échantillonner ensuite coûtait ~10× le nécessaire. La
    marge ×2 garde un sous-échantillonnage régulier sur une image déjà réduite. Jamais agrandie ; côtés pairs (YUV 4:2:0).
    """
    width, height = max(2, int(width)), max(2, int(height))
    scale = min(1.0, (samples / float(width * height)) ** 0.5)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def _coerce_columns(columns: int) -> int:
    value = int(columns)
    if value < 1:
        return 1
    return min(value, MAX_SCOPE_COLUMNS)


def _coerce_bins(bins: int) -> int:
    value = int(bins)
    if value < 8:
        return 8
    return min(value, MAX_VECTORSCOPE_BINS)


def _coerce_color_space(color_space: ColorSpace | str) -> ColorSpace:
    try:
        return ColorSpace(color_space)
    except ValueError:
        return ColorSpace.REC709


def _coerce_levels(levels: VideoLevels | str) -> VideoLevels:
    try:
        return VideoLevels(levels)
    except ValueError:
        return VideoLevels.VIDEO


def sampling_stride(
    pixel_count: int, *, max_samples: int = MAX_SCOPE_SAMPLES,
) -> int:
    """Pas d'échantillonnage à appliquer pour rester sous le budget.

    Renvoie ``1`` (aucun sous-échantillonnage) tant que l'image
    tient dans le budget, et un pas régulier au-delà. Le pas est
    arrondi à un entier pour que la colonne de waveform reste
    correctement répartie.
    """
    if pixel_count <= max_samples or max_samples <= 0:
        return 1
    return max(1, pixel_count // max_samples)


def _accumulate(
    frame: ScopeFrame,
    *,
    columns: int,
    bins: int,
    color_space: ColorSpace,
    levels: VideoLevels,
    tolerance: int,
    stride: int = 1,
    vector_step: int | None = None,
) -> ScopeResult:
    """Calcule les quatre scopes et les alertes en **une seule passe**.

    Chaque pixel échantillonné alimente simultanément l'histogramme
    de luminance, les trois histogrammes de canaux, la waveform, la
    parade, le vectorscope et le comptage d'écrêtage. C'est ce qui
    rend l'analyse temps réel : cinq parcours séparés de l'image
    coûteraient cinq fois plus pour le même résultat.

    Args:
        frame: l'image à analyser ;
        columns: nombre de colonnes de waveform / parade ;
        bins: résolution du vectorscope ;
        color_space: espace de la luminance ;
        levels: mode de niveaux (impacte vectorscope et alertes) ;
        tolerance: marge d'écrêtage, en niveaux ;
        stride: pas d'échantillonnage (``1`` = tous les pixels) ;
        vector_step: pas d'échantillonnage du vectorscope. Par
            défaut, un quart de celui des autres scopes.
    """
    width = frame.width
    low, high = levels_bounds(levels)
    span = max(high - low, 1.0)

    # Coefffficients de luminance, résolus une fois pour toutes.
    kr, kg, kb = _LUMA_COEFFICIENTS[color_space]

    hist_luma = [0] * 256
    hist_r = [0] * 256
    hist_g = [0] * 256
    hist_b = [0] * 256
    waveform = [[0.0] * 256 for _ in range(columns)]
    parade = [[[0.0] * 256 for _ in range(3)] for _ in range(columns)]
    vector = [[0.0] * bins for _ in range(bins)]
    column_counts = [0] * columns
    black_count = 0
    white_count = 0
    sampled = 0

    # En mode FULL, l'écrêtage broadcast n'a pas de sens : on
    # économise les deux comparaisons par pixel.
    alerting = levels is VideoLevels.VIDEO
    margin = max(0, int(tolerance)) if alerting else 0
    black_limit = VIDEO_BLACK_POINT - margin
    white_limit = VIDEO_WHITE_POINT + margin

    pixels = frame.pixels
    if stride > 1:
        pixels = pixels[::stride]
    for index in range(len(pixels)):
        r, g, b = pixels[index]
        # Un ``ScopeFrame`` peut être construit à la main avec des
        # flottants : on caste une fois par canal plutôt que de
        # supposer des entiers, comme le font les ``compute_*``.
        r = int(r)
        g = int(g)
        b = int(b)
        y = kr * r + kg * g + kb * b
        yi = int(y + 0.5)
        if yi < 0:
            yi = 0
        elif yi > 255:
            yi = 255
        hist_luma[yi] += 1
        if r > 255:
            r = 255
        elif r < 0:
            r = 0
        if g > 255:
            g = 255
        elif g < 0:
            g = 0
        if b > 255:
            b = 255
        elif b < 0:
            b = 0
        hist_r[r] += 1
        hist_g[g] += 1
        hist_b[b] += 1

        # ``width`` peut être plus petit que le nombre de colonnes
        # demandé (vignette très étroite) : on n'alloue alors pas de
        # colonnes qui ne recevraient aucun pixel, elles resteraient
        # vides et la waveform serait écrasée d'un côté.
        column = (index % width) * columns // width
        column_counts[column] += 1
        wave_row = waveform[column]
        wave_row[yi] += 1.0
        parade_column = parade[column]
        parade_column[0][r] += 1.0
        parade_column[1][g] += 1.0
        parade_column[2][b] += 1.0

        if alerting:
            if yi <= black_limit:
                black_count += 1
            elif yi >= white_limit:
                white_count += 1
        sampled += 1

    # --- Vectorscope (deuxième boucle, sur un sous-ensemble) ------------
    # Le vectorscope est une distribution de teinte, pas une mesure
    # spatiale : sur une grande image, un quart des échantillons donne
    # une image de la distribution indiscernable à l'œil, pour un
    # quart du coût. On lui consacre donc sa propre boucle, qui n'a
    # pas à porter le bookkeeping spatial de la waveform et de la
    # parade. Sur une image de taille normale, il reste exact.
    if vector_step is None:
        vector_step = max(1, stride * 4)
    vector_pixels = pixels[::max(1, vector_step)]
    for pixel in vector_pixels:
        r, g, b = int(pixel[0]), int(pixel[1]), int(pixel[2])
        u = -0.114572 * r - 0.385428 * g + 0.5 * b
        v = 0.5 * r - 0.454153 * g - 0.045847 * b
        u_norm = (u + 64.0) / 128.0
        v_norm = (v + 64.0) / 128.0
        y_norm = ((kr * r + kg * g + kb * b) - low) / span
        if y_norm < 0.0:
            y_norm = 0.0
        elif y_norm > 1.0:
            y_norm = 1.0
        # Facteur d'atténuation pour les zones sous le noir Legal.
        weight = y_norm * 1.5
        if weight > 1.0:
            weight = 1.0
        col = int(u_norm * bins)
        row = int((1.0 - v_norm) * bins)
        if col < 0:
            col = 0
        elif col >= bins:
            col = bins - 1
        if row < 0:
            row = 0
        elif row >= bins:
            row = bins - 1
        vector[row][col] += weight

    # --- Normalisation -------------------------------------------------
    for column in range(columns):
        total = column_counts[column]
        if total > 0:
            inv = 1.0 / total
            wave_row = waveform[column]
            for level in range(256):
                wave_row[level] *= inv
            parade_column = parade[column]
            for channel in range(3):
                row_values = parade_column[channel]
                for level in range(256):
                    row_values[level] *= inv
    if sampled > 0:
        maximum = 0.0
        for row_values in vector:
            for value in row_values:
                if value > maximum:
                    maximum = value
        if maximum > 0.0:
            inv = 1.0 / maximum
            for row_values in vector:
                for index in range(bins):
                    row_values[index] *= inv

    alerts = ScopeAlerts(0.0, 0.0)
    if alerting and sampled > 0:
        alerts = ScopeAlerts(
            black_clipping=black_count / sampled,
            highlight_clipping=white_count / sampled,
        )
    return ScopeResult(
        histogram_luma=tuple(hist_luma),
        histogram_r=tuple(hist_r),
        histogram_g=tuple(hist_g),
        histogram_b=tuple(hist_b),
        waveform=tuple(tuple(row) for row in waveform),
        parade=tuple(
            tuple(tuple(row) for row in column) for column in parade
        ),
        vectorscope=tuple(tuple(row) for row in vector),
        alerts=alerts,
        pixel_count=len(frame.pixels),
        columns=columns,
        vectorscope_bins=bins,
        color_space=color_space,
        levels=levels,
    )


# ---------------------------------------------------------------------------
# Histogrammes
# ---------------------------------------------------------------------------


def compute_histogram(
    frame: ScopeFrame,
    *,
    color_space: ColorSpace | str = ColorSpace.REC709,
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Calcule les histogrammes luminance + R/G/B.

    Retourne ``(hist_luma, hist_r, hist_g, hist_b)``, chacun de
    longueur 256. La luminance est calculée avec les coefficients de
    l'espace demandé puis arrondie à l'entier le plus proche (bucket
    ``0‑255``).
    """
    luma = [0] * 256
    hr = [0] * 256
    hg = [0] * 256
    hb = [0] * 256
    for r, g, b in frame.pixels:
        y = sample_luminance(r, g, b, color_space)
        # Arrondi « banker's » évité : on ajoute 0.5 puis on floor.
        yi = int(y + 0.5)
        if yi < 0:
            yi = 0
        elif yi > 255:
            yi = 255
        luma[yi] += 1
        hr[min(255, max(0, int(r)))] += 1
        hg[min(255, max(0, int(g)))] += 1
        hb[min(255, max(0, int(b)))] += 1
    return (
        tuple(luma), tuple(hr), tuple(hg), tuple(hb),
    )


# ---------------------------------------------------------------------------
# Waveform
# ---------------------------------------------------------------------------


def compute_waveform(
    frame: ScopeFrame,
    *,
    columns: int = DEFAULT_SCOPE_COLUMNS,
    color_space: ColorSpace | str = ColorSpace.REC709,
) -> tuple[tuple[float, ...], ...]:
    """Waveform luminance : densité par colonne × niveau.

    Chaque pixel est rattaché à une colonne selon sa position
    horizontale, puis à un niveau selon sa luminance. La densité de
    chaque cellule est normalisée à 1.0 (1.0 = tous les pixels de la
    colonne sont à ce niveau).

    Returns:
        ``(columns, 256)`` de flottants normalisés.
    """
    cols = _coerce_columns(columns)
    grid = [[0.0] * 256 for _ in range(cols)]
    counts = [0] * cols
    width = frame.width
    for index, (r, g, b) in enumerate(frame.pixels):
        column = (index % width) * cols // width
        y = sample_luminance(r, g, b, color_space)
        yi = int(y + 0.5)
        if yi < 0:
            yi = 0
        elif yi > 255:
            yi = 255
        grid[column][yi] += 1.0
        counts[column] += 1
    for column in range(cols):
        total = counts[column]
        if total > 0:
            row = grid[column]
            for level in range(256):
                row[level] /= total
    return tuple(tuple(row) for row in grid)


# ---------------------------------------------------------------------------
# Parade
# ---------------------------------------------------------------------------


def compute_parade(
    frame: ScopeFrame,
    *,
    columns: int = DEFAULT_SCOPE_COLUMNS,
) -> tuple[tuple[tuple[float, ...], ...], ...]:
    """Parade RGB : trois waveform côte à côte (R, V, B).

    Returns:
        ``(columns, 256, 3)`` de flottants normalisés. L'axe du
        dernier indice est l'ordre ``(r, g, b)``.
    """
    cols = _coerce_columns(columns)
    grid = [
        [[0.0] * 256 for _ in range(3)] for _ in range(cols)
    ]
    counts = [0] * cols
    width = frame.width
    for index, pixel in enumerate(frame.pixels):
        column = (index % width) * cols // width
        counts[column] += 1
        for channel in range(3):
            value = pixel[channel]
            grid[column][channel][min(255, max(0, int(value)))] += 1.0
    for column in range(cols):
        total = counts[column]
        if total > 0:
            for channel in range(3):
                row = grid[column][channel]
                for level in range(256):
                    row[level] /= total
    return tuple(
        tuple(tuple(row) for row in column) for column in grid
    )


# ---------------------------------------------------------------------------
# Vectorscope
# ---------------------------------------------------------------------------


def compute_vectorscope(
    frame: ScopeFrame,
    *,
    bins: int = DEFAULT_VECTORSCOPE_BINS,
    levels: VideoLevels | str = VideoLevels.VIDEO,
) -> tuple[tuple[float, ...], ...]:
    """Vectorscope : distribution teinte × saturation.

    La conversion suit la matrice **YUV Broadcast** (BT.709) :

    .. code-block:: text

        Y =  0.2126 R + 0.7152 G + 0.0722 B
        U = -0.1146 R - 0.3854 G + 0.5000 B   (+128)
        V =  0.5000 R - 0.4542 G - 0.0458 B   (+128)

    U et V sont centrés sur 128 avec une amplitude de ±0.5 * 255
    ≈ ±64. Le rayon donne la saturation, l'angle la teinte. La
    ligne de teinte de peau (≈ +123° sur l'axe 0‑180° de Broadcast)
    tombe dans le quadrant bas‑droit, quadrant classique du
    vectorscope professionnel.

    Returns:
        ``(bins, bins)`` de flottants normalisés à 1.0, indexé
        ``[ligne][colonne]`` où la ligne 0 correspond au haut du
        cercle (V minimal, soit rouge).
    """
    size = _coerce_bins(bins)
    low, high = levels_bounds(levels)
    span = max(high - low, 1.0)
    grid = [[0.0] * size for _ in range(size)]
    count = 0
    for r, g, b in frame.pixels:
        rf = float(r)
        gf = float(g)
        bf = float(b)
        u = -0.114572 * rf - 0.385428 * gf + 0.5 * bf
        v = 0.5 * rf - 0.454153 * gf - 0.045847 * bf
        # On normalise sur la plage Legal pour que les couleurs très
        # sombres ne saturent pas artificiellement le bord du
        # cercle. En mode FULL, la span est 255 et rien n'est
        # écrêté.
        u_norm = (u + 64.0) / 128.0
        v_norm = (v + 64.0) / 128.0
        # On met à l'échelle la composante de chrominance par la
        # luminance normalisée, comme le fait un vectorscope
        # broadcast : une image très sombre a une chrominance
        # apparemment nulle.
        y = sample_luminance(rf, gf, bf, ColorSpace.REC709)
        y_norm = (y - low) / span
        if y_norm < 0.0:
            y_norm = 0.0
        elif y_norm > 1.0:
            y_norm = 1.0
        # Facteur d'atténuation pour les zones sous le noir Legal.
        weight = min(1.0, y_norm * 1.5)
        # ``u_norm`` / ``v_norm`` valent 0.5 pour un achromatique (le
        # centre du cercle) et 0.0 / 1.0 aux extrêmes du vecteur.
        # On les projette donc directement dans ``[0, size)`` :
        #   colonne = u_norm * size
        #   ligne   = (1 - v_norm) * size  (axe Y inversé)
        col = int(u_norm * size)
        row = int((1.0 - v_norm) * size)
        if col < 0:
            col = 0
        elif col >= size:
            col = size - 1
        if row < 0:
            row = 0
        elif row >= size:
            row = size - 1
        grid[row][col] += weight
        count += 1
    if count > 0:
        maximum = 0.0
        for grid_row in grid:
            for value in grid_row:
                if value > maximum:
                    maximum = value
        if maximum > 0.0:
            inv = 1.0 / maximum
            for grid_row in grid:
                for index in range(size):
                    grid_row[index] *= inv
    return tuple(tuple(grid_row) for grid_row in grid)


# ---------------------------------------------------------------------------
# Alertes
# ---------------------------------------------------------------------------


def compute_alerts(
    frame: ScopeFrame,
    *,
    levels: VideoLevels | str = VideoLevels.VIDEO,
    tolerance: int = 0,
) -> ScopeAlerts:
    """Détecte l'écrêtage des noirs et des hautes lumières.

    En mode ``VIDEO``, on compte les pixels dont la luminance est
    ≤ 16 (noirs) ou ≥ 235 (blancs).

    ``tolerance`` est une **marge de tolérance en niveaux**, à
    l'extérieur de la plage légale : un pixel à 14 n'est signalé
    qu'avec ``tolerance >= 2``. Cela sert à ignorer le bruit de
    quantification vidéo, qui produit des valeurs à 15‑17 pour du
    noir « propre » — sans marge, une image parfaitement étalonnée
    déclencherait une fausse alerte en permanence.

    En mode ``FULL``, il n'y a pas d'écrêtage broadcast : les
    alertes restent à zéro (les valeurs extrêmes sont légitime
    permises).
    """
    try:
        mode = VideoLevels(levels)
    except ValueError:
        mode = VideoLevels.VIDEO
    if mode is VideoLevels.FULL:
        return ScopeAlerts(0.0, 0.0)
    margin = max(0, int(tolerance))
    black_limit = VIDEO_BLACK_POINT - margin
    white_limit = VIDEO_WHITE_POINT + margin
    black_count = 0
    white_count = 0
    total = 0
    for r, g, b in frame.pixels:
        y = sample_luminance(r, g, b, ColorSpace.REC709)
        if y <= black_limit:
            black_count += 1
        if y >= white_limit:
            white_count += 1
        total += 1
    if total == 0:
        return ScopeAlerts(0.0, 0.0)
    return ScopeAlerts(
        black_clipping=black_count / total,
        highlight_clipping=white_count / total,
    )


# ---------------------------------------------------------------------------
# Analyse complète
# ---------------------------------------------------------------------------


def analyze_frame(
    frame: ScopeFrame,
    *,
    columns: int = DEFAULT_SCOPE_COLUMNS,
    vectorscope_bins: int = DEFAULT_VECTORSCOPE_BINS,
    color_space: ColorSpace | str = ColorSpace.REC709,
    levels: VideoLevels | str = VideoLevels.VIDEO,
    clip_tolerance: int = 0,
) -> ScopeResult:
    """Lance les quatre analyses + alertes sur une image.

    Point d'entrée unique : la couche UI n'appelle que cette
    fonction.

    Elle n'appelle pas les cinq ``compute_*`` à la suite : ces
    fonctions parcourent chacune l'image entière, ce qui coûtait
    ~3,6 s sur une frame 1080p en Python pur — dix fois trop pour
    un rafraîchissement à 10 img/s. On fait donc **une seule passe**
    d'accumulation (:func:`_accumulate`) et, au-delà de
    :data:`MAX_SCOPE_SAMPLES` pixels, un sous-échantillonnage
    régulier.

    Le sous-échantillonnage est sans effet visible : un scope
    affiche quelques centaines de points, et 262 144 échantillons
    reste statistiquement très représentatif d'une image 1080p. Le
    résultat reste par ailleurs exact pour les images de taille
    modeste (HD, DVD, vignettes), qui ne sont jamais sous-échantillonnées.
    """
    cols = _coerce_columns(columns)
    bins = _coerce_bins(vectorscope_bins)
    space = _coerce_color_space(color_space)
    mode = _coerce_levels(levels)
    stride = sampling_stride(len(frame.pixels))
    result = _accumulate(
        frame,
        columns=cols,
        bins=bins,
        color_space=space,
        levels=mode,
        tolerance=clip_tolerance,
        stride=stride,
        # Le vectorscope n'est sous-échantillonné que si l'image est
        # effectivement surdimensionnée : sur une image normale il
        # reste exact, comme le sont les trois autres scopes.
        vector_step=1 if len(frame.pixels) <= MAX_SCOPE_SAMPLES
        else stride * 4,
    )
    return result


# ---------------------------------------------------------------------------
# Conversions couleur utilisées par le rendu
# ---------------------------------------------------------------------------


def rgb_to_yuv709(
    r: float, g: float, b: float,
) -> tuple[float, float, float]:
    """Convertit un triplet RGB 0‑255 en YUV Broadcast (U/V centrés sur 128)."""
    rf = float(r)
    gf = float(g)
    bf = float(b)
    y = 0.2126 * rf + 0.7152 * gf + 0.0722 * bf
    u = -0.114572 * rf - 0.385428 * gf + 0.5 * bf + 128.0
    v = 0.5 * rf - 0.454153 * gf - 0.045847 * bf + 128.0
    return (y, u, v)


def hue_degrees(r: float, g: float, b: float) -> float:
    """Teinte en degrés (0‑360) d'un triplet RGB, via HSV.

    Renvoie ``0.0`` pour un gris (saturation nulle), ce qui est la
    convention usuelle : le centre du vectorscope n'a pas de teinte.
    """
    rf = float(r) / 255.0
    gf = float(g) / 255.0
    bf = float(b) / 255.0
    maximum = max(rf, gf, bf)
    minimum = min(rf, gf, bf)
    delta = maximum - minimum
    if delta <= 1e-9:
        return 0.0
    if maximum == rf:
        hue = 60.0 * (((gf - bf) / delta) % 6.0)
    elif maximum == gf:
        hue = 60.0 * (((bf - rf) / delta) + 2.0)
    else:
        hue = 60.0 * (((rf - gf) / delta) + 4.0)
    if hue < 0.0:
        hue += 360.0
    return hue


def saturation_percent(r: float, g: float, b: float) -> float:
    """Saturation HSV en pourcentage (0‑100)."""
    rf = float(r) / 255.0
    gf = float(g) / 255.0
    bf = float(b) / 255.0
    maximum = max(rf, gf, bf)
    minimum = min(rf, gf, bf)
    if maximum <= 0.0:
        return 0.0
    return 100.0 * (maximum - minimum) / maximum


#: Position de la ligne de teinte de peau sur le cercle du
#: vectorscope Broadcast, en degrés. La valeur 123° est la référence
#: de l'IREF : une peau éclairée correctement se place sur cette
#: ligne, légèrement à gauche du magenta.
SKIN_TONE_LINE_DEGREES = 123.0


def skin_tone_reference(r: float, g: float, b: float) -> float:
    """Distance normalisée d'un triplet à la ligne de teinte de peau.

    Retourne ``0.0`` si le triplet est exactement sur la ligne, et
    ``1.0`` (ou plus) s'il est à l'opposé. La mesure angulaire est
    normalisée sur 180° (demi‑cercle) car le cercle de teinte est
    symétrique par rapport à l'axe des deuxariants.
    """
    hue = hue_degrees(r, g, b)
    if hue == 0.0 and saturation_percent(r, g, b) < 1e-6:
        # Gris : on considère qu'il est sur la ligne (l'écrêtage
        # de teinte n'a pas de sens pour un achromatique).
        return 0.0
    delta = abs(hue - SKIN_TONE_LINE_DEGREES)
    if delta > 180.0:
        delta = 360.0 - delta
    return delta / 180.0


__all__ = [
    "ColorSpace",
    "DEFAULT_HISTOGRAM_BINS",
    "DEFAULT_SCOPE_COLUMNS",
    "DEFAULT_VECTORSCOPE_BINS",
    "MAX_SCOPE_COLUMNS",
    "MAX_SCOPE_SAMPLES",
    "MAX_VECTORSCOPE_BINS",
    "SKIN_TONE_LINE_DEGREES",
    "ScopeAlerts",
    "ScopeFrame",
    "ScopeResult",
    "VideoLevels",
    "VIDEO_BLACK_POINT",
    "VIDEO_WHITE_POINT",
    "analyze_frame",
    "compute_alerts",
    "compute_histogram",
    "compute_parade",
    "compute_vectorscope",
    "compute_waveform",
    "hue_degrees",
    "levels_bounds",
    "rgb_to_yuv709",
    "sample_luminance",
    "sampling_stride",
    "saturation_percent",
    "skin_tone_reference",
]
