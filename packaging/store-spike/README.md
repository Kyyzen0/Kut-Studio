# Spike bac à sable macOS (ADR-0013, étape 1)

Mesures réelles de Kut-Studio signé avec `com.apple.security.app-sandbox`. **Aucun code applicatif n'est modifié** : on
construit le bundle existant (`build.py`), on le signe binaire par binaire, on le lance par LaunchServices (`open`), comme
le fera le Store. Les résultats sont dans [l'ADR-0013 §1](../../docs/adr/0013-publication-mac-app-store.md).

Rien de secret n'est versionné : pas de `.p12`, pas de profil (`*.mobileprovision`, `*.provisionprofile` sont ignorés).

## Contenu

| Fichier | Rôle |
| --- | --- |
| `shell/` | Projet Xcode « coquille » jetable (app vide, `com.kutstudio.app`, App Sandbox, équipe 3SZ5D643QC, signature automatique). Sert à faire créer l'App ID par Xcode (`xcodebuild -allowProvisioningUpdates`). Ne fait pas partie du produit. |
| `entitlements/main.plist` | droits du binaire principal : `app-sandbox`, `files.user-selected.read-write`, `files.bookmarks.app-scope`, `device.audio-input`, `cs.allow-unsigned-executable-memory`. |
| `entitlements/main-no-ume.plist` | idem **sans** `allow-unsigned-executable-memory` (la variante à retenir). |
| `entitlements/inherit.plist` | `app-sandbox` + `inherit` pour FFmpeg / ffprobe embarqués et l'exécutable auxiliaire de la sonde. |
| `sign.sh` | signe un `.app` binaire par binaire (sans `--deep`), runtime renforcé, certificat « Apple Development ». |
| `bundle_ffmpeg.py` | copie le FFmpeg Homebrew et ses dylibs dans le bundle (réécriture `@loader_path`). **Jouet** : Homebrew est GPL, ce n'est pas le FFmpeg LGPL de l'étape 2. |
| `make_testdata.py` | crée `demo.kut` + `clip.mp4` dans le bundle (le bac à sable lit son bundle : seul moyen de donner un projet sans boîte de dialogue). |
| `probe.py`, `build_probe.sh` | `Probe.app` : sonde stdlib qui s'exécute dans le bac à sable et imprime un JSON par mesure. |
| `probe_gui.py`, `build_probe_gui.sh` | `ProbeGui.app` : même chose, avec Qt et la réception de `FileOpen` (essai d'extension de fichier ; voir l'ADR). |
| `replay.sh` | enchaîne tout (coquille, construction, signature, mesures avec et sans `allow-unsigned-executable-memory`, journal des refus). |

## Rejouer

Prérequis : macOS sur Apple Silicon, Xcode (connecté au compte Apple, équipe 3SZ5D643QC), certificat « Apple Development »
dans le trousseau, `.venv` du dépôt (PyInstaller, PySide6), FFmpeg Homebrew.

```sh
packaging/store-spike/replay.sh          # ≈ 2 min ; SPIKE_DIR, PYTHON, IDENTITY modifiables
```

Pièges mesurés :

* Un `.app` signé sandbox lancé depuis le shell hérite de `HOME` réel : toujours le lancer par `open -n` (LaunchServices),
  qui met `HOME` dans le conteneur. `open --stdout/--stderr/--env` renvoient la sortie et l'environnement.
* Le conteneur `~/Library/Containers/com.kutstudio.app/Data` est illisible depuis le shell (« Operation not permitted »,
  protection des données d'app). Pour le relire : `Probe.app` avec `KUT_PROBE_CAT=<fichier>`.
* Pour voir le journal de l'application sur `stderr` plutôt que dans le conteneur : `KUT_STUDIO_LOG_DIR=/dev/null/x`.
* `log` est un mot réservé de zsh : utiliser `/usr/bin/log show --predicate 'eventMessage CONTAINS "Sandbox:"'`.
* Une instance tuée brutalement laisse le drapeau « arrêt brutal GPU » dans le conteneur (aperçu CPU) : isoler avec
  `KUT_STUDIO_CACHE_DIR=<dossier du conteneur>`.

## À faire à la main (boîtes de dialogue : non automatisable ici)

Avec `$SPIKE_DIR/Kut-Studio.app` (jamais `/Applications/Kut-Studio.app`). **Travailler sur une COPIE d'un projet** (une sonde ne doit jamais toucher un vrai projet ; `probe.py` ne fait que lire le fichier choisi et ne crée / supprime que des copies voisines à nom unique, test : `tests/test_store_spike_probe.py`) :

1. **Fichier > Ouvrir** un `.kut` placé hors du bundle (par ex. `~/Desktop`) : s'ouvre-t-il ? Le message « verrou non posé » est attendu.
2. Modifier le projet puis **Enregistrer** : l'écriture atomique (fichier temporaire voisin + renommage) réussit-elle ?
3. **Fichier > Enregistrer sous**, puis fermer et **rouvrir via Fichier > Ouvrir récent** : les médias référencés par chemin sont-ils retrouvés ?
4. **Importer** un média depuis `~/Movies`, enregistrer, quitter, relancer, rouvrir : le média est-il encore lisible (sans signet) ?
5. **Exporter** vers `~/Desktop` : le fichier `.partial` voisin est-il refusé ?
6. **Voix off** : la fenêtre d'autorisation du micro apparaît-elle, et l'enregistrement produit-il du son ?
7. Double-clic Finder sur un `.kut` : l'application s'ouvre-t-elle avec le projet ?

`log stream --predicate 'eventMessage CONTAINS "Sandbox:"'` pendant ces essais donne la raison de chaque refus.
