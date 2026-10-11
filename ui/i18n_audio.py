"""Libellés de la page Audio (mixage en direct, vumètres ; fusionnés dans :mod:`ui.i18n`)."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


AUDIO_TRANSLATIONS: dict[str, dict[str, str]] = {
    "page.audio": _t("Audio", "Audio", "Audio"),
    "page.audio.tip": _t(
        "Mixer le son : le mixeur et ses vumètres, la sonie de la sortie, la timeline. En lecture, on entend le mixage "
        "de l'export.",
        "Mix the sound: the mixer and its meters, the output loudness, the timeline. During playback you hear the "
        "export mix.",
        "Mezclar el sonido: el mezclador y sus vúmetros, la sonoridad de la salida, la línea de tiempo. Durante la "
        "reproducción se oye la mezcla de la exportación.",
    ),
    "inserts.button": _t("FX", "FX", "FX"),
    "inserts.button.count": _t("FX {count}", "FX {count}", "FX {count}"),
    "inserts.tip": _t(
        "Inserts de {name} : des effets qui traitent la somme de ses clips, après leur volume et leur ducking",
        "Inserts of {name}: effects that process the sum of its clips, after their volume and ducking",
        "Inserts de {name}: efectos que procesan la suma de sus clips, después de su volumen y su ducking",
    ),
    "inserts.title": _t("Inserts de la piste · {name}", "Track inserts · {name}", "Inserts de la pista · {name}"),
    "inserts.hint": _t(
        "Dans l'ordre de la liste, sur la somme des clips de la piste : le compresseur voit tout ce que la piste fait "
        "entendre. Le vumètre de la piste mesure la sortie de la chaîne.",
        "In list order, on the sum of the track's clips: a compressor hears everything the track plays. The track "
        "meter reads the output of the chain.",
        "En el orden de la lista, sobre la suma de los clips de la pista: el compresor oye todo lo que reproduce la "
        "pista. El vúmetro de la pista mide la salida de la cadena.",
    ),
    "inserts.empty": _t(
        "Aucun insert : la piste sort telle quelle.",
        "No inserts: the track plays as it is.",
        "Sin inserts: la pista suena tal cual.",
    ),
    "shortcuts.command.page_audio": _t("Page Audio", "Audio page", "Página Audio"),
}

__all__ = ["AUDIO_TRANSLATIONS"]
