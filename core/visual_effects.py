"""Transformations visuelles et images-clés pour Kut-Studio.

Ce module encapsule toute la logique métier pure liée à la
composition visuelle :

- :class:`ClipTransform` — position, échelle, rotation, opacité ;
- :class:`TransformKeyframe` — image-clé d'une propriété ;
- :class:`EvaluatedTransform` — résultat figé de l'évaluation ;
- :func:`evaluate_transform` — interpolation linéaire d'un
  :class:`ClipTransform` le long d'une liste d'images-clés ;
- :func:`build_ffmpeg_expression` — génération d'une expression
  compatible avec la syntaxe du filtergraph du moteur de rendu.

Le module est volontairement pur : aucune dépendance à une
bibliothèque graphique ou à un binaire externe. Il peut être
testé en CLI et utilisé par tout consommateur (moteur d'export,
outils d'aperçu, scripts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


ANIMATABLE_PROPERTIES: tuple[str, ...] = (
    "position_x",
    "position_y",
    "scale",
    "rotation",
    "opacity",
)
"""Liste exhaustive des propriétés animables d'un clip."""

_SCALE_MIN = 0.05
_SCALE_MAX = 10.0
_ROTATION_MIN = -3600.0
_ROTATION_MAX = 3600.0
_OPACITY_MIN = 0.0
_OPACITY_MAX = 1.0
_POSITION_MIN = -4.0
_POSITION_MAX = 4.0


_PROPERTY_BOUNDS: dict[str, tuple[float, float]] = {
    "position_x": (_POSITION_MIN, _POSITION_MAX),
    "position_y": (_POSITION_MIN, _POSITION_MAX),
    "scale": (_SCALE_MIN, _SCALE_MAX),
    "rotation": (_ROTATION_MIN, _ROTATION_MAX),
    "opacity": (_OPACITY_MIN, _OPACITY_MAX),
}
"""Bornes acceptées pour chaque propriété animable."""


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipTransform:
    """Transform 2D d'un clip vidéo, sans dépendance à la durée du clip.

    Les valeurs sont stockées dans un repère normalisé par rapport au
    canvas : ``position_x=1.0`` correspond à un décalage d'une largeur
    de canvas vers la droite, ``scale=1.0`` à la taille naturelle, etc.

    Attributes:
        position_x: Décalage horizontal normalisé (défaut ``0.0`` = centre).
        position_y: Décalage vertical normalisé (défaut ``0.0`` = centre).
        scale: Échelle du clip (défaut ``1.0``).
        rotation: Rotation en degrés (défaut ``0.0``).
        opacity: Opacité (défaut ``1.0``).
    """

    position_x: float = 0.0
    position_y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0

    def __post_init__(self) -> None:
        """Valide les bornes des champs du transform."""
        _validate_value("position_x", self.position_x)
        _validate_value("position_y", self.position_y)
        _validate_value("scale", self.scale)
        _validate_value("rotation", self.rotation)
        _validate_value("opacity", self.opacity)

    def with_property(self, name: str, value: float) -> "ClipTransform":
        """Retourne un nouveau transform avec la propriété ``name`` mise à jour.

        L'instance courante étant immuable, un nouvel objet est créé.
        """
        if name not in ANIMATABLE_PROPERTIES:
            raise ValueError(f"Propriété inconnue : {name!r}.")
        current = getattr(self, name)
        new_value = _coerce_value(name, value)
        return ClipTransform(
            position_x=self.position_x,
            position_y=self.position_y,
            scale=self.scale,
            rotation=self.rotation,
            opacity=self.opacity,
            **{name: new_value},
        )


# ---------------------------------------------------------------------------
# Images-clés
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransformKeyframe:
    """Image-clé d'une propriété d'un :class:`ClipTransform`.

    Le temps ``time_seconds`` est local au clip : ``0.0`` correspond
    au début du clip, ``clip.duration`` à sa fin. Une image-clé ne
    peut pas dépasser la durée du clip ; une seule image-clé est
    autorisée par couple ``(property_name, time_seconds)``.
    """

    property_name: str
    time_seconds: float
    value: float

    def __post_init__(self) -> None:
        """Valide l'image-clé."""
        if self.property_name not in ANIMATABLE_PROPERTIES:
            raise ValueError(
                f"Propriété de keyframe inconnue : {self.property_name!r}."
            )
        if self.time_seconds < 0.0:
            raise ValueError(
                f"time_seconds doit être positif ou nul "
                f"(reçu : {self.time_seconds})."
            )
        _validate_value(self.property_name, self.value)


# ---------------------------------------------------------------------------
# Évaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluatedTransform:
    """Résultat immuable de l'évaluation d'un transform à un instant donné."""

    position_x: float
    position_y: float
    scale: float
    rotation: float
    opacity: float

    def as_transform(self) -> ClipTransform:
        """Retourne un nouveau :class:`ClipTransform` équivalent."""
        return ClipTransform(
            position_x=self.position_x,
            position_y=self.position_y,
            scale=self.scale,
            rotation=self.rotation,
            opacity=self.opacity,
        )


