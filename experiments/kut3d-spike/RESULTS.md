# Spike Kut-3D, phase 0 : résultats de la mesure des voies de rendu

Scène : cube texturé (damier procédural 256x256, échantillonnage nearest), caméra perspective (FOV 45°), test de
profondeur, éclairage lambert, image 512x512, angle 35°. Machine : macOS (Darwin 27.2), Python 3.14.7, PySide6 6.11.2.
Commande : `python experiments/kut3d-spike/run_spike.py` (une voie = un sous-processus, timeout 30 s).
Le temps par image est celui de la **2e image** (après chauffe), contexte déjà créé, y compris la relecture CPU.

**Tout ce qui suit est mesuré sur macOS uniquement. Windows et Linux n'ont PAS été testés.**

## Tableau par voie

| Voie | Fonctionne | Backend / plateforme Qt | Temps / image | Fenêtre | Écart avec numpy |
|---|---|---|---|---|---|
| numpy (rasteriseur logiciel) | oui | CPU, aucune plateforme Qt | ~20-25 ms | non | référence |
| QRhi (`QRhiWidget` jamais affiché) | oui, **seulement** avec `cocoa` | Metal, `QT_QPA_PLATFORM=cocoa` | ~3-7 ms | non (aucune fenêtre visible) | 99,997 % des pixels à ±12 |
| GL hors écran (`QOffscreenSurface` + FBO) | oui, **seulement** avec `cocoa` | OpenGL 4.1 Core, `cocoa` | ~0,7-1,3 ms | non | 99,997 % des pixels à ±12 |
| moderngl | non testé | module absent de l'environnement (rien installé) | - | - | - |

"Fenêtre : non" = après le rendu, `QGuiApplication.allWindows()` ne contient aucune fenêtre visible (mesuré dans le
worker). Je n'ai pas observé d'icône de Dock ni de fenêtre à l'écran ; la plateforme `cocoa` initialise toutefois
l'application native, ce qui n'est pas la même chose qu'un processus sans interface.

### Messages d'erreur exacts

