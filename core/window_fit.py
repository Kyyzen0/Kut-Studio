"""Fait tenir une fenêtre dans la zone utilisable d'un écran (fonctions pures, sans Qt).

Une taille ou une position enregistrée sur un grand écran (un Mac mini en 4K) ne vaut rien sur un portable : restaurée telle
quelle, la fenêtre dépasse l'écran et le Dock passe par-dessus la timeline. Tout ce qui vient d'un réglage ou d'une taille
d'origine passe donc par :func:`fit_rect` avant d'être appliqué.
"""

from __future__ import annotations

#: Marge réservée à la barre de titre d'une fenêtre (Qt compte la taille du contenu, pas celle du cadre) et à l'ombre.
FRAME_ALLOWANCE: int = 40

Rect = tuple[int, int, int, int]            # x, y, largeur, hauteur


def fit_rect(rect: Rect, available: Rect, *, frame: int = 0, center: bool = False) -> Rect:
    """Borne ``rect`` à la zone utilisable ``available`` (taille d'abord, puis position).

    * la largeur et la hauteur ne dépassent jamais celles de la zone (moins ``frame`` px de hauteur pour la barre de titre) ;
    * la fenêtre est ramenée entièrement dans la zone ; une fenêtre hors de tout écran revient donc dans l'écran ;
    * ``center`` : on la centre (taille d'origine, aucune position enregistrée).

    Les valeurs absurdes (négatives, nulles) donnent une fenêtre d'au moins 1 px, jamais une exception.
    """
    x, y, width, height = (int(value) for value in rect)
    ax, ay, aw, ah = (int(value) for value in available)
    aw, ah = max(1, aw), max(1, ah)
    top = ay + min(frame, ah - 1)                  # le contenu commence sous la barre de titre
    width = max(1, min(width, aw))
    height = max(1, min(height, ay + ah - top))
    if center:
        x = ax + (aw - width) // 2
        y = top + (ay + ah - top - height) // 2
    x = max(ax, min(x, ax + aw - width))
    y = max(top, min(y, ay + ah - height))
    return x, y, width, height


def pick_screen(rect: Rect, screens: list[Rect], preferred: Rect | None = None) -> Rect | None:
    """Choisit l'écran (zone utilisable, coordonnées logiques) où restaurer ``rect``.

    Celui qui recouvre le plus le rectangle enregistré gagne (égalité : le premier de la liste) : un panneau enregistré sur
    un moniteur secondaire toujours branché y reste. Si le rectangle ne rencontre plus aucun écran (moniteur débranché),
    repli sur ``preferred`` (l'écran de la fenêtre principale) puis sur le premier écran. Liste vide : ``None`` (l'appelant
    garde alors le rectangle tel quel, sans lever).
    """
    if not screens:
        return None
    x, y, width, height = (int(value) for value in rect)
    best: Rect | None = None
    best_area = 0
    for screen in screens:
        sx, sy, sw, sh = screen
        overlap_w = min(x + max(width, 0), sx + sw) - max(x, sx)
        overlap_h = min(y + max(height, 0), sy + sh) - max(y, sy)
        area = overlap_w * overlap_h if overlap_w > 0 and overlap_h > 0 else 0
        if area > best_area:
            best, best_area = screen, area
    if best is not None:
        return best
    return preferred if preferred is not None else screens[0]


__all__ = ["FRAME_ALLOWANCE", "fit_rect", "pick_screen"]
