# `Track.automation` : une seule représentation

> **Statut** : la courbe s'édite dans la timeline (2026-10-09) — voir « Édition dans la timeline » en fin de document.
> Le modèle, le plan de rendu et l'export de l'automation (et du ducking) sont vivants et testés.

Point ouvert du rapport de stabilisation : « `Track.automation` a deux représentations (liste de points ou
`TrackAutomation`) ; la normalisation est faite à l'usage plutôt qu'au chargement ».

## Avant

Un projet fraîchement chargé (ou une affectation `track.automation = [...]`) portait une **liste** de `AutomationPoint` ;
dès que `AudioAutomationService` touchait la piste, la liste devenait une **`TrackAutomation`** (`object.__setattr__`). Chaque
lecteur devait donc tester les deux formes : le plan de rendu (`hasattr(.., "points")` puis `isinstance(.., list)`),
`project_io` (`_iter_automation_points`), `sequences` (`getattr(track.automation, "points", track.automation)`) et plusieurs
tests. Oublier un lecteur, c'était perdre une courbe (c'est arrivé : le premier réglage après l'ouverture effaçait toute la
courbe).

## Maintenant

**La forme canonique est `core.audio_automation.TrackAutomation`**, sur chaque piste, vide par défaut.

* **Garantie par le modèle** : `Track.__setattr__` convertit tout ce qu'on affecte à `automation`
  (`core.audio_automation.coerce_track_automation`). C'est l'unique point d'écriture : le constructeur
  (`Track(automation=[...])`), `track.automation = [...]`, `project_io` et le code ou les tests anciens y passent. Une
  `TrackAutomation` est conservée telle quelle (même objet, les éditions en place restent visibles), `None` donne une courbe
  vide, une liste ou un tuple de points est enveloppé et trié, tout autre type lève `TypeError`. La conversion est idempotente.
* **Pourquoi `__setattr__` plutôt qu'une propriété ou `__post_init__`** : `Track` est une `dataclass` non figée dont les champs
  sont lus par `fields()`, `==`, `repr` et `copy` ; une propriété doublerait le champ (`_automation`) et casserait ces mécanismes,
  et `__post_init__` ne couvre pas les affectations ultérieures. `copy.copy`, `copy.deepcopy` et l'historique
  (`ProjectHistory`, qui fait des `deepcopy`) restaurent `__dict__` sans repasser par `__setattr__`, et copient du contenu déjà
  canonique.
* **Chargement** : `project_io._deserialize_track` fabrique la `TrackAutomation` depuis la liste de points du fichier (points
  invalides écartés, tri) ; un fichier d'une version sans automation donne une courbe vide. La sérialisation lit
  `track.automation.points`.
* **Chemins « à double forme » supprimés** : `render_plan._build_audio_layer`, `project_io._iter_automation_points`,
  `sequences.create_sequence_from_selection` (lecture et écriture de la courbe recalée), `AudioAutomationService.ensure_automation`
  (plus rien à envelopper). Un test interdit leur retour.
* **`TrackAutomation.track_id`** n'est plus validé (`[A-Za-z0-9._:-]`) ni comparé : c'est une étiquette pour les messages
  d'erreur. Il le fallait : maintenant que *toute* piste porte une courbe, un projet dont une piste s'appelle « Piste 1 » ne doit
  pas cesser de s'ouvrir.

## Inchangé

Le format `.kut` : `automation` reste une liste de `{time_seconds, gain_db, fade_seconds}`, `CURRENT_VERSION` (16) et
`SUPPORTED_VERSIONS` ne bougent pas, et `save → load → save` donne le même fichier. Le seul écart observable concerne des
données **construites en mémoire** : une liste de points non triée affectée à une piste est désormais triée (un fichier chargé
l'était déjà) ; l'export, qui triait lui-même, produit les mêmes chaînes FFmpeg (comparées avant et après sur un projet à trois
courbes).

## Limites

* Écrire `object.__setattr__(track, "automation", [...])` contourne la normalisation : plus aucun appelant ne le fait.
* Les modules typés (mypy) doivent affecter une `TrackAutomation` : la liste n'est acceptée qu'à l'exécution, par tolérance.

## Édition dans la timeline (2026-10-09)

**La courbe.** Une bande sous les clips d'une piste audio (`ui/timeline_widgets/automation_lane.py`), affichée dès que la
courbe a des points ou à la demande (menu ⋯ de la piste › « Courbe de volume » ; ce choix d'affichage n'est ni dans le
`.kut` ni dans l'historique). Double-clic : un point au gain et à l'instant pointés (aimanté comme un clip, calé sur une
image) ; glisser : temps et gain sans dépasser les voisins (Maj : gain seul) ; Suppr ou le menu contextuel : supprimer,
remettre à 0 dB, effacer la courbe. Un geste, une entrée d'historique. La bande trace
`core.audio_automation.automation_pieces`, la fonction dont l'export tire son expression `volume` : ce qu'on voit est ce
qu'on entend. Échelle verticale : −24 à +12 dB (`DEFAULT_GAIN_MIN_DB` / `DEFAULT_GAIN_MAX_DB`).

**Sémantique du maintien (`fade_seconds`).** Entre deux points, le gain précédent est tenu `fade_seconds` (celui du point
d'arrivée) puis rejoint le point d'arrivée en ligne droite. Jusqu'au 2026-10-09, un maintien **nul** était un cas à part :
le gain sautait à celui du point d'arrivée juste après le point précédent, alors qu'un maintien d'une microseconde donnait
presque la ligne droite. Un maintien nul est désormais la ligne droite (la courbe varie continûment avec le maintien), ce
que pose l'éditeur ; un maintien qui atteint le point d'arrivée reste un saut à son instant. Aucune interface ne créait
de points avant cette date : seuls des projets écrits à la main ou par script peuvent changer d'écoute.

**Correctif d'export au passage.** Un clip qui commençait au milieu d'une rampe recevait `t-(-inf)` dans son expression
`volume` (la borne extérieure du premier morceau était remplacée par −∞ avant d'écrire la rampe) : FFmpeg refusait le
graphe. La rampe garde maintenant ses vraies bornes, seule sa condition extérieure saute
(`test_a_rubber_band_ramp_is_heard_from_a_clip_that_starts_in_its_middle`, rendu réel).

**Menu ⋯ d'une piste audio.** « Rôle de la piste » (voix, musique, effets sonores, autre : ce que lisent le ducking
automatique, les sous-titres automatiques et la coupe au rythme) et « Baisser sous… » (ducking de cette piste sous une
autre, réglage par défaut ; les presets du menu Réseaux sociaux › Audio règlent ensuite toutes les paires).

