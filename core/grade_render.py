"""Étalonnage animé à l'export (ADR-0008, étape 6) : les filtres de :func:`core.export_engine._build_color_grade_filters` dont
un réglage a des images clés ``color.*``.

Sans image clé, ce module n'est jamais appelé : la chaîne est celle d'avant, caractère pour caractère. Le choix de chaque rendu est
**mesuré** (ADR-0008, « Résultats de l'étape 6 » ; ``tools/measure_grade_cost.py``) :

- **expression** du temps local du clip, comme les effets (étape 3) : contraste et saturation (``eq=…:eval=frame``), teinte
  (``hue=h=…``) ;
- **fichier de commandes** ``sendcmd`` (une instruction par image, une demi-image avant l'image visée, règles de
  :mod:`core.effect_sendcmd`) : ombres et hautes lumières (``colorbalance``), température (``colorchannelmixer``), et pour
  l'exposition et les roues une table de 256 niveaux envoyée à ``curves`` (:mod:`core.grade_tables`). ``lutrgb`` animé coûte ×150.

Un seul fichier de commandes par étalonnage, lu par le premier ``sendcmd`` posé devant le premier filtre qu'il pilote. Chaque
filtre piloté porte un nom (``curves@v0cge``) pour que la commande le vise dans un graphe complexe.

Les valeurs sont celles du modèle (:class:`core.grade_targets.GradeCurves`, bornées comme l'inspecteur) puis la même conversion que
la chaîne statique. Un étage dont un réglage est animé est **toujours émis**, y compris aux images où la valeur est neutre (là où
la chaîne statique l'omet) : ``curves`` et ``colorbalance`` neutres sont l'identité exacte ; ``eq`` et ``hue`` neutres ajoutent
au plus l'arrondi d'une conversion YUV (mesuré dans l'ADR).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from fractions import Fraction

from .animation_ffmpeg import curve_expression
from .color_grading import ColorGrade
from .color_wheels import channel_transfer
from .effect_sendcmd import SendcmdContext, frame_count, sendcmd_filter, write_command_stream
from .grade_tables import curves_points, exposure_table, wheel_tables
from .grade_targets import GradeCurves, scalar_property_id, wheel_property_id
from .color_grading import WHEELS

MAX_COMMAND_BYTES = 32 * 1024 * 1024
"""Taille maximale du texte de commandes d'un étalonnage (tous fichiers). Mesuré (ADR-0008, étape 6) : FFmpeg 6.1.1 et 7.1.5 gardent en
mémoire, après lecture, ~40 à 45 fois la taille des fichiers de commandes (9.0.2 : quelques fois). 32 Mo restent sous ~1,5 Go sur ces
versions ; une table de 256 points pèse ~4,7 Ko par image (exposition : ~7 000 images, quatre minutes à 30 i/s ; roues colorées : deux à
trois fois moins). Au-delà, :class:`AnimatedGrade` lève ``ValueError`` (message sans donnée de l'utilisateur) : le fichier partiel
est supprimé avec les autres temporaires de l'export."""

WINDOW_BYTES = 192 * 1024
"""Taille maximale du texte d'**un** fichier de commandes. Mesuré (ADR-0008, étape 6) : FFmpeg 6.1.1 et 7.1.5 lisent un fichier de
commandes avec une mémoire de l'ordre de 1000 fois sa taille et croissante plus vite que lui (un fichier de 3,7 Mo, 800 lignes de
256 points : 4,4 Go de mémoire résidente avant la première image ; la 9.0.2 : 19 Mo). La mémoire est **rendue** après la lecture de
chaque fichier : l'étalonnage écrit donc **une fenêtre par fichier** (autant de ``sendcmd`` à la suite) et le pic ne dépend plus de la
durée du clip, seulement de la fenêtre (800 lignes en 8 fichiers : 164 Mo)."""

EXPRESSION_TIME = "t"
"""Variable de temps de ``eq`` et de ``hue`` : le temps local du clip (``PTS`` repart de zéro avant les effets et l'étalonnage)."""

_HALF_FRAME = Fraction(1, 2)
_EXPOSURE = scalar_property_id("exposure")
_BALANCE = (scalar_property_id("shadows"), scalar_property_id("highlights"))
_TEMPERATURE = scalar_property_id("temperature")
_WHEEL_CHANNEL_IDS = tuple(wheel_property_id(wheel, channel) for wheel in WHEELS for channel in "rgb")
_WHEEL_IDS = tuple(wheel_property_id(wheel, channel) for wheel in WHEELS for channel in "rgby")


def _number(value: float) -> str:
    """Nombre pour une instruction : six décimales, zéros de queue retirés, jamais de notation scientifique."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-", "-0") else text


def _unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


def _name(base: str, tag: str, letter: str) -> str:
    return f"{base}@{re.sub(r'[^0-9A-Za-z_]', '_', tag) or 'cg'}{letter}"


class AnimatedGrade:
    """Les étages animés d'un :class:`~core.color_grading.ColorGrade` : chaque méthode rend le filtre de l'étage, ou ``None`` si
    rien n'y est animé (ou si le flux n'est pas connu : l'étage garde alors sa valeur de base et la chaîne statique le décide)."""

    def __init__(self, curves: GradeCurves, context: SendcmdContext | None, tag: str, fmt: Callable[[float], str]) -> None:
        self.curves = curves
        self.base = curves.base
        self.context = context
        self.tag = tag
        self.fmt = fmt
        self._load: str | None = None
        self.first = curves.at(context.start) if context is not None else curves.base
        # Roues : un seul canal ``master`` quand R, V et B restent égaux à tout instant (seule la molette ``y`` s'anime et les
        # palets de base sont égaux). Sinon trois canaux, trois fois plus de texte.
        self.uniform_wheels = not curves.animated(*_WHEEL_CHANNEL_IDS) and all(
            getattr(self.base, wheel).r == getattr(self.base, wheel).g == getattr(self.base, wheel).b for wheel in WHEELS
        )
        self.exposure_named = _name("curves", tag, "e")
        self.wheels_named = _name("curves", tag, "w")
        self.balance_named = _name("colorbalance", tag, "b")
        self.temperature_named = _name("colorchannelmixer", tag, "t")

    # -- Étages par expression -------------------------------------------------------------------------------------

    def _expression(self, property_id: str) -> str:
        spec = self.curves.specs[property_id]
        return curve_expression(self.curves.curves[property_id], time_var=EXPRESSION_TIME,
                                minimum=spec.minimum, maximum=spec.maximum)

    def eq(self) -> str | None:
        contrast, saturation = scalar_property_id("contrast"), scalar_property_id("saturation")
        if not self.curves.animated(contrast, saturation):
            return None
        if self.curves.animated(contrast):
            contrast_text = f"'1+{self._expression(contrast)}'"
        else:
            contrast_text = self.fmt(1.0 + float(self.base.contrast))
        if self.curves.animated(saturation):
            saturation_text = f"'{self._expression(saturation)}'"
        else:
            saturation_text = self.fmt(float(self.base.saturation))
        return f"eq=contrast={contrast_text}:saturation={saturation_text}:eval=frame"

    def hue(self) -> str | None:
        property_id = scalar_property_id("hue")
        if not self.curves.animated(property_id):
            return None
        return f"hue=h='{self._expression(property_id)}'"

    # -- Étages par sendcmd ----------------------------------------------------------------------------------------

    def _driven(self, *property_ids: str) -> bool:
        return self.context is not None and self.curves.animated(*property_ids)

    def _loaded(self, text: str) -> str:
        """``text`` précédé, la première fois, des ``sendcmd`` qui lisent les fichiers de commandes de l'étalonnage (un par fenêtre
        de :data:`WINDOW_BYTES`, voir cette constante)."""
        if self._load is None:
            windows = _windows(self._command_lines(self._frame_budget()), WINDOW_BYTES)
            self._load = ",".join(sendcmd_filter(write_command_stream(window)) for window in windows)
            return f"{self._load},{text}"
        return text

    def exposure(self) -> str | None:
        if not self._driven(_EXPOSURE):
            return None
        points = curves_points(exposure_table(self.first.exposure))
        return self._loaded(f"{self.exposure_named}=master='{points}'")

    def balance(self) -> str | None:
        if not self._driven(*_BALANCE):
            return None
        shadow, highlight = _unit(self.first.shadows), _unit(self.first.highlights)
        text = (f"{self.balance_named}=rs={self.fmt(shadow)}:gs={self.fmt(shadow)}:bs={self.fmt(shadow)}:"
                f"rh={self.fmt(highlight)}:gh={self.fmt(highlight)}:bh={self.fmt(highlight)}")
        return self._loaded(text)

    def temperature(self) -> str | None:
        if not self._driven(_TEMPERATURE):
            return None
        from .export_engine import temperature_gains

        red, green, blue = temperature_gains(float(self.first.temperature))
        return self._loaded(f"{self.temperature_named}=rr={self.fmt(red)}:gg={self.fmt(green)}:bb={self.fmt(blue)}")

    def wheels(self) -> str | None:
        if not self._driven(*_WHEEL_IDS):
            return None
        tables = wheel_tables(self.first)
        if self.uniform_wheels:
            options = f"master='{curves_points(tables[0])}'"
        else:
            options = ":".join(f"{channel}='{curves_points(table)}'" for channel, table in zip(("red", "green", "blue"), tables))
        return self._loaded(f"{self.wheels_named}={options}")

    # -- Fichier de commandes --------------------------------------------------------------------------------------

    def _frame_budget(self) -> int:
        """Images du flux (:func:`core.effect_sendcmd.frame_count` borne déjà le nombre de lignes) ; la taille du texte est bornée au fil de
        l'écriture par :data:`MAX_COMMAND_BYTES`."""
        if self.context is None:
            raise RuntimeError("Étalonnage animé sans contexte de flux.")
        return frame_count(self.context)

    def _command_lines(self, count: int) -> Iterator[str]:
        """Le fichier de commandes, ligne par ligne (écrit au fil de l'eau, jamais en entier en mémoire) : une ligne par image où une
        valeur change, ``instant cible option valeur[, …];``."""
        context = self.context
        if context is None:
            raise RuntimeError("Étalonnage animé sans contexte de flux.")
        fps = context.fps
        exposure_on = self._driven(_EXPOSURE)
        balance_on = self._driven(*_BALANCE)
        shadows_on, highlights_on = (self._driven(item) for item in _BALANCE)
        temperature_on = self._driven(_TEMPERATURE)
        wheels_on = self._driven(*_WHEEL_IDS)
        previous: dict[str, object] = {}
        size = 0
        from .export_engine import temperature_gains

        for index in range(count):
            grade = self.curves.at(context.start + float(Fraction(index) / fps))
            commands: list[str] = []
            if exposure_on and previous.get("exposure") != grade.exposure:
                previous["exposure"] = grade.exposure
                commands.append(f"{self.exposure_named} master '{curves_points(exposure_table(grade.exposure))}'")
            if balance_on:
                if shadows_on and previous.get("shadows") != (shadow := _number(_unit(grade.shadows))):
                    previous["shadows"] = shadow
                    commands.extend(f"{self.balance_named} {option} {shadow}" for option in ("rs", "gs", "bs"))
                if highlights_on and previous.get("highlights") != (highlight := _number(_unit(grade.highlights))):
                    previous["highlights"] = highlight
                    commands.extend(f"{self.balance_named} {option} {highlight}" for option in ("rh", "gh", "bh"))
            if temperature_on:
                gains = tuple(_number(gain) for gain in temperature_gains(float(grade.temperature)))
                if previous.get("temperature") != gains:
                    previous["temperature"] = gains
                    commands.extend(f"{self.temperature_named} {option} {gain}" for option, gain in zip(("rr", "gg", "bb"), gains))
            if wheels_on:
                transfers = tuple(channel_transfer(grade, channel) for channel in range(3))
                if previous.get("wheels") != transfers:
                    previous["wheels"] = transfers
                    commands.extend(self._wheel_commands(grade, previous))
            if not commands:
                continue
            instant = max(0.0, context.start + float((Fraction(index) - _HALF_FRAME) / fps))
            line = f"{instant:.9f} " + ", ".join(commands) + ";"
            size += len(line) + 1
            if size > MAX_COMMAND_BYTES:
                raise ValueError("Animation d'étalonnage trop longue pour l'export (trop de données).")
            yield line

    def _wheel_commands(self, grade: ColorGrade, previous: dict[str, object]) -> list[str]:
        tables = wheel_tables(grade)
        if self.uniform_wheels:
            if previous.get("table") == tables[0]:
                return []
            previous["table"] = tables[0]
            return [f"{self.wheels_named} master '{curves_points(tables[0])}'"]
        commands = []
        for channel, table in zip(("red", "green", "blue"), tables):
            if previous.get(channel) != table:
                previous[channel] = table
                commands.append(f"{self.wheels_named} {channel} '{curves_points(table)}'")
        return commands


def _windows(lines: Iterable[str], budget: int) -> Iterator[list[str]]:
    """``lines`` découpées en fenêtres d'au plus ``budget`` octets (une ligne démesurée a sa propre fenêtre) ; jamais de fenêtre vide
    (un plan sans aucune ligne n'écrit rien). Une seule fenêtre est en mémoire à la fois."""
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > budget:
            yield current
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        yield current


def animated_grade(
    grade: object, animation, context: SendcmdContext | None, tag: str, fmt: Callable[[float], str],
) -> AnimatedGrade | None:
    """Les étages animés de ``grade`` d'après les images clés ``color.*`` de ``animation`` ; ``None`` quand rien n'est animé
    (sans image clé, graphe de nœuds, ou aucune propriété d'étalonnage rendue) : la chaîne statique est alors celle d'avant."""
    from .grade_targets import is_animatable_grade

    if not animation or not is_animatable_grade(grade):
        return None
    grade = ColorGrade() if grade is None else grade           # jamais étalonné : les images clés partent du réglage neutre
    curves = GradeCurves(grade, animation)
    return AnimatedGrade(curves, context, tag, fmt) if curves else None


__all__ = ["EXPRESSION_TIME", "MAX_COMMAND_BYTES", "AnimatedGrade", "animated_grade"]
