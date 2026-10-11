# Page Audio et mixage en direct

Étape 5 de l'[ADR-0002](adr/0002-socle-nodal-etalonnage-composition-pages.md), première partie : **entendre le vrai
mixage pendant la lecture**, le voir sur de vrais vumètres, sur une page dédiée.

## Avant

Le moniteur temps réel n'a qu'un lecteur (un `QMediaPlayer`) : en lecture, on n'entendait que le son du clip qu'il
montre. Ni les pistes audio (musique, voix off), ni le mixage (volume, pan, mute, solo, automation, effets, ducking,
Master) ne s'entendaient avant l'export. Le mixeur n'avait pas de vumètre : aucun niveau n'était mesuré.

## Utilisation

- **Page Audio** (quatrième page, après Montage, Couleur et Composition) : le bouton *Audio* au centre de la barre
  supérieure, *Fenêtre › Page Audio*, ou l'entrée *Audio* du rail. Le mixeur et ses vumètres sous un moniteur plus
  petit, avec la timeline ; l'inspecteur sur son onglet Audio, les médias à gauche. La page garde la disposition qu'on
  lui laisse, comme les autres.
- **En lecture, sur toutes les pages**, on entend le mixage de l'export à partir de la tête de lecture : ce que
  l'export produira, à l'échantillon près.
- **Vumètres** : chaque tranche montre la crête de sa piste (après fader, pan, automation et ducking) ; le Master
  montre la crête et le RMS de ce qui sort, et la sonie BS.1770 momentanée (400 ms) et à court terme (3 s), en LUFS.
  Vert, jaune au-dessus de −18 dBFS, rouge au-dessus de −6 dBFS ; un trait garde la crête 1,5 s. À l'arrêt, tout
  retombe : jamais de niveau estimé.

## Fonctionnement

```
lecture ─▶ plan audio (fenêtre : tête − 3 s → fin, sans image, Master compris)
        ─▶ graphe audio de l'export (origine audio, points de mesure par piste)
        ─▶ FFmpeg : PCM flottant 48 kHz sur sa sortie standard
        ─▶ fil de lecture : vumètres (MixMeter), tampon de 2 s
        ─▶ QAudioSink (mode « push ») : la sortie audio
```

- **Un flux par lecture, pas des morceaux** (`core/live_mix.py`). Un processus FFmpeg rend le graphe audio de l'export
  (`ExportEngine._build_filter_complex(audio_only=True)`), en continu. Les effets qui ont une mémoire (compresseurs,
  ducking, écho) ne sont jamais coupés à une jonction. Pause, saut ou modification relancent le flux.
- **Origine audio** (`audio_origin`). Le mixage commençait toujours au début de la timeline, et le son avant la tête
  était calculé puis jeté : rendre 10 s à 15 min de la timeline coûtait 1,95 s (deux pistes simples, M4), contre
  0,06 s au début. Le graphe part maintenant d'une origine : une couche qui commence après est retardée d'autant, une
  couche déjà en cours est **coupée avant sa chaîne** (`atrim` du début à l'origine : ni les effets ni le volume ne
  traitent ce qui précède), les bases silencieuses et le ducking durent ce qui reste. Le plan est fenêtré : un clip
  fini avant n'est même pas ouvert.
  - Les **horodatages sont conservés** (`asetpts` ajoute le temps coupé) : `afade` et `volume` (`t`) les lisent, donc
    fondus et automation restent calculés dans le temps propre du clip (vérifié sur FFmpeg 7.1 et 9.0). Un clip
    remappé (vitesse, inversion) garde l'ancien chemin : sa chaîne est coupée après.
  - Ce que ça change, mesuré : un clip avec l'effet *Normalisation* (`loudnorm`) lu à 15 min, **19 s** avant le
    premier son si la coupe suit la chaîne, **0,6 s** en la coupant avant. Reste le décodage de la source jusqu'à
    l'origine (0,47 s à 15 min), proportionnel à la position mais rapide.
  - Vérifié : le mixage pris à une origine est celui de l'export à partir de cet instant, à 3·10⁻⁴ près (−70 dBFS ;
    l'enveloppe d'automation est évaluée par paliers de 256 échantillons dont la phase suit le point de coupe),
    y compris quand l'origine tombe dans un fondu. `0` : le graphe historique, inchangé.
- **Pré-lecture** de 3 s : le flux part 3 s avant la tête, et ce début est calculé puis jeté. Le relâchement d'un
  ducking et la fenêtre de la sonie à court terme partent avec leur historique.
