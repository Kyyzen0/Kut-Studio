#!/usr/bin/env python3
"""Banc de validation de ``sendcmd`` (ADR-0008, étape 1) : outil hors ligne, jamais importé par l'application.

Question : pour un filtre FFmpeg dont l'option n'accepte pas une expression du temps (``lutrgb``, ``colorbalance``,
``colorchannelmixer``, ``curves``, ``gblur``, ``unsharp``, ``rgbashift``), est-ce qu'un fichier ``sendcmd`` qui change
l'option à chaque image donne, à l'image ``n``, **la même sortie** que le filtre statique réglé sur la valeur ``v(n)`` ?

Méthode, par cas (filtre x paramètre) et par scénario (décalage, cadence...) :

1. une entrée synthétique déterministe (mire ``testsrc2`` + bruit à graine fixe, 320x180, 3 s), encodée sans perte
   (FFV1, ``yuv420p``) une seule fois puis réutilisée par toutes les versions de FFmpeg ;
2. une sortie ANIMÉE : ``sendcmd`` suivi du filtre nommé ``filtre@t``, une commande par image, valeur en rampe linéaire ;
3. pour des images ``n`` échantillonnées, une sortie STATIQUE : même entrée, même amont, ``select`` de l'image ``n``, filtre
   écrit avec la valeur ``v(n)`` ;
4. comparaison octet à octet (max, moyenne, part des octets qui diffèrent de plus d'un niveau). Validé si max <= 1.

Les chaînes de filtres reproduisent celles de l'application (``core/export_engine.py``, ``core/color_wheels.py``,
``core/color_render.py``) : ordre, formats ``yuv420p`` / ``rgba``, conversion co-sitée devant les filtres RVB. Elles sont
RECOPIÉES ici (l'outil n'importe rien de l'application) : à resynchroniser à la main si l'application change.

Usage (stdlib + numpy)::

    python tools/validate_sendcmd.py run --work DIR --out resultat.json [--ffmpeg ffmpeg] [--cpuflags0]
    python tools/validate_sendcmd.py report a.json b.json c.json

Dans Docker : monter le dépôt en lecture seule et un dossier de travail en écriture (voir ``docs/ffmpeg-sendcmd.md``).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

WIDTH, HEIGHT = 320, 180
TOLERANCE = 1                                   # critère de validation : écart maximal, en niveaux
PIX_BYTES = {"rgba": 4.0, "yuv420p": 1.5, "yuva444p": 4.0, "gbrp": 3.0}

# Conversion 4:2:0 -> RVB de l'application (core/chroma_siting.py), recopiée.
SITING_SCALE = "scale=flags=bilinear+full_chroma_int+accurate_rnd:in_h_chr_pos=0:in_v_chr_pos=0"
SITING_444 = SITING_SCALE + ",format=yuva444p16le"
SITING_RGBA = SITING_444 + ",format=rgba"


# ----------------------------------------------------------------------------------------------------------------------
# Fonctions pures (testées sans FFmpeg)
# ----------------------------------------------------------------------------------------------------------------------

def format_number(value: float) -> str:
    """Nombre pour un argument FFmpeg : six décimales, zéros de queue retirés, jamais de notation scientifique."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") and text != "-0" else "0"


def ramp_value(start: float, end: float, index: int, count: int) -> float:
    """Valeur de la rampe linéaire à l'image ``index`` : ``start`` à 0, ``end`` à ``count - 1`` (bornée aux deux bouts)."""
    if count <= 1:
        return start
    clamped = min(max(index, 0), count - 1)
    return start + (end - start) * clamped / (count - 1)


def frame_time(index: int, rate_num: int, rate_den: int, timing: str = "early", shift: float = 0.0) -> float:
    """Instant d'une commande pour l'image ``index`` d'un flux à ``rate_num/rate_den`` images par seconde.

    ``exact`` : l'instant de l'image elle-même (neuf décimales) ; ``exact_us`` : le même, tronqué à la microseconde (l'unité
    interne de ``sendcmd``). ``early`` : une demi-image avant (la commande part sur la première image
    dont l'instant est >= à celui de la commande, donc sur la bonne image, sans dépendre de l'arrondi du texte).
    ``shift`` : décalage ajouté (le flux du filtre ne commence pas à zéro).
    """
    if timing not in ("exact", "exact_us", "early"):
        raise ValueError("timing : exact, exact_us ou early")
    base = index * rate_den / rate_num
    if timing == "early" and index > 0:
        base = (index - 0.5) * rate_den / rate_num
    return base + shift


