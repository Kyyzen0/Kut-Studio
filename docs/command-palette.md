# Rechercher une fonction (palette de commandes)

Un champ de recherche qui trouve **n'importe quelle fonction du programme par son nom**, même cachée dans un menu : on tape ce qu'on veut faire (« suivi », « couper », « sous-titres », « couleur »), on choisit, on ouvre.

## Pour l'utilisateur

- **Ouvrir** : le bouton « Rechercher… » de la barre du haut, ou le raccourci **Ctrl+Maj+P** (⇧⌘P sous macOS). Le raccourci se change dans Préférences ▸ Raccourcis (commande « Rechercher une fonction »).
  *Pourquoi pas Ctrl+K / ⌘K ?* Ce raccourci est déjà « couper à la tête de lecture » (convention de montage) : le reprendre aurait cassé un geste courant.
- **Fermer** : Échap, le bouton « Fermer », un clic en dehors de la boîte ou le raccourci d'ouverture. Le focus revient où il était.
- **Chercher** : la casse et les accents sont ignorés (`etalonnage` trouve « Étalonnage »). La recherche porte sur le titre, l'indication (le chemin du menu), les mots-clés et les titres **anglais** (une interface en français trouve « tracking »). Les synonymes sont reconnus : couper / cut / split, suivi / track, sous-titres / captions / srt, couleur / étalonnage / color, vitesse / speed, export / exporter (liste : `core/command_search.py`, `SYNONYMS`).
- **Choisir** : ↑ ↓ pour se déplacer, Entrée pour ouvrir ; six résultats au plus. Chaque résultat montre une icône, le titre, une indication et son raccourci.
- **Fonction grisée** (par exemple « Couper » sans clip) : affichée en grisé, avec la raison quand elle est connue, sans bouton.
- **Aller à…** : les pages (Montage, Composition, Couleur, Audio), les onglets de l'inspecteur (Clip, Couleur, Effets, Audio, Graphiques, Compositing, Suivi) et les bibliothèques (Médias, Texte et sous-titres, Effets, Transitions, Sons, Graphiques, Séquences) sont aussi des résultats.
- Les cinq dernières fonctions choisies s'affichent quand le champ est vide (gardées en mémoire pour la session ; aucun réglage n'est écrit).

## Niveaux d'usage : une exception voulue

Les menus du niveau **Simple** n'affichent aucun outil du niveau supérieur (voir [usage-levels.md](usage-levels.md)). La palette, elle, **les montre** : c'est la découvrabilité (« on ne voit strictement rien »).

| Résultat | Bouton | Effet |
|---|---|---|
| du niveau courant ou d'un niveau inférieur | « Ouvrir » | lance la fonction |
| d'un niveau **supérieur** | « Passer en Studio » / « Passer en Avancé » | change le niveau (Préférences, signal et rangement habituels, `MainWindow.set_usage_level`) **puis** lance la fonction |
| grisé dans le contexte | aucun | rien ne se lance |

Entrée sur une ligne verrouillée fait la même chose que son bouton. Le changement est réversible à tout moment (Fenêtre ▸ Niveau d'usage, Préférences, puce de la barre du haut). Le niveau d'usage ne désactive toujours rien : la palette ne contourne ni les verrous de piste ou de calque, ni l'état « grisé » d'une commande.

## Pour le développeur

| Pièce | Fichier |
|---|---|
| Recherche **pure** (normalisation, synonymes, score, classement par niveau, bornes) | `core/command_search.py` |
| Boîte (voile + champ + puces + liste), sans connaissance de la fenêtre | `ui/command_palette.py` |
| Registre, bouton de la barre du haut, exécution | `ui/main_window_mixins/command_palette.py` |
| Textes fr / en / es | `ui/i18n_palette.py` |

- **Le registre est lu dans les menus** à chaque ouverture : toute nouvelle entrée de menu est trouvable sans maintenance. Pour chaque `QAction` : titre traduit (et titre anglais quand la clé de traduction est connue), raccourci, chemin de menu, état activé, **niveau** lu dans `MENU_GATES` (`menu_levels.py`) y compris pour les entrées que le niveau courant a retirées du menu (`_LevelMenu.items`) ; les entrées d'un sous-menu de niveau supérieur héritent de son niveau. Les trois actions de page du menu Fenêtre sont remplacées par « Aller à la page … ».
- Les destinations « Aller à… » ont leur niveau lu dans `PAGE_LEVELS`, `SECTION_LEVELS` et `_group_levels` de l'inspecteur.
- La commande `command_palette` est une action de **fenêtre** sans entrée de menu (le bouton l'ouvre aussi) : ajouter une entrée au menu Fenêtre aurait changé la référence d'Avancé (`tests/data/menus_before_usage_levels.json`).
- **Entrées bornées** : le champ accepte 200 caractères, la recherche examine 8 mots et 5 000 entrées au plus ; un message d'erreur ne recopie jamais la saisie en entier.
- **Aucune écriture** : ouvrir ou fermer la palette ne touche ni au projet, ni à l'historique (annuler / rétablir), ni à la mise en page.
- Le widget est créé une fois à la construction de la fenêtre, enfant de la fenêtre (jamais de `replaceWidget` / `setParent` sur un widget existant).
- Tests : `tests/test_command_search.py` (modèle pur), `tests/test_command_palette.py` (fenêtre hors écran : ouverture par le raccourci et le bouton, niveaux, clavier, langues, historique).
