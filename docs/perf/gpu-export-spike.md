# Essai : exporter par le compositeur GPU (ADR-0001)

**Date :** 2026-10-10 · **Outil :** [`tools/perf/gpu_export_spike.py`](../../tools/perf/gpu_export_spike.py) ·
**Mesures :** [`gpu-export-spike.json`](gpu-export-spike.json)

L'[ADR-0001](../adr/0001-moteur-unifie-gpu-adaptatif.md) recommandait un essai rapide avant de construire le moteur
unifié : composer **sur GPU** l'export TikTok Onlyfab, avec le code existant, et le comparer à l'export actuel. Le but
était de vérifier l'idée et de trouver ce qui coûte, pas d'écrire l'exporteur définitif.

```bash
KUT_STUDIO_CONFIG_DIR=$(mktemp -d) python -m tools.perf.gpu_export_spike \
    --project ~/Documents/onlyfab_tiktok_fr.kut --workdir /tmp/essai --raster-threads 0 4 8
```

Machine : Apple M4 (4 cœurs performance + 6 efficacité), macOS 27.2, PySide6 6.11.2, FFmpeg 9.0.2, Metal.
Montage : 1080×1920, 30 i/s, 31 s, 3 clips vidéo sans effet, 54 calques graphiques (46 normaux, 7 en Addition, 1 en
Incrustation) découpés en 9 éléments, flou de mouvement de la séquence actif.

## Ce que fait l'essai

Chaque image est composée à partir du **même plan de rendu** que l'export (`build_render_plan`) :

| Étape | Export actuel | Essai GPU |
| --- | --- | --- |
| Vidéo | décodée et composée dans le graphe FFmpeg | décodée par FFmpeg en `yuv420p` (un fil par clip), plans envoyés tels quels au GPU |
| Calques graphiques | `MographRenderer` → PNG sur disque → `.ffconcat` → FFmpeg | **même** découpage (`graphics_program`), **même** rastériseur, image envoyée directement en texture, seulement quand son contenu change |
| Composition | `filter_complex` FFmpeg (RVBA) | `plan_frame` exécuté par le vrai `GpuPreviewWidget` (Metal) |
| Sortie | conversion BT.709 puis VideoToolbox | les mêmes : `OUTPUT_COLOR_STAGE` et `resolve_video_encoder` |