def sendcmd_text(commands_per_frame: list[list[tuple[str, str, str]]], rate_num: int, rate_den: int,
                 timing: str = "early", shift: float = 0.0) -> str:
    """Fichier de commandes ``sendcmd`` : une ligne par image, ``<instant> <cible> <option> <valeur>, ...;``.

    ``commands_per_frame[n]`` : les triplets ``(cible, option, valeur)`` de l'image ``n``. La valeur garde ses apostrophes
    (``'clip(a,b)'``) : l'analyseur de ``sendcmd`` les honore comme celui des graphes.
    """
    lines = []
    for index, triples in enumerate(commands_per_frame):
        if not triples:
            continue
        instant = frame_time(index, rate_num, rate_den, timing, shift)
        stamp = f"{math.floor(instant * 1e6 + 1e-6) / 1e6:.6f}" if timing == "exact_us" else f"{instant:.9f}"
        body = ", ".join(f"{target} {option} {value}" for target, option, value in triples)
        lines.append(f"{stamp} {body};")
    return "\n".join(lines) + "\n"


def compare_arrays(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
    """Écarts octet à octet entre deux sorties : ``max``, ``mean``, ``pct_gt1`` (en %) ; tailles différentes : ``max`` = 255."""
    if left.shape != right.shape:
        return {"max": 255.0, "mean": 255.0, "pct_gt1": 100.0}
    diff = np.abs(left.astype(np.int16) - right.astype(np.int16))
    if diff.size == 0:
        return {"max": 0.0, "mean": 0.0, "pct_gt1": 0.0}
    return {"max": float(diff.max()), "mean": float(diff.mean()), "pct_gt1": float((diff > TOLERANCE).mean() * 100.0)}


def is_valid(stats: list[dict[str, float]], tolerance: int = TOLERANCE) -> bool:
    """Vrai si toutes les images comparées sont à ``tolerance`` niveau près (et qu'il y en a au moins une)."""
    return bool(stats) and all(entry["max"] <= tolerance for entry in stats)


def sample_indices(count: int, short: bool = False) -> list[int]:
    """Images échantillonnées : 0, 1, 2, 15, 30, 59, 89 et la dernière (ou 0, 1, milieu, dernière si ``short``)."""
    wanted = [0, 1, count // 2, count - 1] if short else [0, 1, 2, 15, 30, 59, 89, count - 1]
    return sorted({n for n in wanted if 0 <= n < count})


def parse_version(banner: str) -> str:
    """``9.0.2`` depuis la première ligne de ``ffmpeg -version`` (``ffmpeg version 6.1.1-3ubuntu5 ...``)."""
    match = re.search(r"ffmpeg version\s+(\S+)", banner)
    return match.group(1) if match else "inconnue"


# ----------------------------------------------------------------------------------------------------------------------
# Cas : filtre x paramètre. Chaînes recopiées de l'application.
# ----------------------------------------------------------------------------------------------------------------------

Triples = list[tuple[str, str, str]]


@dataclass(frozen=True)
class Case:
    """Un paramètre animable. ``body(v, named)`` : le filtre (ou sous-graphe) réglé sur ``v``, ses instances nommées
    ``@t`` / ``@g`` si ``named`` ; ``commands(v)`` : les commandes ``sendcmd`` qui l'amènent à ``v``."""

    key: str
    start: float
    end: float
    pre: str                                     # chaîne devant le filtre (conversion co-sitée pour les filtres RVB)
    out_fmt: str
    body: Callable[[float, bool], str]
    commands: Callable[[float], Triples]
    expr_body: Callable[[float, float, int, str, float], str] | None = None     # variante « expression du temps » (eval=frame)
    integer: bool = False
    core: bool = False                           # fait partie du sous-ensemble joué sur les scénarios de bord


def _name(fname: str, named: bool, tag: str = "t") -> str:
    return f"{fname}@{tag}" if named else fname


def _simple(fname: str, options: Callable[[float], list[tuple[str, str]]], command_options: tuple[str, ...] | None = None,
            ) -> tuple[Callable[[float, bool], str], Callable[[float], Triples]]:
    """Filtre ``fname=opt=val:...`` et ses commandes (par défaut toutes les options variables)."""
    def body(value: float, named: bool) -> str:
        return _name(fname, named) + "=" + ":".join(f"{o}={x}" for o, x in options(value))

    def commands(value: float) -> Triples:
        return [(_name(fname, True), o, x) for o, x in options(value) if command_options is None or o in command_options]

    return body, commands


def exposure_expr(stops: float) -> str:
    """Expression ``lutrgb`` de l'exposition (``core/color_wheels.py:exposure_filter``)."""
    gain = f"{2.0 ** stops:.6f}"
    v = "(val/maxval)"
    linear = f"if(lte({v},0.04045),{v}/12.92,pow(({v}+0.055)/1.055,2.4))"
    scaled = f"({gain}*{linear})"
    encoded = f"if(lte({scaled},0.0031308),12.92*{scaled},1.055*pow({scaled},{1.0 / 2.4:.6f})-0.055)"
    return f"'clip({encoded}*maxval+0.5,0,maxval)'"


def wheel_expr(slope: float, base: float, power: float) -> str:
    """Expression ``lutrgb`` d'un canal de roue (``core/color_wheels.py:wheels_filter``)."""
    return f"'clip(pow(max(0,{slope:.6f}*val/maxval{base:+.6f}),{power:.6f})*maxval+0.5,0,maxval)'"


def temperature_gains(temperature: float) -> tuple[float, float, float]:
    """Gains R, V, B de la température (``core/export_engine.py:temperature_gains``)."""
    amount = 0.3 * max(-1.0, min(1.0, temperature / 100.0))
    return 1.0 + amount, 1.0 - amount * (0.2126 - 0.0722) / 0.7152, 1.0 - amount


def _lutrgb_case(key: str, start: float, end: float, expr: Callable[[float], str], core: bool = False) -> Case:
    body, commands = _simple("lutrgb", lambda v: [("r", expr(v)), ("g", expr(v)), ("b", expr(v))])
    return Case(key, start, end, SITING_RGBA + ",", "rgba", body, commands, core=core)


def _eq_case(key: str, start: float, end: float, option: str, others: dict[str, str], core: bool = False) -> Case:
    def options(v: float) -> list[tuple[str, str]]:
        values = {"brightness": "0", "contrast": "1", "saturation": "1", **others, option: format_number(v)}
        return [(k, values[k]) for k in ("brightness", "contrast", "saturation")]

    body, commands = _simple("eq", options)

    def expr_body(a: float, b: float, count: int, var: str, rate: float) -> str:
        parts = ":".join(f"{k}={x}" for k, x in options(0.0) if k != option)
        return f"eq@t={option}='{_ramp_expr(a, b, count, var, rate)}':{parts}:eval=frame"

    return Case(key, start, end, "", "yuv420p", body, commands, expr_body, core=core)


def _ramp_expr(a: float, b: float, count: int, var: str, rate: float) -> str:
    """Rampe en expression FFmpeg : ``n`` (index d'image) ou ``t`` (instant), ``N`` / ``T`` pour ``geq``."""
    if var in ("n", "N"):
        return f"{format_number(a)}+({format_number(b - a)})*{var}/{count - 1}"
    return f"{format_number(a)}+({format_number(b - a)})*{var}*{format_number(rate)}/{count - 1}"


def _colorbalance_case(key: str, start: float, end: float, prefix: str, core: bool = False) -> Case:
    def options(v: float) -> list[tuple[str, str]]:
        return [(f"{c}{prefix}", format_number(v)) for c in "rgb"]

    body, commands = _simple("colorbalance", options)
    return Case(key, start, end, SITING_RGBA + ",", "rgba", body, commands, core=core)


def _hue_case(key: str, option: str, start: float, end: float, core: bool = False) -> Case:
    body, commands = _simple("hue", lambda v: [(option, format_number(v))])

    def expr_body(a: float, b: float, count: int, var: str, rate: float) -> str:
        return f"hue@t={option}='{_ramp_expr(a, b, count, var, rate)}'"

    return Case(key, start, end, "", "yuv420p", body, commands, expr_body, core=core)


def _temperature_options(temperature: float) -> list[tuple[str, str]]:
    red, green, blue = temperature_gains(temperature)
    return [("rr", format_number(red)), ("gg", format_number(green)), ("bb", format_number(blue))]


def _curves_options(level: float) -> list[tuple[str, str]]:
    return [("master", f"'0/0 0.5/{format_number(level)} 1/1'")]


def _vignette_case() -> Case:
    body, commands = _simple("vignette", lambda v: [("angle", format_number(v))])

    def expr_body(a: float, b: float, count: int, var: str, rate: float) -> str:
        return f"vignette@t=angle='{_ramp_expr(a, b, count, var, rate)}':eval=frame"

    return Case("vignette.angle", 0.0, 0.7853981633974483, "", "yuv420p", body, commands, expr_body, core=True)


def _geq_case() -> Case:
    """``geq`` (brume de chaleur) : l'expression de l'application (``p(X-décalage,Y)``), ici le gain d'une rampe."""
    pre = SITING_SCALE + ",format=yuva444p,"

    def body(value: float, named: bool) -> str:
        e = f"'p(X,Y)*{format_number(value)}'"
        return f"{_name('geq', named)}=lum={e}:cb='p(X,Y)':cr='p(X,Y)':a='p(X,Y)'"

    def commands(value: float) -> Triples:
        return [("geq@t", "lum", f"'p(X,Y)*{format_number(value)}'")]

    def expr_body(a: float, b: float, count: int, var: str, rate: float) -> str:
        e = f"'p(X,Y)*({_ramp_expr(a, b, count, var, rate)})'"
        return f"geq@t=lum={e}:cb='p(X,Y)':cr='p(X,Y)':a='p(X,Y)'"

    return Case("geq.gain", 0.5, 1.0, pre, "yuva444p", body, commands, expr_body)


def _glow_cases() -> list[Case]:
    """Bloom (``_glow_filter``) : sous-graphe ``split`` + ``lutrgb`` + ``gblur`` + ``blend``, instances nommées dans un graphe
    complexe. Seuil 0,6 ; gain = intensité / (1 - seuil) ; sigma = rayon."""
    def graph(gain: float, sigma: float, named: bool) -> str:
        curve = f"'clip((val-{format_number(0.6 * 255.0)})*{format_number(gain)},0,255)'"
        return (f"format=rgba,split[ga][gb];[gb]{_name('lutrgb', named, 'gl')}=r={curve}:g={curve}:b={curve},"
                f"{_name('gblur', named, 'gb')}=sigma={format_number(sigma)}[gg];"
                "[ga][gg]blend=all_mode=addition:c3_mode=normal:c3_opacity=0,format=rgba,format=yuva444p")

    def curve(gain: float) -> str:
        return f"'clip((val-{format_number(0.6 * 255.0)})*{format_number(gain)},0,255)'"

    gain_case = Case(
        "glow.intensity(lutrgb@gl)", 0.5, 2.5, SITING_444 + ",", "yuva444p",
        lambda v, named: graph(v / 0.4, 4.0, named),
        lambda v: [("lutrgb@gl", ch, curve(v / 0.4)) for ch in ("r", "g", "b")])
    blur_case = Case(
        "glow.radius(gblur@gb)", 0.5, 8.0, SITING_444 + ",", "yuva444p",
        lambda v, named: graph(1.5, v, named),
        lambda v: [("gblur@gb", "sigma", format_number(v)), ("gblur@gb", "sigmaV", format_number(v))], core=True)
    return [gain_case, blur_case]


def build_cases() -> list[Case]:
    """Tous les cas du banc. Les bornes suivent celles du modèle (exposition +-2 EV, saturation 0..2...)."""
    cases: list[Case] = [_lutrgb_case("lutrgb.exposure", -2.0, 2.0, exposure_expr, core=True)]
    cases.append(_lutrgb_case("lutrgb.wheel_slope", 0.7, 1.4, lambda v: wheel_expr(v, 0.02, 0.9)))
    cases.append(_lutrgb_case("lutrgb.wheel_base", -0.1, 0.1, lambda v: wheel_expr(1.1, v, 1.0)))
    cases.append(_lutrgb_case("lutrgb.wheel_power", 0.6, 1.6, lambda v: wheel_expr(1.0, 0.0, v)))
    cases.append(_eq_case("eq.contrast", 0.5, 1.5, "contrast", {"saturation": "1.2"}, core=True))
    cases.append(_eq_case("eq.saturation", 0.0, 2.0, "saturation", {"contrast": "1.1"}))
    cases.append(_eq_case("eq.brightness", -0.3, 0.3, "brightness", {}))
    cases.append(_hue_case("hue.h", "h", -90.0, 90.0, core=True))
    cases.append(_hue_case("hue.s", "s", 0.0, 2.0))
    cases.append(_colorbalance_case("colorbalance.shadows", -1.0, 1.0, "s", core=True))
    cases.append(_colorbalance_case("colorbalance.highlights", -1.0, 1.0, "h"))
    body, commands = _simple("colorchannelmixer", _temperature_options)
    cases.append(Case("colorchannelmixer.temperature", -100.0, 100.0, SITING_RGBA + ",", "rgba", body, commands, core=True))
    body, commands = _simple("curves", _curves_options)
    cases.append(Case("curves.master_mid", 0.3, 0.7, SITING_RGBA + ",", "rgba", body, commands, core=True))
    # ``sigmaV`` vaut -1 (« comme sigma ») et est résolu à l'initialisation : sans commande ``sigmaV``, seul l'horizontal bouge.
    body, commands = _simple("gblur", lambda v: [("sigma", format_number(v)), ("sigmaV", format_number(v))])
    cases.append(Case("gblur.sigma(yuv420p)", 0.5, 8.0, "", "yuv420p", body, commands, core=True))
    cases.append(Case("gblur.sigma(gbrp)", 0.5, 8.0, SITING_RGBA + ",format=gbrp,", "gbrp", body, commands))
    body, commands = _simple("unsharp", lambda v: [("luma_msize_x", "5"), ("luma_msize_y", "5"), ("luma_amount", format_number(v))],
                             ("luma_amount",))
    cases.append(Case("unsharp.luma_amount", 0.1, 1.5, "", "yuv420p", body, commands))

    def shift_body(value: float, named: bool) -> str:
        pixels = int(round(value))
        return (f"format=rgba,{_name('rgbashift', named)}=rh={pixels}:bh={-pixels}:edge=smear,"
                "format=rgba,format=yuva444p")

    def shift_commands(value: float) -> Triples:
        pixels = int(round(value))
        return [("rgbashift@t", "rh", str(pixels)), ("rgbashift@t", "bh", str(-pixels))]

    cases.append(Case("rgbashift.shift", 0.0, 8.0, SITING_444 + ",", "yuva444p", shift_body, shift_commands, integer=True, core=True))
    cases.append(_vignette_case())
    body, commands = _simple("vignette", lambda v: [("angle", format_number(v)), ("eval", "frame")], ("angle",))
    cases.append(Case("vignette.angle(eval=frame)", 0.0, 0.7853981633974483, "", "yuv420p", body, commands, core=True))
    cases.append(_geq_case())
    cases.extend(_glow_cases())
    return cases


# ----------------------------------------------------------------------------------------------------------------------
# Scénarios : d'où viennent les images, et ce que voit le filtre
# ----------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    key: str
    source: str = "in30"                         # entrée : in30 (30 i/s) ou in2997 (30000/1001)
    input_opts: tuple[str, ...] = ()
    upstream: str = "null"                       # chaîne appliquée avant sendcmd, identique pour animé et statique
    rate: tuple[int, int] = (30, 1)              # cadence du flux à l'entrée de sendcmd
    count: int = 90                              # nombre d'images de la rampe
    shift: float = 0.0                           # décalage des commandes (flux qui ne commence pas à zéro)
    timing: str = "early"
    global_opts: tuple[str, ...] = ()
    expect: str = "pass"                         # pass, ou fail (mise en garde : on documente l'échec attendu)


def build_scenarios() -> list[Scenario]:
    return [
        Scenario("base"),
        Scenario("base_exact", timing="exact"),
        Scenario("base_exact_us", timing="exact_us"),
        Scenario("seek_ss1", input_opts=("-ss", "1"), count=60),
        Scenario("trim_reset", upstream="trim=start=1:end=3,setpts=PTS-STARTPTS", count=60),
        Scenario("trim_noreset_unshifted", upstream="trim=start=1:end=3", count=60, expect="fail"),
        Scenario("trim_noreset_shifted", upstream="trim=start=1:end=3", count=60, shift=1.0),
        Scenario("seek_copyts_unshifted", input_opts=("-ss", "1"), global_opts=("-copyts",), count=60, expect="fail"),
        Scenario("seek_copyts_shifted", input_opts=("-ss", "1"), global_opts=("-copyts",), count=60, shift=1.0),
        Scenario("fps24", upstream="fps=24", rate=(24, 1), count=72),
        Scenario("fps60", upstream="fps=60", rate=(60, 1), count=180),
        Scenario("ntsc_2997", source="in2997", rate=(30000, 1001), count=90),
        Scenario("ntsc_to_30", source="in2997", upstream="fps=30", rate=(30, 1), count=90),
        Scenario("app_like", upstream="trim=start=0.5:end=2.5,setpts=PTS-STARTPTS,fps=30,setpts=PTS-STARTPTS", count=60),
    ]


EXPR_SCENARIOS = ("base", "seek_ss1", "trim_reset", "trim_noreset_unshifted", "fps24", "fps60", "ntsc_2997", "ntsc_to_30", "app_like")
"""Scénarios où l'on compare aussi l'expression du temps (``eval=frame``) à ``sendcmd``."""


# ----------------------------------------------------------------------------------------------------------------------
# Exécution FFmpeg
# ----------------------------------------------------------------------------------------------------------------------

class Runner:
    """Lance FFmpeg (liste d'arguments, jamais de shell) ; ``cpu0`` ajoute ``-cpuflags 0`` (chemins C, sans SIMD)."""

    def __init__(self, ffmpeg: str, work: Path, cpu0: bool = False) -> None:
        self.ffmpeg = ffmpeg
        self.work = work
        self.cpu0 = cpu0

    def version(self) -> str:
        banner = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout
        return parse_version(banner.splitlines()[0])

    def cost_command(self, source: str, chain: str) -> list[str]:
        """Commande de mesure du coût (sortie nulle). Mêmes options globales que :meth:`command`, dont ``-cpuflags 0`` quand
        ``cpu0`` est demandé : sans cela les temps du rapport seraient ceux des chemins SIMD, étiquetés « C »."""
        cmd = [self.ffmpeg, "-v", "error", "-nostdin", "-y"]
        if self.cpu0:
            cmd += ["-cpuflags", "0"]
        cmd += ["-f", "lavfi", "-i", source, "-filter_complex", f"[0:v]{chain}[o]", "-map", "[o]", "-f", "null", "-"]
        return cmd

    def command(self, source: Path, scenario: Scenario, graph: str, out_fmt: str, frames: int | None = None) -> list[str]:
        cmd = [self.ffmpeg, "-v", "error", "-nostdin", "-y"]
        if self.cpu0:
            cmd += ["-cpuflags", "0"]
        cmd += [*scenario.global_opts, *scenario.input_opts, "-i", str(source), "-filter_complex", graph, "-map", "[o]",
                "-fps_mode", "passthrough"]
        if frames is not None:
            cmd += ["-frames:v", str(frames)]
        return [*cmd, "-f", "rawvideo", "-pix_fmt", out_fmt, "-"]

    def run(self, cmd: list[str]) -> tuple[bytes, str]:
        done = subprocess.run(cmd, capture_output=True, check=False)
        return (done.stdout if done.returncode == 0 else b""), done.stderr.decode("utf-8", "replace")[-400:]

    def ensure_inputs(self) -> None:
        """Crée les entrées de référence si absentes (mire + bruit à graine fixe, sans perte), une fois pour toutes les versions."""
        for name, rate in (("in30", "30"), ("in2997", "30000/1001")):
            path = self.work / (name + ".mkv")
            if path.exists():
                continue
            graph = f"testsrc2=s={WIDTH}x{HEIGHT}:r={rate}:d=3,noise=alls=20:allf=t:all_seed=7,format=yuv420p"
            subprocess.run([self.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", graph, "-c:v", "ffv1", str(path)], check=True)


def _frames(raw: bytes, out_fmt: str) -> np.ndarray:
    size = int(WIDTH * HEIGHT * PIX_BYTES[out_fmt])
    usable = len(raw) // size * size
    return np.frombuffer(raw[:usable], dtype=np.uint8).reshape(-1, size) if usable else np.zeros((0, size), dtype=np.uint8)


def _graph(scenario: Scenario, middle: str) -> str:
    return f"[0:v]{scenario.upstream},{middle}[o]"


def _values(case: Case, scenario: Scenario) -> list[float]:
    values = [ramp_value(case.start, case.end, n, scenario.count) for n in range(scenario.count + 2)]
    return [float(round(v)) for v in values] if case.integer else values


def run_case(runner: Runner, case: Case, scenario: Scenario, mode: str = "sendcmd", var: str = "n") -> dict:
    """Un cas dans un scénario. ``mode`` : ``sendcmd`` ou ``expr`` (expression du temps, variable ``var``)."""
    source = runner.work / (scenario.source + ".mkv")
    values = _values(case, scenario)
    tag = f"{case.key}.{scenario.key}.{mode}{var if mode == 'expr' else ''}".replace("(", "_").replace(")", "_")
    if mode == "sendcmd":
        text = sendcmd_text([case.commands(v) for v in values], *scenario.rate, timing=scenario.timing, shift=scenario.shift)
        cmd_file = runner.work / (tag + ".cmd")
        cmd_file.write_text(text, encoding="utf-8")
        middle = f"sendcmd=f={cmd_file},{case.pre}{case.body(values[0], True)}"
    else:
        assert case.expr_body is not None
        middle = case.pre + case.expr_body(case.start, case.end, scenario.count, var, scenario.rate[0] / scenario.rate[1])
    raw, error = runner.run(runner.command(source, scenario, _graph(scenario, middle), case.out_fmt))
    animated = _frames(raw, case.out_fmt)
    result: dict = {"case": case.key, "scenario": scenario.key, "mode": mode if mode == "sendcmd" else "expr_" + var,
                    "frames": int(len(animated)), "ok": False, "max": None, "mean": None, "pct_gt1": None, "note": ""}
    if not len(animated):
        result["note"] = "animé : échec FFmpeg : " + " ".join(error.split())[:160]
        return result
    picks = [n for n in sample_indices(scenario.count, short=scenario.key not in ("base",)) if n < len(animated)]

    def static(n: int) -> dict:
        mid = f"select='eq(n,{n})',{case.pre}{case.body(values[n], False)}"
        out, err = runner.run(runner.command(source, scenario, _graph(scenario, mid), case.out_fmt, frames=1))
        frame = _frames(out, case.out_fmt)
        if not len(frame):
            return {"max": 255.0, "mean": 255.0, "pct_gt1": 100.0, "error": err}
        return compare_arrays(animated[n], frame[0])

    with ThreadPoolExecutor(max_workers=4) as pool:
        stats = list(pool.map(static, picks))
    errors = [s["error"] for s in stats if "error" in s]
    result.update(max=max(s["max"] for s in stats), mean=round(float(np.mean([s["mean"] for s in stats])), 4),
                  pct_gt1=round(max(s["pct_gt1"] for s in stats), 3), ok=is_valid(stats),
                  note=("statique : échec FFmpeg : " + " ".join(errors[0].split())[:120]) if errors else "")
    worst = max(range(len(stats)), key=lambda i: stats[i]["max"])
    result["worst_frame"] = picks[worst]
    result["per_frame_max"] = {str(n): s["max"] for n, s in zip(picks, stats)}
    return result


def _cube_text(invert: bool) -> str:
    """Un LUT 3D de 2 points par axe : l'identité, ou son inverse (le rouge varie le plus vite)."""
    rows = []
    for b in (0, 1):
        for g in (0, 1):
            for r in (0, 1):
                rows.append(" ".join(str(1 - c if invert else c) for c in (r, g, b)))
    return "LUT_3D_SIZE 2\n" + "\n".join(rows) + "\n"


def run_probes(runner: Runner) -> list[dict]:
    """Options que la doc dit « non commandables » : une commande à 1 s change-t-elle vraiment la sortie ?

    Pour chaque sonde, la dernière image animée est comparée à la sortie statique AVANT (valeur A) et APRÈS (valeur B) la
    commande : ``applied`` si elle égale B, ``ignored`` si elle égale A, sinon ``other``. FFmpeg n'affiche aucune erreur.
    """
    cube_a, cube_b = runner.work / "probe_a.cube", runner.work / "probe_b.cube"
    cube_a.write_text(_cube_text(False), encoding="utf-8")
    cube_b.write_text(_cube_text(True), encoding="utf-8")
    probes = [("lut3d.file", "lut3d", "file", str(cube_a), str(cube_b)),
              ("geq.lum", "geq", "lum", "'p(X,Y)'", "'p(X,Y)*0.5'")]
    results = []
    scenario = Scenario("probe")
    source = runner.work / "in30.mkv"
    for key, fname, option, value_a, value_b in probes:
        cmd_file = runner.work / (key + ".cmd")
        cmd_file.write_text(sendcmd_text([[(fname + "@t", option, value_b)]] * 2, 1, 1, "early", shift=0.5), encoding="utf-8")
        extra = ":cb='p(X,Y)':cr='p(X,Y)'" if fname == "geq" else ""
        pre = SITING_RGBA + "," if fname == "lut3d" else ""

        def graph(value: str, front: str) -> str:
            return f"[0:v]{front}{pre}{fname}@t={option}={value}{extra}[o]"

        animated, error = runner.run(runner.command(source, scenario, graph(value_a, f"sendcmd=f={cmd_file},"), "rgba"))
        frames = _frames(animated, "rgba")
        entry: dict = {"probe": key, "frames": int(len(frames)), "result": "échec", "error": " ".join(error.split())[:160]}
        if len(frames):
            diffs = {}
            for label, value in (("A", value_a), ("B", value_b)):
                still, _ = runner.run(runner.command(source, scenario, graph(value, "select='eq(n,89)',"), "rgba", frames=1))
                one = _frames(still, "rgba")
                diffs[label] = compare_arrays(frames[-1], one[0])["max"] if len(one) else 255.0
            entry["max_vs_A"], entry["max_vs_B"] = diffs["A"], diffs["B"]
            entry["result"] = "applied" if diffs["B"] <= TOLERANCE else ("ignored" if diffs["A"] <= TOLERANCE else "other")
        results.append(entry)
    return results


def last_line(data: bytes | str | None) -> str:
    """Dernière ligne non vide d'une sortie d'erreur (au plus 200 caractères), ``""`` s'il n'y en a pas."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else (data or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def run_cost(runner: Runner, cases: list[Case], repeat: int = 3) -> list[dict]:
    """Coût : taille du fichier de commandes pour 60 s à 30 i/s, temps de rendu animé et statique (720p, 5 s)."""
    results = []
    for case in cases:
        size = len(sendcmd_text([case.commands(ramp_value(case.start, case.end, n, 1800)) for n in range(1800)], 30, 1).encode())
        entry = {"case": case.key, "cmd_bytes_60s": size}
        if case.key in ("lutrgb.exposure", "lutrgb.wheel_slope", "eq.contrast", "hue.h", "colorbalance.shadows",
                        "colorchannelmixer.temperature", "curves.master_mid", "gblur.sigma(yuv420p)", "rgbashift.shift",
                        "glow.radius(gblur@gb)"):
            cmd_file = runner.work / "cost.cmd"
            cmd_file.write_text(sendcmd_text([case.commands(ramp_value(case.start, case.end, n, 150)) for n in range(150)], 30, 1),
                                encoding="utf-8")
            source = "testsrc2=s=1280x720:r=30:d=5,format=yuv420p"
            mid = case.start + (case.end - case.start) / 2
            for label, chain in (("animated_s", f"sendcmd=f={cmd_file},{case.pre}{case.body(case.start, True)}"),
                                 ("static_s", f"{case.pre}{case.body(mid, False)}")):
                cmd = runner.cost_command(source, chain)
                best: float | None = None
                error = ""
                for _ in range(repeat):
                    began = time.perf_counter()
                    proc = subprocess.run(cmd, capture_output=True, check=False)
                    elapsed = time.perf_counter() - began
                    if proc.returncode != 0:                  # un rendu en échec s'arrête vite : sa durée n'est pas un coût
                        error = last_line(proc.stderr)
                        best = None
                        break
                    best = elapsed if best is None else min(best, elapsed)
                if best is None:
                    entry[f"{label}_error"] = error or "échec du rendu"
                else:
                    entry[label] = round(best, 3)
        results.append(entry)
    return results


# ----------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ----------------------------------------------------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    runner = Runner(args.ffmpeg, work, cpu0=args.cpuflags0)
    runner.ensure_inputs()
    cases = [c for c in build_cases() if not args.only or any(part in c.key for part in args.only.split(","))]
    scenarios = [s for s in build_scenarios() if not args.scenarios or s.key in args.scenarios.split(",")]
    report: dict = {"ffmpeg": runner.version(), "cpuflags0": args.cpuflags0, "results": [], "probes": [], "cost": []}
    for scenario in scenarios:
        for case in cases:
            if scenario.key != "base" and not case.core:
                continue
            if "sendcmd" in args.modes.split(","):
                report["results"].append(run_case(runner, case, scenario))
            if "expr" in args.modes.split(",") and case.expr_body and scenario.key in EXPR_SCENARIOS:
                for var in (("n", "t") if case.key != "geq.gain" else ("N", "T")):
                    report["results"].append(run_case(runner, case, scenario, "expr", var))
            if report["results"]:
                print(report["results"][-1]["case"], scenario.key, report["results"][-1]["max"], file=sys.stderr, flush=True)
    if args.probes or (not args.only and not args.scenarios):
        report["probes"] = run_probes(runner)
    if args.cost:
        report["cost"] = run_cost(runner, cases)
    Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Tableau cas x scénario x version (écart maximal ; ``OK`` si <= 1) à partir des fichiers JSON de ``run``."""
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.files]
    keys = sorted({(r["case"], r["scenario"], r["mode"]) for rep in reports for r in rep["results"]})
    head = "| cas | scénario | mode | " + " | ".join(f"{rep['ffmpeg']}{' (C)' if rep['cpuflags0'] else ''}" for rep in reports) + " |"
    print(head)
    print("|" + "---|" * (3 + len(reports)))
    for key in keys:
        cells = []
        for rep in reports:
            found = [r for r in rep["results"] if (r["case"], r["scenario"], r["mode"]) == key]
            cells.append("-" if not found else (f"{'OK' if found[0]['ok'] else 'KO'} ({found[0]['max']:g})" if found[0]["max"] is not None else "KO (échec)"))
        print("| " + " | ".join([*key, *cells]) + " |")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="rejoue le banc avec un FFmpeg")
    run.add_argument("--ffmpeg", default="ffmpeg")
    run.add_argument("--work", required=True, help="dossier de travail (entrées, fichiers de commandes)")
    run.add_argument("--out", required=True, help="fichier JSON de résultat")
    run.add_argument("--cpuflags0", action="store_true", help="ajoute -cpuflags 0 (chemins C)")
    run.add_argument("--cost", action="store_true", help="mesure aussi la taille des commandes et les temps de rendu")
    run.add_argument("--probes", action="store_true", help="joue aussi les sondes d'options non commandables")
    run.add_argument("--modes", default="sendcmd,expr", help="sendcmd, expr ou les deux (expressions du temps)")
    run.add_argument("--only", default="", help="sous-chaînes de cas, séparées par des virgules")
    run.add_argument("--scenarios", default="", help="clés de scénarios, séparées par des virgules")
    run.set_defaults(handler=cmd_run)
    report = sub.add_parser("report", help="assemble des résultats JSON en tableau")
    report.add_argument("files", nargs="+")
    report.set_defaults(handler=cmd_report)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
