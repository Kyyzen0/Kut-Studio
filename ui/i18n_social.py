"""Textes de la vidéo sociale (fusionnés dans :mod:`ui.i18n`) : formats verticaux, cadrage, grille rythmique, texte
animé, effets lumineux, SFX, templates « Night » et export pour les réseaux.

Les identifiants techniques (formats, plateformes, presets) viennent de ``core`` ; seuls leurs libellés vivent ici.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


SOCIAL_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Cadrage d'un clip vidéo (inspecteur, transformation avancée) ---------------------------------------------
    "animation.property.fill": _t("Remplir le cadre", "Fill the frame", "Llenar el cuadro"),
    "animation.property.pan_x": _t("Cadrage X", "Framing X", "Encuadre X"),
    "animation.property.pan_y": _t("Cadrage Y", "Framing Y", "Encuadre Y"),
    # --- Menu « Réseaux sociaux » ---------------------------------------------------------------------------------
    "menu.social": _t("&Réseaux sociaux", "&Social", "&Redes sociales"),
    "social.menu.new_project": _t("Nouveau projet réseaux sociaux…", "New social media project…",
                                  "Nuevo proyecto para redes sociales…"),
    "social.menu.sequence_settings": _t("Réglages de la séquence…", "Sequence settings…", "Ajustes de la secuencia…"),
    "social.menu.zones": _t("Zones de la plateforme", "Platform safe zones", "Zonas de la plataforma"),
    "social.menu.fill_frame": _t("Remplir le cadre (recadrer)", "Fill the frame (reframe)", "Llenar el cuadro (reencuadrar)"),
    "social.menu.ken_burns": _t("Ken Burns sur la sélection", "Ken Burns on selection", "Ken Burns en la selección"),
    "social.menu.photos": _t("Photos importées", "Imported photos", "Fotos importadas"),
    "social.menu.photo_fill": _t("Remplir le cadre", "Fill the frame", "Llenar el cuadro"),
    "social.menu.photo_ken_burns": _t("Ken Burns automatique", "Automatic Ken Burns", "Ken Burns automático"),
    "social.platform.none": _t("Aucune", "None", "Ninguna"),
    "social.platform.tiktok": _t("TikTok", "TikTok", "TikTok"),
    "social.platform.reels": _t("Instagram Reels", "Instagram Reels", "Instagram Reels"),
    "social.platform.shorts": _t("YouTube Shorts", "YouTube Shorts", "YouTube Shorts"),
    "social.platform.instagram_feed": _t("Instagram (fil)", "Instagram (feed)", "Instagram (feed)"),
    # --- Raccourcis --------------------------------------------------------------------------------------------------
    "shortcuts.category.social": _t("Réseaux sociaux", "Social", "Redes sociales"),
    "shortcuts.command.social_new_project": _t("Nouveau projet réseaux sociaux", "New social media project",
                                               "Nuevo proyecto para redes sociales"),
    "shortcuts.command.sequence_settings": _t("Réglages de la séquence", "Sequence settings", "Ajustes de la secuencia"),
    "shortcuts.command.social_fill_frame": _t("Remplir le cadre", "Fill the frame", "Llenar el cuadro"),
    "shortcuts.command.social_ken_burns": _t("Ken Burns", "Ken Burns", "Ken Burns"),
    # --- Historique ------------------------------------------------------------------------------------------------
    "history.social.sequence_settings": _t("Réglages de la séquence", "Sequence settings", "Ajustes de la secuencia"),
    "history.social.fill_on": _t("Remplir le cadre", "Fill the frame", "Llenar el cuadro"),
    "history.social.fill_off": _t("Cadre entier (bandes)", "Fit the frame (bars)", "Cuadro completo (bandas)"),
    "history.social.ken_burns": _t("Ken Burns", "Ken Burns", "Ken Burns"),
    # --- Messages ----------------------------------------------------------------------------------------------------
    "social.message.no_video": _t("Sélectionnez un ou plusieurs clips vidéo.", "Select one or more video clips.",
                                  "Seleccione uno o varios clips de vídeo."),
    "social.message.no_photo": _t("Sélectionnez des clips vidéo ou des photos.", "Select video clips or photos.",
                                  "Seleccione clips de vídeo o fotos."),
    # --- Pistes d'un projet social -----------------------------------------------------------------------------------
    "social.track.v1": _t("Images", "Footage", "Imágenes"),
    "social.track.g1": _t("Titres", "Titles", "Títulos"),
    "social.track.a1": _t("Musique", "Music", "Música"),
    "social.track.a2": _t("Voix", "Voice", "Voz"),
    "social.track.a3": _t("SFX", "SFX", "SFX"),
    # --- Formats et assistant ------------------------------------------------------------------------------------------
    "social.format.vertical": _t("Vertical", "Vertical", "Vertical"),
    "social.format.portrait": _t("Portrait", "Portrait", "Retrato"),
    "social.format.square": _t("Carré", "Square", "Cuadrado"),
    "social.format.landscape": _t("Paysage", "Landscape", "Horizontal"),
    "social.format.choice": _t("{name} · {ratio} · {width} × {height}", "{name} · {ratio} · {width} × {height}",
                               "{name} · {ratio} · {width} × {height}"),
    "social.fps": _t("{fps} i/s", "{fps} fps", "{fps} fps"),
    "social.dialog.new_title": _t("Nouveau projet réseaux sociaux", "New social media project",
                                  "Nuevo proyecto para redes sociales"),
    "social.dialog.new_intro": _t(
        "Un projet au cadre du réseau visé, avec ses pistes : images, titres, musique, voix et SFX.",
        "A project framed for the target network, with its tracks: footage, titles, music, voice and SFX.",
        "Un proyecto con el cuadro de la red elegida y sus pistas: imágenes, títulos, música, voz y SFX.",
    ),
    "social.dialog.default_name": _t("Vidéo sociale", "Social video", "Vídeo social"),
    "social.dialog.name": _t("Nom", "Name", "Nombre"),
    "social.dialog.format": _t("Format", "Format", "Formato"),
    "social.dialog.fps": _t("Cadence", "Frame rate", "Fotogramas por segundo"),
    "social.dialog.zones": _t("Zones affichées", "Safe zones shown", "Zonas mostradas"),
    "social.dialog.template": _t("Template", "Template", "Plantilla"),
    "social.dialog.no_template": _t("Projet vide", "Empty project", "Proyecto vacío"),
    "social.dialog.create": _t("Créer", "Create", "Crear"),
    "social.dialog.cancel": _t("Annuler", "Cancel", "Cancelar"),
    "social.sequence.title": _t("Réglages de la séquence", "Sequence settings", "Ajustes de la secuencia"),
    "social.sequence.custom": _t("Personnalisé", "Custom", "Personalizado"),
    "social.sequence.width": _t("Largeur", "Width", "Ancho"),
    "social.sequence.height": _t("Hauteur", "Height", "Alto"),
    "social.sequence.pixels": _t(" px", " px", " px"),
    "social.sequence.apply": _t("Appliquer", "Apply", "Aplicar"),
    "social.sequence.note": _t(
        "Les positions suivent le nouveau cadre ; les textes et formes gardent leur taille en pixels.",
        "Positions follow the new frame; texts and shapes keep their size in pixels.",
        "Las posiciones siguen el nuevo cuadro; los textos y formas conservan su tamaño en píxeles.",
    ),
    # --- Inspecteur : groupe « Projet » -------------------------------------------------------------------------------
    "inspector.project.field": _t("{field} : {value}", "{field}: {value}", "{field}: {value}"),
    "inspector.project.size": _t("{width} × {height}", "{width} × {height}", "{width} × {height}"),
    "inspector.project.black": _t("Noir", "Black", "Negro"),
}