- **Canaux du flux**, par image : 0-1 le mixage entendu ; 2-3 le même, pondéré K (BS.1770 à 48 kHz, deux `biquad`) ;
  puis deux par piste mesurée, son sous-mixage pris juste avant le mixage final (`track_taps`). Au-delà de 24 pistes,
  seul le Master est mesuré (un flux FFmpeg a au plus 64 canaux).
- **Vumètres** (`MixMeter`) : par blocs de 50 ms, sur exactement ce qui part vers la sortie. Sonie : moyenne de la
  puissance pondérée K sur 8 blocs (momentanée) ou 60 (court terme), `−0,691 + 10·log10(somme des canaux)`. Vérifié sur
  le cas 1 de l'EBU Tech 3341 (sinus 1 kHz à −23 dBFS sur les deux canaux) : −23,0 LUFS.
- **Lecteur** (`ui/live_mix_player.py`). Un fil lit le flux, mesure et range le son dans un tampon borné (2 s) ; un
  minuteur de l'interface (10 ms) le pousse dans un `QAudioSink` de 100 ms. Au premier envoi, le son déjà dépassé par
  la tête (le temps de lancer FFmpeg) et la latence de la sortie sont sautés : le premier échantillon entendu est celui
  de la tête. L'instant entendu se lit sur ce que la sortie a **joué** (`processedUSecs`) : 3 ms de la tête mesurés sur
  une vraie sortie macOS. Le fil ne crée ni ne détruit aucun objet Qt (voir l'interblocage des scopes).
- **Fenêtre** (`ui/main_window_mixins/live_mix.py`). À chaque synchronisation du moniteur, l'état voulu (lecture,
  projet, révision de l'historique, Master) est comparé à ce qui joue. Une modification relance le flux une fois restée
  telle quelle 0,2 s (un fader qu'on glisse ne relance pas FFmpeg à chaque pas) ; un écart de plus de 0,25 s entre le
  son et la tête (saut, gel) aussi. Le lecteur du moniteur se tait pendant ce temps : le mixage contient déjà son son,
  celui des sources Multicam (qui ne jouent plus à part) et des compositions.
- **Repli** : FFmpeg qui échoue (y compris un plan ou une commande impossibles à construire : l'échec est retenu pour
  cet état du projet, pas réessayé à chaque tic), qui ne tient pas le temps réel (trois recalages en 5 s, des effets trop lourds), ou
  aucune sortie audio (une machine sans carte son) : le moniteur garde son propre son, et le flux n'est pas relancé tant
  que le projet ne change pas. `KUT_STUDIO_LIVE_MIX=off` le coupe (tests, audit
  d'interface).

## Mesures (Apple M4)

| | |
| --- | --- |
| Rendre 10 s de mixage à 15 min, avant l'origine audio | 1,95 s |
| Premier son après Lecture | ≈ 100 ms (290 ms la première fois : FFmpeg sonde ses options une fois par session) |
| Écart entre le son entendu et la tête | 3 ms |

## Limites

- Une modification pendant la lecture s'entend après 0,2 s de repos et une relance (≈ 100 ms de silence).
- Rien en dehors de la lecture : pas de son au balayage de la tête.
- Une séquence imbriquée calcule encore son son depuis son début (correct, plus lent pour une longue séquence).
- Les vumètres des pistes vidéo à son intégré n'ont pas de tranche dans le mixeur (comme avant).

## Code

| Module | Rôle |
| --- | --- |
| `core/live_mix.py` | plan et commande du flux, pondération K, `MixMeter` |
| `core/export_engine.py` | `audio_origin` et `track_taps` du graphe audio |
| `core/edit_history.py` | `revision` : le projet a changé |
| `ui/live_mix_player.py` | lecteur : fil de lecture, calage, `QAudioSink` |
| `ui/level_meter.py` | vumètre (crête, RMS, crête maintenue) |
| `ui/mixer_panel.py` | vumètres des tranches et du Master, sonie |
| `ui/main_window_mixins/live_mix.py` | lancement, arrêt, relance, repli |

Tests : `tests/test_live_mix.py` (origine égale à l'export, points de mesure, Master, fenêtre, sonie EBU, vrai FFmpeg) ;
`tests/test_live_mix_player.py` (calage à l'échantillon, vumètres, échec, fenêtre, page Audio) ;
`tests/test_audio_mixer.py` (vumètres du mixeur).
