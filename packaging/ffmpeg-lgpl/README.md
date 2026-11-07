# FFmpeg LGPL arm64 pour le Mac App Store (ADR-0013, étape 2)

Construit FFmpeg 7.1.5 + libass (et FreeType, HarfBuzz, FriBidi, dav1d) **sans `--enable-gpl`**, avec VideoToolbox et AudioToolbox, en statique, pour macOS arm64.
Tout est expliqué dans [`docs/ffmpeg-lgpl.md`](../../docs/ffmpeg-lgpl.md) (reconstruire, empreintes, audit, obligations LGPL).

| Fichier | Rôle |
| --- | --- |
| `versions.env` | versions, URL et **SHA-256** de chaque archive source (à changer ensemble) |
| `build.sh` | télécharge, vérifie, construit et installe dans `_build/prefix` (ignoré par git) |
| `make-source-offer.sh` | prépare (sans rien publier) le contenu du dépôt public de l'offre de sources ; `source-offer/README.md` en est la page d'accueil |
| `licenses/` | textes des licences copiés des archives (à livrer avec l'application) |

```sh
packaging/ffmpeg-lgpl/build.sh
export KUT_STUDIO_FFMPEG_DIR="$PWD/packaging/ffmpeg-lgpl/_build/prefix/bin"   # build.py l'embarque, tool_paths le résout
```
