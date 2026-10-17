# ADR-0007 : le modèle d'intentions — des objets paramétriques stockés, une sortie dérivée, un « détacher » explicite

**Statut :** Proposé le 2026-10-18 (à relire : aucune décision n'est encore prise, voir « Décisions à prendre »).
**Date :** 2026-10-18
**Décideurs :** mainteneur de Kut-Studio

Demande de l'utilisateur (idée d'octobre 2026, reprise le 2026-10-15) : pouvoir éditer **le résultat voulu** d'une action
plutôt que ses étapes (« le texte suit la voiture », avec un pourcentage de lissage et un décalage en pixels), et laisser
un utilisateur avancé **ouvrir la chaîne sous-jacente** (suivi 2D, transformation, lissage, décalage, composition). Le tout
déterministe, sans IA obligatoire. Les niveaux d'usage (ADR-0006, étape 4) sont livrés : cet ADR décide le **modèle de
données** qui doit les rendre utiles. Il ne code rien.

Faits relevés dans le code le 2026-10-18 (lecture seule). Ce qui n'a pas été vérifié est dit à chaque endroit.

---

## Résumé de la décision

1. Une **intention** est un objet de projet **immuable**, typé par fonctionnalité, qui ne stocke que des **paramètres
   sémantiques** et des références ; sa **sortie est dérivée** dans le plan de rendu par une fonction pure. Elle n'est
   jamais écrite dans le `.kut`. C'est exactement le motif de `TrackLink` : on le **nomme et on le généralise**, on ne
   l'invente pas.
2. On **ne crée pas de conteneur générique** `Intent(kind, params)`. Chaque intention garde sa classe, sa validation et sa
   migration ; elles partagent des **règles** (cinq invariants, ci-dessous) vérifiées par des tests communs.
3. **Détacher** (`bake_*`) est une action **explicite** qui écrit la chaîne dérivée en primitives éditables (keyframes,
   nœuds) puis retire l'intention. Une édition manuelle d'un résultat dérivé n'existe pas : on passe par les paramètres
   ou par « détacher ». Pas de divergence silencieuse.
4. La **forme simple est canonique** : une normalisation à l'édition (jamais au chargement) ramène l'intention à une
   primitive quand elle est équivalente, comme `simplify` le fait pour la couleur.
5. Le format `.kut` suit la règle déjà en place : **version minimale nécessaire**, un ancien build **refuse** ce qu'il ne
   comprend pas au lieu de l'ignorer.
6. **Une seule voie d'écriture** : les intentions se créent, se modifient et se détachent par les mêmes opérations de
   session que l'interface et le mode script (ADR-0005).

---

## Contexte : ce qui existe déjà

### 1. Une seule vraie intention : la liaison de suivi

- `TrackLink` (`core/tracking_model.py:611-644`) stocke `source_clip_id`, `tracker_ids`, `target`, position / rotation /
  échelle, `reference_index`, `enabled`, `continuation_ids`. **Aucune image clé** n'est stockée pour une liaison
  (`core/tracking_bindings.py:1-12`).
- La sortie est **dérivée** : `effective_clip_state` (`core/tracking_bindings.py:582`) calcule des keyframes linéaires
  simplifiées, appelées depuis le plan de rendu (`core/render_plan.py:533-535`).
- **Un « détacher » existe** : `bake_link` (`core/tracking_ops.py:567-582`) écrit les keyframes calculées dans
  `clip.transform_keyframes` / `clip.animation` puis retire la liaison. Une opération par liaison, sans retour hors
  historique. Le comportement du bouton dans l'interface n'a pas été vérifié.
- La **stabilisation** (`Stabilization`, `core/tracking_model.py:795-805`) suit le même motif (stockée, dérivée par
  `_compute_state`) mais **n'a pas de « détacher »** : seulement `remove_stabilization` (`core/tracking_ops.py:620`).

### 2. Des générateurs ponctuels, qui ne sont pas des intentions

Reframe 9:16 suivi (`core/follow_reframe.py:90-109`), Ken Burns (`core/ken_burns.py:85-100`), beat cut
(`core/beat_edit.py:22`), diaporama photo (`core/template_slots.py:211,232`) **écrivent le résultat** (keyframes, coupes,
clips) **sans rien garder de leurs paramètres**. Le résultat est éditable, mais on ne peut plus « ré-régler le curseur ».
C'est un choix cohérent pour un geste ponctuel ; ce n'en est pas un quand l'utilisateur veut réajuster.

### 3. Un précédent de forme simple canonique : la couleur

`simplify` (`core/color_nodes.py:386-393`) ramène un graphe à un seul nœud sans label, qualifieur, fenêtre, flou ni
netteté à son `ColorGrade`. Elle n'est appelée **qu'à l'édition** (`edit_nodes`, `core/color_grading.py:1425-1435`) et
garantit un fichier identique à l'ancien format pour un clip non découpé (`core/color_nodes.py:7`). Le retour arrière
fonctionne : retirer un nœud ou un label ramène à un nœud, que `simplify` rend à `ColorGrade`.
Limite relevée : un étalonnage neutre n'est pas normalisé vers « sans étalonnage » (`simplify` ne teste pas l'identité).

