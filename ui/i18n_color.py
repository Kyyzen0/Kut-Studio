"""Libellés de la page Couleur : pages, nœuds d'étalonnage, roues (fusionnés dans :mod:`ui.i18n`).

Lift / Gamma / Gain / Offset restent en anglais dans les trois langues, comme dans les logiciels d'étalonnage (DaVinci
Resolve les garde ainsi en français et en espagnol) ; l'infobulle dit ce que chaque roue règle.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


_WHEEL_GESTURES = _t(
    "Palet : la couleur · molette : le niveau · double-clic : remise à zéro · Maj : réglage fin.",
    "Puck: the color · dial: the level · double-click: reset · Shift: fine adjustment.",
    "Disco: el color · rueda: el nivel · doble clic: restablecer · Mayús: ajuste fino.",
)


def _wheel_tip(fr: str, en: str, es: str) -> dict[str, str]:
    return {language: f"{text} {_WHEEL_GESTURES[language]}" for language, text in (("fr", fr), ("en", en), ("es", es))}


COLOR_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Pages ---------------------------------------------------------------------------------------------------
    "page.edit": _t("Montage", "Edit", "Edición"),
    "page.color": _t("Couleur", "Color", "Color"),
    "page.switcher": _t("Pages", "Pages", "Páginas"),
    "page.edit.tip": _t(
        "Page Montage : médias, timeline, inspecteur. Chaque page garde sa disposition.",
        "Edit page: media, timeline, inspector. Each page keeps its own layout.",
        "Página Edición: medios, línea de tiempo, inspector. Cada página conserva su disposición.",
    ),
    "page.color.tip": _t(
        "Page Couleur : grand moniteur et scopes, nœuds et roues d'étalonnage. Chaque page garde sa disposition.",
        "Color page: large viewer and scopes, grading nodes and wheels. Each page keeps its own layout.",
        "Página Color: visor grande y scopes, nodos y ruedas de corrección. Cada página conserva su disposición.",
    ),
    # --- Panneau Couleur -------------------------------------------------------------------------------------------
    "color.panel.title": _t("Couleur", "Color", "Color"),
    "color.panel.no_clip": _t(
        "Sélectionnez un clip vidéo dans la timeline pour l'étalonner.",
        "Select a video clip in the timeline to grade it.",
        "Selecciona un clip de vídeo en la línea de tiempo para corregir su color.",
    ),
    "color.panel.node_caption": _t("Nœud {number} / {count}", "Node {number} / {count}", "Nodo {number} / {count}"),
    "color.nodes.title": _t("Nœuds d'étalonnage", "Grading nodes", "Nodos de corrección"),
    "color.nodes.source": _t("Source", "Source", "Fuente"),
    "color.nodes.output": _t("Sortie", "Output", "Salida"),
    # --- Nœud ------------------------------------------------------------------------------------------------------
    "color.node.tip": _t(
        "Clic : régler ce nœud · glisser : changer sa place dans la chaîne · double-clic : le nommer.",
        "Click: adjust this node · drag: move it along the chain · double-click: name it.",
        "Clic: ajustar este nodo · arrastrar: moverlo en la cadena · doble clic: ponerle nombre.",
    ),
    "color.node.default": _t("Réglage", "Grade", "Ajuste"),
    "color.node.bypassed": _t("Contourné", "Bypassed", "Omitido"),
    "color.node.add": _t("Ajouter un nœud en série (Alt+S)", "Add a serial node (Alt+S)",
                         "Añadir un nodo en serie (Alt+S)"),
    "color.node.bypass": _t("Contourner le nœud (Ctrl+D)", "Bypass the node (Ctrl+D)", "Omitir el nodo (Ctrl+D)"),
    "color.node.enable": _t("Réactiver le nœud (Ctrl+D)", "Enable the node (Ctrl+D)", "Reactivar el nodo (Ctrl+D)"),
    "color.node.rename": _t("Nommer le nœud…", "Name the node…", "Nombrar el nodo…"),
    "color.node.rename_title": _t("Nom du nœud", "Node name", "Nombre del nodo"),
    "color.node.rename_label": _t("Nom (vide : aucun)", "Name (empty: none)", "Nombre (vacío: ninguno)"),
    "color.node.reset": _t("Réinitialiser le nœud", "Reset the node", "Restablecer el nodo"),
    "color.node.remove": _t("Supprimer le nœud (Suppr)", "Delete the node (Del)", "Eliminar el nodo (Supr)"),
    "color.node.add_parallel": _t("Ajouter un nœud parallèle (Alt+P)", "Add a parallel node (Alt+P)",
                                  "Añadir un nodo paralelo (Alt+P)"),
    "color.node.add_layer": _t("Ajouter un nœud de calque (Alt+L)", "Add a layer node (Alt+L)",
                               "Añadir un nodo de capa (Alt+L)"),
    "color.mixer.parallel": _t(
        "Mélangeur parallèle : chaque branche corrige la même image, les corrections s'additionnent.",
        "Parallel mixer: each branch grades the same picture, and the corrections add up.",
        "Mezclador paralelo: cada rama corrige la misma imagen y las correcciones se suman.",
    ),
    "color.mixer.layer": _t(
        "Mélangeur de calques : la branche la plus basse passe dessus, là où son qualifieur la sélectionne.",
        "Layer mixer: the lowest branch goes on top, wherever its qualifier selects it.",
        "Mezclador de capas: la rama más baja pasa por encima, allí donde su calificador la selecciona.",
    ),
    "color.compare": _t(
        "Comparer avant / après : à gauche du trait, l'image sans étalonnage (glisser le trait pour le déplacer).",
        "Compare before / after: left of the line, the picture without grading (drag the line to move it).",
        "Comparar antes / después: a la izquierda de la línea, la imagen sin corrección (arrastra la línea).",
    ),
    "color.tab.wheels": _t("Roues", "Wheels", "Ruedas"),
    "color.tab.qualifier": _t("Qualificateur", "Qualifier", "Calificador"),
    "color.tab.windows": _t("Fenêtres", "Windows", "Ventanas"),
    "color.tab.detail": _t("Flou", "Blur", "Desenfoque"),
    # --- Fenêtres, flou --------------------------------------------------------------------------------------------
    "color.window.add_rectangle": _t("Ajouter une fenêtre rectangulaire", "Add a rectangle window",
                                     "Añadir una ventana rectangular"),
    "color.window.add_ellipse": _t("Ajouter une fenêtre elliptique", "Add an ellipse window",
                                   "Añadir una ventana elíptica"),
    "color.window.add_polygon": _t("Ajouter une forme libre (glisser ses sommets dans le viewer)",
                                   "Add a free shape (drag its points in the viewer)",
                                   "Añadir una forma libre (arrastra sus vértices en el visor)"),
    "color.window.remove": _t("Supprimer la fenêtre", "Delete the window", "Eliminar la ventana"),
    "color.window.name": _t("Fenêtre {index}", "Window {index}", "Ventana {index}"),
    "color.window.shape.rectangle": _t("Rectangle", "Rectangle", "Rectángulo"),
    "color.window.shape.ellipse": _t("Ellipse", "Ellipse", "Elipse"),
    "color.window.shape.polygon": _t("Forme libre", "Free shape", "Forma libre"),
    "color.window.inverted": _t("inversée", "inverted", "invertida"),
    "color.window.invert": _t("Inverser (corriger l'extérieur)", "Invert (grade the outside)",
                              "Invertir (corregir el exterior)"),
    "color.window.operation": _t("Combinaison avec les fenêtres au-dessus", "Combination with the windows above",
                                 "Combinación con las ventanas de arriba"),
    "color.window.mode.add": _t("Ajouter", "Add", "Añadir"),
    "color.window.mode.subtract": _t("Soustraire", "Subtract", "Restar"),
    "color.window.mode.intersect": _t("Intersection", "Intersect", "Intersección"),
    "color.window.position_x": _t("Position X", "Position X", "Posición X"),
    "color.window.position_y": _t("Position Y", "Position Y", "Posición Y"),
    "color.window.width": _t("Largeur", "Width", "Ancho"),
    "color.window.height": _t("Hauteur", "Height", "Alto"),
    "color.window.rotation": _t("Rotation", "Rotation", "Rotación"),
    "color.window.feather": _t("Douceur", "Softness", "Suavidad"),
    "color.window.empty": _t(
        "Aucune fenêtre : le nœud corrige toute l'image (ou ce que son qualifieur sélectionne).",
        "No window: the node grades the whole picture (or what its qualifier selects).",
        "Sin ventana: el nodo corrige toda la imagen (o lo que selecciona su calificador).",
    ),
    "color.window.hint": _t(
        "Dans le viewer : glisser la fenêtre la déplace, ses poignées changent sa taille (Maj : proportions) et sa "
        "rotation. Pour qu'elle suive un objet : panneau Tracking, « Lier à », puis la fenêtre.",
        "In the viewer: drag the window to move it, its handles change its size (Shift: proportions) and rotation. "
        "To make it follow an object: Tracking panel, “Link to”, then the window.",
        "En el visor: arrastra la ventana para moverla, sus tiradores cambian su tamaño (Mayús: proporciones) y su "
        "rotación. Para que siga un objeto: panel Tracking, «Vincular a» y la ventana.",
    ),
    "color.window.highlight_tip": _t(
        "Montre dans le moniteur ce que la clé du nœud (qualifieur × fenêtres) sélectionne, le reste en gris. N'agit "
        "pas sur l'export.",
        "Shows in the viewer what the node's key (qualifier × windows) selects, the rest in grey. Does not affect the "
        "export.",
        "Muestra en el visor lo que selecciona la clave del nodo (calificador × ventanas), el resto en gris. No afecta "
        "a la exportación.",
    ),
    "color.detail.blur": _t("Flou", "Blur", "Desenfoque"),
    "color.detail.sharpen": _t("Netteté", "Sharpen", "Nitidez"),
    "color.detail.hint": _t(
        "Le flou et la netteté agissent sur la correction du nœud, là où sa clé (qualifieur × fenêtres) la "
        "sélectionne : une fenêtre inversée floute l'arrière-plan. Flou en pixels de la séquence.",
        "Blur and sharpen act on the node's correction, wherever its key (qualifier × windows) selects it: an "
        "inverted window blurs the background. Blur in sequence pixels.",
        "El desenfoque y la nitidez actúan sobre la corrección del nodo, allí donde su clave (calificador × ventanas) "
        "la selecciona: una ventana invertida desenfoca el fondo. Desenfoque en píxeles de la secuencia.",
    ),
    # --- Qualifieur ------------------------------------------------------------------------------------------------
    "color.qualifier.enable": _t("Qualifier ce nœud", "Qualify this node", "Calificar este nodo"),
    "color.qualifier.invert": _t("Inverser", "Invert", "Invertir"),
    "color.qualifier.highlight": _t("Afficher la sélection", "Show the selection", "Mostrar la selección"),
    "color.qualifier.highlight_tip": _t(
        "Montre dans le moniteur ce que le qualifieur sélectionne, le reste en gris. N'agit pas sur l'export.",
        "Shows in the viewer what the qualifier selects, the rest in grey. Does not affect the export.",
        "Muestra en el visor lo que selecciona el calificador, el resto en gris. No afecta a la exportación.",
    ),
    "color.qualifier.pick": _t("Pipette", "Eyedropper", "Cuentagotas"),
    "color.qualifier.pick_tip": _t(
        "Cliquez dans le viewer : la couleur qui arrive à ce nœud sous le clic est sélectionnée. Maj + clic élargit "
        "la sélection jusqu'à elle.",
        "Click in the viewer: the color reaching this node under the click is selected. Shift + click widens the "
        "selection to it.",
        "Haz clic en el visor: se selecciona el color que llega a este nodo bajo el clic. Mayús + clic amplía la "
        "selección hasta él.",
    ),
    "color.qualifier.hue": _t("Teinte", "Hue", "Tono"),
    "color.qualifier.sat": _t("Saturation", "Saturation", "Saturación"),
    "color.qualifier.lum": _t("Luminance", "Luminance", "Luminancia"),
    "color.qualifier.center": _t("Centre", "Center", "Centro"),
    "color.qualifier.width": _t("Largeur", "Width", "Ancho"),
    "color.qualifier.soft": _t("Douceur", "Softness", "Suavidad"),
    "color.qualifier.low": _t("Bas", "Low", "Bajo"),
    "color.qualifier.high": _t("Haut", "High", "Alto"),
    # --- Roues -----------------------------------------------------------------------------------------------------
    "color.wheel.lift": _t("Lift", "Lift", "Lift"),
    "color.wheel.gamma": _t("Gamma", "Gamma", "Gamma"),
    "color.wheel.gain": _t("Gain", "Gain", "Gain"),
    "color.wheel.offset": _t("Offset", "Offset", "Offset"),
    "color.wheel.lift.tip": _wheel_tip("Lift : les noirs (les blancs ne bougent pas).",
                                       "Lift: the blacks (the whites stay put).",
                                       "Lift: los negros (los blancos no se mueven)."),
    "color.wheel.gamma.tip": _wheel_tip("Gamma : les tons moyens (noirs et blancs ne bougent pas).",
                                        "Gamma: the midtones (blacks and whites stay put).",
                                        "Gamma: los medios tonos (negros y blancos no se mueven)."),
    "color.wheel.gain.tip": _wheel_tip("Gain : les blancs (les noirs ne bougent pas).",
                                       "Gain: the whites (the blacks stay put).",
                                       "Gain: los blancos (los negros no se mueven)."),
    "color.wheel.offset.tip": _wheel_tip("Offset : tout le signal, d'autant.",
                                         "Offset: the whole signal, by the same amount.",
                                         "Offset: toda la señal, en la misma cantidad."),
    "color.wheel.readout": _t("R {r}  V {g}  B {b}  Y {y}", "R {r}  G {g}  B {b}  Y {y}", "R {r}  V {g}  A {b}  Y {y}"),
    # --- Inspecteur ------------------------------------------------------------------------------------------------
    "inspector.color.node": _t(
        "Ces réglages sont ceux du nœud {number} / {count}{label}.",
        "These settings belong to node {number} / {count}{label}.",
        "Estos ajustes son los del nodo {number} / {count}{label}.",
    ),
    # --- Historique ------------------------------------------------------------------------------------------------
    "history.color.node_add": _t("Ajouter un nœud d'étalonnage", "Add a grading node", "Añadir un nodo de corrección"),
    "history.color.node_remove": _t("Supprimer un nœud d'étalonnage", "Delete a grading node",
                                    "Eliminar un nodo de corrección"),
    "history.color.node_bypass": _t("Contourner un nœud", "Bypass a node", "Omitir un nodo"),
    "history.color.node_enable": _t("Réactiver un nœud", "Enable a node", "Reactivar un nodo"),
    "history.color.node_rename": _t("Nommer un nœud", "Name a node", "Nombrar un nodo"),
    "history.color.node_move": _t("Déplacer un nœud", "Move a node", "Mover un nodo"),
    "history.color.node_reset": _t("Réinitialiser un nœud", "Reset a node", "Restablecer un nodo"),
    "history.color.wheel": _t("Roue {wheel}", "{wheel} wheel", "Rueda {wheel}"),
    "history.color.node_parallel": _t("Ajouter un nœud parallèle", "Add a parallel node", "Añadir un nodo paralelo"),
    "history.color.node_layer": _t("Ajouter un nœud de calque", "Add a layer node", "Añadir un nodo de capa"),
    "history.color.qualifier": _t("Qualifieur", "Qualifier", "Calificador"),
    "history.color.pick": _t("Pipette du qualifieur", "Qualifier eyedropper", "Cuentagotas del calificador"),
    "history.color.window_add": _t("Ajouter une fenêtre", "Add a window", "Añadir una ventana"),
    "history.color.window_remove": _t("Supprimer une fenêtre", "Delete a window", "Eliminar una ventana"),
    "history.color.window": _t("Fenêtre", "Window", "Ventana"),
    "history.color.window_drag": _t("Ajuster une fenêtre dans le viewer", "Adjust a window in the viewer",
                                    "Ajustar una ventana en el visor"),
    "history.color.detail": _t("Flou et netteté", "Blur and sharpen", "Desenfoque y nitidez"),
    "status.color.pick_outside": _t("Pipette : le clic est hors de l'image du clip.",
                                    "Eyedropper: the click is outside the clip's picture.",
                                    "Cuentagotas: el clic está fuera de la imagen del clip."),
    "status.color.pick_failed": _t("Pipette : couleur illisible ({error}).", "Eyedropper: color unreadable ({error}).",
                                   "Cuentagotas: color ilegible ({error})."),
    # --- Raccourcis et panneau -------------------------------------------------------------------------------------
    "shortcuts.command.page_edit": _t("Page Montage", "Edit page", "Página Edición"),
    "shortcuts.command.page_color": _t("Page Couleur", "Color page", "Página Color"),
    "workspace.panel.color": _t("Couleur", "Color", "Color"),
    "workspace.panel.clips": _t("Plans", "Clips", "Planos"),
    "color.strip.nodes": _t("{count} nœuds", "{count} nodes", "{count} nodos"),
}
