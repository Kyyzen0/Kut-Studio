"""Traductions des panneaux et de la disposition (fusionnées dans :mod:`ui.i18n`).

Disposition des panneaux, inspecteur, bibliothèque, timeline, aperçu et export : les textes qui étaient écrits en
dur dans ``ui/``. Les clés suivent la convention du reste du dépôt (``domaine.élément``).
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


PANELS_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Disposition : noms des panneaux et des zones ---------------------------------------------------------
    "workspace.panel.timeline": _t("Timeline", "Timeline", "Línea de tiempo"),
    "workspace.panel.viewer": _t("Viewer", "Viewer", "Visor"),
    "workspace.panel.media": _t("Médias", "Media", "Medios"),
    "workspace.panel.inspector": _t("Inspecteur", "Inspector", "Inspector"),
    "workspace.panel.mixer": _t("Mixeur", "Mixer", "Mezclador"),
    "workspace.area.left": _t("Zone gauche", "Left area", "Zona izquierda"),
    "workspace.area.center": _t("Zone centrale", "Centre area", "Zona central"),
    "workspace.area.right": _t("Zone droite", "Right area", "Zona derecha"),
    "workspace.area.bottom": _t("Zone basse", "Bottom area", "Zona inferior"),
    # --- Disposition : menu d'options d'un panneau ---------------------------------------------------------------
    "workspace.action.float": _t("Détacher le panneau", "Detach panel", "Desacoplar panel"),
    "workspace.action.dock": _t("Rattacher", "Attach", "Acoplar"),
    "workspace.action.maximize": _t("Maximiser le panneau", "Maximize panel", "Maximizar panel"),
    "workspace.action.reset_size": _t("Réinitialiser la taille", "Reset size", "Restablecer el tamaño"),
    "workspace.action.close": _t("Fermer le panneau", "Close panel", "Cerrar panel"),
    "workspace.action.move_to": _t("Déplacer vers…", "Move to…", "Mover a…"),
    "workspace.options_tooltip": _t("Options du panneau {panel}", "{panel} panel options", "Opciones del panel {panel}"),
    "workspace.dock_tooltip": _t("Rattacher à la fenêtre principale", "Attach to the main window",
                                 "Acoplar a la ventana principal"),
    # --- Espaces de travail nommés ---------------------------------------------------------------------------------
    "workspace.dialog.title": _t("Espace de travail", "Workspace", "Espacio de trabajo"),
    "workspace.dialog.name_label": _t("Nom de l'espace de travail :", "Workspace name:", "Nombre del espacio de trabajo:"),
    "workspace.dialog.saved": _t("Disposition enregistrée sous « {name} ».", "Layout saved as “{name}”.",
                                 "Disposición guardada como «{name}»."),
    "workspace.dialog.save_failed": _t("Impossible d'enregistrer cet espace de travail.",
                                       "Could not save this workspace.", "No se pudo guardar este espacio de trabajo."),
    # --- Vocabulaire commun ------------------------------------------------------------------------------------------
    "mograph.layers.add_button": _t("+ Ajouter", "+ Add", "+ Añadir"),
    "common.add": _t("Ajouter", "Add", "Añadir"),
    "common.none": _t("Aucun", "None", "Ninguno"),
    "common.text": _t("Texte", "Text", "Texto"),
    "common.size": _t("Taille", "Size", "Tamaño"),
    "common.type": _t("Type", "Type", "Tipo"),
    "common.source": _t("Source", "Source", "Origen"),
    # --- Rail latéral et navigation supérieure (tables traduites à l'affichage) --------------------------------------
    "rail.media": _t("Médias", "Media", "Medios"),
    "rail.sequences": _t("Séquences", "Sequences", "Secuencias"),
    "rail.edit": _t("Éditer", "Edit", "Editar"),
    "rail.effects": _t("Effets", "Effects", "Efectos"),
    "rail.color": _t("Couleur", "Color", "Color"),
    "rail.text": _t("Texte", "Text", "Texto"),
    "rail.transitions": _t("Transitions", "Transitions", "Transiciones"),
    "rail.audio": _t("Audio", "Audio", "Audio"),
    "rail.graphics": _t("Graphiques", "Graphics", "Gráficos"),
    "rail.templates": _t("Modèles", "Templates", "Plantillas"),
    # --- Infobulles de la barre d'outils de la timeline ({hint} = raccourci courant) ---------------------------------
    "timeline.tip.blade": _t("Outil lame{hint}", "Blade tool{hint}", "Herramienta de cuchilla{hint}"),
    "timeline.tip.roll": _t(
        "Roll{hint} : déplace la coupe entre deux clips",
        "Roll{hint}: moves the cut between two clips",
        "Roll{hint}: mueve el corte entre dos clips",
    ),
    "timeline.tip.slip": _t(
        "Slip{hint} : change le contenu sans bouger le clip",
        "Slip{hint}: changes the content without moving the clip",
        "Slip{hint}: cambia el contenido sin mover el clip",
    ),
    "timeline.tip.slide": _t(
        "Slide{hint} : glisse le clip et ajuste ses voisins",
        "Slide{hint}: slides the clip and adjusts its neighbors",
        "Slide{hint}: desliza el clip y ajusta sus vecinos",
    ),
    "timeline.tip.ripple": _t(
        "Ripple{hint} : referme le trou après un trim droit ou une suppression",
        "Ripple{hint}: closes the gap after a right trim or a deletion",
        "Ripple{hint}: cierra el hueco tras un recorte derecho o una eliminación",
    ),
    "timeline.tip.marker": _t(
        "Marqueur au playhead{hint}",
        "Marker at playhead{hint}",
        "Marcador en el cabezal{hint}",
    ),
    # --- En-têtes de piste et transitions (tables traduites à l'affichage) -------------------------------------------
    "timeline.track.video": _t("VIDÉO", "VIDEO", "VÍDEO"),
    "timeline.track.audio": _t("AUDIO", "AUDIO", "AUDIO"),
    "timeline.track.subtitle": _t("TEXTE", "TEXT", "TEXTO"),
    "timeline.track.graphics": _t("GRAPHISME", "GRAPHICS", "GRÁFICOS"),
    "timeline.transition.default": _t("TRANSITION", "TRANSITION", "TRANSICIÓN"),
    # --- Barre supérieure et lecture ---------------------------------------------------------------------------------
    "topbar.saved": _t("●  Enregistré", "●  Saved", "●  Guardado"),
    "topbar.dirty": _t("●  Non enregistré", "●  Unsaved", "●  Sin guardar"),
    "topbar.saved_tooltip": _t("Projet enregistré", "Project saved", "Proyecto guardado"),
    "topbar.dirty_tooltip": _t("Modifications non enregistrées", "Unsaved changes", "Cambios sin guardar"),
    "topbar.reset_layout": _t(
        "Réinitialiser la disposition des panneaux",
        "Reset the panel layout",
        "Restablecer la disposición de los paneles",
    ),
    "topbar.settings": _t("Réglages", "Settings", "Ajustes"),
    "topbar.export": _t("Exporter", "Export", "Exportar"),
    "topbar.pause": _t("Pause", "Pause", "Pausa"),
    "topbar.play": _t("Lecture", "Play", "Reproducir"),
    "topbar.default_name": _t("Mon montage", "My edit", "Mi montaje"),
    # --- Barre d'outils de la timeline -------------------------------------------------------------------------------
    "timeline.tip.record": _t(
        "Enregistrer sur les pistes audio armées",
        "Record on armed audio tracks",
        "Grabar en las pistas de audio armadas",
    ),
    "timeline.tip.fit": _t("Voir toute la timeline", "Show the whole timeline", "Ver toda la línea de tiempo"),
    # --- Menu d'un clip, pistes et transitions -----------------------------------------------------------------------
    "timeline.menu.cut": _t("Couper au playhead", "Cut at playhead", "Cortar en el cabezal"),
    "timeline.menu.toggle": _t("Activer / désactiver", "Enable / disable", "Activar / desactivar"),
    "timeline.menu.ripple_delete": _t(
        "Supprimer et refermer",
        "Delete and close the gap",
        "Eliminar y cerrar el hueco",
    ),
    "timeline.slip_preview": _t("slip {delta}s", "slip {delta}s", "slip {delta}s"),
    "timeline.clip_count_one": _t("{count} clip", "{count} clip", "{count} clip"),
    "timeline.clip_count_many": _t("{count} clips", "{count} clips", "{count} clips"),
    "timeline.transition.crossfade": _t("FONDU", "CROSSFADE", "FUNDIDO"),
    "timeline.transition.fade_black": _t("NOIR", "BLACK", "NEGRO"),
    "timeline.transition.wipe_left": _t("BALAYAGE ←", "WIPE ←", "BARRIDO ←"),
    "timeline.transition.wipe_right": _t("BALAYAGE →", "WIPE →", "BARRIDO →"),
    "timeline.transition.tooltip": _t(
        "Cliquer pour modifier cette transition",
        "Click to edit this transition",
        "Haga clic para modificar esta transición",
    ),
    "timeline.track.locked": _t("Verrouillée", "Locked", "Bloqueada"),
    "timeline.track.hidden": _t("Masquée", "Hidden", "Oculta"),
    "timeline.track.muted": _t("Muette", "Muted", "Silenciada"),
    "timeline.track.collapsed": _t("Réduite", "Collapsed", "Contraída"),
    "timeline.track.actions": _t("Actions de la piste", "Track actions", "Acciones de la pista"),
    "timeline.track.collapse_toggle": _t("Réduire ou développer", "Collapse or expand", "Contraer o expandir"),
    "timeline.junction": _t("Jonction à {time}s", "Junction at {time}s", "Unión en {time}s"),
    "preview.crossfade_overlay": _t("Fondu enchaîné · 0.5 s", "Cross dissolve · 0.5 s", "Fundido encadenado · 0.5 s"),
    # --- Visionneuse -------------------------------------------------------------------------------------------------
    "preview.no_clip": _t(
        "Aucun clip sous la tête de lecture\nDéplacez la tête de lecture ou sélectionnez un clip dans la timeline.",
        "No clip under the playhead\nMove the playhead or select a clip in the timeline.",
        "Ningún clip bajo el cabezal\nMueva el cabezal o seleccione un clip en la línea de tiempo.",
    ),
    "preview.title": _t("VISIONNEUSE", "VIEWER", "VISOR"),
    "preview.back": _t("Reculer de 2 s", "Back 2 s", "Retroceder 2 s"),
    "preview.forward": _t("Avancer de 2 s", "Forward 2 s", "Avanzar 2 s"),
    "preview.import": _t("Importer un média", "Import a media", "Importar un medio"),
    "preview.no_source": _t(
        "{name} n’a pas encore de média source\nImportez ou reliez le fichier pour l’afficher dans la visionneuse.",
        "{name} has no source media yet\nImport or relink the file to display it in the viewer.",
        "{name} aún no tiene medio de origen\nImporte o reenlace el archivo para mostrarlo en el visor.",
    ),
    "preview.selected_clip": _t("Le clip sélectionné", "The selected clip", "El clip seleccionado"),
    # --- Export du projet --------------------------------------------------------------------------------------------
    "render.export.quality.high": _t("Élevée", "High", "Alta"),
    "render.export.quality.standard": _t("Standard", "Standard", "Estándar"),
    "render.export.quality.low": _t("Basse", "Low", "Baja"),
    "render.export.title": _t("EXPORT DU PROJET", "PROJECT EXPORT", "EXPORTACIÓN DEL PROYECTO"),
    "render.export.close_tooltip": _t("Fermer l'export", "Close the export", "Cerrar la exportación"),
    "render.export.subtitle": _t(
        "Préparez les paramètres de sortie de votre montage.",
        "Set up the output settings of your edit.",
        "Prepare los ajustes de salida de su montaje.",
    ),
}

__all__ = ["PANELS_TRANSLATIONS"]
