# Mise à jour automatique (macOS) : recette manuelle

À faire sur **le M1 Air puis le M4 mini**, avec une **vraie** préversion signée. Ce que ces tests ne peuvent pas prouver (le vrai remplacement d'une
application en cours d'exécution, la vraie signature, Gatekeeper, LaunchServices, le vrai `open`) est ici ; le reste (assistant `/bin/sh` sur de vrais
dossiers temporaires avec un faux bundle, retour arrière, interruption, validation) est couvert par `tests/test_update_install.py` et
`tests/test_update_install_ui.py`. Conception : [../updates.md](../updates.md), section 3 bis. Défauts : [fiche-defaut.md](fiche-defaut.md).

**Fiche de séance** : Mac ______ · macOS ______ · version installée (N−1) ______ · version publiée (N) ______ · date ______.

**Garde-fous** : ne rien tester sur l'application que vous utilisez pour travailler sans l'avoir sauvegardée ; garder une copie de `Kut-Studio.app` (N−1) et
de `~/Library/Application Support/Kut-Studio` avant de commencer. Les versions de test sont des **préversions** (`vX.Y.Z-beta.N`, case « pre-release ») :
elles ne sont proposées qu'à une copie qui a activé « préversions » ou qui est elle-même une préversion.

## Préparer les deux préversions

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| P1 | Publier `vX.Y.Z-beta.1` **signée** (secrets Developer ID, notarisation, signature Ed25519 des mises à jour) selon [../updates.md](../updates.md) §§ 6-7 | Release avec `Kut-Studio-…-macos-arm64.zip`, `SHA256SUMS.txt` et la signature ; notes qui disent « signé et notarisé » | |
| P2 | Sur le Mac : installer la préversion précédente **dans `/Applications`** (pas depuis l'image disque, pas depuis le dossier de téléchargement) | **Aide › À propos** : mode « application macOS (/Applications/Kut-Studio.app) » | |
| P3 | Préparer un projet de test enregistré et un réglage visible (langue, thème) | Notés pour la vérification finale | |

## 1. Mise à jour qui réussit

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| U1 | **Aide › Rechercher des mises à jour…** | La préversion est proposée (version, date, notes, taille) | |
| U2 | **Télécharger** | Barre de progression ; « Paquet vérifié » ; le bouton principal est **Installer et redémarrer** (pas « Quitter et installer… ») ; le texte dit « Kut-Studio va se fermer, se mettre à jour et se rouvrir. Vos projets et préférences ne sont pas touchés. » | |
| U3 | Ouvrir un projet avec une **modification non enregistrée**, puis cliquer **Installer et redémarrer** et répondre **Non** à la confirmation | Rien ne se passe, la fenêtre reste ouverte ; aucun dossier caché `.Kut-Studio-update-*` n'apparaît à côté de l'application (Finder : ⇧⌘. pour voir les fichiers cachés) | |
| U4 | Recommencer, confirmer, puis **Annuler** à la demande d'enregistrement du projet | Tout reste ouvert, le dossier caché est supprimé, l'application est inchangée (`À propos` : N−1) | |
| U5 | Recommencer, confirmer, **Enregistrer** | La fenêtre se ferme, **puis** l'application se rouvre seule (≈ 10 à 40 s) ; **Gatekeeper n'affiche aucun avertissement** ; À propos : N | |
| U6 | Au démarrage de N | Un message « Mise à jour installée : N » avec « Notes de version » ; il **ne revient pas** au lancement suivant | |
| U7 | Finder (fichiers cachés visibles) dans `/Applications` | Aucun `.Kut-Studio.app.previous-*` ni `.Kut-Studio-update-*` ne subsiste | |
| U8 | Terminal : `codesign --verify --deep --strict --verbose=2 /Applications/Kut-Studio.app`, `spctl --assess --type execute --verbose /Applications/Kut-Studio.app`, `xattr -l /Applications/Kut-Studio.app` | Vérifications réussies ; `source=Notarized Developer ID` ; **pas** de `com.apple.quarantine` (voulu : la signature Ed25519 est la garantie) | |
| U9 | Réglages et projet de test (P3) | Intacts ; le projet s'ouvre sans « entrée ignorée » | |
| U10 | Ouvrir `~/Library/Caches/Kut-Studio/updates/install.log` | Chaque étape journalisée : début, application fermée, état, renommages, relance, « succès », « terminé » | |
| U11 | Un **rendu en cours** pendant le clic sur **Installer et redémarrer** | La fermeture demande confirmation du rendu ; refuser garde tout ouvert | |

## 2. Retour arrière : une nouvelle version qui ne démarre pas

On publie une **préversion de test cassée**, jamais une version destinée aux utilisateurs.

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| R1 | Construire `vX.Y.Z-beta.2` comme la précédente, puis la **casser volontairement** avant de la publier : par exemple faire échouer le démarrage dans `main.py` (`raise SystemExit(1)` avant l'ouverture de la fenêtre) sur une branche de test **jamais fusionnée** ; version distincte, signature Ed25519 valide (la signature est correcte : seule l'application est cassée) | Release pré-version `beta.2` publiée | |
| R2 | Installer N (réussie, section 1), puis chercher les mises à jour : la `beta.2` est proposée ; **Installer et redémarrer** | La fenêtre se ferme, la `beta.2` est mise en place et lancée | |
| R3 | Attendre (au plus ≈ 90 s) | La `beta.2` ne démarre pas ; **l'application N est relancée seule** | |
| R4 | Au démarrage de N | Message « La mise à jour a échoué » : version précédente **restaurée**, motif (« la nouvelle version n'a pas démarré »), bouton **Ouvrir le journal** | |
| R5 | `À propos` ; Finder (fichiers cachés visibles) ; `install.log` | N est de retour et fonctionne (R4 inclus) ; aucun `.previous-*`, `.failed` ni staging ; le journal contient « retour arrière : start_timeout » et « précédente restaurée » | |
| R6 | Variante « crash juste après le démarrage » : une `beta.3` qui écrit son fichier démarré puis quitte avant 3 s (`os._exit(1)` 1 s après l'ouverture de la fenêtre) | Même issue, motif « s'est arrêtée juste après son démarrage » (`crashed_after_start`) | |
| R7 | Réinstaller (si besoin) la N saine, supprimer la `beta.2` de la liste des versions proposées | Le Mac est revenu à un état normal ; supprimer aussi la release de test sur GitHub | |

## 3. Cas où l'installation reste assistée

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| S1 | Lancer N−1 **depuis le dossier de téléchargement** après un premier lancement par double-clic (macOS peut l'exécuter par translocation) | Le bouton principal est **Quitter et installer…**, et la ligne « Installation automatique indisponible : … emplacement temporaire » apparaît | |
| S2 | Application installée dans un dossier sans droit d'écriture pour l'utilisateur standard (ou sur une image disque montée) | Même repli assisté, avec la raison (droits / volume en lecture seule) | |
| S3 | Une préversion **sans** signature Ed25519 valide (ou signée par une autre clé) | Bouton **Quitter et installer…** (parcours assisté) ; jamais d'installation automatique | |
| S4 | Une préversion dont le bundle est signé par **un autre équipe** ou dont l'identifiant diffère (fabriquer à la main) | La préparation échoue avec le motif traduit, rien n'est modifié, repli assisté | |

## 4. Après la recette

Cocher le tableau, copier les écarts dans des fiches de défaut (échec d'installation ou de retour arrière = **bloquant**) et renseigner
[../updates.md](../updates.md) § 9 (« Exécuté sur macOS ») une fois les sections 1 et 2 réussies sur **les deux** Mac. Attention aux effets de bord : la
section 2 installe volontairement une version cassée ; restaurer N−1 ou N ensuite et supprimer les releases de test.
