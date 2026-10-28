# Sources de FFmpeg pour Kut-Studio (offre de sources LGPL)

Ce dépôt contient **le code source correspondant** au `ffmpeg` et au `ffprobe` livrés avec l'application Kut-Studio (version Mac App Store), et les scripts qui les
construisent. Il ne contient **pas** le code de Kut-Studio.

* `sources/` : les archives d'amont, **non modifiées** (FFmpeg, libass, FreeType, HarfBuzz, FriBidi, dav1d), avec `SHA256SUMS`.
* `build/` : `build.sh` et `versions.env` (versions, URL, empreintes) : ce sont ceux de la version livrée.
* `licenses/` : les textes des licences (FFmpeg LGPL 2.1 ou ultérieure, libass ISC, FreeType FTL, HarfBuzz MIT, FriBidi LGPL 2.1 ou ultérieure, dav1d BSD-2).
* `NOTICE.md` : la liste des composants et l'offre écrite.

## Reconstruire (macOS Apple Silicon, outils en ligne de commande Xcode)

```sh
cd build && KUT_FFMPEG_LGPL_DIR="$PWD/_work" ./build.sh      # ≈ 7 minutes ; vérifie chaque archive par SHA-256
shasum -a 256 _work/prefix/bin/ffmpeg                          # comparer au hash publié pour la version
```

Pour construire avec vos propres sources, remplacez l'archive concernée dans `_work/src` et son empreinte dans `versions.env`. Le programme `ffmpeg` est lancé par
l'application comme un processus séparé : vous pouvez l'exécuter seul avec les mêmes options.

Les binaires de la version livrée sont reconstructibles **bit à bit** quand le dossier de construction est le même (le chemin du préfixe est écrit dans la chaîne `configuration:` du binaire).
