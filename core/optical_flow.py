"""Flux optique : paramètres, backends, estimateur et interpolateur (le contrat que tout le reste appelle).

Ce module ne contient pas d'algorithme : il décrit *ce qu'on demande* (:class:`FlowParams`, issus de la qualité choisie),
*qui sait le faire* (:class:`OpticalFlowBackend`) et *ce qu'on fait quand le résultat ne serait pas fiable*
(:class:`Fallback`). Le premier backend, NumPy (:mod:`core.flow_numpy`), tourne partout sur le processeur ; Metal, CUDA, Vulkan,
OpenCL, CoreML ou un réseau neuronal s'ajouteront en implémentant le même contrat, sans que le plan d'interpolation
(:mod:`core.frame_interpolation`), le cache, la préparation (:mod:`core.retime_prepare`) ni le graphe d'export ne changent.

Trois rôles, un moteur :

- :class:`FlowEstimator` — **analyse** une paire d'images : coupure, images identiques, sinon les flux aller et retour
  (:class:`PairAnalysis`, ce que le cache stocke) ;
- :class:`FrameInterpolator` — **fabrique** l'image au point ``t`` d'une paire analysée, ou retombe sur un résultat honnête ;
- :class:`OpticalFlowEngine` — les deux, pour un backend et une qualité.

**Jamais de sortie silencieusement différente.** Chaque image fabriquée dit ce qu'elle est (:class:`Fallback`) : issue du flux,
reprise telle quelle (images identiques), prise à l'image la plus proche (coupure : deux plans ne se mélangent pas) ou mélangée
(flux peu fiable). L'appelant en tient le compte et le dit à l'utilisateur.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Protocol

from .time_remapping import FlowQuality

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from .flow_field import FlowField

    Plane = npt.NDArray[np.float32]
    Frame = npt.NDArray[np.float32]

CancelCheck = Callable[[], bool]

ENGINE_VERSION = 2
"""Version de l'algorithme : à incrémenter dès qu'un champ stocké, ou une image fabriquée, ne serait plus celle recalculée.