* GL hors écran, `QT_QPA_PLATFORM=offscreen` et `minimal` : `RuntimeError: QOpenGLContext.create() a échoué`
  (ces deux plugins n'ont pas de contexte OpenGL natif sur macOS).
* QRhi, `offscreen` et `minimal` : `grabFramebuffer()` retourne une image nulle ; le `QRhiWidget` n'obtient jamais
  de QRhi (pas de Metal avec ces plugins), donc `initialize()` / `render()` ne sont pas appelés.
* QRhi « pure » (sans widget) : `QRhi.create(QRhi.Implementation.Metal, QRhiMetalInitParams())` **réussit** (backend
  `Metal`, plateforme `cocoa`), mais `QRhi.beginOffscreenFrame(cb, flags)` ne peut pas servir : il demande un
  `QRhiCommandBuffer` en argument, `QRhiCommandBuffer()` lève `NotImplementedError("Class 'QRhiCommandBuffer' cannot
  be instantiated.")`, et la fonction ne renvoie que `FrameOpResult` (le tampon créé par Qt n'est jamais rendu à
  l'appelant). `beginOffscreenFrame(None)` retourne `FrameOpSuccess` mais on ne peut enregistrer aucune commande.
  Voir `probe_offscreen_frame()` dans `render_qrhi.py`.
  Contournement qui fonctionne : un `QRhiWidget` jamais `show()`, dont `grabFramebuffer()` déclenche `initialize`/`render`
  (c'est le chemin de `ui/gpu_preview.py`, avec lecture par `grabFramebuffer` plutôt que `readBackTexture`).

### Limites rencontrées dans les liaisons PySide6 6.11.2 (toutes contournées dans le code)

* `QRhi` : noms exposés (via `dir(QtGui)`) : `QRhi`, `QRhiBuffer`, `QRhiTexture`, `QRhiGraphicsPipeline`,
  `QRhiMetalInitParams`, `QRhiGles2InitParams`, `QRhiNullInitParams`, `QRhiReadbackDescription/Result`,
  `QRhiTextureRenderTarget`, `QRhiRenderBuffer`, `QShader`… **Absents** : `QRhiVulkanInitParams`,
  `QRhiD3D11InitParams` / `QRhiD3D12InitParams` (donc création directe de Vulkan et Direct3D impossible en Python
  depuis ce build macOS ; ces classes n'existent peut-être que dans les builds de leurs plateformes, non vérifié).
* QRhi exige des shaders `.qsb` : générés à l'exécution avec `pyside6-qsb` (fourni par PySide6) depuis du GLSL 440
  (`shaders/cube.vert`, `cube.frag`). Dépendance de build supplémentaire.
* `QOpenGLExtraFunctions` : `glBufferData` et `glBufferSubData` n'existent pas ; on passe par `QOpenGLBuffer`,
  `QOpenGLVertexArrayObject`, `QOpenGLTexture`.
* `glDrawElements(..., indices)` ne dessine rien (le `ctypes.c_void_p(0)` n'est pas transmis comme valeur ; un `int`
  est refusé) : indices dépliés, `glDrawArrays`. `glGetError` restait à 0 : l'échec est silencieux.
* `QMatrix3x3` construite depuis une liste donne un éclairage faux ; `setUniformValue(nom, float)` laisse le
  uniform à 0. Contournements : `QMatrix4x4` + `mat3(...)`, et un `vec4`. Deux pièges silencieux de plus.

## Ce qui est mesuré / ce qui est supposé

Mesuré : tout le tableau ci-dessus ; les trois voies donnent la même image à l'oeil (PNG dans `out/`, vérifiés) ;
les tests `test_spike.py` comparent GL et QRhi à numpy (≥ 90 % des pixels à ≤ 12 niveaux ; mesuré 99,997 %).

Supposé, **non vérifié** : Windows (Direct3D 11/12 pour QRhi, WARP possible pour du rendu sans GPU), Linux (Vulkan ou
OpenGL via EGL, `offscreen`/`xcb`), machines sans GPU ni pilote, CI Linux sans affichage (le plugin `offscreen` n'a
pas de GL sur macOS ; sur Linux avec EGL/Mesa il en a généralement un, à confirmer). Les temps GPU ne comparent pas
les voies équitablement : GL ne fait qu'un `glDrawArrays` ; la relecture CPU est incluse dans les deux.

## Recommandation pour Kut-3D

1. **Moteur de référence et de test : le rasteriseur numpy** (ou, à terme, son équivalent en Rust/C dans la
   couche native). Mesuré : toujours disponible, aucune fenêtre ni GPU, déterministe bit à bit (test), ~20 ms pour
   512x512 sur un cube de 12 triangles. Il sert d'oracle en CI sur les 4 systèmes ; les voies GPU se testent par
   comparaison avec lui (tolérance documentée). Il n'est pas un moteur de production : le coût est linéaire en
   triangles et en pixels, boucle Python par triangle.
2. **Moteur de production : QRhi** (via `QRhiWidget` pour le moniteur, déjà utilisé par `ui/gpu_preview.py`).
   Argument : même abstraction que l'existant (Metal / D3D11 / OpenGL), un seul code de shader (`.qsb` multi-cibles),
   résultat identique à numpy ici, et le rendu final peut passer par le même chemin que l'aperçu. Réserves mesurées :
   pas d'API hors écran « pure » exploitable en Python (il faut un `QRhiWidget` jamais affiché et la plateforme
   `cocoa` sur macOS ; `offscreen` et `minimal` ne conviennent pas), et le build des `.qsb`.
3. **GL hors écran : plan B seulement.** Plus simple et le plus rapide mesuré, mais OpenGL est déprécié sur macOS
   (4.1 maximum, aucune évolution), les liaisons Python sont lacunaires (voir ci-dessus) et la voie ne rend rien avec
   `offscreen`/`minimal`. À éviter comme base.
4. **moderngl** : non évalué (absent). À mesurer avant décision si on veut une voie GL indépendante de Qt ; il
   dépend aussi d'OpenGL.

Conséquence pratique : pour l'export sans fenêtre (rendu par lot, CI), prévoir l'ordre de repli QRhi puis numpy, avec
détection au démarrage par sous-processus (comme `run_spike.py`) car un échec GPU est silencieux ou fatal.
**À valider avant de s'engager** : Windows et Linux (CI incluse), et si un `QRhiWidget` jamais affiché reste
sans fenêtre sur ces systèmes.

## Relancer

```
python experiments/kut3d-spike/run_spike.py        # tableau + out/cube_<voie>.png
python -m pytest experiments/kut3d-spike -q        # tests (voies GPU skippées si indisponibles)
ruff check experiments/kut3d-spike && ruff format --check experiments/kut3d-spike
```