def evaluate_transform(
    transform: ClipTransform,
    keyframes: Iterable[TransformKeyframe],
    clip_local_time: float,
    clip_duration: float | None = None,
) -> EvaluatedTransform:
    """Évalue ``transform`` à ``clip_local_time`` en tenant compte des keyframes.

    Règles d'interpolation :

    - avant la première keyframe : valeur de base du transform ;
    - après la dernière keyframe : dernière valeur des keyframes ;
    - entre deux keyframes : interpolation linéaire.

    ``clip_local_time`` est clampé dans ``[0, clip_duration]`` si
    ``clip_duration`` est fourni. Ni ``transform`` ni ``keyframes``
    ne sont mutés.
    """
    if clip_duration is not None and clip_duration > 0.0:
        if clip_local_time < 0.0:
            clip_local_time = 0.0
        elif clip_local_time > clip_duration:
            clip_local_time = float(clip_duration)

    # Indexation des keyframes par propriété, triées par temps croissant.
    by_property: dict[str, list[TransformKeyframe]] = {
        name: [] for name in ANIMATABLE_PROPERTIES
    }
    for kf in keyframes:
        if kf.property_name not in by_property:
            # La validation est faite dans le dataclass, mais on double-
            # check ici pour rester robuste aux itérations arbitraires.
            continue
        if clip_duration is not None and kf.time_seconds > clip_duration + 1e-6:
            continue  # Keyframe située après la fin du clip.
        by_property[kf.property_name].append(kf)
    for name in by_property:
        by_property[name].sort(key=lambda k: k.time_seconds)
        # Suppression des doublons (ne garde que la dernière valeur
        # pour un temps donné).
        deduped: list[TransformKeyframe] = []
        for kf in by_property[name]:
            if deduped and abs(deduped[-1].time_seconds - kf.time_seconds) < 1e-9:
                deduped[-1] = kf
            else:
                deduped.append(kf)
        by_property[name] = deduped

    def _resolve(name: str) -> float:
        base = getattr(transform, name)
        kfs = by_property.get(name, [])
        if not kfs:
            return base
        # Avant la première keyframe : valeur de base du transform.
        if clip_local_time < kfs[0].time_seconds:
            return base
        # Après la dernière keyframe : sa valeur.
        if clip_local_time >= kfs[-1].time_seconds:
            return kfs[-1].value
        for prev, nxt in zip(kfs, kfs[1:]):
            if prev.time_seconds <= clip_local_time <= nxt.time_seconds:
                span = nxt.time_seconds - prev.time_seconds
                if span <= 1e-9:
                    return nxt.value
                alpha = (clip_local_time - prev.time_seconds) / span
                return prev.value + (nxt.value - prev.value) * alpha
        return kfs[-1].value

    return EvaluatedTransform(
        position_x=_resolve("position_x"),
        position_y=_resolve("position_y"),
        scale=_resolve("scale"),
        rotation=_resolve("rotation"),
        opacity=_resolve("opacity"),
    )


# ---------------------------------------------------------------------------
# Expression FFmpeg
# ---------------------------------------------------------------------------


