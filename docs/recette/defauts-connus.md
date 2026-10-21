# Défauts connus (trouvés par la recette)

Un défaut ici est soit **corrigé** (PR liée), soit **connu** avec sa PR de suivi. Gabarit : [fiche-defaut.md](fiche-defaut.md).

| Id | Parcours | Constat | Gravité | État |
| --- | --- | --- | --- | --- |
| D-001 | (a) TikTok, étape T7 | **Une transition posée après coup recale le clip entrant vers l'arrière** (le chevauchement vaut la durée de la transition), **sans faire reculer ce qui le suit** : le clip entrant finit plus tôt et un **trou noir** de la durée de la transition apparaît entre lui et le clip suivant. Pire si le clip entrant a déjà été coupé : ses morceaux suivants ne bougent pas, et le trou tombe **au milieu** (ex. : 0,4 s noires entre les deux premiers morceaux d'un plan coupé sur le rythme). Constaté par le banc (analyse « noir » de la sortie, parcours TikTok). `docs/scripting.md` (« Transitions ») décrit le recalage ; rien ne dit que le reste de la piste est décalé. **À trancher** : comportement voulu (poser le chevauchement soi-même, comme le fait le script du banc) ou défaut (propager le décalage sur la piste, comme une édition « ripple ») ? Vérifier le geste de l'interface (T7). | majeur si l'interface a le même effet, mineur si c'est voulu | à qualifier en vraie fenêtre ; **aucune PR de suivi** pour l'instant |
