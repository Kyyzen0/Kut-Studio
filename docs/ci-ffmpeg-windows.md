# Installation de FFmpeg sur les runners Windows

Les jobs Windows de `.github/workflows/multiplatform.yml` et de `.github/workflows/release.yml` passent par l'action
composite locale `.github/actions/install-ffmpeg-windows`. Raison : le dépôt `community.chocolatey.org` répond parfois
406 (Not Acceptable), et l'étape pouvait alors échouer, ou « réussir » sans FFmpeg (l'étape *Verify media tools* échouait
ensuite). C'est un incident externe, mais il bloquait la CI sans qu'aucun test ait tourné.

## Ce que fait l'action

1. `choco install ffmpeg` avec trois tentatives (attente de 15 s puis 30 s), chacune suivie d'un test réel
   (`Get-Command ffmpeg` et `ffprobe`) : le code de sortie de choco ne suffit pas.
2. Seulement si FFmpeg est toujours introuvable : téléchargement du build **figé** `ffmpeg-9.0.2-essentials_build.zip` de
   gyan.dev, publié sur <https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2> (trois tentatives), vérification du
   **SHA-256** épinglé dans l'action (échec net en cas de différence), extraction dans `$RUNNER_TEMP`, ajout du dossier
   `bin` à `GITHUB_PATH`. Rien de ce qui est téléchargé n'est lancé, hormis `ffmpeg -version` de *Verify media tools*.

La version 9.0.2 est celle de la branche 9.x que Chocolatey installait aux derniers runs.

## Changer de version

Choisir une version sur la page des releases de `GyanD/codexffmpeg`, télécharger le zip `essentials_build`, calculer
`shasum -a 256` (il doit égaler le champ `digest` de l'API GitHub), puis mettre à jour `FFMPEG_VERSION` et
`FFMPEG_SHA256` dans l'action.

## Tester le repli

L'entrée `force-fallback: "true"` saute Chocolatey. À activer temporairement (`with:` sur l'étape de la PR), jamais à
laisser dans le dépôt.
