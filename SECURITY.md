# Politique de sécurité

## Signaler une vulnérabilité

Merci de **ne pas** ouvrir de ticket public pour une faille de sécurité. Utilisez le signalement privé de GitHub :
onglet **Security** du dépôt › **Report a vulnerability**
([lien direct](https://github.com/Kyyzen0/Kut-Studio/security/advisories/new)).

Précisez la version de Kut-Studio, le système, et, si possible, les étapes pour reproduire (un projet `.kut` ou un script
minimal qui provoque le problème). Vous aurez un accusé de réception ; le correctif est publié dans une nouvelle version,
annoncée dans le [journal des modifications](CHANGELOG.md).

## Versions prises en charge

Seule la **dernière version publiée** reçoit des correctifs de sécurité
([Releases](https://github.com/Kyyzen0/Kut-Studio/releases/latest)).

## Ce qui compte comme une faille

Kut-Studio ouvre des fichiers venus d'ailleurs (projets `.kut`, médias, préréglages). Sont notamment des failles :

- un fichier qui **fait exécuter du code** sur la machine qui l'ouvre ;
- un projet qui injecte un **filtre FFmpeg** (lecture d'un fichier local, écriture hors du dossier de sortie) ;
- un fichier qui fait **lire ou écrire hors** de ce que l'utilisateur a choisi ;
- la mise à jour automatique qui installerait un paquet non vérifié.

La règle de conception est qu'**un `.kut` ne s'exécute jamais** ; elle est protégée par un test
(`tests/test_no_dynamic_execution_guard.py`, voir l'[ADR-0005](docs/adr/0005-mode-script-frontiere-de-securite.md)).

## Chaîne de publication

Les versions sont publiées uniquement par le workflow `release.yml`, déclenché par un tag `vX.Y.Z` que seul un administrateur
du dépôt peut créer. Chaque paquet est accompagné de ses empreintes SHA-256 (`SHA256SUMS.txt`), que l'application vérifie
avant d'installer une mise à jour.
