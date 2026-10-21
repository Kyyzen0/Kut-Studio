# Séance soutenue de 20 min (M1 Air) et mesure de référence de lecture

Le MacBook Air M1 **n'a pas de ventilateur** : après quelques minutes de charge il **bride** (throttling). Cette séance mesure ce que l'utilisateur de formats courts
vivra au bout de 20 min de montage, et fournit la base des objectifs de la phase 2 (ADR-0011 : « images perdues et gigue relevées au début et à la fin »).
À faire aussi sur le **M4 mini** (référence : on s'attend à ne pas voir de bridage).

## Préparation

- Mac **sur secteur**, **batterie > 80 %**, luminosité à 50 %, **aucune autre application** ouverte (fermer Safari, Mail, Messages), Wi-Fi comme d'habitude, pas de
  Spotlight en cours d'indexation (attendre 10 min après un redémarrage), Mac **à température ambiante** : le laisser 15 min au repos avant de commencer (au
  toucher, le dessous est tiède, pas chaud). Noter la pièce (≈ °C) : ______.
- Projet : le **parcours (a) TikTok** (ou le montage multicam si l'on veut le pire cas) déjà construit, plus un projet **4K** (un plan 3840x2160 de 60 s ; voir « Référence de lecture »).
- Dans Terminal, **hors de l'application**, lancer le relevé thermique : `python -m tools.recette thermal --minutes 20 --interval 30 --output seance-thermique.jsonl`
  (lecture seule de `pmset -g therm` ; il note, toutes les 30 s, le niveau d'avertissement et la limite de vitesse du processeur quand le système bride).
  Il ne donne **pas** la température : celle-ci se note à la main.

## Déroulé (20 min)

À chaque jalon, remplir une ligne. « Images perdues » : le diagnostic matériel de l'application (Préférences › Performance › **Copier le diagnostic** : « Images reçues · affichées ·
perdues »). « Fréquence de lecture » : nombre d'images affichées par seconde mesuré sur 10 s (images affichées au jalon − images affichées 10 s plus tôt, divisé par 10). « Gigue » : à l'œil,
0 = régulier, 1 = saccades rares, 2 = saccades fréquentes, 3 = pénible.

| Jalon | Activité | Images affichées / perdues (cumul) | Fréquence de lecture (i/s) | Gigue 0–3 | Température perçue (dessous du Mac) | Limite thermique du relevé | Aperçu en qualité réduite ? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| t = 0 | Lecture en boucle du montage, 60 s | | | | tiède / chaud | | |
| t = 5 min | Lecture + scrub + coupes (travail normal) | | | | | | |
| t = 10 min | **Export 9:16 lancé** pendant que l'on continue à lire (charge maximale) | | | | | | |
| t = 15 min | Lecture, export en cours ou terminé | | | | | | |
| t = 20 min | Lecture du même passage qu'à t = 0, 60 s | | | | | | |

Observer en plus : le bandeau « qualité d'aperçu » ou toute réduction automatique (résolution d'aperçu, motion blur coupé) ; la durée de l'export lancé à t = 10 min **comparée à
celle d'un export seul** (banc : `python -m tools.recette run --journeys tiktok`) — le **ratio** est le chiffre de throttling ; le moment où le bandeau thermique du relevé apparaît pour la première fois ; l'échauffement
du dessous (tiède → chaud → brûlant).

## Résultat à reporter

- Ratio fréquence de lecture t = 20 / t = 0 : ______ (≈ 1 = pas de bridage).
- Images perdues entre t = 0 et t = 20 : ______.
- Ratio durée d'export sous charge / export seul : ______.
- Premier instant de bridage relevé : ______ min.
- Verdict M1 Air : *tient / dégrade (de ___ % à ___ %) / ne tient pas* ; **joindre** `seance-thermique.jsonl`.

## Référence de lecture (4K et 1080p, 60 s, gigue, images perdues) — les deux Mac

Base de la phase 2. Redémarrer l'application, projet vide, **un seul plan**. Pour chaque ligne : lire 60 s sans toucher à rien, relever images affichées / perdues avant et après (diagnostic),
fréquence moyenne, gigue 0–3, et le choix de rendu du moniteur (GPU Metal ou CPU).

| Média | Machine | Aperçu (Auto / GPU / CPU) | Images affichées / perdues sur 60 s | Fréquence moyenne (i/s) | Gigue 0–3 |
| --- | --- | --- | --- | --- | --- |
| 1080p 25–30 i/s H.264 (`h1.mp4`) | M1 Air | | | | |
| 1080p | M4 mini | | | | |
| 4K 30 i/s H.264 | M1 Air | | | | |
| 4K | M4 mini | | | | |
| 1080p + 3 calques animés (parcours c, début) | M1 Air | | | | |
| 1080p + 3 calques animés | M4 mini | | | | |

Un plan 4K se fabrique avec FFmpeg (aucun fichier à commiter) : `ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=30:duration=60 -vf scale=3840:2160 -c:v libx264 -crf 24 -pix_fmt yuv420p 4k.mp4`.
Variante de contrôle : relancer avec `KUT_STUDIO_GPU_PREVIEW=off` (repli CPU, voir [../gpu-preview.md](../gpu-preview.md)) pour mesurer la différence.