def build_ffmpeg_expression(
    property_name: str,
    base_value: float,
    keyframes: Iterable[TransformKeyframe],
    *,
    time_var: str = "T",
) -> str:
    """Génère une expression FFmpeg interpolant ``base_value`` sur ``keyframes``.

    L'expression retournée utilise ``time_var`` comme variable de
    temps local du clip (en secondes, par convention). Le résultat
    est syntaxiquement compatible avec les filtres ``geq``
    (notamment pour l'opacité animée), ``fade`` et tout filtre qui
    interprète une expression arithmétique FFmpeg complète.

    Conventions :
    - avant la première keyframe : ``base_value`` ;
    - après la dernière keyframe : dernière valeur ;
    - entre deux keyframes : interpolation linéaire ;
    - si une seule keyframe : retour à la valeur de base avant
      l'image-clé, puis valeur de l'image-clé ensuite ;
    - notation décimale simple, jamais scientifique.

    Structure produite (n keyframes, base = b) :

        if(lt(T,t_1), b,
            if(lt(T,t_2), linear(v_1, v_2, t_1, t_2),
                if(lt(T,t_3), linear(v_2, v_3, t_2, t_3),
                    ...
                    if(lt(T,t_n), linear(v_{n-1}, v_n, t_{n-1}, t_n), v_n))))

    Note d'intégration : ``,`` est le séparateur d'options de la
    couche ``-filter_complex`` de FFmpeg. Le consommateur doit donc
    échapper les virgules de l'expression en ``\\,`` (par
    :func:`escape_filter_complex_commas`) avant injection dans un
    filtre FFmpeg.
    """
    _validate_value(property_name, base_value)
    sorted_kfs: list[TransformKeyframe] = []
    for kf in keyframes:
        if kf.property_name != property_name:
            continue
        sorted_kfs.append(kf)
    sorted_kfs.sort(key=lambda k: k.time_seconds)

    if not sorted_kfs:
        return _format_number(base_value)

    def _v(value: float) -> str:
        return _format_number(float(value))

    if len(sorted_kfs) == 1:
        single = sorted_kfs[0]
        before = _v(base_value)
        after = _v(single.value)
        t0 = _v(single.time_seconds)
        return f"if(lt({time_var},{t0}),{before},{after})"

    def _linear(
        prev_value: float,
        t_start: float,
        nxt_value: float,
        t_end: float,
    ) -> str:
        """``v_prev + ((T - t_start)/(t_end - t_start)) * (v_nxt - v_prev)``."""
        span = t_end - t_start
        if span <= 1e-9:
            return _v(nxt_value)
        alpha = f"(({time_var})-{_v(t_start)})/{_v(span)}"
        return f"{_v(prev_value)}+{alpha}*({_v(nxt_value)}-{_v(prev_value)})"

    # Construction : chaîne de ``if(lt(...))`` imbriqués.
    #
    # Schéma cible (n keyframes, base = b) :
    #   if(lt(T,t_1), b,
    #     if(lt(T,t_2), linear(v_1, v_2, t_1, t_2),
    #       ...
    #         if(lt(T,t_n), linear(v_{n-1}, v_n, t_{n-1}, t_n), v_n)))
    #
    # Une imbrication ``if`` par keyframe (n imbrications au total).
    pieces: list[str] = []
    opens = 0
    last_index = len(sorted_kfs) - 1

    for index in range(len(sorted_kfs)):
        kf = sorted_kfs[index]
        if index == 0:
            # Première keyframe : la branche « vrai » du premier ``if``
            # est la valeur de base.
            pieces.append(
                f"if(lt({time_var},{_v(kf.time_seconds)}),"
                f"{_v(base_value)},"
            )
            opens += 1
            continue
        prev = sorted_kfs[index - 1]
        linear_expr = _linear(
            prev.value, prev.time_seconds, kf.value, kf.time_seconds
        )
        # Chaque keyframe i ≥ 1 ouvre (en plus) un ``if(lt(T, t_i),
        # linear_{i-1, i}, ...)``. Le ``if`` ouvert a :
        # - branche « vrai » : interp linéaire entre la kf précédente et la courante ;
        # - branche « faux » : soit la valeur ``v_n`` (dernière kf),
        #   soit un nouveau ``if`` récursif (kf intermédiaire).
        if index == last_index:
            # Dernière keyframe : on émet un ``if`` complet et
            # auto-fermé, qui n'augmente pas le compteur ``opens``.
            pieces.append(
                f"if(lt({time_var},{_v(kf.time_seconds)}),"
                f"{linear_expr},{_v(kf.value)})"
            )
        else:
            # Kf intermédiaire : on ouvre un ``if`` qui sera fermé
            # par le ``if`` de la dernière kf ou par les fermetures
            # globales à la fin.
            pieces.append(
                f"{linear_expr},if(lt({time_var},{_v(kf.time_seconds)}),"
                f"{_v(kf.value)},"
            )
            opens += 1
    # Ferme toutes les ``if(lt(`` ouvertes.
    pieces.append(")" * opens)
    return "".join(pieces)


def escape_filter_complex_commas(expression: str) -> str:
    """Échappe les virgules d'une expression pour ``-filter_complex``.

    Au sein de ``-filter_complex``, ``,`` sépare les options d'un
    filtre. Notre expression interpolée contient
    ``if(lt(T,t),A,B)`` qui inclut des virgules « utiles ». On les
    échappe toutes en ``\\,`` pour que FFmpeg les interprète
    littéralement comme une seule valeur d'option.

    Les virgules **déjà** échappées (``\\,``) sont préservées telles
    quelles pour ne pas doubler l'échappement.
    """
    escaped_chars: list[str] = []
    index = 0
    text = expression
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == ",":
            # Échappement déjà présent : on le préserve tel quel.
            escaped_chars.append(char)
            escaped_chars.append(",")
            index += 2
            continue
        if char == ",":
            escaped_chars.append("\\")
            escaped_chars.append(",")
            index += 1
            continue
        escaped_chars.append(char)
        index += 1
    return "".join(escaped_chars)


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _validate_value(property_name: str, value: float) -> None:
    """Lève ``ValueError`` si ``value`` est hors bornes pour la propriété."""
    if property_name not in _PROPERTY_BOUNDS:
        raise ValueError(f"Propriété inconnue : {property_name!r}.")
    low, high = _PROPERTY_BOUNDS[property_name]
    if value != value:  # NaN
        raise ValueError(f"Valeur NaN refusée pour {property_name!r}.")
    if value < low or value > high:
        raise ValueError(
            f"Valeur hors bornes pour {property_name!r} : "
            f"{value} (attendu [{low}, {high}])."
        )


def _coerce_value(property_name: str, value: float) -> float:
    """Valide puis retourne ``value``."""
    _validate_value(property_name, value)
    return float(value)


def _format_number(value: float) -> str:
    """Formate un flottant pour le filtergraph FFmpeg (pas de scientifique)."""
    if value == 0:
        return "0"
    text = f"{value:.6f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.0"
