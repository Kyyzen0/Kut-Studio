# Design QA — passe de polish visuel

> Ce document dit **ce qui a été changé, ce qui a été vérifié et ce qui ne l'a pas été**. Il ne déclare pas que le design est
> « beau » : aucun test ne peut le faire. Les tests gardent des contrastes, des jetons, de la géométrie et des états ; le rendu
> final doit être regardé par une personne (voir « À valider à l'œil »).

La passe a été faite en quatre jalons (PR #32 : jalons 1 à 3, cette branche : jalon 4 et suite). Aucune architecture n'a été
refaite, aucune fonctionnalité n'a été ajoutée, et aucun comportement métier n'a changé.

## 1. Le système de design

Une seule source pour chaque valeur visuelle :

| Fichier | Rôle |
|---|---|
| `ui/design_system.py` | Jetons de **mesure** : espacements (échelle 4/8/12/16/24/32), rayons, tailles de contrôle, icônes, typographie (7 rôles), graisses, mouvement, variantes de bouton, états (`idle` / `working` / `success` / `warning` / `error`), `DIALOG_MARGINS`. |
| `ui/theme.py` | Jetons de **couleur** : `ThemePalette` (sombre, clair, système), feuille de style globale construite sur ces jetons, `qt_palette` (publie la palette Qt), `OVERLAY` (repères posés sur l'image). |
| `ui/theming.py` | Changement de thème **à chaud** : remplace les couleurs des styles locaux jeton par jeton. |
| `ui/panel_header.py`, `ui/empty_state.py`, `ui/search_field.py`, `ui/properties_widgets/section_box.py` | Composants communs : bandeau de panneau, état vide, champ de recherche, section repliable. |
| `ui/overlay_paint.py` | Halo sombre sous chaque repère dessiné sur l'image. |

Chiffres (avant la passe → maintenant) :

- `setStyleSheet` locaux dans `ui/` : **301 → 271** ;
- couleurs hexadécimales codées en dur hors `ui/theme.py` : **72 → 26** (les restantes sont des valeurs de **données** : couleur de
  texte par défaut, clés d'incrustation, l'overlay de débogage) ;
- tests de jetons : `tests/test_design_tokens.py` (contrastes, thèmes complets, jetons obligatoires, pas de valeurs critiques
  dupliquées), `tests/test_theme_live_switch.py` (un thème changé en direct est identique, au pixel près, à un démarrage dans ce thème).

Les deux thèmes ont été corrigés **par calcul de contraste** (texte ≥ 7:1, texte secondaire ≥ 4,5:1, texte désactivé ≥ 3:1,
graphiques et contrôles ≥ 3:1, filets ≥ 2:1) ; le thème clair n'est pas une inversion du sombre (il a ses propres surfaces et son
accent plus profond).

## 2. Zones modifiées

- **Timeline** : couleur de clip par catégorie (vidéo, audio, titre, graphique, imbriqué, cassé) et non plus par identifiant ;
  sélection, survol et désactivé distincts ; en-têtes de pistes alignés (nom, type, états) ; règle avec graduations secondaires ;
  miniatures discrètes ; forme d'onde sous la bande du nom ; losanges d'image-clé (repos, survol, sélection).
- **Inspecteur** : sections repliables (clavier compris), un seul langage de losange d'image-clé (la transformation avancée
  réutilise le bouton de l'inspecteur), rangée des préréglages de vitesse sans chevauchement.
- **Visionneuse** : en-tête commun, résumé de résolution aligné, repères (guides, zones de sécurité, grille, poignées) lisibles
  sur image claire comme sombre.
- **Éditeur de courbes** : grille à deux niveaux, unité sur l'axe, curseur de lecture au jeton de la timeline, losanges et
  poignées Bézier discrets.
- **Multicam** : le Programme (contour neutre fort, repère inversé), l'angle actif (anneau d'accent **et** le mot « ACTIF »), les
  sources inactives (filet discret). La couleur d'angle n'est jamais le seul signal.
- **Suivi** : une mesure douteuse se lit à son rond pointillé, une mesure perdue à sa croix ; la pastille d'un tracker dit sa santé ;
  boutons à icône.
- **Export et file de rendu** : variantes de bouton, états, barre de progression unique, état vide.
- **Bibliothèque** : champ de recherche commun, titre qui suit la page, carte de média (le marqueur « manquant » ne recouvre plus le
  badge d'usage), icônes à la place des glyphes (favoris, calques, séquences).
- **Préférences et dialogues** : marges communes, onglet Performance défilant, « Fermer » traduit, dialogue de texte qui suit le thème.
- **Outils** : `tools/capture_ui.py` (captures de référence), `tools/ui_audit.py` (audit structurel).

## 3. Problèmes trouvés et corrigés

Quelques-uns, concrets, trouvés **en regardant les captures** (pas par les tests) :

1. Dans les dialogues sombres, des **panneaux presque blancs** (liste des tags, lignes de sources Multicam, texte quasi invisible
   dessus). Cause : l'application ne posait que du QSS, jamais de `QPalette` ; tout widget sans fond explicite prenait la palette
   claire du système. Correction à la racine : `qt_palette`.
2. L'onglet **Performance** des préférences : les listes du bloc « Matériel » étaient écrasées à 16 px et illisibles. Il défile désormais.
3. La rangée des préréglages de vitesse **recouvrait la section suivante** après un changement d'onglet (défaut présent avant la
   passe ; révélé par les polices de Linux). Le libellé passe au-dessus de la rangée, qui prend toute la largeur.
4. Le triangle « média manquant » était peint **par-dessus** le badge « ×N » de la carte.
5. Le curseur de lecture de l'éditeur de courbes était **rose** (un jeton d'erreur) alors que celui de la timeline est en accent.
6. Le titre du panneau de bibliothèque restait « Médias » sur la page Transitions ou Effets.
7. Le gestionnaire de tags affichait « **Close** » en anglais dans une interface française.
8. Les glyphes texte utilisés comme icônes (◀◀ ▶▶ ■ ★ ☆ ● ○ ◆ ◇ ◈) sont remplacés par des icônes de la famille de l'interface, qui
   suivent le thème.