### 4. Un contre-exemple : la vitesse

`TimeRemapping.speed` (`core/time_remapping.py:81-93`) est la forme simple ; la courbe est une propriété `time.speed`
keyframée (`core/time_map.py:740-746`). `time_map_for` choisit entre `ConstantTimeMap` et `PiecewiseTimeMap`
(`core/time_map.py:758-777`). **Aucune normalisation** : une courbe à valeur constante reste une courbe (certitude
moyenne : lecture du code, aucun test exécuté).

### 5. Un détachement sans retour : « convertir en composition »

`convert_to_composition` (`core/composition_ops.py:157-227`) remplace des clips par un `CompositionGraph`, **sans garder
de référence** vers les clips d'origine : le retour passe par l'historique seulement. Il n'existe pas de fonction
inverse (non trouvée).

### 6. Le format `.kut`

Écriture à la **version minimale nécessaire** (`_build_payload`, `core/project_io.py:517-523` : 17 par défaut, 18 si un
masque courbe existe, 19 si un média a une matrice forcée). Un ancien build **refuse** une version inconnue
(`_validate_envelope`, `core/project_io.py:774-786`) ; les clés optionnelles sont absentes quand elles valent leur défaut
(`core/project_io.py:459-462`). Le niveau d'usage n'est jamais écrit dans le `.kut` (`docs/usage-levels.md:34`).

### 7. Les ADR voisins

- ADR-0006 : « Non destructif : aucune étape ne modifie un média ; tout est données du projet + rendu dérivé. »
  (ligne 115). Aucun texte du dépôt ne parle encore d'« intention » (recherche faite).
- ADR-0005 : « Un script déclaratif est une donnée, pas un programme » (R2), « une seule voie d'écriture : la session »
  (§4), versionné, champs inconnus = erreur.

---

## Décision

### 1. Définition

Une **intention** est une donnée de projet :

- **immuable** (comme `TrackLink`, `Clip.composition`) ;
- portée par un clip (ou un calque) et visant une **propriété cible** (position, échelle, vitesse, étalonnage…) ;
- composée de **paramètres sémantiques** (un lissage en %, un décalage en pixels, une source) et non de valeurs
  calculées ;
- dont la **sortie est calculée à la demande** dans le plan de rendu par une fonction pure
  (`effective_*`), jamais persistée.

Critère pour qu'une fonction soit une intention plutôt qu'un générateur : **l'utilisateur voudra-t-il ré-régler le
résultat plus tard ?** Oui : intention. Geste ponctuel (Ken Burns à l'ajout d'une photo, coupe sur les temps) :
générateur, inchangé.

### 2. Cinq invariants (testés pour chaque intention)

1. **Déterminisme** : mêmes paramètres et mêmes sources, même sortie, à l'image près. Pas d'aléatoire non fixé par
   l'identifiant, pas d'IA obligatoire.
2. **Sortie jamais stockée** : le `.kut` ne contient que les paramètres. Pas de cache périmé dans le projet.
3. **Équivalence** : la sortie dérivée est identique à celle d'une chaîne écrite à la main avec les mêmes primitives
   (test : `bake` puis rendu = rendu avant `bake`, à une tolérance fixée).
4. **Réversibilité de l'effet** : désactiver ou retirer l'intention rend le clip à l'état qu'il aurait sans elle.
5. **Version de la règle** : la dérivation porte un numéro (comme `RULE_VERSION` de `core/color_matrix.py`) ; changer
   l'algorithme sans le bumper est interdit. Sans cela, ouvrir un ancien projet dans une version plus récente changerait
   sa sortie sans que personne le voie.

### 3. Détacher

- Action **explicite** et **nommée** (`bake_<intention>`), alignée sur `bake_link` : écrit les primitives éditables,
  retire l'intention, **une** entrée d'historique, le retour passe par « Annuler ».
- **Pas d'édition manuelle d'une sortie dérivée.** L'interface affiche la sortie dérivée en lecture seule et propose
  « Modifier les paramètres » ou « Détacher ». Une édition manuelle qui détacherait en silence est refusée : c'est la
  divergence silencieuse que l'ADR veut éviter.
- Lacune à combler pour appliquer la règle : la **stabilisation** n'a pas de `bake`.

### 4. Forme simple canonique

Chaque intention déclare sa **forme simple** (la primitive équivalente) et une fonction de normalisation **appelée à
l'édition seulement**, jamais au chargement (un chargement ne réécrit rien : il est sans perte). Règle de non-régression :
un fichier sans intention est identique octet pour octet à celui d'avant. Lacune à combler : la **vitesse** (courbe
constante), à traiter quand une intention de vitesse existera.

