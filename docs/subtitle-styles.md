# Styles de sous-titres : de l'éditeur à l'export

Un style (`core/text_style.TextStyle`) se règle dans l'éditeur de style ; l'**export** et l'**aperçu fidèle** le
traduisent en fichier ASS lu par libass (`core/subtitle_io.py`), le **moniteur** en dessine une approximation Qt
(`ui/preview_panel.py`). Ce document dit ce que chaque champ devient aux deux endroits.

Un clip au style standard passe par un fichier SRT et `force_style` ; dès qu'un style diffère, il passe par un ASS.

## Correspondance

| Champ du style | ASS (export) | Moniteur |
| --- | --- | --- |
| police, taille, couleur, opacité du texte | `Fontname`, `Fontsize`, `PrimaryColour` | police, corps, couleur |
| contour (couleur, épaisseur) | `OutlineColour`, `Outline` | bordure de l'étiquette (approximation) |
| ombre (`shadow_color`, `shadow_offset`) | `BackColour` (**la couleur de l'ombre en ASS**), `Shadow` | non dessinée |
| **fond** (`background_color`, `background_opacity`) | un **calque de boîte** sous le texte (ci-dessous) | fond de l'étiquette |
| marges internes du fond (`padding_x`, `padding_y`) | `\xbord` / `\ybord` du calque de boîte | marges de l'étiquette |
| alignement 3 × 3 | `Alignment` | alignement dans le cadre |
| marges (`margin_x`, `margin_y`, 0 à 80) | `MarginL/R/V`, en unités du script | marges, ramenées au cadre |
| **décalage** (`position_x`, `position_y`) | `\pos` | décalage du cadre |

Les unités du script ASS sont 1920 × 1080 (`PlayResX/Y`) : libass les ramène au cadre de la vidéo, donc une marge de
48 vaut 4,4 % de la hauteur quelle que soit la définition.

## Le fond : un calque de boîte

En ASS, `BackColour` est la couleur de l'**ombre**, pas celle du fond. Le fond se dessine avec `BorderStyle` 3 (boîte
opaque), où `OutlineColour` est la couleur de la boîte et `Outline` sa marge interne. Comme cette boîte remplace le
contour du texte, le fond est un **calque à part**, juste en dessous :

```
Dialogue: 0,…,DefaultBox,,…,{\xbord20\ybord8\pos(1152,978)}Bonjour    ← la boîte (texte invisible)
Dialogue: 1,…,Default,,…,{\pos(1152,978)}Bonjour                      ← le texte, avec son contour et son ombre
```

Le style `<nom>Box` reprend la police, la taille, l'alignement et les marges du style : la boîte épouse le texte. Un fond
n'existe que si le style a **une couleur de fond et une opacité non nulle**.

Avant ce calque, la couleur de fond était écrite dans `BackColour` : elle ne s'affichait jamais, et la couleur d'ombre du
style n'était jamais utilisée (l'ombre était toujours transparente).

## La position

`position_x` et `position_y` sont des **pourcentages du cadre**, positifs vers la droite et vers le bas. Le point de
base est celui de l'alignement et des marges (le bord, à la marge, ou le centre) ; le décalage s'y ajoute et s'écrit
`\pos(x,y)`. Sans décalage, rien n'est écrit : libass place le texte avec le style et les marges, comme toujours.

Le moniteur applique le même calcul, dans le cadre de la séquence, et suit le cadre quand la fenêtre change de taille.

## Limites

- Le fond d'un texte sur plusieurs lignes est une boîte **par ligne** dans libass ; le moniteur en dessine une seule
  autour du bloc.
- Le moniteur ne dessine ni l'ombre, ni un vrai contour de lettres (sa bordure entoure l'étiquette) : l'aperçu fidèle,
  rendu par libass, montre l'image exacte quand il est prêt.
- Les marges sont bornées à 80 par le modèle, alors que l'éditeur propose jusqu'à 200.

Tests : `tests/test_subtitle_box_position.py` (fichier ASS, rendu libass lu pixel par pixel, placement du moniteur),
`tests/test_libass_subtitles.py`, `tests/test_export_text_styles.py`.
