"""Banc de recette scripté de Kut-Studio (ADR-0011, phase 1).

Le banc génère des médias de test **déterministes** (aucun binaire dans le dépôt), construit trois projets types par des
scripts déclaratifs (ADR-0005), les exporte par la ligne de commande, **sans fenêtre**, et mesure : durées, tailles, pic de
mémoire, ``ffprobe`` de la sortie, image noire ou gelée, plantage. Le rapport (JSON + Markdown) compare à une référence par
**ratios** : aucun seuil de durée ne fait échouer un test.

    python -m tools.recette run --journeys tiktok,youtube,motion3d --scale full
    python -m tools.recette smoke                  # le smoke test de la CI : un montage de 10 s exporté en CPU

Ce que le banc ne prouve PAS (écran, GPU, lecture, interface) : ``docs/recette/README.md``. La partie humaine de la recette
(vraie fenêtre sur le M1 Air et le M4 mini) est dans ``docs/recette/``.
"""

from __future__ import annotations

GENERATOR_VERSION = 1
"""À incrémenter quand un générateur change ce qu'il écrit : l'empreinte de recette change, les médias sont régénérés."""
REPORT_VERSION = 1
"""Version du schéma du rapport JSON."""
