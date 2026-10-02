"""Suivi d'un point d'une image à la suivante : corrélation croisée normalisée.

Seul module du tracking qui dépend de **numpy**, importé à la demande :
Kut-Studio démarre, ouvre, rend et exporte sans lui ; seule l'analyse
l'exige (voir :func:`require_numpy`).

Algorithme (par image et par point, en pixels d'analyse)
--------------------------------------------------------

1. **Prédiction** : position précédente + vitesse (amortie).
2. **Zone de recherche** centrée sur la prédiction, bornée à l'image ;
   trop rognée par le bord → point **hors de l'image** (perdu).
3. **NCC** (corrélation croisée normalisée, Lewis 1995) du motif sur la
   zone : numérateur par FFT (``numpy.fft.rfft2``), moyennes et variances
   locales par images intégrales. Score ∈ [−1, 1], insensible aux
   variations de luminosité et de contraste.
4. **Sous-pixel** : parabole sur les voisins du pic, en x puis en y.
5. **Confiance** = score du pic : ≥ ``good`` suivi, ≥ ``minimum``
   incertain, sinon **perdu** (aucune position n'est inventée).
6. **Motif** : figé (image de départ) ou adaptatif (ré-échantillonné à la
   position sous-pixel après chaque image fiable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .tracking_model import AdaptMode, SampleStatus


class TrackingUnavailable(RuntimeError):
    """numpy absent : l'analyse est impossible (le reste de l'application fonctionne)."""


def require_numpy():
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        raise TrackingUnavailable(
            "Le tracking nécessite numpy (pip install numpy)."
        ) from exc
    return numpy


@dataclass
class StepResult:
    """Résultat d'une image pour un point (pixels d'analyse)."""

    x: float
    y: float
    confidence: float
    status: SampleStatus
    reason: str = ""


def sample_patch(np, frame, x: float, y: float, half_w: int, half_h: int):
    """Motif ``(2·half_h+1) × (2·half_w+1)`` centré en ``(x, y)`` (bilinéaire).

    ``None`` si le motif sort de l'image.
    """
    height, width = frame.shape
    if x - half_w < 0 or y - half_h < 0 or x + half_w > width - 1 or y + half_h > height - 1:
        return None
    xs = x + np.arange(-half_w, half_w + 1, dtype=np.float64)
    ys = y + np.arange(-half_h, half_h + 1, dtype=np.float64)
    x0 = np.floor(xs).astype(np.intp)
    y0 = np.floor(ys).astype(np.intp)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = (xs - x0)[None, :]
    fy = (ys - y0)[:, None]
    top = frame[np.ix_(y0, x0)] * (1 - fx) + frame[np.ix_(y0, x1)] * fx
    bottom = frame[np.ix_(y1, x0)] * (1 - fx) + frame[np.ix_(y1, x1)] * fx
    return (top * (1 - fy) + bottom * fy).astype(np.float64)


def _fft_size(n: int) -> int:
    """Taille rapide pour la FFT (produit de 2, 3, 5)."""
    size = max(1, int(n))
    while True:
        m = size
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return size
        size += 1


def ncc_map(np, window, template):
    """Carte NCC « valide » de ``template`` sur ``window`` (forme ``(H−h+1, W−w+1)``)."""
    H, W = window.shape
    h, w = template.shape
    n = float(h * w)
    t = template - template.mean()
    t_norm = math.sqrt(float((t * t).sum()))
    if t_norm < 1e-6:
        return None  # motif uniforme : rien à suivre
    fh, fw = _fft_size(H), _fft_size(W)
    spectrum = np.fft.rfft2(window, (fh, fw)) * np.conj(np.fft.rfft2(t, (fh, fw)))
    numerator = np.fft.irfft2(spectrum, (fh, fw))[: H - h + 1, : W - w + 1]
    integral = np.zeros((H + 1, W + 1))
    integral[1:, 1:] = window.cumsum(0).cumsum(1)
    squares = np.zeros((H + 1, W + 1))
    squares[1:, 1:] = (window * window).cumsum(0).cumsum(1)

    def box(table):
        return table[h:, w:] - table[:-h, w:] - table[h:, :-w] + table[:-h, :-w]

    local_sum = box(integral)
    local_sq = box(squares)
    variance = np.maximum(local_sq - local_sum * local_sum / n, 0.0)
    denominator = np.sqrt(variance) * t_norm
    result = np.zeros_like(numerator)
    mask = denominator > 1e-6 * t_norm * max(1.0, math.sqrt(n))
    result[mask] = numerator[mask] / denominator[mask]
    return np.clip(result, -1.0, 1.0)


def _subpixel(left: float, centre: float, right: float) -> float:
    """Décalage du sommet entre trois scores voisins.

    Ajustement gaussien (parabole sur les logarithmes) quand les trois
    scores sont positifs : moins biaisé qu'une parabole pour un pic de
    corrélation ; parabole sinon.
    """
    if left > 1e-6 and centre > 1e-6 and right > 1e-6:
        a, b, c = math.log(left), math.log(centre), math.log(right)
        denominator = a - 2.0 * b + c
        if abs(denominator) > 1e-12:
            return max(-0.5, min(0.5, 0.5 * (a - c) / denominator))
    denominator = left - 2.0 * centre + right
    if abs(denominator) < 1e-12:
        return 0.0
    return max(-0.5, min(0.5, 0.5 * (left - right) / denominator))


def _peak(np, scores):
    """``(ligne, colonne, score, dx, dy)`` du maximum, sous-pixel compris."""
    flat_index = int(np.argmax(scores))
    row, col = divmod(flat_index, scores.shape[1])
    peak = float(scores[row, col])
    dx = _subpixel(scores[row, col - 1], peak, scores[row, col + 1]) if 0 < col < scores.shape[1] - 1 else 0.0
    dy = _subpixel(scores[row - 1, col], peak, scores[row + 1, col]) if 0 < row < scores.shape[0] - 1 else 0.0
    return row, col, peak, dx, dy


class PointMatcher:
    """État de suivi d'un point au fil des images (pixels d'analyse)."""

    def __init__(
        self,
        frame,
        x: float,
        y: float,
        *,
        pattern: tuple[float, float],
        search: tuple[float, float],
        min_confidence: float,
        good_confidence: float,
        adapt: str = AdaptMode.ADAPTIVE,
    ) -> None:
        self.np = require_numpy()
        self.half_w = max(4, int(round(pattern[0] / 2.0)))
        self.half_h = max(4, int(round(pattern[1] / 2.0)))
        self.search_half_w = max(self.half_w + 2, int(round(search[0] / 2.0)))
        self.search_half_h = max(self.half_h + 2, int(round(search[1] / 2.0)))
        self.min_confidence = float(min_confidence)
        self.good_confidence = float(good_confidence)
        self.adapt = adapt
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0
        self.template = sample_patch(self.np, frame, self.x, self.y, self.half_w, self.half_h)
        # Motif d'origine : en mode adaptatif, il recale la position tant
        # qu'il reste reconnaissable (pas de dérive cumulée des mises à jour).
        self.reference = self.template

    @property
    def ready(self) -> bool:
        return self.template is not None

    def step(self, frame) -> StepResult:
        """Cherche le motif dans ``frame`` ; met à jour la position si fiable."""
        np = self.np
        if self.template is None:
            return StepResult(self.x, self.y, 0.0, SampleStatus.LOST, "out_of_frame")
        height, width = frame.shape
        px, py = self.x + self.vx, self.y + self.vy
        x0 = int(math.floor(px)) - self.search_half_w
        y0 = int(math.floor(py)) - self.search_half_h
        x1 = int(math.floor(px)) + self.search_half_w + 1
        y1 = int(math.floor(py)) + self.search_half_h + 1
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
        th, tw = self.template.shape
        if cx1 - cx0 < tw + 2 or cy1 - cy0 < th + 2:
            return StepResult(px, py, 0.0, SampleStatus.LOST, "out_of_frame")
        window = frame[cy0:cy1, cx0:cx1].astype(np.float64)
        scores = ncc_map(np, window, self.template)
        if scores is None or scores.size == 0:
            return StepResult(px, py, 0.0, SampleStatus.LOST, "flat")
        row, col, peak, dx, dy = _peak(np, scores)
        # Pic collé à un bord de recherche rogné par l'image : le motif voudrait
        # sortir du cadre. On le dit plutôt que de bloquer la position au bord.
        if (
            (col == 0 and x0 < 0) or (row == 0 and y0 < 0)
            or (col == scores.shape[1] - 1 and x1 > width) or (row == scores.shape[0] - 1 and y1 > height)
        ):
            return StepResult(px, py, max(0.0, min(1.0, peak)), SampleStatus.LOST, "out_of_frame")
        new_x = cx0 + col + dx + self.half_w
        new_y = cy0 + row + dy + self.half_h
        confidence = max(0.0, min(1.0, peak))
        if self.reference is not self.template and confidence >= self.min_confidence:
            refined = self._refine_with_reference(frame, new_x, new_y)
            if refined is not None:
                new_x, new_y = refined
        if not (math.isfinite(new_x) and math.isfinite(new_y)):
            return StepResult(px, py, 0.0, SampleStatus.LOST, "invalid")
        if confidence < self.min_confidence:
            return StepResult(new_x, new_y, confidence, SampleStatus.LOST, "low_confidence")
        status = SampleStatus.TRACKED if confidence >= self.good_confidence else SampleStatus.UNCERTAIN
        # Vitesse amortie : une image incertaine compte moins dans la prédiction.
        weight = 0.7 if status is SampleStatus.TRACKED else 0.3
        self.vx = weight * (new_x - self.x) + (1 - weight) * self.vx
        self.vy = weight * (new_y - self.y) + (1 - weight) * self.vy
        self.x, self.y = new_x, new_y
        if self.adapt == AdaptMode.ADAPTIVE and status is SampleStatus.TRACKED:
            patch = sample_patch(np, frame, new_x, new_y, self.half_w, self.half_h)
            if patch is not None:
                self.template = patch
        return StepResult(new_x, new_y, confidence, status)

    def _refine_with_reference(self, frame, x: float, y: float):
        """Recalage sur le motif d'origine autour de ``(x, y)`` (±3 px), s'il est fiable."""
        np = self.np
        height, width = frame.shape
        th, tw = self.reference.shape
        radius = 3
        x0 = int(math.floor(x)) - self.half_w - radius
        y0 = int(math.floor(y)) - self.half_h - radius
        x1, y1 = x0 + tw + 2 * radius + 1, y0 + th + 2 * radius + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return None
        scores = ncc_map(np, frame[y0:y1, x0:x1].astype(np.float64), self.reference)
        if scores is None or scores.size == 0:
            return None
        row, col, peak, dx, dy = _peak(np, scores)
        if peak < self.good_confidence:
            return None
        return (x0 + col + dx + self.half_w, y0 + row + dy + self.half_h)


def suggest_features(frame, count: int = 4, *, min_distance: float = 0.18, margin: float = 0.12):
    """Points saillants (coins) bien répartis : critère de Shi-Tomasi.

    Retourne au plus ``count`` positions ``(x, y)`` en pixels de ``frame``,
    éloignées d'au moins ``min_distance`` × la diagonale, hors d'une marge
    ``margin`` (fraction de la taille). Sert à la stabilisation automatique.
    """
    np = require_numpy()
    image = np.asarray(frame, dtype=np.float64)
    height, width = image.shape
    gy, gx = np.gradient(image)
    radius = max(2, int(min(width, height) / 120))

    def box(values):
        table = np.zeros((height + 1, width + 1))
        table[1:, 1:] = values.cumsum(0).cumsum(1)
        size = 2 * radius + 1
        out = np.zeros_like(values)
        out[radius:height - radius, radius:width - radius] = (
            table[size:, size:] - table[:-size, size:] - table[size:, :-size] + table[:-size, :-size]
        )
        return out

    sxx, syy, sxy = box(gx * gx), box(gy * gy), box(gx * gy)
    trace_half = (sxx + syy) / 2.0
    response = trace_half - np.sqrt(((sxx - syy) / 2.0) ** 2 + sxy * sxy)
    mx, my = int(width * margin), int(height * margin)
    response[:my, :] = 0
    response[height - my:, :] = 0
    response[:, :mx] = 0
    response[:, width - mx:] = 0
    order = np.argsort(response, axis=None)[::-1]
    spacing = min_distance * math.hypot(width, height)
    chosen: list[tuple[float, float]] = []
    best = float(response.flat[order[0]]) if order.size else 0.0
    for flat in order[: 50000]:
        value = float(response.flat[flat])
        if value <= 0 or value < best * 0.01:
            break
        y, x = divmod(int(flat), width)
        if all(math.hypot(x - cx, y - cy) >= spacing for cx, cy in chosen):
            chosen.append((float(x), float(y)))
            if len(chosen) >= count:
                break
    return chosen


def self_check() -> str:
    """Contrôle de l'analyse sans média ni FFmpeg (smoke test de l'application construite).

    Vérifie que numpy et ses extensions sont utilisables (empaquetage) et que
    le suivi retrouve un déplacement sous-pixel connu. Retourne ``""`` si tout
    va bien, sinon la raison de l'échec.
    """
    try:
        np = require_numpy()
    except TrackingUnavailable as exc:
        return str(exc)
    yy, xx = np.mgrid[0:120, 0:160].astype(np.float64)

    def frame(cx: float, cy: float):
        return 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / 50.0) + 90.0 * np.exp(
            -((xx - cx - 8) ** 2 + (yy - cy + 5) ** 2) / 12.0
        )

    matcher = PointMatcher(
        frame(60.0, 60.0), 60.0, 60.0, pattern=(32, 32), search=(80, 80),
        min_confidence=0.5, good_confidence=0.8,
    )
    step = matcher.step(frame(63.5, 57.75))
    error = math.hypot(step.x - 63.5, step.y - 57.75)
    if step.status is not SampleStatus.TRACKED or error > 0.25:
        return f"suivi incorrect ({step.status.name}, erreur {error:.3f} px)"
    return ""


__all__ = [
    "PointMatcher", "StepResult", "TrackingUnavailable", "ncc_map", "require_numpy",
    "sample_patch", "self_check", "suggest_features",
]