### 5. Persistance et versions

Règle de `core/project_io.py` appliquée telle quelle : un nouveau type d'intention ajoute des clés **optionnelles
absentes à leur défaut** et n'élève la version du fichier que s'il y en a une. Un ancien build refuse ; il ne perd rien
en silence.

### 6. Une seule voie d'écriture

Créer, modifier et détacher une intention passent par des **opérations de session** (ADR-0005). Le mode script produit
les mêmes opérations que l'interface, avec les mêmes validations. Un script ne peut donc pas écrire une sortie dérivée.

### 7. Interface : pas de nouveau mode global

Les intentions apparaissent dans l'inspecteur comme les autres groupes, classées par niveau d'usage : paramètres au
niveau **Studio**, « Détacher » au niveau **Avancé** (même mécanisme que `docs/usage-levels.md`, mêmes tests
d'énumération). Un interrupteur global « mode Studio / mode Avancé » en plus des trois niveaux n'est **pas proposé**.

---

## Options étudiées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| **A. Conteneur générique** `Intent(kind, params)` | une seule classe, une seule persistance | typage faible, validation et migration par `kind` à la main, erreurs à l'exécution | écarté |
| **B. Objets typés par fonctionnalité** partageant cinq invariants | motif déjà en place (`TrackLink`), validation typée, migrations locales | un peu de répétition | **retenu** |
| **C. Statu quo** (générateurs seulement) | rien à faire | pas de curseur ré-ajustable, résultats éditables mais sans mémoire de l'intention | écarté pour les cas où l'on ré-ajuste |
| **D. Tout en graphe de nœuds** (ADR-0002) | un seul modèle | lourd pour des cas simples, la forme simple disparaît | écarté ; le graphe reste une **cible possible de « détacher »** |

---

## Conséquences

**Positives.** Un vocabulaire commun pour ce qui existe (liaison de suivi, stabilisation) et ce qui vient ; les
générateurs ponctuels restent légers ; le mode script n'a pas de nouvelle surface ; la compatibilité des fichiers suit des
règles déjà éprouvées.

**Négatives et risques.**
- La dérivation se recalcule à chaque construction du plan : le coût existe déjà pour le suivi (non mesuré ici) et devra
  être mesuré pour chaque nouvelle intention (cache d'aperçu : `RENDER_ENGINE_VERSION`).
- La règle 5 (version de la dérivation) est nouvelle pour le suivi : il faudra choisir son premier numéro sans changer les
  projets existants.
- Interdire l'édition manuelle d'une sortie dérivée est un choix d'interface qui peut déplaire : la voie de sortie est
  « Détacher ».
- Normaliser à l'édition seulement laisse des fichiers en forme non simple s'ils ont été écrits ainsi : accepté, car le
  chargement ne doit rien réécrire.

---

## Plan d'action (chaque étape est une PR à part, fusionnable seule)

0. **Cet ADR** (documentation seulement).
1. **Formaliser le motif sur `TrackLink`** : tests communs des cinq invariants (équivalence `bake` / rendu, sortie jamais
   dans le `.kut`, déterminisme), sans changer le comportement ni le format. Choisir la version initiale de la dérivation.
2. **`bake_stabilization`** : le « détacher » manquant, avec son test d'équivalence.
3. **Choisir le premier nouveau cas** parmi : « le texte suit un sujet » avec lissage et décalage (l'exemple de la demande),
   une intention de vitesse (avec sa normalisation), ou un reframe 9:16 qui garde ses paramètres. Décision à prendre avec
   l'utilisateur à ce moment, sur mesure d'usage et non sur principe.
4. **Interface** : groupe d'inspecteur classé par niveau, « Détacher » en Avancé, entrées dans le manifeste d'énumération.
5. **Mode script** : verbes de session pour créer, modifier et détacher.

---

## Décisions à prendre (par l'utilisateur, avant d'accepter cet ADR)

1. Accepte-t-on **l'option B** (objets typés partageant des invariants) plutôt qu'un conteneur générique ?
2. Accepte-t-on d'**interdire l'édition manuelle d'une sortie dérivée** (voie unique : paramètres ou « Détacher ») ?
3. Quel est le **premier cas** de l'étape 3 : le texte qui suit un sujet, la vitesse, ou le reframe 9:16 ?
4. Le principe « version de la dérivation » (invariant 5) est-il acceptable pour le suivi, qui n'en a pas aujourd'hui ?

## Limites de cet ADR

Lecture du code seule : aucun test exécuté, le comportement du bouton « détacher » dans l'interface n'a pas été vérifié,
le coût de la dérivation n'a pas été mesuré, et `docs/tracking.md`, `docs/composition.md`, `docs/time-remapping.md` n'ont
pas été lus. L'absence de fonction inverse de « convertir en composition » repose sur une recherche, pas sur une
preuve.