2 : lissage avec a priori de mouvement nul (une zone plate immobile n'hérite plus du mouvement d'un petit objet) et images
identiques détectées par la fraction de pixels changés (et non l'écart moyen) ; les analyses de la version 1 sont ignorées."""

CUT_HISTOGRAM = 0.45
CUT_CORRELATION = 0.6
"""Coupure franche : histogrammes de luminance très différents (variation totale ≥ 0,45) **et** structures sans rapport
(corrélation normalisée < 0,6). Un flash change l'histogramme mais garde la structure : ce n'est pas une coupure."""
IDENTICAL_LEVEL = 0.02
IDENTICAL_FRACTION = 2e-5
"""Deux images sont les mêmes (caméra immobile, image dupliquée) quand moins de 0,002 % de leurs pixels diffèrent de plus de 0,02
en luminance (≈ 5 niveaux sur 255 : le bruit de compression). On compte les pixels qui ont **changé**, on ne fait pas une moyenne :
un petit objet qui bouge dans une grande image immobile (un carré de 540 px à 4K) a un écart moyen minuscule et doit pourtant être
interpolé (mesuré : l'ancien critère « écart moyen < 5e-4 » le jugeait identique et sautait le flux)."""
MIN_FLOW_SCORE = 0.35
"""Confiance minimale d'une image fabriquée par le flux ; en deçà, un mélange simple est plus honnête qu'un mouvement douteux."""

_LUMA = (0.2126, 0.7152, 0.0722)


@dataclass(frozen=True)
class FlowParams:
    """Réglages de l'analyse. Tout ce qui change le résultat y figure : c'est une partie de la clé du cache de mouvement.

    Attributes:
        scale: facteur de réduction de la grille d'analyse par rapport à la résolution de travail (1 = pleine, 2 = moitié,
            4 = quart).
        levels: niveaux de la pyramide (du plus grossier au plus fin).
        iterations: itérations par niveau.
        window: rayon (px) de la fenêtre de Lucas-Kanade.
        smoothing: rayon (px) du lissage pondéré par la texture.
        regularization: régularisation de Tikhonov, relative à la trace du tenseur de structure.
    """

    scale: int = 2
    levels: int = 5
    iterations: int = 3
    window: int = 5
    smoothing: int = 4
    regularization: float = 1e-3

    def key(self) -> tuple[object, ...]:
        """Identité stable des réglages (clé de cache, empreinte)."""
        return (self.scale, self.levels, self.iterations, self.window, self.smoothing, self.regularization)


_PROFILES: dict[FlowQuality, FlowParams] = {
    FlowQuality.DRAFT: FlowParams(scale=4, levels=4, iterations=2, window=4, smoothing=3),
    FlowQuality.BALANCED: FlowParams(scale=2, levels=5, iterations=3, window=5, smoothing=4),
    FlowQuality.BEST: FlowParams(scale=1, levels=6, iterations=4, window=6, smoothing=5),
}


def params_for(quality: FlowQuality) -> FlowParams:
    """Réglages d'une qualité. ``AUTO`` vaut ``BALANCED`` à l'export ; l'aperçu adaptatif choisit plus bas, seul, sur le moniteur."""
    return _PROFILES[FlowQuality.BALANCED if quality is FlowQuality.AUTO else quality]


class Fallback(str, Enum):
    """Ce qu'une image fabriquée est réellement."""

    NONE = "none"
    """Issue du flux optique."""
    IDENTICAL = "identical"
    """Les deux images sont les mêmes : reprise telle quelle, aucun calcul."""
    SCENE_CUT = "scene_cut"
    """Coupure entre les deux images : l'image la plus proche, jamais un mélange de deux plans."""
    LOW_CONFIDENCE = "low_confidence"
    """Flux peu fiable (occlusions, flash, bruit) : mélange simple des deux images."""


STORAGE_REDUCTION = 2
"""Les champs sont conservés à la moitié de la grille d'analyse (le mouvement est lisse : mesuré, l'erreur d'une image
interpolée ne change pas) et en demi-précision : quatre fois moins de place que la grille entière en flottants 16 bits."""


@dataclass(frozen=True)
class PairAnalysis:
    """Résultat de l'analyse d'une paire d'images consécutives : de quoi fabriquer n'importe quel point de ``]a, b[``.

    **La forme stockée est la définition.** Les champs ne sont gardés que réduits et en demi-précision (c'est ce que le
    cache écrit) ; l'analyse fraîche passe par la même réduction avant d'être utilisée. Une image fabriquée à partir d'un
    calcul à froid et à partir du cache est donc **identique au bit près** : le cache ne change jamais le rendu.

    ``forward`` / ``backward`` sont absents pour une coupure ou des images identiques (aucun flux n'a été estimé).
    """

    status: Fallback = Fallback.NONE
    forward: FlowField | None = None
    backward: FlowField | None = None

    _CODES: ClassVar[dict[Fallback, int]] = {Fallback.NONE: 0, Fallback.IDENTICAL: 1, Fallback.SCENE_CUT: 2}

    @classmethod
    def from_flows(cls, forward: FlowField, backward: FlowField) -> PairAnalysis:
        """Analyse issue de deux flux bruts : réduits à la forme stockée puis quantifiés (une seule fois, ici)."""
        return cls(Fallback.NONE, _stored_form(forward), _stored_form(backward))

    def fields(self, width: int, height: int) -> tuple[FlowField, FlowField]:
        """Les deux flux ramenés à la grille ``width × height`` des images à fabriquer (vecteurs mis à l'échelle)."""
        if self.forward is None or self.backward is None:
            raise ValueError("cette paire n'a pas de flux (coupure ou images identiques)")
        return self.forward.resized(width, height), self.backward.resized(width, height)

    def pack(self) -> dict[str, npt.NDArray[np.generic]]:
        """Forme stockée dans le cache : les six plans en demi-précision (ou un tableau vide) et le code d'état."""
        import numpy as np

        if self.forward is None or self.backward is None:
            flow = np.zeros((6, 1, 1), dtype=np.float16)
        else:
            flow = np.concatenate((self.forward.pack(), self.backward.pack()))
        return {"flow": flow, "status": np.array([self._CODES[self.status]], dtype=np.int8)}

    @classmethod
    def unpack(cls, stored: dict[str, npt.NDArray[np.generic]]) -> PairAnalysis:
        """Inverse de :meth:`pack` ; lève ``ValueError`` si la forme est incohérente (le cache jette alors l'entrée)."""
        import numpy as np

        from .flow_field import FlowField

        flow, status = stored["flow"], stored["status"]
        if flow.ndim != 3 or flow.shape[0] != 6 or flow.dtype != np.float16 or status.shape != (1,):
            raise ValueError("analyse de paire stockée : forme inattendue")
        codes = {code: state for state, code in cls._CODES.items()}
        state = codes.get(int(status[0]))
        if state is None:
            raise ValueError("analyse de paire stockée : état inconnu")
        if state is not Fallback.NONE:
            return cls(state)
        halves = flow.astype(np.float16, copy=False)                       # le type a été vérifié : ``copy=False`` ne copie rien
        return cls(Fallback.NONE, FlowField.unpack(halves[:3]), FlowField.unpack(halves[3:]))


def _stored_form(field: FlowField) -> FlowField:
    """``field`` réduit de moitié, vecteurs ramenés à cette grille, puis quantifié en demi-précision."""
    from .flow_field import FlowField

    reduced = field.resized(max(1, -(-field.width // STORAGE_REDUCTION)), max(1, -(-field.height // STORAGE_REDUCTION)))
    return FlowField.unpack(reduced.pack())


@dataclass(frozen=True)
class SynthesizedFrame:
    """Une image fabriquée, et ce qu'elle vaut.

    Attributes:
        pixels: ``(hauteur, largeur, canaux)``, flottants 0…1 (alpha prémultiplié s'il y en a un).
        confidence: 0…1, mesurée (accord des flux, erreur après déformation, pixels couverts) — pas une promesse.
        unreliable: part des pixels dont la confiance est tombée sous 0,5.
        fallback: ce que l'image est réellement.
    """

    pixels: Frame
    confidence: float
    unreliable: float
    fallback: Fallback


class FlowCancelled(Exception):
    """Le calcul a été interrompu à la demande de l'appelant (levée par tout backend quand ``cancel()`` répond vrai)."""


class BackendUnavailable(RuntimeError):
    """Le backend demandé n'existe pas ou ne peut pas tourner ici (message à montrer tel quel)."""


class BackendPreference(str, Enum):
    """Choix de l'utilisateur : le meilleur disponible, le processeur, ou un accélérateur."""

    AUTO = "auto"
    CPU = "cpu"
    GPU = "gpu"


class OpticalFlowBackend(Protocol):
    """Ce qu'un backend sait faire. Implémenté par :class:`NumpyBackend` ; un backend GPU l'implémentera à son tour."""

    name: str
    version: int
    device: str
    """``"cpu"`` ou ``"gpu"``."""

    def available(self) -> bool:
        """Peut-il tourner sur cette machine ?"""
        ...

    def analyze(self, a: Plane, b: Plane, params: FlowParams, cancel: CancelCheck | None = None) -> PairAnalysis:
        """Analyse deux plans de luminance (0…1, même taille, déjà à la grille d'analyse)."""
        ...

    def synthesize(self, a: Frame, b: Frame, pair: PairAnalysis, t: float) -> SynthesizedFrame:
        """L'image au point ``t`` de ``]a, b[`` (images à la résolution de travail)."""
        ...


class NumpyBackend:
    """Backend processeur : NumPy seul, déterministe, disponible partout (:mod:`core.flow_numpy`)."""

    name = "numpy"
    version = 2
    device = "cpu"

    def available(self) -> bool:
        return True

    def analyze(self, a: Plane, b: Plane, params: FlowParams, cancel: CancelCheck | None = None) -> PairAnalysis:
        from . import flow_numpy as engine

        if engine.changed_fraction(a, b, IDENTICAL_LEVEL) < IDENTICAL_FRACTION:
            return PairAnalysis(Fallback.IDENTICAL)
        if engine.scene_change(a, b) >= CUT_HISTOGRAM and engine.correlation(a, b) < CUT_CORRELATION:
            return PairAnalysis(Fallback.SCENE_CUT)
        forward, backward = engine.estimate_pair(a, b, params, cancel)
        return PairAnalysis.from_flows(forward, backward)

    def synthesize(self, a: Frame, b: Frame, pair: PairAnalysis, t: float) -> SynthesizedFrame:
        from . import flow_numpy as engine

        if pair.status is Fallback.IDENTICAL:
            return SynthesizedFrame(a, 1.0, 0.0, Fallback.IDENTICAL)
        if pair.status is Fallback.SCENE_CUT or pair.forward is None or pair.backward is None:
            return SynthesizedFrame(a if t < 0.5 else b, 0.0, 1.0, Fallback.SCENE_CUT)
        forward, backward = pair.fields(a.shape[1], a.shape[0])
        pixels, confidence, unreliable = engine.synthesize(a, b, forward, backward, t)
        if confidence < MIN_FLOW_SCORE:
            return SynthesizedFrame(engine.blend(a, b, t), confidence, unreliable, Fallback.LOW_CONFIDENCE)
        return SynthesizedFrame(pixels, confidence, unreliable, Fallback.NONE)


_BACKENDS: tuple[OpticalFlowBackend, ...] = (NumpyBackend(),)
"""Les backends connus, du préféré au repli. Un backend GPU s'insère avant le processeur."""


def backends() -> tuple[OpticalFlowBackend, ...]:
    return _BACKENDS


def select_backend(preference: BackendPreference = BackendPreference.AUTO) -> OpticalFlowBackend:
    """Le backend demandé ; ``AUTO`` prend le premier disponible. Refus explicite (jamais de repli silencieux) pour ``GPU``."""
    if preference is BackendPreference.AUTO:
        for backend in _BACKENDS:
            if backend.available():
                return backend
        raise BackendUnavailable("Aucun backend de flux optique n'est disponible.")
    wanted = "gpu" if preference is BackendPreference.GPU else "cpu"
    for backend in _BACKENDS:
        if backend.device == wanted and backend.available():
            return backend
    raise BackendUnavailable(
        "Aucun accélérateur GPU n'est disponible pour le flux optique : choisissez « Auto » ou « Processeur »."
        if wanted == "gpu" else "Le backend processeur du flux optique est indisponible."
    )


def luma_plane(frame: Frame) -> Plane:
    """Luminance (0…1) d'une image ``(h, w, c)`` (Rec. 709 sur les valeurs encodées ; un seul canal est repris tel quel)."""
    if frame.shape[2] < 3:
        return frame[:, :, 0]
    return (_LUMA[0] * frame[:, :, 0] + _LUMA[1] * frame[:, :, 1] + _LUMA[2] * frame[:, :, 2]).astype(frame.dtype)


def analysis_plane(frame: Frame, scale: int) -> Plane:
    """La luminance d'``frame`` réduite à la grille d'analyse (``scale`` = 1, 2 ou 4 : moyennes 2×2 successives)."""
    from . import flow_numpy as engine

    plane = luma_plane(frame)
    for _ in range(max(0, int(scale).bit_length() - 1)):
        plane = engine.halve(plane)
    return plane


def blend_frames(a: Frame, b: Frame, t: float) -> Frame:
    """``a·(1 − t) + b·t`` : le mélange image à image (mode « Mélange d'images »), sans estimation de mouvement."""
    return ((1.0 - t) * a + t * b).astype(a.dtype)


class FlowEstimator:
    """Analyse des paires pour un backend et des réglages."""

    def __init__(self, backend: OpticalFlowBackend, params: FlowParams) -> None:
        self.backend = backend
        self.params = params

    def analyze(self, a: Frame, b: Frame, cancel: CancelCheck | None = None) -> PairAnalysis:
        """Analyse deux images consécutives (résolution de travail) ; la réduction à la grille d'analyse est faite ici."""
        return self.backend.analyze(
            analysis_plane(a, self.params.scale), analysis_plane(b, self.params.scale), self.params, cancel
        )


class FrameInterpolator:
    """Fabrication des images d'une paire analysée pour un backend."""

    def __init__(self, backend: OpticalFlowBackend) -> None:
        self.backend = backend

    def interpolate(self, a: Frame, b: Frame, pair: PairAnalysis, t: float) -> SynthesizedFrame:
        """L'image au point ``t`` (0 < t < 1) de ``]a, b[`` ; elle dit ce qu'elle est (:class:`Fallback`)."""
        return self.backend.synthesize(a, b, pair, t)


class OpticalFlowEngine:
    """Un backend et une qualité : l'estimateur et l'interpolateur qui vont ensemble."""

    def __init__(self, quality: FlowQuality, preference: BackendPreference = BackendPreference.AUTO) -> None:
        self.quality = quality
        self.params = params_for(quality)
        self.backend = select_backend(preference)
        self.estimator = FlowEstimator(self.backend, self.params)
        self.interpolator = FrameInterpolator(self.backend)

    @property
    def identity(self) -> tuple[object, ...]:
        """Tout ce qui, avec les images, détermine le résultat : moteur, backend (nom, version), réglages d'analyse."""
        return (ENGINE_VERSION, self.backend.name, self.backend.version, *self.params.key(), *classification_key())


def classification_key() -> tuple[float, ...]:
    """Les seuils qui décident ce qu'une paire *est* (identique, coupure, confiance trop basse) : ils changent le verdict stocké dans
    le cache, donc l'identité de l'analyse. Retoucher l'un d'eux invalide les analyses faites avec l'ancienne valeur."""
    return (CUT_HISTOGRAM, CUT_CORRELATION, IDENTICAL_LEVEL, IDENTICAL_FRACTION, MIN_FLOW_SCORE)


def self_check() -> str | None:
    """Contrôle de l'application construite (smoke test) : le backend NumPy tourne et suit un décalage connu.

    Une texture lisse est décalée de ``(5, 2)`` px : le flux estimé doit le retrouver, et l'image intermédiaire au milieu doit être
    plus proche du décalage de moitié qu'un simple mélange. ``None`` si tout va bien, sinon la cause (NumPy ou une extension C
    manquante dans l'empaquetage, algorithme faussé).
    """
    try:
        import numpy as np

        from . import flow_numpy as engine
    except Exception as error:  # noqa: BLE001 - un module absent de l'empaquetage ne doit pas faire planter le contrôle
        return f"NumPy ou le backend de flux optique est indisponible ({error})"
    try:
        rng = np.random.default_rng(3)
        coarse = rng.random((12, 16)).astype(np.float32)
        texture = engine.box_mean(engine.box_mean(np.kron(coarse, np.ones((6, 6), np.float32)), 3), 3)       # 72 × 96, lisse
        first = np.repeat(texture[:, :, None], 3, axis=2)
        second = np.roll(first, (2, 5), axis=(0, 1))
        params = FlowParams(scale=1, levels=4, iterations=3, window=4, smoothing=3)
        forward, backward = engine.estimate_pair(first[:, :, 0], second[:, :, 0], params)
        interior = (slice(12, -12), slice(12, -12))
        shift_x, shift_y = float(forward.u[interior].mean()), float(forward.v[interior].mean())
        if abs(shift_x - 5.0) > 0.6 or abs(shift_y - 2.0) > 0.6:
            return f"flux optique faussé : décalage (5, 2) retrouvé comme ({shift_x:.2f}, {shift_y:.2f})"
        halfway = np.roll(first, (1, 2), axis=(0, 1))
        fields = (forward, backward)
        synthesized, _confidence, _unreliable = engine.synthesize(first, second, *fields, 0.5)
        if float(np.abs(synthesized - halfway)[interior].mean()) >= float(np.abs(engine.blend(first, second, 0.5) - halfway)[interior].mean()):
            return "l'image intermédiaire n'est pas plus juste qu'un mélange simple"
        PairAnalysis.unpack(PairAnalysis.from_flows(forward, backward).pack())
    except Exception as error:  # noqa: BLE001 - la cause est rapportée telle quelle
        return f"flux optique en échec ({type(error).__name__}: {error})"
    return None


__all__ = [
    "CUT_CORRELATION",
    "CUT_HISTOGRAM",
    "ENGINE_VERSION",
    "IDENTICAL_FRACTION",
    "IDENTICAL_LEVEL",
    "MIN_FLOW_SCORE",
    "STORAGE_REDUCTION",
    "BackendPreference",
    "BackendUnavailable",
    "Fallback",
    "FlowCancelled",
    "FlowEstimator",
    "FlowParams",
    "FrameInterpolator",
    "NumpyBackend",
    "OpticalFlowBackend",
    "OpticalFlowEngine",
    "PairAnalysis",
    "SynthesizedFrame",
    "analysis_plane",
    "backends",
    "blend_frames",
    "classification_key",
    "luma_plane",
    "params_for",
    "select_backend",
    "self_check",
]
