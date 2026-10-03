# Multicam

Un projet Kut-Studio peut regrouper plusieurs caméras et enregistreurs d'un même tournage dans une **source Multicam**,
les synchroniser (timecode, son, repère, début des clips ou à la main), les regarder ensemble, puis **monter le programme
en cliquant sur les angles pendant la lecture**.

> Ce document est d'abord le **contrat de conception** du chantier ; il est complété (guide utilisateur, performances,
> limites) à mesure que les morceaux sont livrés.

## Principe : rien de neuf à côté de l'existant

```
Projet
├── médias (partagés)
├── Séquence « Master »                      ← montage (séquence active)
│     V1 : [ Cam 1 ][ Cam 1 ][ Cam 3 ][ Cam 2 ]   ← clips imbriqués de « Concert », chacun avec un angle_id
└── Séquence « Concert »  (source Multicam)  ← Sequence.multicam décrit les angles
      V1 : Cam 1  ────────────────────────
      V2 : Cam 2        ──────────────────
      V3 : Cam 3            ──────────
      A1 : Recorder  ─────────────────────────
```

* Une **source Multicam** est une `Sequence` ordinaire (`core/project_model.py`) dont le champ `multicam`
  (`core/multicam_model.py`) liste les **angles**. Un angle = **une piste** : vidéo pour une caméra (le clip porte l'image
  et le son de la caméra, comme partout dans le projet) ou audio pour un enregistreur externe.
* Le **décalage de synchronisation d'un angle est la position de ses clips** dans la séquence. Il n'est stocké nulle
  part ailleurs : déplacer un angle à la main, c'est déplacer ses clips (annulable comme n'importe quel déplacement),
  la synchronisation automatique ne fait que proposer ces positions.
* Un **segment du montage** est un clip imbriqué ordinaire (`Clip.sequence_id` = la source) portant `Clip.angle_id`.
  Couper, déplacer, rogner, ajouter un effet ou une transition, imbriquer, dupliquer, annuler : tout fonctionne sans cas
  particulier. **Changer d'angle = couper à la tête de lecture et donner un autre `angle_id` à la moitié droite.**
* L'**étalonnage, la LUT, la transformation, le recadrage et la stabilisation d'une caméra** sont portés par le clip de
  l'angle *dans la source* : ils s'appliquent à toutes ses apparitions sans être recopiés à chaque coupe.
* **Un seul moteur de rendu** : le `RenderPlan` construit le sous-plan de la source en omettant les couches des angles
  inactifs (`core/multicam.py` : `TrackFilter`). Seul l'angle actif d'un segment est donc rendu ; deux segments du même
  angle partagent un sous-plan ; une transition entre deux angles en rend deux, naturellement.

## Modèle (`core/multicam_model.py`)

| Objet | Rôle |
| --- | --- |
| `MulticamSource` | `Sequence.multicam` : `angles` (ordre = numérotation), `audio` (politique), `sync_method`. |
| `MulticamAngle` | `id` stable (référencé par `Clip.angle_id`), `name`, `track_id`, `color_index` (rang dans la palette de l'interface, jamais une couleur), `sync_method`, `sync_status`, `sync_confidence`. |
| `MulticamAudio` | `mode` : `FOLLOW_VIDEO` (le son suit l'angle), `FIXED` (une source principale, `angle_ids[0]`), `MIX` (plusieurs sources). |
| `SyncMethod` | `timecode`, `audio`, `marker`, `start`, `manual`, `positions`. |
| `SyncStatus` | `none`, `excellent`, `good`, `uncertain`, `failed`, `manual`. |

Un angle inconnu (`angle_id` non vide qui ne désigne aucun angle) rend le segment **vide** et le signale
(`multicam_issues`) ; un `angle_id` vide désigne le premier angle. Les pistes qui n'appartiennent à aucun angle
(graphiques, sous-titres, calque d'effets ajoutés dans la source) sont **toujours** rendues.

## Synchronisation

Toutes les méthodes produisent la même chose : **une position de départ par angle** (en secondes, la plus petite = 0).

| Méthode | Source du décalage |
| --- | --- |
| Timecode | timecode de début lu par ffprobe (`core/timecode.py`) ; un média sans timecode ne bloque pas : il garde le début des clips et l'angle est marqué `none` |
| Audio | corrélation croisée locale (`core/audio_sync.py`, NumPy seul), avec score de confiance |
| Repère | un repère posé dans chaque clip de la timeline |
| Début des clips | tous les angles commencent à 0 |
| Positions actuelles | clips déjà alignés sur la timeline : leurs positions relatives sont gardées |
| Manuelle | l'utilisateur déplace l'angle ou saisit une valeur |

La confiance ne dit jamais « bonne » faute de pouvoir mesurer : `excellent` / `good` / `uncertain` / `failed`.

## Audio

La politique est un attribut de la **source** (`MulticamSource.audio`) : l'audio d'un segment est calculé à partir d'elle
au moment de construire le sous-plan, jamais recopié dans les clips. `FOLLOW_VIDEO` : le son de l'angle actif ;
`FIXED` : le son d'un angle choisi (typiquement l'enregistreur) quel que soit l'angle vidéo ; `MIX` : plusieurs sources.
Les coupes audio et vidéo restent dissociables (un clip imbriqué posé sur une piste audio n'apporte que le son).

## Ce chantier ne fait pas

Multicam dans une Multicam (un angle est un média ou une séquence ordinaire, jamais une autre source Multicam),
caméras distantes, capture en direct, synchronisation LTC, fingerprints avancés, proxys automatiques : l'architecture
les laisse possibles (voir « Architecture future »).