9. Le changement de langue laissait des initiales tronquées dans le rail latéral (corrigé au jalon 2).

## 4. Captures et outils

```bash
python -m tools.capture_ui --out <dossier>                                  # sombre + clair, 4 tailles, toutes les scènes
python -m tools.capture_ui --out <dossier> --themes dark --sizes 1440x900   # un thème, une taille
python -m tools.capture_ui --out <dossier> --scale 2                        # HiDPI simulé
python -m tools.ui_audit --sizes 1180x720,1280x720,1440x900,1920x1080       # audit structurel
```

Une capture par `<thème>/<largeur>x<hauteur>/<scène>.png` et un `manifest.json`. Scènes : accueil, éditeur, timeline, un onglet
de l'inspecteur par capture (clip, couleur, effets, audio, graphiques, compositing, suivi), une page de bibliothèque par capture
(médias, audio, effets, transitions, texte, séquences, graphiques…), export et file de rendu, moniteur Multicam, éditeur de
courbes, préférences. 23 scènes par thème et par taille. **Chaque fenêtre démarre dans une configuration neuve** : l'outil ne lit ni
n'écrit les réglages de l'utilisateur (`ui_audit.isolate_user_config`).

## 5. Tailles et environnements testés

- **Audit structurel** (`tools/ui_audit.py`, 0 constat bloquant) : 1180×720, 1280×720, 1440×900, 1920×1080, 2560×1080 (écran large),
  polices agrandies de 20 % (1180×720 et 1280×720), parcours clavier de tous les dialogues.
- **HiDPI** : captures à l'échelle ×2 (éditeur et inspecteur, sombre et clair) relues pour la netteté des icônes et le texte.
- **CI** : macOS, Ubuntu et Windows, Python 3.11 (suite complète, smoke test).
- **Linux** : reproduit en local avec une police large (Verdana, proportions proches de DejaVu) ; non regardé sur une vraie machine Linux.

