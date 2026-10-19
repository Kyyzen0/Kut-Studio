"""Textes des 25 transitions du catalogue élargi (fusionnés dans :mod:`ui.i18n`).

Le français vient de :mod:`core.transition_catalog` (source unique, aussi utilisée pour les presets intégrés) ; seuls
l'anglais et l'espagnol sont écrits ici.
"""

from __future__ import annotations

from core.transition_catalog import EXTENDED_TRANSITION_CATALOG

# valeur d'enum -> ((nom en, description en), (nom es, description es))
_OTHER_LANGUAGES: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "cover_left": (("Cover left", "The new clip enters from the right and covers the old one, which stays in place."),
                   ("Cubrir a la izquierda", "El clip nuevo entra por la derecha y cubre al antiguo, que se queda en su sitio.")),
    "cover_right": (("Cover right", "The new clip enters from the left and covers the old one, which stays in place."),
                    ("Cubrir a la derecha", "El clip nuevo entra por la izquierda y cubre al antiguo, que se queda en su sitio.")),
    "cover_up": (("Cover up", "The new clip rises from the bottom and covers the old one, which stays in place."),
                 ("Cubrir hacia arriba", "El clip nuevo sube desde abajo y cubre al antiguo, que se queda en su sitio.")),
    "cover_down": (("Cover down", "The new clip descends from the top and covers the old one, which stays in place."),
                   ("Cubrir hacia abajo", "El clip nuevo baja desde arriba y cubre al antiguo, que se queda en su sitio.")),
    "reveal_left": (("Reveal left", "The old clip leaves to the left and uncovers the new one, which stays in place."),
                    ("Descubrir a la izquierda", "El clip antiguo sale por la izquierda y deja ver el nuevo, que se queda en su sitio.")),
    "reveal_right": (("Reveal right", "The old clip leaves to the right and uncovers the new one, which stays in place."),
                     ("Descubrir a la derecha", "El clip antiguo sale por la derecha y deja ver el nuevo, que se queda en su sitio.")),
    "reveal_up": (("Reveal up", "The old clip leaves through the top and uncovers the new one, which stays in place."),
                  ("Descubrir hacia arriba", "El clip antiguo sale por arriba y deja ver el nuevo, que se queda en su sitio.")),
    "reveal_down": (("Reveal down", "The old clip leaves through the bottom and uncovers the new one, which stays in place."),
                    ("Descubrir hacia abajo", "El clip antiguo sale por abajo y deja ver el nuevo, que se queda en su sitio.")),
    "wipe_tl": (("Corner wipe, top left", "The new clip sweeps across the picture from the bottom-right corner to the top-left corner."),
                ("Barrido desde arriba a la izquierda", "El clip nuevo barre la imagen desde la esquina inferior derecha hasta la esquina superior izquierda.")),
    "wipe_tr": (("Corner wipe, top right", "The new clip sweeps across the picture from the bottom-left corner to the top-right corner."),
                ("Barrido desde arriba a la derecha", "El clip nuevo barre la imagen desde la esquina inferior izquierda hasta la esquina superior derecha.")),
    "wipe_bl": (("Corner wipe, bottom left", "The new clip sweeps across the picture from the top-right corner to the bottom-left corner."),
                ("Barrido desde abajo a la izquierda", "El clip nuevo barre la imagen desde la esquina superior derecha hasta la esquina inferior izquierda.")),
    "wipe_br": (("Corner wipe, bottom right", "The new clip sweeps across the picture from the top-left corner to the bottom-right corner."),
                ("Barrido desde abajo a la derecha", "El clip nuevo barre la imagen desde la esquina superior izquierda hasta la esquina inferior derecha.")),
    "diag_tl": (("Diagonal, top left", "A diagonal edge sweeps across the picture from the bottom-right corner to the top-left corner."),
                ("Diagonal desde arriba a la izquierda", "Un borde en diagonal cruza la imagen desde la esquina inferior derecha hacia la esquina superior izquierda.")),
    "diag_tr": (("Diagonal, top right", "A diagonal edge sweeps across the picture from the bottom-left corner to the top-right corner."),
                ("Diagonal desde arriba a la derecha", "Un borde en diagonal cruza la imagen desde la esquina inferior izquierda hacia la esquina superior derecha.")),
    "diag_bl": (("Diagonal, bottom left", "A diagonal edge sweeps across the picture from the top-right corner to the bottom-left corner."),
                ("Diagonal desde abajo a la izquierda", "Un borde en diagonal cruza la imagen desde la esquina superior derecha hacia la esquina inferior izquierda.")),
    "diag_br": (("Diagonal, bottom right", "A diagonal edge sweeps across the picture from the top-left corner to the bottom-right corner."),
                ("Diagonal desde abajo a la derecha", "Un borde en diagonal cruza la imagen desde la esquina superior izquierda hacia la esquina inferior derecha.")),
    "horz_open": (("Horizontal open", "The new clip appears as a horizontal band in the center, widening toward the top and bottom."),
                  ("Apertura horizontal", "El clip nuevo aparece como una banda horizontal en el centro, que se ensancha hacia arriba y hacia abajo.")),
    "horz_close": (("Horizontal close", "The new clip closes in from the top and bottom toward the center."),
                   ("Cierre horizontal", "El clip nuevo se cierra desde arriba y desde abajo hacia el centro.")),
    "vert_open": (("Vertical open", "The new clip appears as a vertical band in the center, widening toward the left and right."),
                  ("Apertura vertical", "El clip nuevo aparece como una banda vertical en el centro, que se ensancha hacia la izquierda y la derecha.")),
    "vert_close": (("Vertical close", "The new clip closes in from the left and right toward the center."),
                   ("Cierre vertical", "El clip nuevo se cierra desde la izquierda y la derecha hacia el centro.")),
    "smooth_up": (("Smooth up", "The new clip rises from the bottom with a soft edge."),
                  ("Suave hacia arriba", "El clip nuevo sube desde abajo con un borde suave.")),
    "smooth_down": (("Smooth down", "The new clip descends from the top with a soft edge."),
                    ("Suave hacia abajo", "El clip nuevo baja desde arriba con un borde suave.")),
    "fade_fast": (("Fast fade", "A crossfade that speeds up: the new clip takes over quickly."),
                  ("Fundido rápido", "Un fundido encadenado que se acelera: el clip nuevo se impone enseguida.")),
    "fade_slow": (("Slow fade", "A crossfade that lingers on the old clip before moving to the new one."),
                  ("Fundido lento", "Un fundido encadenado que se demora en el clip antiguo antes de pasar al nuevo.")),
    "fade_grays": (("Fade through grays", "Both clips turn black and white during the fade."),
                   ("Fundido por grises", "Los dos clips pasan a blanco y negro durante el fundido.")),
}


def _build() -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for entry in EXTENDED_TRANSITION_CATALOG:
        value = entry.transition_type.value
        (name_en, description_en), (name_es, description_es) = _OTHER_LANGUAGES[value]
        table[f"transitions.preset.{value}.name"] = {"fr": entry.name, "en": name_en, "es": name_es}
        table[f"transitions.preset.{value}.description"] = {
            "fr": entry.description, "en": description_en, "es": description_es,
        }
    return table


TRANSITIONS_TRANSLATIONS: dict[str, dict[str, str]] = _build()
