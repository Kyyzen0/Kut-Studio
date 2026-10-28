# Notices des composants tiers

Kut-Studio est publié sous licence MIT (`LICENSE`). Ce fichier liste les composants tiers **que la version Mac App Store embarque avec FFmpeg**
(ADR-0013, étape 2). Les autres dépendances (PySide6 / Qt sous LGPL v3, numpy, etc.) seront ajoutées à l'étape « écran Licences » ; elles ne sont pas
encore listées ici **[à compléter]**. Les textes complets des licences sont dans `packaging/ffmpeg-lgpl/licenses/`.

## FFmpeg 7.1.5 (ffmpeg, ffprobe) : LGPL 2.1 ou ultérieure

Copyright (c) 2000-2026 les développeurs de FFmpeg (https://ffmpeg.org). Construit **sans** `--enable-gpl`, `--enable-version3` ni `--enable-nonfree` ;
il ne contient ni x264, ni x265, ni aucune bibliothèque GPL. Il est lancé par Kut-Studio comme un **processus séparé** ; Kut-Studio n'est pas lié à lui.
Texte : `packaging/ffmpeg-lgpl/licenses/FFmpeg-LGPL-2.1.txt` (et `FFmpeg-LICENSE.md`). Les sources sont celles d'amont, **sans aucune modification**.

| Bibliothèque (liée statiquement dans `ffmpeg` / `ffprobe`) | Version | Licence | Fichier |
| --- | --- | --- | --- |
| libass | 0.17.5 | ISC | `libass-ISC.txt` |
| FreeType | 2.15.0 | FreeType License (FTL) | `FreeType-FTL.txt` |
| HarfBuzz | 15.0.0 | MIT (« Old MIT ») | `HarfBuzz-MIT.txt` |
| dav1d | 1.5.4 | BSD-2-Clause | `dav1d-BSD-2.txt` |
| FriBidi | 1.0.17 | LGPL 2.1 ou ultérieure | `FriBidi-LGPL-2.1.txt` |

Bibliothèques du système macOS liées dynamiquement et **non livrées** : zlib, libiconv, AudioToolbox, VideoToolbox, CoreText, CoreMedia, CoreVideo…

> Portions of this software are copyright © The FreeType Project (www.freetype.org). All rights reserved.

## Offre écrite de sources (LGPL 2.1 §6)

Pendant au moins **trois ans** à compter de la distribution de cette version, toute personne qui reçoit `ffmpeg` ou `ffprobe` avec Kut-Studio peut obtenir,
**gratuitement** (hors frais de support), le code source complet correspondant : les archives d'amont de FFmpeg 7.1.5, libass 0.17.5, FreeType 2.15.0, HarfBuzz 15.0.0,
FriBidi 1.0.17 et dav1d 1.5.4 (SHA-256 dans `packaging/ffmpeg-lgpl/versions.env`) et les scripts de construction (`packaging/ffmpeg-lgpl/build.sh`) qui produisent les binaires fournis.

Où les demander : dans un **dépôt public dédié** (sources, scripts, empreintes ; sans le code de Kut-Studio), lié depuis le site et l'écran Licences : **[URL à fixer, décision D2 de l'ADR-0013 ; le dépôt reste à créer par le mainteneur, contenu prêt avec `packaging/ffmpeg-lgpl/make-source-offer.sh`]** ; en attendant la publication, à l'adresse de contact de l'éditeur indiquée sur la fiche de l'application.

Les sources d'amont sont aussi disponibles à : https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz, https://github.com/libass/libass/releases/tag/0.17.5,
https://downloads.sourceforge.net/project/freetype/freetype2/2.15.0/, https://github.com/harfbuzz/harfbuzz/releases/tag/15.0.0, https://github.com/fribidi/fribidi/releases/tag/v1.0.17, https://code.videolan.org/videolan/dav1d/-/tags/1.5.4.

Détails, empreintes et obligations (dont ce qui demande une validation juridique) : `docs/ffmpeg-lgpl.md`.
