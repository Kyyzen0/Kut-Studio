# Miroir GitLab

`.github/workflows/mirror-gitlab.yml` recopie `main` et les tags vers un dépôt GitLab **privé** à chaque push, comme
copie de sauvegarde. Tant que les deux secrets ci-dessous n'existent pas, le job affiche « miroir non configuré » et
se termine sans échec. Le workflow ne contient aucun identifiant.

## Mise en place (faite par l'utilisateur, une fois)

1. Sur GitLab, créer un **projet vide et privé** (sans README, pour que le premier push ne diverge pas).
2. Dans ce projet : *Settings > Access tokens*, créer un **jeton de dépôt** (*project access token*) avec le rôle
   *Maintainer* (nécessaire pour pousser sur la branche protégée `main`) et la portée **`write_repository`** seule.
   Copier le jeton, il n'est affiché qu'une fois.
3. Dans GitHub : *Settings > Secrets and variables > Actions > New repository secret*, ajouter :
   * `GITLAB_MIRROR_URL` : l'URL https du projet **sans identifiants**, par exemple
     `https://gitlab.com/<groupe>/<projet>.git` ;
   * `GITLAB_MIRROR_TOKEN` : le jeton de l'étape 2.
4. Le prochain push sur `main` (ou tag) pousse le miroir ; vérifier l'onglet *Actions > Mirror GitLab*.

## Comportement

* Poussés : la branche `main` et tous les tags. Pas de poussée forcée : si le miroir diverge, le job échoue au lieu de
  réécrire l'historique de la copie.
* Le jeton est masqué dans les journaux (`::add-mask::`) et passé par un en-tête HTTP, jamais dans l'URL ; permissions
  du workflow : `contents: read`.
* Révoquer le jeton côté GitLab, ou supprimer les secrets, suffit à arrêter le miroir.
* Garde : `tests/test_gitlab_mirror_workflow.py`.
