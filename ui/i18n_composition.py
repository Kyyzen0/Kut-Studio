"""Libellés de la page Composition (composition nodale, fusionnés dans :mod:`ui.i18n`)."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


COMPOSITION_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Page et panneau -----------------------------------------------------------------------------------------
    "page.composition": _t("Composition", "Composition", "Composición"),
    "page.composition.tip": _t(
        "Composer le clip choisi par nœuds, comme dans Fusion : sources, transformations, fusions, masques, "
        "incrustations, effets et étalonnage.",
        "Composite the selected clip with nodes, like in Fusion: sources, transforms, merges, masks, keys, effects and "
        "grading.",
        "Componer el clip elegido con nodos, como en Fusion: fuentes, transformaciones, fusiones, máscaras, "
        "incrustaciones, efectos y corrección de color.",
    ),
    "shortcuts.command.page_composition": _t("Page Composition", "Composition page", "Página Composición"),
    "shortcuts.command.composition_convert": _t("Convertir la sélection en composition",
                                                "Convert the selection into a composition",
                                                "Convertir la selección en una composición"),
    "shortcuts.command.composition_new": _t("Nouvelle composition", "New composition", "Nueva composición"),
    "workspace.panel.composition": _t("Composition", "Composition", "Composición"),
    "comp.panel.title": _t("Composition", "Composition", "Composición"),
    "comp.panel.no_clip": _t(
        "Sélectionnez un clip de composition (violet dans la timeline), ou des calques à convertir : clic droit dans la "
        "timeline › Convertir en composition.",
        "Select a composition clip (purple in the timeline), or layers to convert: right-click in the timeline › "
        "Convert into a composition.",
        "Selecciona un clip de composición (violeta en la línea de tiempo), o capas para convertir: clic derecho en la "
        "línea de tiempo › Convertir en composición.",
    ),
    "comp.panel.new": _t("Nouvelle composition", "New composition", "Nueva composición"),
    "comp.panel.add": _t("Ajouter un nœud (après le nœud choisi)", "Add a node (after the selected node)",
                         "Añadir un nodo (después del nodo elegido)"),
    "comp.panel.remove": _t("Supprimer le nœud (Suppr)", "Delete the node (Del)", "Eliminar el nodo (Supr)"),
    "comp.panel.view_node": _t(
        "Voir le nœud choisi dans le viewer (à l'arrêt) plutôt que la sortie. N'agit pas sur l'export.",
        "Show the selected node in the viewer (when paused) instead of the output. Does not affect the export.",
        "Ver el nodo elegido en el visor (en pausa) en lugar de la salida. No afecta a la exportación.",
    ),
    "comp.nodes.title": _t("Nœuds de la composition", "Composition nodes", "Nodos de la composición"),
    "comp.node.tip": _t(
        "Clic : choisir le nœud. Glisser la sortie d'un nœud (rond à droite) sur l'entrée d'un autre (à gauche) pour "
        "les relier ; glisser un lien hors de son entrée pour le défaire.",
        "Click: select the node. Drag a node's output (dot on the right) onto another node's input (on the left) to "
        "link them; drag a link off its input to undo it.",
        "Clic: elegir el nodo. Arrastra la salida de un nodo (punto a la derecha) a la entrada de otro (a la "
        "izquierda) para unirlos; arrastra un enlace fuera de su entrada para deshacerlo.",
    ),
    "comp.node.media": _t("Média", "Media", "Medio"),
    "comp.node.graphic": _t("Texte", "Text", "Texto"),
    "comp.node.solid": _t("Couleur unie", "Solid color", "Color sólido"),
    "comp.node.transform": _t("Transformation", "Transform", "Transformación"),
    "comp.node.merge": _t("Fusion", "Merge", "Fusión"),
    "comp.node.mask": _t("Masque", "Mask", "Máscara"),
    "comp.node.key": _t("Incrustation", "Key", "Incrustación"),
    "comp.node.effects": _t("Effets", "Effects", "Efectos"),
    "comp.node.grade": _t("Étalonnage", "Grade", "Corrección"),
    "comp.node.output": _t("Sortie", "Output", "Salida"),
    "comp.port.background": _t("Fond", "Background", "Fondo"),
    "comp.port.foreground": _t("Premier plan", "Foreground", "Primer plano"),
    "comp.port.input": _t("Entrée", "Input", "Entrada"),
    # --- Inspecteur d'un nœud -------------------------------------------------------------------------------------
    "comp.field.label": _t("Nom", "Name", "Nombre"),
    "comp.field.asset": _t("Média", "Media", "Medio"),
    "comp.field.no_asset": _t("(aucun)", "(none)", "(ninguno)"),
    "comp.field.start": _t("Début", "Start", "Inicio"),
    "comp.field.source_in": _t("Entrée de la source", "Source in", "Entrada de la fuente"),
    "comp.field.source_out": _t("Sortie de la source", "Source out", "Salida de la fuente"),
    "comp.field.duration": _t("Durée", "Duration", "Duración"),
    "comp.field.gain": _t("Gain", "Gain", "Ganancia"),
    "comp.field.muted": _t("Muet", "Muted", "Silenciado"),
    "comp.field.fill": _t("Remplir le cadre", "Fill the frame", "Llenar el cuadro"),
    "comp.field.pan_x": _t("Recadrage X", "Reframe X", "Reencuadre X"),
    "comp.field.pan_y": _t("Recadrage Y", "Reframe Y", "Reencuadre Y"),
    "comp.field.text": _t("Texte", "Text", "Texto"),
    "comp.field.color": _t("Couleur", "Color", "Color"),
    "comp.field.position_x": _t("Position X", "Position X", "Posición X"),
    "comp.field.position_y": _t("Position Y", "Position Y", "Posición Y"),
    "comp.field.scale": _t("Échelle", "Scale", "Escala"),
    "comp.field.rotation": _t("Rotation", "Rotation", "Rotación"),
    "comp.field.opacity": _t("Opacité", "Opacity", "Opacidad"),
    "comp.field.anchor_x": _t("Ancrage X", "Anchor X", "Anclaje X"),
    "comp.field.anchor_y": _t("Ancrage Y", "Anchor Y", "Anclaje Y"),
    "comp.field.flip_h": _t("Miroir horizontal", "Flip horizontally", "Espejo horizontal"),
    "comp.field.flip_v": _t("Miroir vertical", "Flip vertically", "Espejo vertical"),
    "comp.field.animated": _t(
        "Animé : les images-clés reprises de la conversion restent ; les champs règlent la valeur de départ.",
        "Animated: the keyframes kept from the conversion remain; the fields set the base value.",
        "Animado: los fotogramas clave de la conversión se mantienen; los campos ajustan el valor base.",
    ),
    "comp.field.blend": _t("Mode de fusion", "Blend mode", "Modo de fusión"),
    "comp.field.key_color": _t("Couleur à retirer", "Color to remove", "Color a quitar"),
    "comp.field.tolerance": _t("Tolérance", "Tolerance", "Tolerancia"),
    "comp.field.softness": _t("Douceur", "Softness", "Suavidad"),
    "comp.field.spill": _t("Débordement retiré", "Spill removed", "Derrame eliminado"),
    "comp.field.add_effect": _t("Ajouter un effet", "Add an effect", "Añadir un efecto"),
    "comp.field.remove_effect": _t("Retirer l'effet", "Remove the effect", "Quitar el efecto"),
    "comp.field.effect_enabled": _t("Actif", "Enabled", "Activo"),
    "comp.output.hint": _t(
        "La sortie : l'image du clip de composition. Reliez-y le dernier nœud ; ce qui n'y mène pas ne coûte rien.",
        "The output: the picture of the composition clip. Link the last node to it; whatever does not lead to it costs "
        "nothing.",
        "La salida: la imagen del clip de composición. Une a ella el último nodo; lo que no lleva a ella no cuesta "
        "nada.",
    ),
    # --- Timeline, historique, état ------------------------------------------------------------------------------
    "comp.action.convert": _t("Convertir en composition", "Convert into a composition", "Convertir en composición"),
    "comp.action.open": _t("Ouvrir la composition", "Open the composition", "Abrir la composición"),
    "history.comp.convert": _t("Convertir en composition", "Convert into a composition", "Convertir en composición"),
    "history.comp.new": _t("Nouvelle composition", "New composition", "Nueva composición"),
    "history.comp.add": _t("Ajouter un nœud de composition", "Add a composition node",
                           "Añadir un nodo de composición"),
    "history.comp.remove": _t("Supprimer un nœud de composition", "Delete a composition node",
                              "Eliminar un nodo de composición"),
    "history.comp.connect": _t("Relier deux nœuds", "Link two nodes", "Unir dos nodos"),
    "history.comp.disconnect": _t("Défaire un lien", "Remove a link", "Deshacer un enlace"),
    "history.comp.edit": _t("Régler un nœud de composition", "Adjust a composition node",
                            "Ajustar un nodo de composición"),
    "status.comp.converted": _t("Composition créée : {count} calques en nœuds.", "Composition created: {count} "
                                "layers turned into nodes.", "Composición creada: {count} capas convertidas en nodos."),
    "status.comp.dropped": _t("Non repris par la composition : {items}.", "Not kept by the composition: {items}.",
                              "No conservado por la composición: {items}."),
    "status.comp.refused": _t("Composition impossible : {reason}", "Composition not possible: {reason}",
                              "Composición imposible: {reason}"),
}
