# Fiche de défaut (gabarit)

Une fiche par défaut. Copier ce bloc dans une issue GitHub (ou dans `defauts-connus.md` pour un défaut inscrit comme connu). Une fiche **sans version, sans machine et sans étapes** est renvoyée.

```markdown
**Titre** : (une phrase : ce qui se passe, où)

**Identifiant** : D-___        **Date** : ____-__-__        **Parcours / étape** : (a|b|c) T__ / M__ / D__ / A__ / B__ / Z__

**Version de Kut-Studio** : ______ (Aide › À propos)        **Installation** : paquet notarisé / sources / autre
**Machine** : MacBook Air M1 / Mac mini M4 / autre ______        **macOS** : ______        **Mémoire** : ____ Go
**Rendu de l'aperçu** : Auto / GPU / CPU (diagnostic)        **Encodeur d'export** : CPU / VideoToolbox

**Gravité** : ☐ bloquant (plantage, perte de données, installation ou mise à jour impossible)
              ☐ majeur (fonction inutilisable, résultat faux à l'export)
              ☐ mineur (gêne, contournement simple)
              ☐ cosmétique

**Reproductibilité** : ☐ toujours  ☐ souvent (__ fois sur __)  ☐ une fois

**Étapes pour reproduire** (numérotées, depuis une installation propre ou le projet `project.kut` du banc) :
1.
2.
3.

**Résultat attendu** : (la colonne de la checklist)

**Résultat observé** :

**Captures / vidéo écran / fichier** : (noms de fichiers ; ne pas coller de média personnel)

**Diagnostic** : (coller « Copier le diagnostic » ; joindre les 50 dernières lignes de `~/Library/Logs/Kut-Studio/kut-studio.log`)
**Rapport de plantage système** (si l'application s'est fermée) : nom du fichier `Kut-Studio-….ips`
**Rapport du banc** (si le défaut se reproduit par script) : `rapport.md` + ligne de commande

**Ce que le banc scripté en dit** : ☐ ne le voit pas (attendu : écran / GPU / lecture)  ☐ le voit (code / `ffprobe` / noir-gelé)  ☐ pas essayé

**Contournement** :
**Correction** : PR #____   (ou « inscrit comme défaut connu » dans docs/recette/defauts-connus.md)
```

Règle de la porte de sortie (ADR-0011) : chaque écart constaté est soit **corrigé** (PR liée), soit **inscrit comme défaut connu** avec sa PR de suivi. Un défaut « bloquant » ou « majeur » ne peut pas être inscrit sans décision du mainteneur.