Couverture : clips à transform, opacité, mode de fusion et effets GPU, bandes de calques et calques à mode de fusion.
Le reste (imbrication, transitions, remappage, masques et étalonnage des clips, calques d'effets, sous-titres) est
refusé d'emblée, avec la liste de ce qui manque.

## Résultats

| | Export actuel | Essai GPU |
| --- | ---: | ---: |
| Durée totale (31 s de vidéo, cache des calques vide) | 28,2 s | **19,3 s** |
| Vitesse | 33 i/s | **48 i/s** (1,6× le temps réel) |
| Gain | | **×1,5** |

Le gain se limite à ×1,5 parce que l'essai garde la rastérisation actuelle. Le temps se répartit ainsi :

| Étape de l'essai GPU | Durée | Part |
| --- | ---: | ---: |
| Rastérisation Qt des calques (1 499 images plein cadre) | 11,0 s | 57 % |
| Rendu GPU et relecture synchrone (`grabFramebuffer`, ~7 ms par image) | 6,6 s | 34 % |
| Préparation des envois, conversion de la relecture | 0,4 s | 2 % |
| Attente du décodage / de l'encodage | 0,3 s | 2 % |

Le décodage et l'encodage, dans leurs propres fils et processus, ne coûtent presque rien en attente.

### Fidélité

**Images brutes, sans encodeur** (sortie GPU contre `tests/render_probe.render_frame`, le graphe d'export) :

| Image | PSNR | Écart moyen | Écart max |
| --- | ---: | ---: | ---: |
| 279 (9,3 s) | 53,1 dB | 0,32 / 255 | **1** / 255 |
| 480 (16,0 s) | 53,0 dB | 0,33 / 255 | **1** / 255 |
| 46 (1,5 s) | 39,0 dB | 2,2 / 255 | 58 / 255 |
| 558 (18,6 s) | 39,7 dB | 2,3 / 255 | 43 / 255 |
| 800 (26,7 s) | 43,6 dB | 1,3 / 255 | 30 / 255 |

Sur les images 279 et 480, l'écart ne dépasse pas l'arrondi. Sur les autres, il vient d'une seule cause, vérifiée pixel
par pixel : **le passage de la chroma 4:2:0 en pleine résolution.** Le GPU interpole la chroma (bilinéaire), alors
que swscale, à l'export, répète chaque échantillon sur son bloc de 2×2. Les deux résultats ne diffèrent que sur les
plans à grain chromatique (`print_hook.mp4`, `services_bg.mp4`). Exemple au pixel (740, 482) de l'image 46 : la
vidéo vaut (212, 164, 45) pour swscale et (195, 167, 96) avec `scale=flags=bilinear+full_chroma_int`, ce que voit le
GPU. La fusion en Incrustation par-dessus est exacte dans les deux rendus. Cet écart existe **aussi dans le moniteur
actuel** : les tests de parité ne le voient pas parce que leurs mires ont une chroma constante par bloc de 2×2
(`tests/gpu_harness.make_frame`).

**Fichiers encodés** : PSNR moyen 42,5 dB, SSIM 0,981. Pour comparaison, ré-encoder la référence au même débit avec
VideoToolbox donne 47,8 dB. Le « pire » moment (16 s, 35,8 dB) est un plan à photo très texturée qui commence sur une
image clé : c'est du bruit d'encodage, l'image brute y est exacte à 1/255 près. À l'œil, sur la planche-contact, les
deux rendus sont identiques.

**Déterminisme** : les exports à 0, 4 et 8 fils de rastérisation donnent des images décodées identiques au bit près
(même MD5).

## Ce que l'essai a appris

1. **La rastérisation est le vrai goulot, et elle est bloquée par le GIL.** Avec 4 ou 8 fils, la durée totale ne
   change pas (19,0 et 19,9 s). Le calcul des clés, qui reste dans le fil principal, prend alors 3,7–4,9 s (≈ 1 s
   sans concurrence), le rendu GPU passe de 6,6 à 8,4–9,1 s et l'attente du décodage de 0,3 à 2 s. Le coût est surtout du **Python** (évaluation de la scène, calque par calque),
   pas du dessin Qt. Les fils n'aident que l'écriture des PNG de l'export actuel (C++ sans GIL).
2. **Les textures animées par matrice de l'ADR sont le bon levier, et ce montage le confirme.** Sur 31 s, les 54
   calques n'ont que **223 contenus distincts**. Aujourd'hui, 1 499 éléments plein cadre sont rastérisés. Sur 1 727
   changements d'état d'un calque, **768 ne sont que des déplacements** (matrice ou opacité), qu'un shader absorbe, et
   aucun calque qui bouge n'a de flou de mouvement. Le reste correspond à des allers-retours entre les mêmes
   contenus, qu'un cache par contenu absorbe.
3. **La relecture synchrone coûte ~7 ms par image.** `grabFramebuffer` attend la fin du GPU à chaque image. Il faut
   une relecture asynchrone (2 à 3 images en vol) ; or le binding PySide6 6.11 de `QRhi.beginOffscreenFrame` est
   inutilisable (il attend un `QRhiCommandBuffer` au lieu du pointeur de sortie). Pistes : relire depuis le `render()`
   du widget (`readBackTexture` avec un `QRhiReadbackResult` rendu à l'image suivante), ou la piste C de l'ADR si
   rien ne marche.
4. **Un `QRhiWidget` jamais montré recrée un QRhi à chaque `grabFramebuffer`**, alors que l'exécuteur garde les
   ressources du premier (« Texture … belongs to QRhi A … used with QRhi B »). Les images restaient justes, mais c'est
   un comportement indéfini. Correctif : `WA_DontShowOnScreen` puis `show()`, et le QRhi reste le même. Le banc
   `tools/perf/gpu_bench.py` (mesure du cache des mattes) avait le même défaut, corrigé en même temps.
5. **Le moteur GPU existant tient la route** sur un vrai montage : 930 images, 9 éléments à modes de fusion, sans
   erreur ni repli, avec l'exécuteur du moniteur inchangé.

## Estimation pour la suite (à vérifier)

Si les calques graphiques deviennent des textures rastérisées une fois par contenu (≈ 223 rastérisations, plus
petites que le cadre, au lieu de 1 499 plein cadre), la rastérisation passerait de ~11 s à ~1–2 s. L'export
tomberait alors vers 8–9 s, soit **×3 environ** face à l'export actuel. Avec une relecture asynchrone en plus, on
viserait ×5. Ce sont des projections à partir des mesures ci-dessus, pas des mesures.

## Recommandation

L'idée tient : le GPU compose ce montage fidèlement et plus vite, avec le code existant. L'ordre conseillé pour
l'étape 2 (composition complète) :

1. **Calques graphiques en textures par contenu**, animées par matrice et opacité dans le shader. C'est le plus gros
   gain mesuré, et il profite aussi au moniteur.
2. **Relecture asynchrone** pour l'export (ou rendu dans un fil dédié).
3. **Décider de la chroma 4:2:0** : aligner le GPU sur swscale (échantillon répété) ou l'export sur une interpolation
   (`full_chroma_int`), puis ajouter une mire à chroma non constante aux tests de parité.

Sujets que l'essai n'a pas abordés : audio (repris de la référence pour le fichier final), PyAV et la version LGPL de
FFmpeg (le décodage et l'encodage passent ici par la ligne de commande, et leur attente est déjà négligeable),
machines sans GPU, Windows et Linux.
