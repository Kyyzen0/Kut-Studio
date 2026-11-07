# Politique de la CI : macOS seul sur les PR

Décision de l'utilisateur du 2026-10-28 ([ADR-0013](adr/0013-publication-mac-app-store.md) §11) : le projet vise le
Mac App Store seul pour l'instant ; Windows et Linux sont **mis de côté** (ils ne sont plus prioritaires). Leurs
runners sont lents (jobs coupés à 60 min) et ralentissaient chaque PR.

| Déclencheur | Jobs lancés |
| --- | --- |
| `pull_request`, `push` sur `main` | `macOS / Python 3.11` (matrice réduite à `macos-latest`) et `macOS / libass (sous-titres)` |
| `schedule` (dimanche 03:00 UTC), `workflow_dispatch` | tout : macOS, Windows, Ubuntu et `Debian 13 / FFmpeg 7.1 / Python 3.13` |

* Rien n'est supprimé : les jobs Windows, Ubuntu et Debian restent **définis** dans
  `.github/workflows/multiplatform.yml`, ainsi que tous les tests. L'exécution hebdomadaire sert à voir une dérive
  sans bloquer les PR ; un lancement manuel (onglet *Actions* > *Multiplatform* > *Run workflow*) fait la même chose
  à la demande.
* La couverture de code et le cliquet (mesurés sur Linux) ne tournent donc plus que dans l'exécution hebdomadaire.
* `release.yml` et `deploy-website.yml` ne changent pas : la publication des releases reste identique.
* Aucune vérification n'est exigée par ce changement ; la protection de branche reste celle du dépôt.
* Garde : `tests/test_ci_workflow.py` (déclencheurs, jobs non macOS limités à `schedule` / `workflow_dispatch`).
