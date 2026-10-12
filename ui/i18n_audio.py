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
    "inserts.bus_title": _t("Inserts du bus · {name}", "Bus inserts · {name}", "Inserts del bus · {name}"),
    "inserts.bus_hint": _t(
        "Dans l'ordre de la liste, sur la somme des pistes envoyées sur ce bus, avant son fader.",
        "In list order, on the sum of the tracks sent to this bus, before its fader.",
        "En el orden de la lista, sobre la suma de las pistas enviadas a este bus, antes de su fader.",
    ),
    "bus.add": _t("Nouveau bus", "New bus", "Nuevo bus"),
    "bus.add.tip": _t(
        "Ajouter un bus : un sous-mixage (fader, inserts) entre des pistes et le Master",
        "Add a bus: a submix (fader, inserts) between tracks and the Master",
        "Añadir un bus: una submezcla (fader, inserts) entre pistas y el Master",
    ),
    "bus.route.master": _t("Master", "Master", "Master"),
    "bus.route.tip": _t(
        "Sortie de {name} : le Master, ou un bus", "Output of {name}: the Master, or a bus",
        "Salida de {name}: el Master, o un bus",
    ),
    "bus.remove.tip": _t(
        "Supprimer le bus {name} (ses pistes retournent au Master)",
        "Delete bus {name} (its tracks go back to the Master)",
        "Eliminar el bus {name} (sus pistas vuelven al Master)",
    ),
    "bus.rename.tip": _t("Double-clic : renommer le bus", "Double-click: rename the bus", "Doble clic: renombrar el bus"),
    "bus.rename.title": _t("Renommer le bus", "Rename bus", "Renombrar bus"),
    "bus.rename.label": _t("Nom du bus :", "Bus name:", "Nombre del bus:"),
    "history.audio.bus_add": _t("Ajouter un bus", "Add a bus", "Añadir un bus"),
    "history.audio.bus_remove": _t("Supprimer un bus", "Delete a bus", "Eliminar un bus"),
    "history.audio.bus_rename": _t("Renommer un bus", "Rename a bus", "Renombrar un bus"),
    "history.audio.bus_volume": _t("Volume d'un bus", "Bus volume", "Volumen de un bus"),
    "history.audio.bus_mute": _t("Couper un bus", "Mute a bus", "Silenciar un bus"),
    "history.audio.bus_route": _t("Sortie d'une piste", "Track output", "Salida de una pista"),
    "inserts.empty": _t(
        "Aucun insert : la piste sort telle quelle.",
        "No inserts: the track plays as it is.",
        "Sin inserts: la pista suena tal cual.",
    ),
    "shortcuts.command.page_audio": _t("Page Audio", "Audio page", "Página Audio"),
}

__all__ = ["AUDIO_TRANSLATIONS"]
