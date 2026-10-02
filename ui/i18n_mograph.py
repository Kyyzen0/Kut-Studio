"""Traductions du moteur motion graphics (fusionnées dans :mod:`ui.i18n`).

Séparées du dictionnaire principal pour garder ce dernier lisible ; les
clés suivent les mêmes conventions (``animation.property.*`` pour les
propriétés animables, ``shortcuts.command.*`` pour les commandes…).
"""

from __future__ import annotations

MOGRAPH_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Propriétés de transform avancées ---------------------------------
    "animation.property.anchor_x": {"fr": "Ancrage X", "en": "Anchor X", "es": "Anclaje X"},
    "animation.property.anchor_y": {"fr": "Ancrage Y", "en": "Anchor Y", "es": "Anclaje Y"},
    "animation.property.scale_x": {"fr": "Échelle X", "en": "Scale X", "es": "Escala X"},
    "animation.property.scale_y": {"fr": "Échelle Y", "en": "Scale Y", "es": "Escala Y"},
    "animation.property.skew": {"fr": "Inclinaison", "en": "Skew", "es": "Inclinación"},
    "animation.property.flip_h": {"fr": "Miroir horizontal", "en": "Flip horizontal", "es": "Volteo horizontal"},
    "animation.property.flip_v": {"fr": "Miroir vertical", "en": "Flip vertical", "es": "Volteo vertical"},
    # --- Propriétés de calque ----------------------------------------------
    "graphics.property.width": {"fr": "Largeur", "en": "Width", "es": "Ancho"},
    "graphics.property.height": {"fr": "Hauteur", "en": "Height", "es": "Alto"},
    "graphics.property.corner_radius": {"fr": "Rayon d'angle", "en": "Corner radius", "es": "Radio de esquina"},
    "graphics.property.stroke_width": {"fr": "Épaisseur du contour", "en": "Stroke width", "es": "Grosor del contorno"},
    "graphics.property.font_size": {"fr": "Corps du texte", "en": "Font size", "es": "Tamaño de fuente"},
    "graphics.property.tracking": {"fr": "Approche (tracking)", "en": "Tracking", "es": "Espaciado (tracking)"},
    "graphics.property.line_spacing": {"fr": "Interligne", "en": "Line spacing", "es": "Interlineado"},
    "graphics.property.shadow_offset_x": {"fr": "Ombre X", "en": "Shadow X", "es": "Sombra X"},
    "graphics.property.shadow_offset_y": {"fr": "Ombre Y", "en": "Shadow Y", "es": "Sombra Y"},
    "graphics.property.shadow_blur": {"fr": "Flou de l'ombre", "en": "Shadow blur", "es": "Desenfoque de sombra"},
    # --- Masques ------------------------------------------------------------
    "mask.property.position_x": {"fr": "Masque · position X", "en": "Mask · position X", "es": "Máscara · posición X"},
    "mask.property.position_y": {"fr": "Masque · position Y", "en": "Mask · position Y", "es": "Máscara · posición Y"},
    "mask.property.width": {"fr": "Masque · largeur", "en": "Mask · width", "es": "Máscara · ancho"},
    "mask.property.height": {"fr": "Masque · hauteur", "en": "Mask · height", "es": "Máscara · alto"},
    "mask.property.rotation": {"fr": "Masque · rotation", "en": "Mask · rotation", "es": "Máscara · rotación"},
    "mask.property.feather": {"fr": "Masque · contour adouci", "en": "Mask · feather", "es": "Máscara · suavizado"},
    "mask.property.expansion": {"fr": "Masque · dilatation", "en": "Mask · expansion", "es": "Máscara · expansión"},
    "mask.property.opacity": {"fr": "Masque · opacité", "en": "Mask · opacity", "es": "Máscara · opacidad"},
    # --- Modes de fusion ------------------------------------------------------
    "blend.normal": {"fr": "Normal", "en": "Normal", "es": "Normal"},
    "blend.multiply": {"fr": "Produit", "en": "Multiply", "es": "Multiplicar"},
    "blend.screen": {"fr": "Écran", "en": "Screen", "es": "Trama"},
    "blend.overlay": {"fr": "Incrustation", "en": "Overlay", "es": "Superponer"},
    "blend.darken": {"fr": "Obscurcir", "en": "Darken", "es": "Oscurecer"},
    "blend.lighten": {"fr": "Éclaircir", "en": "Lighten", "es": "Aclarar"},
    "blend.add": {"fr": "Addition", "en": "Add", "es": "Añadir"},
    "blend.difference": {"fr": "Différence", "en": "Difference", "es": "Diferencia"},
    # --- Commandes et menus -----------------------------------------------------
    "menu.layers": {"fr": "Calques", "en": "Layers", "es": "Capas"},
    "shortcuts.category.motion": {"fr": "Motion graphics", "en": "Motion graphics", "es": "Motion graphics"},
    "shortcuts.command.layer_add_text": {"fr": "Ajouter un texte", "en": "Add text", "es": "Añadir texto"},
    "shortcuts.command.layer_add_shape": {"fr": "Ajouter une forme", "en": "Add shape", "es": "Añadir forma"},
    "shortcuts.command.layer_add_null": {"fr": "Ajouter un contrôleur", "en": "Add controller (null)", "es": "Añadir controlador"},
    "shortcuts.command.layer_add_adjustment": {"fr": "Ajouter un calque d'effets", "en": "Add adjustment layer", "es": "Añadir capa de ajuste"},
    "shortcuts.command.layer_group": {"fr": "Grouper les calques", "en": "Group layers", "es": "Agrupar capas"},
    "shortcuts.command.layer_ungroup": {"fr": "Dégrouper", "en": "Ungroup", "es": "Desagrupar"},
    "shortcuts.command.layer_copy_attributes": {"fr": "Copier les attributs du calque", "en": "Copy layer attributes", "es": "Copiar atributos de la capa"},
    "shortcuts.command.layer_paste_attributes": {"fr": "Coller les attributs", "en": "Paste attributes", "es": "Pegar atributos"},
    "shortcuts.command.view_safe_areas": {"fr": "Zones de sécurité", "en": "Safe areas", "es": "Zonas seguras"},
    "shortcuts.command.view_guides": {"fr": "Afficher les guides", "en": "Show guides", "es": "Mostrar guías"},
    "shortcuts.command.view_grid": {"fr": "Grille (tiers)", "en": "Grid (thirds)", "es": "Cuadrícula (tercios)"},
    "shortcuts.command.mograph_snapping": {"fr": "Magnétisme du viewer", "en": "Viewer snapping", "es": "Imán del visor"},
    "menu.item.add_text_layer": {"fr": "Texte", "en": "Text", "es": "Texto"},
    "menu.item.add_shape_layer": {"fr": "Forme", "en": "Shape", "es": "Forma"},
    "menu.item.add_null_layer": {"fr": "Contrôleur (null)", "en": "Controller (null)", "es": "Controlador (null)"},
    "menu.item.add_adjustment_layer": {"fr": "Calque d'effets (adjustment)", "en": "Adjustment layer", "es": "Capa de ajuste"},
    "menu.item.group_layers": {"fr": "Grouper", "en": "Group", "es": "Agrupar"},
    "menu.item.ungroup_layers": {"fr": "Dégrouper", "en": "Ungroup", "es": "Desagrupar"},
    "menu.item.copy_layers": {"fr": "Copier les calques", "en": "Copy layers", "es": "Copiar capas"},
    "menu.item.paste_layers": {"fr": "Coller les calques à la tête de lecture", "en": "Paste layers at playhead", "es": "Pegar capas en el cabezal"},
    "menu.item.copy_attributes": {"fr": "Copier transform, masques et effets", "en": "Copy transform, masks and effects", "es": "Copiar transformación, máscaras y efectos"},
    "menu.item.paste_attributes": {"fr": "Coller les attributs…", "en": "Paste attributes…", "es": "Pegar atributos…"},
    "menu.item.safe_areas": {"fr": "Zones de sécurité", "en": "Safe areas", "es": "Zonas seguras"},
    "menu.item.show_guides": {"fr": "Guides", "en": "Guides", "es": "Guías"},
    "menu.item.show_grid": {"fr": "Grille (tiers)", "en": "Grid (thirds)", "es": "Cuadrícula (tercios)"},
    "menu.item.show_center": {"fr": "Repère central", "en": "Center mark", "es": "Marca central"},
    "menu.item.viewer_snapping": {"fr": "Magnétisme du viewer", "en": "Viewer snapping", "es": "Imán del visor"},
    "menu.item.add_guide_h": {"fr": "Ajouter un guide horizontal", "en": "Add horizontal guide", "es": "Añadir guía horizontal"},
    "menu.item.add_guide_v": {"fr": "Ajouter un guide vertical", "en": "Add vertical guide", "es": "Añadir guía vertical"},
    "menu.item.clear_guides": {"fr": "Effacer les guides", "en": "Clear guides", "es": "Borrar guías"},
    "menu.item.lock_guides": {"fr": "Verrouiller les guides", "en": "Lock guides", "es": "Bloquear guías"},
    "menu.item.motion_blur": {"fr": "Flou de mouvement (séquence)", "en": "Motion blur (sequence)", "es": "Desenfoque de movimiento (secuencia)"},
    "menu.item.motion_blur_settings": {"fr": "Réglages du flou de mouvement…", "en": "Motion blur settings…", "es": "Ajustes del desenfoque…"},
    "menu.item.presets": {"fr": "Presets motion graphics", "en": "Motion graphics presets", "es": "Presets de motion graphics"},
    "menu.item.save_preset": {"fr": "Enregistrer la sélection comme preset…", "en": "Save selection as preset…", "es": "Guardar selección como preset…"},
}

__all__ = ["MOGRAPH_TRANSLATIONS"]
