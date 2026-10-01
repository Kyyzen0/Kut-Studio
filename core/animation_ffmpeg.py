"""Traduction d'une :class:`~core.animation.AnimationCurve` en expression FFmpeg.

L'expression reprend **exactement** les polynômes de segment calculés par
:mod:`core.animation` (aucun échantillonnage) : l'export évalue donc la même
fonction que l'aperçu, à toutes les images et pour tous les FPS.

Forme produite (``T`` = variable de temps local, ``n`` keyframes) ::

    lt(T,t0)*v0
    + gte(T,t0)*lt(T,t1)*(a+u*(b+u*(c+u*d)))      # u = (T-t0)/(t1-t0)
    + …
    + gte(T,t_{n-1})*v_{n-1}

Une **somme** plutôt que des ``if`` imbriqués, regroupée en **arbre
équilibré** ``((t1+t2)+(t3+t4))…`` : l'évaluateur de FFmpeg limite la
profondeur d'une expression (une somme ou des ``if`` à la suite échouent
au-delà d'une centaine de termes) ; ici elle ne croît qu'en ``log2(n)``. Les bornes de la
propriété sont appliquées par ``clip()``, comme dans
:meth:`core.animation.AnimatableProperty.clamp`.
"""

from __future__ import annotations

from .animation import TIME_EPSILON, AnimationCurve, Segment


def _boundary(time_seconds: float) -> str:
    """Seuil de changement de segment : comme Python, un temps arrondi à la µs."""
    return format_number(time_seconds - TIME_EPSILON)


def format_number(value: float) -> str:
    """Flottant sans notation scientifique, précis au nanoième."""
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"Valeur non finie refusée pour FFmpeg : {value!r}.")
    if abs(value) < 1e-12:
        return "0"
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def _polynomial_expression(coefficients, u: str) -> str:
    a, b, c, d = (format_number(x) for x in coefficients)
    if coefficients[3] != 0:
        return f"({a}+{u}*({b}+{u}*({c}+{u}*{d})))"
    if coefficients[2] != 0:
        return f"({a}+{u}*({b}+{u}*{c}))"
    if coefficients[1] != 0:
        return f"({a}+{u}*{b})"
    return a


def _segment_term(segment: Segment, component: int, time_var: str) -> str:
    u = f"(({time_var})-{format_number(segment.t0)})/{format_number(segment.span)}"
    body = _polynomial_expression(segment.polynomials[component], f"({u})")
    return f"gte({time_var},{_boundary(segment.t0)})*lt({time_var},{_boundary(segment.t1)})*{body}"


def curve_expression(
    curve: AnimationCurve,
    *,
    time_var: str = "T",
    component: int = 0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> str:
    """Expression FFmpeg de la composante ``component`` de ``curve``."""
    keyframes = curve.keyframes
    if not keyframes:
        raise ValueError("Une courbe vide n'a pas d'expression.")
    first = _component(keyframes[0].value, component)
    if len(keyframes) == 1:
        expression = format_number(first)
    else:
        last = _component(keyframes[-1].value, component)
        terms = [f"lt({time_var},{_boundary(keyframes[0].time_seconds)})*{format_number(first)}"]
        terms.extend(_segment_term(segment, component, time_var) for segment in curve.segments)
        terms.append(f"gte({time_var},{_boundary(keyframes[-1].time_seconds)})*{format_number(last)}")
        expression = _balanced_sum(terms)
    if minimum is not None and maximum is not None:
        expression = f"clip({expression},{format_number(minimum)},{format_number(maximum)})"
    return expression


def _balanced_sum(terms: list[str]) -> str:
    """``t1+…+tn`` en arbre équilibré (profondeur ``log2(n)``)."""
    if len(terms) == 1:
        return terms[0]
    middle = len(terms) // 2
    return f"({_balanced_sum(terms[:middle])}+{_balanced_sum(terms[middle:])})"


def _component(value, index: int) -> float:
    if isinstance(value, (tuple, list)):
        return float(value[index])
    return float(value)


__all__ = ["curve_expression", "format_number"]