## 6. Ce que les tests garantissent, et ce qu'ils ne garantissent pas

Garanti : contrastes des deux palettes, présence des jetons, identité d'un thème changé en direct, absence de clipping, de
débordement et de chevauchement aux tailles ci-dessus, ordre de tabulation, noms accessibles des boutons-icônes, lisibilité des
repères Multicam dans les deux thèmes, absence de chevauchement des préréglages de vitesse.

**Non garanti** : l'équilibre visuel, la hiérarchie perçue, le goût. Aucune de ces questions n'a de test.

## 7. Performance

Le banc d'interface (`python -m tools.perf.bench`, la timeline réelle sous un projet synthétique de 100, 1 000 et 10 000 clips) a été
rejoué sur le dernier commit **avant** la passe (`d7315f1`) et sur la branche, dans le même état de machine.

- **Première mesure : une régression réelle.** `refresh_clip_widgets` était environ **1,45× plus lent** dans les neuf scénarios
  (zoom et défilement : ×1,2). Le profil l'a localisée en une minute : 792 appels à `setStyleSheet` (trois par clip) pour 87 % du
  temps. Qt reparse et repolit la feuille à chaque appel, **même avec un texte identique**.
- **Correction** : `ui.theme.set_stylesheet_if_changed` n'écrit la feuille que si elle a changé. Un rafraîchissement sans
  changement d'un clip passe de 156 à 11 ms (3 appels, 264 clips).
- **Après correction** (223 mesures) : une seule reste plus lente que la base de plus de 15 % et 0,3 ms (une sélection au lasso de
  1 000 clips : 1,2 → 1,7 ms) ; 24 sont plus rapides de plus de 30 % (création des clips, zoom, défilement, chargement de projet).
- Les tests `tests/test_design_components.py` gardent la propriété : un rafraîchissement sans changement n'écrit aucune feuille.

Les durées absolues dépendent de la machine ; ce qui compte est le rapport entre les deux arbres, mesurés l'un après l'autre.

## 8. Limites restantes

- **Pas de contrôle de zoom de la visionneuse** (Fit / 50 % / 100 %) : il n'existe pas aujourd'hui, et l'ajouter est une
  fonctionnalité, hors périmètre de cette passe.
- Deux **glyphes texte** subsistent par choix, couverts par des tests : le point de « ● Enregistré » dans la barre du haut, et le
  « ⚠ » de l'en-tête des cartes de la bibliothèque.
- **Menus contextuels, infobulles, notifications** : couverts par la feuille de style globale (rayons, surfaces, séparateurs),
  mais pas relus un par un. Aucun système de toast n'a été ajouté.
- **Animations** : aucune n'a été ajoutée (la passe n'en a pas trouvé d'utile).
- Les **repères du suivi** (rond pointillé, croix) sont dessinés d'après le code et testés par leurs états ; ils n'apparaissent pas
  dans les captures de référence (aucun tracker posé dans la scène de démonstration).
- Les **préférences générales** restent une longue colonne de groupes de boutons radio, lisible mais pas restructurée.
- 271 `setStyleSheet` locaux subsistent ; la plupart posent des couleurs du thème (donc suivent le changement de thème à chaud).

## 9. À valider à l'œil

À regarder par une personne, thème sombre **et** thème clair, à 1440×900 puis à 1180×720 :

1. la timeline : lisibilité des clips (nom, catégorie, sélection), densité des en-têtes de pistes, losanges, forme d'onde ;
2. la visionneuse : cadre, transport, repères sur une vraie image claire ;
3. l'inspecteur : sections repliables, champs numériques, losanges ;
4. le Multicam : l'angle actif se voit-il d'un coup d'œil sans être agressif ? le Programme se distingue-t-il ?
5. l'éditeur de courbes : grille, courbe, poignées ;
6. les dialogues et les préférences ;
7. le thème clair dans son ensemble (il a été réglé par contraste, pas encore jugé « agréable »).
