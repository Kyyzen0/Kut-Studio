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
    "shortcuts.command.page_audio": _t("Page Audio", "Audio page", "Página Audio"),
}

__all__ = ["AUDIO_TRANSLATIONS"]
