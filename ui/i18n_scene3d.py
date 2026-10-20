"""Traductions des clips « scène 3D » (fusionnées dans :mod:`ui.i18n`) : menu, historique, inspecteur, aperçu du moniteur."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


SCENE3D_TRANSLATIONS: dict[str, dict[str, str]] = {
    "menu.item.add_scene3d": _t("Scène 3D…", "3D scene…", "Escena 3D…"),
    "history.scene3d.add": _t("Ajouter une scène 3D", "Add a 3D scene", "Añadir una escena 3D"),
    "dialog.scene3d.open_title": _t("Ajouter une scène 3D", "Add a 3D scene", "Añadir una escena 3D"),
    "dialog.filter.scene3d": _t("Scènes Kut-3D (*.{ext})", "Kut-3D scenes (*.{ext})", "Escenas Kut-3D (*.{ext})"),
    "scene3d.add.refused": _t(
        "Scène 3D refusée : {error}", "3D scene refused: {error}", "Escena 3D rechazada: {error}"),
    "scene3d.add.locked": _t(
        "La piste {track} est verrouillée : la scène 3D n'a pas été ajoutée.",
        "Track {track} is locked: the 3D scene was not added.",
        "La pista {track} está bloqueada: la escena 3D no se ha añadido."),
    "scene3d.reloaded": _t("Scène 3D rechargée.", "3D scene reloaded.", "Escena 3D recargada."),
    "scene3d.inspector.file": _t("Fichier", "File", "Archivo"),
    "scene3d.inspector.reload": _t("Recharger", "Reload", "Recargar"),
    "scene3d.inspector.reload.tooltip": _t(
        "Relit le fichier de scène et ses modèles sur le disque (après les avoir modifiés dans un autre logiciel).",
        "Reads the scene file and its models from disk again (after editing them in another app).",
        "Vuelve a leer el archivo de escena y sus modelos del disco (tras editarlos en otra aplicación)."),
    "scene3d.speed.tooltip": _t(
        "Une scène 3D se joue à sa propre vitesse : elle ne se ralentit, ne s'accélère, ne s'inverse ni ne se fige.",
        "A 3D scene plays at its own speed: it cannot be slowed down, sped up, reversed or frozen.",
        "Una escena 3D se reproduce a su propia velocidad: no se ralentiza, acelera, invierte ni congela."),
    "scene3d.preview.approximate": _t("Aperçu approximatif", "Approximate preview", "Vista previa aproximada"),
    "scene3d.preview.approximate.tooltip": _t(
        "Image 3D réduite et figée à l'arrêt ; la lecture et l'export montrent le rendu exact.",
        "Reduced, still 3D image while stopped; playback and export show the exact render.",
        "Imagen 3D reducida y fija al detenerse; la reproducción y la exportación muestran el resultado exacto."),
}
