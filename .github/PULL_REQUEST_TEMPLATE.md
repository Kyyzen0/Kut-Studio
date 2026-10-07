## Pourquoi

<!-- Le problème observé, mesuré si possible (avant / après). -->

## Ce qui change

<!-- Par commit ou par thème, avec les fichiers clés. -->

## Tests

<!-- Ce qui prouve le changement : tests ajoutés (échouent-ils sur l'ancien code ?), rendu réel relu, mesures. -->

## À savoir

<!-- Ce qui n'a pas été vérifié, limites connues, suite prévue. -->

---

- [ ] Suite complète verte : `python -m pytest -q -n auto --timeout=600`
- [ ] `python -m ruff check .` et `python -m mypy` propres, sans module ajouté à la dette de typage
- [ ] Cliquets verts : textes d'interface (`python -m tools.i18n_audit`, fr / en / es), échecs silencieux (allowlist inchangée ou justifiée), couverture (`python tools/coverage_ratchet.py coverage.json`, si la logique change)
- [ ] Documentation à jour (README EN / FR, `docs/`), ligne ajoutée sous `[Unreleased]` dans `CHANGELOG.md` si l'utilisateur voit le changement
- [ ] Interface : capture avant / après, petites fenêtres et trois langues vérifiées

<!-- Détails de chaque garde : CONTRIBUTING.md. -->
