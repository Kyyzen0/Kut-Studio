# Design QA — Kut-Studio UI evolution

## Evidence

- Source visual truth: `/Users/audrykchesse/Desktop/Capture d’écran 2026-09-30 à 21.57.33.png`
- Implementation screenshot: `docs/kut_studio_redesign_1440x900.png`
- Full-view comparison: `/tmp/kut-design-compare-final.png`
- Viewport: 1440 × 900 logical pixels, device scale factor 1.
- Source pixels: 4096 × 2304. The app region was cropped to 2780 × 1840 at `+570+250`, then normalized to 1440 × 900 for comparison.
- Implementation pixels: 1440 × 900.
- State: dark theme, demo project, first clip selected, scopes collapsed.

## Full-view comparison

The implementation preserves the reference composition: compact top bar, icon rail, media library, central viewer, right inspector, transport strip, and full-width timeline. The dominant green-black surfaces, mint accent, blue/green/purple clip coding, panel dividers, and control density are visually aligned.

The implementation intentionally keeps the existing library folders, favorites, clip metadata, and specialized inspector tools. These are functional product features that the flatter reference mock does not represent. The reference places transform and color controls under “Effets”; the implementation keeps the corrected information architecture: Clip, Couleur, Audio, Effets, with Graphiques and Compositing in the overflow menu.

## Focused comparison

- **Typography:** SF Pro Text / Helvetica Neue fallbacks, compact 9–13 px UI hierarchy, and monospace timecodes match the reference's native macOS-editor character. Small labels remain readable at 1440 × 900.
- **Spacing and layout:** the rail is 52 px, media and inspector retain usable fixed minima, the top bar is 50 px, and the timeline fits all four default tracks without initial vertical scrolling.
- **Colors and tokens:** green-black surfaces and mint accent reuse the centralized theme tokens. Selection, focus, track type, and primary-action states remain distinct.
- **Image and icon fidelity:** this surface does not require photographic assets. Existing vector icons are used consistently; no placeholder drawings or emoji were introduced.
- **Copy and content:** the empty viewer now distinguishes a real timeline gap from a selected clip whose media source is missing. This intentionally improves on the contradictory state in the reference.

## Comparison history

### Iteration 1

Findings: the original implementation had a 112 px labeled rail, duplicated top navigation, six inspector tabs overflowing at 1440 px, a 56 px timeline toolbar, dense two-row track headers, and a viewer message that treated missing media as an empty timeline.

Fixes: compact icon rail, sequence-focused top bar, four primary inspector tabs plus overflow, reduced timeline chrome, single-row track headers with an actions menu, and separate missing-media/empty-timeline states.

Post-fix evidence: `docs/kut_studio_redesign_1440x900.png` shows the corrected proportions and states. The full test suite passes with 1648 passed and 2 skipped.

### Iteration 2

Findings: the first compact timeline still clipped the fourth track and retained excess spacing between rows.

Fixes: reduced header and ruler height, reduced normal track height, and removed row gaps.

Post-fix evidence: the final implementation screenshot shows V1, V2, A1, and S1 simultaneously at 1440 × 900.

## Follow-up polish

- P3: the reference uses flatter media groups while Kut-Studio retains its richer folder and filtering controls.
- P3: the demo project copy differs from the reference filenames because the implementation preserves the existing project fixture and data model.

## Verification

- `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`: 1648 passed, 2 skipped.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python main.py --smoke-test`: passed.
- `git diff --check`: passed.

final result: passed
