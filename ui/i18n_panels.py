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
}

__all__ = ["PANELS_TRANSLATIONS"]
