# Étapes communes : installation, premier lancement, mise à jour

À faire **une fois par Mac** (M1 Air et M4 mini), avant les trois parcours, et à refaire à **chaque candidate de version**. Chaque case « Observé / écart /
capture » se remplit sur place : *OK*, ou *écart* + numéro de fiche de défaut ([fiche-defaut.md](fiche-defaut.md)), et le nom du fichier de capture.

**Fiche de séance** (à recopier en tête de chaque passage) : Mac ______ · macOS ______ · version installée ______ · version précédente ______ · date ______ ·
séance démarrée à ____ h ____ (utile à [plantages-par-heure.md](plantages-par-heure.md)).

## A. Installation propre

Préparer une machine « neuve » pour Kut-Studio : quitter l'application, **supprimer** `/Applications/Kut-Studio.app`, puis déplacer (ne pas supprimer) les dossiers
`~/Library/Application Support/Kut-Studio`, `~/Library/Caches/Kut-Studio` et `~/Library/Logs/Kut-Studio` dans un dossier `sauvegarde-recette` : l'application repart alors
comme chez un nouvel utilisateur. (Le banc scripté, lui, n'y touche jamais.) Repérer la candidate dans les assets de la release (macOS arm64).

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| A1 | Télécharger le paquet macOS arm64 de la candidate depuis la page de la release ; noter la taille | Taille cohérente avec la release (≈ 55 Mo pour la v0.8.0, voir ADR-0011 décision 6) | |
| A2 | Ouvrir le `.dmg` / l'archive, glisser l'application dans `/Applications` | Copie sans erreur | |
| A3 | Terminal : `spctl --assess --type execute --verbose /Applications/Kut-Studio.app` | `accepted`, `source=Notarized Developer ID` (paquet signé **et notarisé**) | |
| A4 | Terminal : `codesign --verify --deep --strict --verbose=2 /Applications/Kut-Studio.app` et `xcrun stapler validate /Applications/Kut-Studio.app` | Vérification réussie ; ticket agrafé valide | |
| A5 | Premier lancement par double-clic dans le Finder (la quarantaine du téléchargement est active) | **Gatekeeper accepte sans avertissement bloquant** ; sinon capturer l'avertissement exact | |

## B. Premier lancement

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| B1 | Observer le démarrage jusqu'à la fenêtre principale ; chronomètre à la main | Fenêtre utilisable, pas de rebond infini dans le Dock, pas de fenêtre d'erreur | |
| B2 | Dans **Aide › À propos de Kut-Studio** : version, système, versions de Qt / PySide6 / Python | La version est celle de la candidate ; mode d'installation « paquet » | |
| B3 | **Préférences** : langue, thème, recherche de mises à jour (défaut : oui) | Réglages ouverts sans erreur ; le dossier `~/Library/Application Support/Kut-Studio` est recréé | |
| B4 | Diagnostic matériel (Préférences, section Performance / encodage matériel ; bouton **Copier le diagnostic**) : coller le texte dans la fiche | Décodage et encodage VideoToolbox détectés ; aperçu GPU « Metal » ; pas de repli CPU inexpliqué | |
| B5 | Ouvrir le journal `~/Library/Logs/Kut-Studio/kut-studio.log` | Une ligne « Démarrage — Python …, macOS … » ; **aucune** ligne `CRITICAL` | |
| B6 | Ouvrir un projet généré par le banc (`project.kut` d'un parcours, copié ailleurs) | Le projet s'ouvre ; **aucun média introuvable** ; pas de message « entrées ignorées » | |

## Z. Mise à jour depuis la version précédente

À faire avec l'installation de la **version précédente** en place (réinstaller la précédente d'abord si besoin, avec des réglages et un projet de test), puis :

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| Z1 | Avec la version N−1 : **Aide › Rechercher des mises à jour…** | « Mise à jour N » proposée (version, date, notes de publication, taille du paquet) | |
| Z2 | **Télécharger** | Barre de progression ; la fenêtre reste non modale ; paquet vérifié (somme de contrôle) | |
| Z3 | **Quitter et installer…**, suivre les étapes affichées | L'application se ferme ; l'installation du système se déroule (voir [../updates.md](../updates.md) § 3) | |
| Z4 | Relancer la version N | **Gatekeeper accepte** ; À propos affiche N | |
| Z5 | Vérifier que les **réglages** (langue, thème, disposition) et le **projet de test** de N−1 sont intacts | Réglages conservés ; le projet s'ouvre sans « entrée ignorée » | |
| Z6 | Journal | Une nouvelle ligne « Démarrage » ; aucune ligne `CRITICAL` | |

Taux de réussite visé (ADR-0011) : **100 %** sur les deux Mac pour A, B et Z. Un échec ouvre une fiche de défaut **bloquante**.
