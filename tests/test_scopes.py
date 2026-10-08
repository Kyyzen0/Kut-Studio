"""Tests des scopes vidéo et du monitoring couleur (tâche 31).

Couvre :

- :mod:`core.scopes` — calculs purs (luminance Rec.709, histogrammes,
  waveform, parade, vectorscope, alertes d'écrêtage) ;
- :mod:`core.scopes_analyzer` — limitation de fréquence, annulation
  des analyses obsolètes, isolation des erreurs ;
- :mod:`core.user_settings` — persistance des préférences de scopes ;
- :mod:`ui.scopes_panel` — disposition, bascule de scope, niveaux,
  alertes, préférences ;
- performances — le coût d'analyse reste linéaire en nombre de
  pixels et borné par la taille des grilles.
"""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest
from timing_budget import WALL_CI_FACTOR, best_cpu_seconds, budget

from core.scopes import (
    ColorSpace,
    SKIN_TONE_LINE_DEGREES,
    ScopeAlerts,
    ScopeFrame,
    ScopeResult,
    VideoLevels,
    analyze_frame,
    compute_alerts,
    compute_histogram,
    compute_parade,
    compute_vectorscope,
    compute_waveform,
    hue_degrees,
    levels_bounds,
    rgb_to_yuv709,
    sample_luminance,
    saturation_percent,
    skin_tone_reference,
)
from core.scopes_analyzer import (
    ScopeAnalysis,
    ScopeAnalyzer,
    ScopeExtractionError,
    ScopeRequest,
)
from core.user_settings import (
    UserSettings,
    load_user_settings,
    save_user_settings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain(analyzer: ScopeAnalyzer, timeout: float = 5.0) -> None:
    """Attend que l'analyseur ait fini sa file (mode asynchrone)."""
    assert analyzer.wait_idle(timeout=timeout), "l'analyseur ne s'est pas vidé"


def _solid_frame(width: int, height: int, rgb: tuple[int, int, int]) -> ScopeFrame:
    """Frame uniforme de la couleur demandée."""
    return ScopeFrame(
        width=width,
        height=height,
        pixels=tuple([rgb] * (width * height)),
    )


def _ramp_frame(width: int, height: int) -> ScopeFrame:
    """Dégradé horizontal rouge → bleu sur une ligne."""
    pixels: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(width):
            t = x / max(width - 1, 1)
            pixels.append((int(255 * t), 0, int(255 * (1 - t))))
    return ScopeFrame(width=width, height=height, pixels=tuple(pixels))


# ---------------------------------------------------------------------------
# Niveaux vidéo
# ---------------------------------------------------------------------------


def test_levels_bounds_video_is_16_235() -> None:
    assert levels_bounds(VideoLevels.VIDEO) == (16.0, 235.0)
    assert levels_bounds("video") == (16.0, 235.0)


def test_levels_bounds_full_is_0_255() -> None:
    assert levels_bounds(VideoLevels.FULL) == (0.0, 255.0)
    assert levels_bounds("full") == (0.0, 255.0)


def test_levels_bounds_unknown_falls_back_to_video() -> None:
    assert levels_bounds("nope") == (16.0, 235.0)


# ---------------------------------------------------------------------------
# Luminance et conversions couleur
# ---------------------------------------------------------------------------


def test_luminance_white_and_black() -> None:
    assert sample_luminance(255, 255, 255) == pytest.approx(255.0, abs=0.01)
    assert sample_luminance(0, 0, 0) == pytest.approx(0.0, abs=0.01)


def test_luminance_uses_rec709_by_default() -> None:
    # Le vert pur pèse 0.7152 en Rec.709 contre 0.587 en Rec.601.
    luma709 = sample_luminance(0, 255, 0)
    luma601 = sample_luminance(0, 255, 0, ColorSpace.REC601)
    assert luma709 > luma601
    assert luma709 == pytest.approx(255 * 0.7152, abs=0.01)


def test_luminance_rec601_known_value() -> None:
    luma = sample_luminance(0, 255, 0, ColorSpace.REC601)
    assert luma == pytest.approx(255 * 0.587, abs=0.01)


def test_luminance_unknown_color_space_falls_back_to_709() -> None:
    assert sample_luminance(0, 255, 0, "nope") == pytest.approx(
        255 * 0.7152, abs=0.01
    )


def test_luminance_is_monotonic_on_grey_ramp() -> None:
    previous = -1.0
    for level in range(0, 256, 16):
        value = sample_luminance(level, level, level)
        assert value > previous
        previous = value


def test_hue_primaries() -> None:
    assert hue_degrees(255, 0, 0) == pytest.approx(0.0, abs=0.5)
    assert hue_degrees(0, 255, 0) == pytest.approx(120.0, abs=0.5)
    assert hue_degrees(0, 0, 255) == pytest.approx(240.0, abs=0.5)


def test_hue_grey_is_zero() -> None:
    assert hue_degrees(128, 128, 128) == 0.0


def test_saturation_percent() -> None:
    assert saturation_percent(255, 255, 255) == pytest.approx(0.0)
    assert saturation_percent(255, 0, 0) == pytest.approx(100.0)
    assert saturation_percent(0, 0, 0) == pytest.approx(0.0)


def test_rgb_to_yuv709_white_is_neutral() -> None:
    y, u, v = rgb_to_yuv709(255, 255, 255)
    assert y == pytest.approx(255.0, abs=0.5)
    assert u == pytest.approx(128.0, abs=0.5)
    assert v == pytest.approx(128.0, abs=0.5)


def test_rgb_to_yuv709_red_has_positive_v() -> None:
    y, u, v = rgb_to_yuv709(255, 0, 0)
    assert y == pytest.approx(0.2126 * 255, abs=0.5)
    assert v > 190.0  # rouge pur : V très haut
    assert u < 100.0  # et U très bas


def test_skin_tone_reference_on_reference_hue() -> None:
    # On fabrique une teinte à SKIN_TONE_LINE_DEGREES.
    import colorsys

    h = SKIN_TONE_LINE_DEGREES / 360.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.5, 1.0)
    distance = skin_tone_reference(r * 255, g * 255, b * 255)
    assert distance == pytest.approx(0.0, abs=1e-6)


def test_skin_tone_reference_grey_is_zero() -> None:
    assert skin_tone_reference(128, 128, 128) == 0.0


def test_skin_tone_reference_far_hue_is_large() -> None:
    # Cyan pur : très loin de la ligne de peau.
    distance = skin_tone_reference(0, 255, 255)
    assert distance > 0.2


# ---------------------------------------------------------------------------
# ScopeFrame
# ---------------------------------------------------------------------------


def test_scope_frame_rejects_bad_dimensions() -> None:
    with pytest.raises(ValueError):
        ScopeFrame(width=0, height=4, pixels=())


def test_scope_frame_rejects_pixel_count_mismatch() -> None:
    with pytest.raises(ValueError):
        ScopeFrame(width=4, height=4, pixels=((0, 0, 0),))


def test_scope_frame_from_rgb_bytes() -> None:
    raw = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
    frame = ScopeFrame.from_rgb_bytes(raw, width=3, height=1)
    assert frame.pixels == ((255, 0, 0), (0, 255, 0), (0, 0, 255))


def test_scope_frame_from_rgba_bytes_ignores_alpha() -> None:
    raw = bytes([255, 0, 0, 128, 0, 255, 0, 64])
    frame = ScopeFrame.from_rgb_bytes(raw, width=2, height=1)
    assert frame.pixels == ((255, 0, 0), (0, 255, 0))


def test_scope_frame_from_bytes_rejects_bad_size() -> None:
    with pytest.raises(ValueError):
        ScopeFrame.from_rgb_bytes(b"\x00\x01", width=4, height=4)


# ---------------------------------------------------------------------------
# Histogrammes
# ---------------------------------------------------------------------------


def test_histogram_counts_all_pixels() -> None:
    frame = _solid_frame(4, 4, (128, 128, 128))
    luma, r, g, b = compute_histogram(frame)
    assert sum(luma) == 16
    assert sum(r) == 16
    assert sum(g) == 16
    assert sum(b) == 16
    # Un gris pur ne produit de l'énergie que sur un canal de
    # luminance ; R et G et B sont tous à 128.
    assert r[128] == 16
    assert luma[128] == 16


def test_histogram_separates_channels() -> None:
    frame = ScopeFrame(
        width=3, height=1, pixels=((255, 0, 0), (0, 255, 0), (0, 0, 255))
    )
    luma, r, g, b = compute_histogram(frame)
    assert r[255] == 1 and r[0] == 2
    assert g[255] == 1 and g[0] == 2
    assert b[255] == 1 and b[0] == 2
    assert sum(luma) == 3


def test_histogram_handles_full_black_and_white() -> None:
    frame = _solid_frame(2, 2, (0, 0, 0))
    luma, _, _, _ = compute_histogram(frame)
    assert luma[0] == 4
    frame = _solid_frame(2, 2, (255, 255, 255))
    luma, _, _, _ = compute_histogram(frame)
    assert luma[255] == 4


# ---------------------------------------------------------------------------
# Waveform
# ---------------------------------------------------------------------------


def test_waveform_shape_and_normalisation() -> None:
    frame = _ramp_frame(16, 4)
    waveform = compute_waveform(frame, columns=8)
    assert len(waveform) == 8
    assert all(len(column) == 256 for column in waveform)
    # Chaque colonne somme à 1 (densité normalisée).
    for column in waveform:
        assert sum(column) == pytest.approx(1.0, abs=1e-6)


def test_waveform_solid_grey_puts_all_density_in_one_level() -> None:
    frame = _solid_frame(8, 8, (128, 128, 128))
    waveform = compute_waveform(frame, columns=4)
    for column in waveform:
        peak_index = max(range(256), key=lambda i: column[i])
        assert peak_index == 128
        assert column[128] == pytest.approx(1.0)


def test_waveform_columns_are_clamped() -> None:
    frame = _solid_frame(2, 2, (10, 10, 10))
    assert len(compute_waveform(frame, columns=1)) == 1
    # 0 et négatif sont ramenés à 1.
    assert len(compute_waveform(frame, columns=0)) == 1
    assert len(compute_waveform(frame, columns=-5)) == 1


# ---------------------------------------------------------------------------
# Parade
# ---------------------------------------------------------------------------


def test_parade_shape_and_normalisation() -> None:
    frame = _ramp_frame(16, 4)
    parade = compute_parade(frame, columns=8)
    assert len(parade) == 8
    for column in parade:
        assert len(column) == 3
        for channel in column:
            assert len(channel) == 256
            assert sum(channel) == pytest.approx(1.0, abs=1e-6)


def test_parade_primaries_land_in_their_channel() -> None:
    frame = ScopeFrame(
        width=1, height=3, pixels=((255, 0, 0), (0, 255, 0), (0, 0, 255))
    )
    parade = compute_parade(frame, columns=1)
    column = parade[0]
    # Canal R : le pixel rouge est à 255.
    assert column[0][255] == pytest.approx(1 / 3, abs=1e-6)
    assert column[1][255] == pytest.approx(1 / 3, abs=1e-6)
    assert column[2][255] == pytest.approx(1 / 3, abs=1e-6)


# ---------------------------------------------------------------------------
# Vectorscope
# ---------------------------------------------------------------------------


def test_vectorscope_shape_and_normalisation() -> None:
    frame = _ramp_frame(16, 8)
    scope = compute_vectorscope(frame, bins=32)
    assert len(scope) == 32
    assert all(len(row) == 32 for row in scope)
    maximum = max(max(row) for row in scope)
    assert maximum == pytest.approx(1.0, abs=1e-6)


def test_vectorscope_grey_is_centred() -> None:
    frame = _solid_frame(8, 8, (128, 128, 128))
    scope = compute_vectorscope(frame, bins=32)
    # Un achromatique n'a pas de chrominance : toute la masse est
    # au centre exact du cercle.
    centre = 16  # bins // 2 - 1
    assert scope[centre][centre] == pytest.approx(1.0)


def test_vectorscope_red_is_off_centre() -> None:
    frame = _solid_frame(8, 8, (255, 0, 0))
    scope = compute_vectorscope(frame, bins=32)
    # La masse est décentrée (le rouge a une chrominance forte).
    total = sum(sum(row) for row in scope)
    assert total > 0
    # Au moins une cellule hors du centre est non nulle.
    off_centre = any(
        scope[r][c] > 0.0
        for r in range(32)
        for c in range(32)
        if abs(r - 16) > 2 or abs(c - 16) > 2
    )
    assert off_centre


def test_vectorscope_bins_are_clamped() -> None:
    frame = _solid_frame(4, 4, (10, 20, 30))
    assert len(compute_vectorscope(frame, bins=1)) == 8  # plancher à 8
    assert len(compute_vectorscope(frame, bins=0)) == 8
    assert len(compute_vectorscope(frame, bins=100000)) == 512  # plafond


# ---------------------------------------------------------------------------
# Alertes d'écrêtage
# ---------------------------------------------------------------------------


def test_alerts_none_for_mid_tone_image() -> None:
    frame = _solid_frame(4, 4, (128, 128, 128))
    alerts = compute_alerts(frame, levels=VideoLevels.VIDEO)
    assert alerts.black_clipping == 0.0
    assert alerts.highlight_clipping == 0.0
    assert alerts.has_any is False


def test_alerts_detect_black_clipping() -> None:
    frame = _solid_frame(4, 4, (0, 0, 0))
    alerts = compute_alerts(frame, levels=VideoLevels.VIDEO)
    assert alerts.black_clipping == pytest.approx(1.0)
    assert alerts.has_any is True


def test_alerts_detect_highlight_clipping() -> None:
    frame = _solid_frame(4, 4, (255, 255, 255))
    alerts = compute_alerts(frame, levels=VideoLevels.VIDEO)
    assert alerts.highlight_clipping == pytest.approx(1.0)


def test_alerts_disabled_in_full_mode() -> None:
    frame = _solid_frame(4, 4, (0, 0, 0))
    alerts = compute_alerts(frame, levels=VideoLevels.FULL)
    assert alerts.black_clipping == 0.0
    assert alerts.highlight_clipping == 0.0


def test_alerts_tolerance_absorbs_noise() -> None:
    # La tolérance est une marge **extérieure** à la plage légale :
    # elle ignore un sous-noir / sur-blanc léger, qui n'est en pratique
    # que du bruit de quantification vidéo.
    frame = _solid_frame(4, 4, (10, 10, 10))
    strict = compute_alerts(frame, levels=VideoLevels.VIDEO, tolerance=0)
    tolerant = compute_alerts(
        frame, levels=VideoLevels.VIDEO, tolerance=8
    )
    assert strict.black_clipping == pytest.approx(1.0)
    assert tolerant.black_clipping == 0.0


def test_alerts_ignore_noise_inside_legal_range() -> None:
    """Un niveau 17 (juste au-dessus du noir légal) n'est pas une
    alerte, même sans tolérance : c'est du bruit, pas du sous-noir."""
    frame = _solid_frame(4, 4, (17, 17, 17))
    alerts = compute_alerts(frame, levels=VideoLevels.VIDEO, tolerance=0)
    assert alerts.black_clipping == 0.0
    assert alerts.highlight_clipping == 0.0


def test_alerts_as_dict() -> None:
    alerts = ScopeAlerts(0.25, 0.5)
    assert alerts.as_dict() == {
        "black_clipping": 0.25,
        "highlight_clipping": 0.5,
    }


# ---------------------------------------------------------------------------
# analyze_frame
# ---------------------------------------------------------------------------


def test_analyze_frame_returns_all_scopes() -> None:
    frame = _ramp_frame(16, 8)
    result = analyze_frame(frame, columns=16, vectorscope_bins=16)
    assert isinstance(result, ScopeResult)
    assert len(result.histogram_luma) == 256
    assert len(result.waveform) == 16
    assert len(result.parade) == 16
    assert len(result.vectorscope) == 16
    assert result.pixel_count == 128
    assert result.columns == 16
    assert result.vectorscope_bins == 16
    assert result.color_space is ColorSpace.REC709
    assert result.levels is VideoLevels.VIDEO


def test_analyze_frame_histogram_rgb_property() -> None:
    result = analyze_frame(_solid_frame(2, 2, (10, 20, 30)), columns=4)
    assert len(result.histogram_rgb) == 3
    assert result.histogram_rgb[0] == result.histogram_r


def test_analyze_frame_respects_color_space() -> None:
    frame = _solid_frame(2, 2, (0, 255, 0))
    rec709 = analyze_frame(frame, color_space=ColorSpace.REC709, columns=4)
    rec601 = analyze_frame(frame, color_space=ColorSpace.REC601, columns=4)
    assert rec709.color_space is ColorSpace.REC709
    assert rec601.color_space is ColorSpace.REC601
    # La luminance du vert diffère entre les deux espaces : les
    # histogrammes ne sont donc pas identiques.
    assert rec709.histogram_luma != rec601.histogram_luma


# ---------------------------------------------------------------------------
# ScopeAnalyzer : limitation de fréquence
# ---------------------------------------------------------------------------


def test_analyzer_throttles_rapid_requests() -> None:
    frame = _solid_frame(4, 4, (50, 50, 50))
    results: list[ScopeAnalysis] = []
    analyzer = ScopeAnalyzer(
        on_result=results.append, min_interval=10.0
    )
    first = analyzer.submit_frame(frame, playhead=0.0, force=True)
    second = analyzer.submit_frame(frame, playhead=0.04, force=False)
    assert first is not None
    # La seconde requête est rejetée : moins de 10 s depuis la
    # précédente.
    assert second is None
    _drain(analyzer)
    assert len(results) == 1


def test_analyzer_force_bypasses_rate_limit() -> None:
    frame = _solid_frame(4, 4, (50, 50, 50))
    results: list[ScopeAnalysis] = []
    analyzer = ScopeAnalyzer(
        on_result=results.append, min_interval=10.0
    )
    analyzer.submit_frame(frame, playhead=0.0, force=True)
    _drain(analyzer)
    analyzer.submit_frame(frame, playhead=1.0, force=True)
    _drain(analyzer)
    # ``force`` ignore la limitation : deux analyses.
    assert len(results) == 2


def test_analyzer_reset_rate_limit_allows_next_request() -> None:
    frame = _solid_frame(4, 4, (50, 50, 50))
    results: list[ScopeAnalysis] = []
    analyzer = ScopeAnalyzer(
        on_result=results.append, min_interval=10.0
    )
    analyzer.submit_frame(frame, playhead=0.0, force=True)
    _drain(analyzer)
    analyzer.reset_rate_limit()
    analyzer.submit_frame(frame, playhead=0.04, force=False)
    _drain(analyzer)
    assert len(results) == 2


def test_analyzer_submit_does_not_block_the_caller() -> None:
    """Le cœur de l'exigence « calculs en arrière-plan ».

    ``submit`` ne doit lancer aucun travail lourd : il dépose la
    demande et rend la main immédiatement, même quand l'extraction
    prend plusieurs secondes.
    """
    release = threading.Event()
    extracted = threading.Event()

    def slow_extractor(request: ScopeRequest) -> ScopeFrame:
        release.wait(timeout=3.0)
        extracted.set()
        return _solid_frame(4, 4, (10, 10, 10))

    analyzer = ScopeAnalyzer(min_interval=0.0, extractor=slow_extractor)
    start = time.perf_counter()
    analyzer.submit(playhead=0.0, ffmpeg_command=["fake"], force=True)
    submit_elapsed = time.perf_counter() - start
    # La propriété elle-même, indépendante de la vitesse du runner : quand submit
    # rend la main, l'extraction (bloquée jusqu'à ``release``) n'est pas finie.
    assert not extracted.is_set(), "submit a attendu la fin de l'extraction"
    # Ceinture et bretelles, en temps MURAL : attendre ne coûte pas de CPU, un
    # submit bloquant passerait un budget CPU. 0,1 ms mesuré en local (avec ou
    # sans couverture) ; seule la préemption d'un runner partagé compte, pas
    # l'instrumentation (quelques lignes exécutées). Un submit bloquant coûte 3 s.
    limit = budget(0.2, ci_factor=WALL_CI_FACTOR, coverage_factor=1.0)
    assert submit_elapsed < limit, (
        f"submit a bloqué le thread appelant ({submit_elapsed:.3f}s, budget {limit:.2f}s)"
    )
    # Le travail est bien en cours, dans le thread de travail.
    deadline = time.monotonic() + 2.0
    while analyzer.stats()["launched"] and not release.is_set():
        if time.monotonic() > deadline:
            break
        time.sleep(0.01)
    release.set()
    _drain(analyzer)
    analyzer.close()


# ---------------------------------------------------------------------------
# ScopeAnalyzer : annulation des analyses obsolètes
# ---------------------------------------------------------------------------


def test_analyzer_marks_stale_results() -> None:
    """Une demande plus récente invalide la précédente.

    On le vérifie en injectant un extracteur lent : la première
    analyse se termine après qu'une demande plus récente a été
    soumise, et doit donc être marquée ``stale`` (et ne pas être
    recalculée).
    """
    started = threading.Event()
    release = threading.Event()

    def slow_extractor(request: ScopeRequest) -> ScopeFrame:
        started.set()
        release.wait(timeout=3.0)
        return _solid_frame(4, 4, (10, 10, 10))

    results: list[ScopeAnalysis] = []
    analyzer = ScopeAnalyzer(
        on_result=results.append, min_interval=0.0, extractor=slow_extractor
    )
    analyzer.submit(playhead=0.0, ffmpeg_command=["fake"], force=True)
    assert started.wait(timeout=2.0)
    # Simule l'arrivée d'une demande plus récente pendant
    # l'extraction : la première devient obsolète.
    analyzer.submit(playhead=9.0, ffmpeg_command=["fake"], force=True)
    release.set()
    _drain(analyzer)
    assert results, "aucun résultat notifié"
    assert results[0].stale is True
    # Le résultat obsolète n'a pas été calculé : grille à zéro.
    assert sum(results[0].result.histogram_luma) == 0
    analyzer.close()


def test_analyzer_pending_request_is_replaced_by_the_newest() -> None:
    """File « le dernier gagne » : une demande en attente est écrasée."""
    release = threading.Event()
    entered = threading.Event()

    def slow_extractor(request: ScopeRequest) -> ScopeFrame:
        entered.set()
        release.wait(timeout=3.0)
        return _solid_frame(4, 4, (10, 10, 10))

    delivered: list[ScopeAnalysis] = []
    analyzer = ScopeAnalyzer(
        on_result=delivered.append, min_interval=0.0,
        extractor=slow_extractor,
    )
    analyzer.submit(playhead=0.0, ffmpeg_command=["fake"], force=True)
    assert entered.wait(timeout=2.0)
    for playhead in (1.0, 2.0, 3.0):
        analyzer.submit(
            playhead=playhead, ffmpeg_command=["fake"], force=True
        )
    release.set()
    _drain(analyzer)
    # Seules deux analyses sont réellement traitées : la première
    # (obsolète) et la dernière (la seule encore d'actualité).
    assert len(delivered) == 2
    assert delivered[-1].stale is False
    assert delivered[-1].request.playhead == 3.0
    analyzer.close()


def test_analyzer_cancel_all_prevents_result_delivery() -> None:
    frame = _solid_frame(4, 4, (30, 30, 30))
    results: list[ScopeAnalysis] = []
    analyzer = ScopeAnalyzer(
        on_result=results.append, min_interval=0.0
    )
    analyzer.submit_frame(frame, playhead=0.0, force=True)
    _drain(analyzer)
    assert len(results) == 1
    analyzer.cancel_all()
    # Après annulation, une nouvelle requête porte un identifiant plus
    # élevé et n'est pas bloquée.
    analyzer.submit_frame(frame, playhead=1.0, force=True)
    _drain(analyzer)
    assert len(results) == 2
    analyzer.close()


def test_analyzer_stats() -> None:
    frame = _solid_frame(4, 4, (60, 60, 60))
    analyzer = ScopeAnalyzer(min_interval=0.0)
    analyzer.submit_frame(frame, playhead=0.0, force=True)
    _drain(analyzer)
    stats = analyzer.stats()
    assert stats["launched"] == 1
    assert stats["completed"] == 1
    analyzer.close()


# ---------------------------------------------------------------------------
# ScopeAnalyzer : isolation des erreurs
# ---------------------------------------------------------------------------


def test_analyzer_reports_extraction_error_without_raising() -> None:
    errors: list[tuple[ScopeRequest, BaseException]] = []
    analyzer = ScopeAnalyzer(
        on_error=lambda req, exc: errors.append((req, exc)),
        min_interval=0.0,
    )
    # Sans commande et sans extracteur : l'analyseur doit remonter
    # l'erreur au callback, pas propager l'exception.
    analyzer.submit(playhead=0.0, ffmpeg_command=[], force=True)
    _drain(analyzer)
    assert len(errors) == 1
    assert isinstance(errors[0][1], ScopeExtractionError)
    analyzer.close()


def test_analyzer_cleans_request_temporary_paths(tmp_path) -> None:
    """Un SRT généré pour les scopes ne survit jamais à son analyse."""
    temporary_subtitle = tmp_path / "scope-subtitle.srt"
    temporary_subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
    analyzer = ScopeAnalyzer(
        min_interval=0.0,
        extractor=lambda _request: _solid_frame(2, 2, (10, 20, 30)),
    )
    analyzer.submit(
        playhead=0.0,
        ffmpeg_command=["fake"],
        temporary_paths=[str(temporary_subtitle)],
        force=True,
    )
    _drain(analyzer)
    assert not temporary_subtitle.exists()
    analyzer.close()


def test_analyzer_cleans_pending_temporary_paths_on_cancel(tmp_path) -> None:
    """Une requête de scopes annulée libère aussi son fichier SRT."""
    entered = threading.Event()
    release = threading.Event()

    def slow_extractor(_request):
        entered.set()
        assert release.wait(timeout=2.0)
        return _solid_frame(2, 2, (10, 20, 30))

    active = tmp_path / "active.srt"
    pending = tmp_path / "pending.srt"
    active.write_text("active")
    pending.write_text("pending")
    analyzer = ScopeAnalyzer(min_interval=0.0, extractor=slow_extractor)
    analyzer.submit(
        playhead=0.0,
        ffmpeg_command=["fake"],
        temporary_paths=[str(active)],
        force=True,
    )
    assert entered.wait(timeout=2.0)
    analyzer.submit(
        playhead=1.0,
        ffmpeg_command=["fake"],
        temporary_paths=[str(pending)],
        force=True,
    )
    analyzer.cancel_all()
    assert not pending.exists()
    release.set()
    _drain(analyzer)
    assert not active.exists()
    analyzer.close()


def test_analyzer_error_does_not_break_later_requests() -> None:
    frame = _solid_frame(4, 4, (70, 70, 70))
    results: list[ScopeAnalysis] = []
    errors: list = []
    analyzer = ScopeAnalyzer(
        on_result=results.append,
        on_error=lambda req, exc: errors.append(exc),
        min_interval=0.0,
    )
    # Première requête : erreur (commande vide).
    analyzer.submit(playhead=0.0, ffmpeg_command=[], force=True)
    _drain(analyzer)
    # Seconde requête : succès.
    analyzer.submit_frame(frame, playhead=1.0, force=True)
    _drain(analyzer)
    assert len(errors) == 1
    assert len(results) == 1
    analyzer.close()


# ---------------------------------------------------------------------------
# ScopeAnalyzer : concurrence
# ---------------------------------------------------------------------------


def test_analyzer_is_thread_safe() -> None:
    """Plusieurs threads soumettent des requêtes : aucun crash, aucun
    résultat corrompu (chaque ``ScopeAnalysis`` porte son propre
    ``request``)."""
    frame = _solid_frame(4, 4, (80, 80, 80))
    results: list[ScopeAnalysis] = []
    results_lock = threading.Lock()
    analyzer = ScopeAnalyzer(min_interval=0.0)

    def on_result(analysis: ScopeAnalysis) -> None:
        with results_lock:
            results.append(analysis)

    analyzer._on_result = on_result

    def worker(index: int) -> None:
        for step in range(5):
            analyzer.submit_frame(
                frame, playhead=index * 10 + step, force=True
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    _drain(analyzer)
    # Les 20 demandes sontparties, mais seules celles qui n'ont pas
    # été écrasées par une plus récente produisent un résultat.
    assert analyzer.stats()["launched"] == 20
    assert 1 <= len(results) <= 20
    # Chaque résultat est cohérent avec sa propre requête.
    for analysis in results:
        assert isinstance(analysis.request, ScopeRequest)
        assert isinstance(analysis.result, ScopeResult)
        assert analysis.request.playhead >= 0.0
    # Le tout dernier résultat n'est jamais obsolète.
    assert results[-1].stale is False
    analyzer.close()


def test_analyzer_does_not_crash_under_repeated_cancel() -> None:
    frame = _solid_frame(4, 4, (90, 90, 90))
    analyzer = ScopeAnalyzer(min_interval=0.0)
    stop = threading.Event()

    def submitter() -> None:
        while not stop.is_set():
            analyzer.submit_frame(frame, playhead=1.0, force=True)
            analyzer.cancel_all()

    thread = threading.Thread(target=submitter)
    thread.start()
    time.sleep(0.2)
    stop.set()
    thread.join(timeout=3.0)
    # Aucun crash : le thread s'est arrêté proprement.
    assert not thread.is_alive()
    analyzer.close()


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


def test_analysis_cost_is_linear_in_pixels() -> None:
    """Le coût d'analyse croît linéairement avec le nombre de pixels.

    On mesure sur deux images de tailles 4× et 8× : le rapport doit
    rester dans une fourchette large (on ne veut pas de complexité
    quadratique déguisée, mais on ne veut pas non plus un test
    flaky sur une machine chargée).
    """
    small = _solid_frame(32, 32, (100, 100, 100))
    large = _solid_frame(64, 64, (100, 100, 100))

    start = time.perf_counter()
    analyze_frame(small, columns=64, vectorscope_bins=32)
    small_time = time.perf_counter() - start

    start = time.perf_counter()
    analyze_frame(large, columns=64, vectorscope_bins=32)
    large_time = time.perf_counter() - start

    # 4× plus de pixels → on tolère jusqu'à 20× le temps (marge
    # large pour la variabilité des machines de CI).
    assert large_time < max(small_time * 20.0, 2.0)


def test_analysis_is_bounded_by_grid_size_not_pixels() -> None:
    """Le coût dominant est la taille des grilles, pas le nombre de
    pixels : deux images de tailles très différentes avec la même
    taille de grille produisent des résultats de dimensions
    identiques."""
    small = analyze_frame(_solid_frame(8, 8, (1, 2, 3)), columns=32,
                          vectorscope_bins=32)
    large = analyze_frame(_solid_frame(64, 64, (1, 2, 3)), columns=32,
                          vectorscope_bins=32)
    assert small.columns == large.columns == 32
    assert small.vectorscope_bins == large.vectorscope_bins == 32
    assert small.pixel_count != large.pixel_count


def test_realistic_frame_analysis_is_fast_enough() -> None:
    """Une image 320×180 (format DVD) s'analyse en moins de 500 ms.

    C'est la borne haute acceptable pour du temps réel à 10 img/s
    : au-delà, l'analyse concurrente par rapport au tick de lecture
    deviendrait le goulot d'étranglement.
    """
    frame = _ramp_frame(320, 180)
    # Temps CPU du meilleur de 3 essais (comme le test 1080p) : le temps mural
    # comptait la préemption par les autres workers xdist (0,535 s observé sur
    # un runner Windows, 0,80 s sur le run couvert Linux). Mesuré le 2026-10-07
    # sur un Mac M : 0,042 s (0,05 à 0,07 s avec 8 processus en parallèle) ;
    # 0,206 s sous le traceur C de coverage.py (Python 3.11, branches).
    elapsed = best_cpu_seconds(lambda: analyze_frame(frame, columns=320, vectorscope_bins=128))
    limit = budget(0.5)
    assert elapsed < limit, f"Analyse trop lente : {elapsed:.3f}s (budget {limit:.2f}s)"


# ---------------------------------------------------------------------------
# Performance : budget d'échantillonnage
# ---------------------------------------------------------------------------


def test_sampling_stride_is_one_for_small_images() -> None:
    from core.scopes import MAX_SCOPE_SAMPLES, sampling_stride

    assert sampling_stride(1024) == 1
    assert sampling_stride(MAX_SCOPE_SAMPLES) == 1


def test_sampling_stride_keeps_the_sample_budget() -> None:
    from core.scopes import MAX_SCOPE_SAMPLES, sampling_stride

    # 1080p doit être ramenée sous le budget d'échantillons.
    stride = sampling_stride(1920 * 1080)
    assert stride > 1
    assert (1920 * 1080) // stride <= MAX_SCOPE_SAMPLES * 1.1
    # Le pas reste petit : on ne veut pas d'un échantillon sur 100.
    assert stride <= 32


def test_small_frames_are_analysed_exactly() -> None:
    """Sous le budget, l'analyse est exhaustive : l'histogramme compte
    exactement tous les pixels, sans approximation."""
    frame = _solid_frame(8, 8, (10, 20, 30))
    result = analyze_frame(frame, columns=8, vectorscope_bins=16)
    assert sum(result.histogram_luma) == 64
    assert sum(result.histogram_r) == 64
    assert sum(result.histogram_g) == 64
    assert sum(result.histogram_b) == 64
    assert result.pixel_count == 64


def test_1080p_analysis_stays_within_realtime_budget() -> None:
    """Une image 1080p doit rester analysable en moins de 250 ms.

    C'est ce qui permet de tenir la cadence limitée à 10 analyses
    par seconde pendant la lecture : l'extraction FFmpeg et l'analyse
    doivent rester devant le tick de 25 Hz.
    """
    from core.scopes import MAX_SCOPE_SAMPLES

    pixels = []
    state = 12345
    for _ in range(1920 * 1080):
        state = (1103515245 * state + 12345) % (1 << 31)
        pixels.append((state % 256, (state >> 8) % 256, (state >> 16) % 256))
    frame = ScopeFrame(width=1920, height=1080, pixels=tuple(pixels))
    # Temps CPU (et non mural) du meilleur de trois essais : insensible à la
    # préemption par les autres workers de tests et d'un runner de CI partagé.
    results: list[ScopeAnalysis] = []
    elapsed = best_cpu_seconds(
        lambda: results.append(analyze_frame(frame, columns=320, vectorscope_bins=128))
    )
    assert sum(results[-1].histogram_luma) <= MAX_SCOPE_SAMPLES * 1.1
    # Budget nominal de 250 ms (0,057 s mesuré le 2026-10-07 sur un Mac M,
    # 0,10 à 0,13 s avec 8 processus en parallèle), ×2,4 sur un runner de CI
    # partagé (0,6 s). Le run couvert de la CI Linux a mesuré 0,872 s : le
    # traceur C de coverage.py (Python 3.11) multiplie ce temps par ~4
    # (0,059 → 0,233 s en local), d'où la majoration d'instrumentation (3 s).
    limit = budget(0.25)
    assert elapsed < limit, f"Analyse 1080p trop lente : {elapsed:.3f}s (budget {limit:.2f}s)"


def test_sampled_1080p_histogram_stays_representative() -> None:
    """Le sous-échantillonnage ne doit pas fausser la lecture.

    On compare la distribution de luminance d'une image 1080p
    analysée telle quelle et analysée via l'accumulateur : les
    niveaux dominants doivent rester dans le même ordre.
    """
    from core.scopes import _accumulate, _coerce_color_space, _coerce_levels

    pixels = []
    state = 999
    for index in range(1920 * 1080):
        state = (1103515245 * state + 12345) % (1 << 31)
        pixels.append((state % 256, (state >> 8) % 256, (state >> 16) % 256))
    frame = ScopeFrame(width=1920, height=1080, pixels=tuple(pixels))
    sampled = _accumulate(
        frame,
        columns=32, bins=16,
        color_space=_coerce_color_space("rec709"),
        levels=_coerce_levels("video"),
        tolerance=0,
        stride=16,
    )
    reference = compute_histogram(frame)
    # Les deux histograms doivent garder la même forme générale :
    # la moyenne de luminance doit rester proche.
    def mean_level(hist: tuple[int, ...]) -> float:
        total = sum(hist)
        return sum(i * n for i, n in enumerate(hist)) / total

    assert abs(
        mean_level(sampled.histogram_luma) - mean_level(reference[0])
    ) < 2.0


# ---------------------------------------------------------------------------
# Persistance des préférences
# ---------------------------------------------------------------------------


def test_user_settings_scopes_defaults() -> None:
    settings = UserSettings()
    assert settings.scopes_visible is True
    assert settings.scopes_layout == "quad"
    assert settings.scopes_view == "waveform"
    assert settings.scopes_levels == "video"
    assert settings.scopes_alerts_enabled is False


def test_user_settings_scopes_roundtrip(tmp_path) -> None:
    # ``UserSettings`` est immuable : on construit une variante.
    settings = dataclasses.replace(
        UserSettings(),
        scopes_visible=False,
        scopes_layout="single",
        scopes_view="vectorscope",
        scopes_levels="full",
        scopes_alerts_enabled=True,
    )
    save_user_settings(settings, tmp_path)

    loaded = load_user_settings(tmp_path)
    assert loaded.scopes_visible is False
    assert loaded.scopes_layout == "single"
    assert loaded.scopes_view == "vectorscope"
    assert loaded.scopes_levels == "full"
    assert loaded.scopes_alerts_enabled is True


def test_user_settings_scopes_invalid_values_fall_back(tmp_path) -> None:
    """Des préférences corrompues retombent sur des valeurs sûres.

    - les énumérations retombent sur leur défaut ;
    - un booléen illisible (``"maybe"``) est traité comme faux, ce
      qui laisse le panneau de scopes replié plutôt que d'ouvrir une
      vue vide sur une préférence abstraite.
    """
    import json

    settings_file = tmp_path / "user_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "scopes_layout": "nope",
                "scopes_view": "nope",
                "scopes_levels": "nope",
                "scopes_visible": "maybe",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_user_settings(tmp_path)
    assert loaded.scopes_layout == "quad"
    assert loaded.scopes_view == "waveform"
    assert loaded.scopes_levels == "video"
    assert loaded.scopes_visible is False


def test_user_settings_missing_scopes_keys_use_defaults(tmp_path) -> None:
    """Un fichier de réglages écrit avant la tâche 31 charge sans
    erreur : les préférences de scopes prennent leurs défauts."""
    import json

    settings_file = tmp_path / "user_settings.json"
    settings_file.write_text(
        json.dumps({"theme_mode": "dark", "language": "fr"}),
        encoding="utf-8",
    )
    loaded = load_user_settings(tmp_path)
    assert loaded.scopes_layout == "quad"
    assert loaded.scopes_view == "waveform"
    assert loaded.scopes_levels == "video"


# ---------------------------------------------------------------------------
# ScopesPanel (UI)
# ---------------------------------------------------------------------------


def _panel(qtbot):
    """Crée un panneau de scopes attaché à une fenêtre de test."""
    from ui.scopes_panel import ScopesPanel

    panel = ScopesPanel()
    qtbot.addWidget(panel)
    panel.resize(720, 460)
    return panel


def _visible_canvases(panel) -> list:
    """Canvas effectivement présents dans la grille du panneau."""
    return [
        canvas
        for canvas in panel._canvases.values()
        if canvas.parentWidget() is not None
    ]


def test_panel_exposes_four_scopes_in_quad_layout(qtbot) -> None:
    from ui.scopes_panel import ScopeLayout

    panel = _panel(qtbot)
    assert panel.layout_mode() is ScopeLayout.QUAD
    assert len(_visible_canvases(panel)) == 4


def test_panel_single_layout_shows_only_the_selected_scope(qtbot) -> None:
    from ui.scopes_panel import ScopeLayout, ScopeView

    panel = _panel(qtbot)
    panel.set_single_view(ScopeView.VECTORSCOPE)
    panel.set_layout_mode(ScopeLayout.SINGLE)
    assert panel.layout_mode() is ScopeLayout.SINGLE
    visible = _visible_canvases(panel)
    assert len(visible) == 1
    assert visible[0].view() is ScopeView.VECTORSCOPE
    # Le sélecteur de scope n'a de sens qu'en disposition unique.
    assert panel.view_combo.isVisibleTo(panel)


def test_panel_layout_change_emits_signal(qtbot) -> None:
    from ui.scopes_panel import ScopeLayout

    panel = _panel(qtbot)
    seen: list[str] = []
    panel.layout_changed.connect(seen.append)
    panel.set_layout_mode(ScopeLayout.SINGLE)
    assert seen == ["single"]


def test_panel_set_result_feeds_every_canvas(qtbot) -> None:
    panel = _panel(qtbot)
    assert panel.result() is None
    result = analyze_frame(_ramp_frame(32, 32), columns=32,
                           vectorscope_bins=32)
    panel.set_result(result)
    assert panel.result() is result
    for canvas in panel._canvases.values():
        assert canvas.result() is result


def test_panel_levels_change_is_propagated_and_announced(qtbot) -> None:
    panel = _panel(qtbot)
    seen: list[str] = []
    panel.levels_changed.connect(seen.append)
    panel.set_levels(VideoLevels.FULL)
    assert panel.levels() is VideoLevels.FULL
    assert seen == ["full"]
    for canvas in panel._canvases.values():
        assert canvas._levels is VideoLevels.FULL


def test_panel_ignores_invalid_levels(qtbot) -> None:
    panel = _panel(qtbot)
    panel.set_levels(VideoLevels.FULL)
    panel.set_levels("nope")
    assert panel.levels() is VideoLevels.FULL


def test_panel_refresh_button_requests_refresh(qtbot) -> None:
    panel = _panel(qtbot)
    with qtbot.waitSignal(panel.refresh_requested, timeout=1000):
        panel.refresh_button.click()


def test_panel_alerts_hidden_when_disabled(qtbot) -> None:
    panel = _panel(qtbot)
    result = analyze_frame(
        _solid_frame(8, 8, (0, 0, 0)), columns=16, vectorscope_bins=16
    )
    assert result.alerts.black_clipping > 0.0
    panel.set_result(result)
    # Alertes désactivées par défaut : rien ne doit apparaître.
    assert not panel._black_alert.isVisibleTo(panel)


def test_panel_alerts_shown_when_enabled(qtbot) -> None:
    panel = _panel(qtbot)
    panel.set_alerts_enabled(True)
    assert panel.alerts_enabled() is True
    panel.set_result(
        analyze_frame(
            _solid_frame(8, 8, (0, 0, 0)), columns=16, vectorscope_bins=16
        )
    )
    assert panel._black_alert.isVisibleTo(panel)
    assert "%" in panel._black_alert.text()
    assert not panel._white_alert.isVisibleTo(panel)
    # Une image propre fait disparaître l'alerte et remet le libellé.
    panel.set_result(
        analyze_frame(
            _solid_frame(8, 8, (128, 128, 128)), columns=16,
            vectorscope_bins=16,
        )
    )
    assert not panel._black_alert.isVisibleTo(panel)
    assert "%" not in panel._black_alert.text()


def test_panel_preferences_roundtrip(qtbot) -> None:
    from ui.scopes_panel import ScopeLayout, ScopeView

    panel = _panel(qtbot)
    panel.set_layout_mode(ScopeLayout.SINGLE)
    panel.set_single_view(ScopeView.PARADE)
    panel.set_levels(VideoLevels.FULL)
    panel.set_alerts_enabled(True)
    prefs = panel.preferences()
    assert prefs["layout"] == "single"
    assert prefs["single_view"] == "parade"
    assert prefs["levels"] == "full"
    assert prefs["alerts_enabled"] == "1"

    restored = _panel(qtbot)
    restored.apply_preferences(prefs)
    assert restored.layout_mode() is ScopeLayout.SINGLE
    assert restored.single_view() is ScopeView.PARADE
    assert restored.levels() is VideoLevels.FULL
    assert restored.alerts_enabled() is True


def test_panel_apply_preferences_ignores_garbage(qtbot) -> None:
    from ui.scopes_panel import ScopeLayout

    panel = _panel(qtbot)
    panel.apply_preferences(
        {"layout": "nope", "single_view": "nope", "levels": "nope"}
    )
    assert panel.layout_mode() is ScopeLayout.QUAD
    assert panel.levels() is VideoLevels.VIDEO


def test_panel_paints_every_scope(qtbot) -> None:
    """Les quatre scopes se dessinent réellement (pas d'exception, ni
    de zone vide) pour un résultat non trivial."""
    panel = _panel(qtbot)
    panel.set_result(analyze_frame(_ramp_frame(64, 64), columns=64,
                                   vectorscope_bins=32))
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)
    for view, canvas in panel._canvases.items():
        image = canvas.grab().toImage()
        assert not image.isNull(), f"{view.value} : rendu vide"
        assert image.width() > 0 and image.height() > 0
        # Une waveform trace point par point : on échantillonne tous
        # les pixels, sinon une trace d'un pixel de haut passe au
        # travers du sous-échantillonnage.
        colors = {
            image.pixel(x, y)
            for x in range(image.width())
            for y in range(image.height())
        }
        assert len(colors) > 2, f"{view.value} : scope non dessinée"


def test_panel_shows_placeholder_without_result(qtbot) -> None:
    from ui.scopes_panel import ScopeView

    panel = _panel(qtbot)
    panel.show()
    qtbot.waitExposed(panel)
    image = panel._canvases[ScopeView.HISTOGRAM].grab().toImage()
    colors = {
        image.pixel(x, y)
        for x in range(0, image.width(), 3)
        for y in range(0, image.height(), 3)
    }
    assert len(colors) > 1, "aucun placeholder dessiné"


# ---------------------------------------------------------------------------
# Intégration MainWindow
# ---------------------------------------------------------------------------


class _FakeAnalyzer:
    """Analyseur de scopes de substitution : enregistre les requêtes."""

    def __init__(self) -> None:
        self.submits: list[dict] = []
        self.cancelled = 0
        self.closed = False

    def would_accept(self, *, force: bool = False) -> bool:
        return not self.closed

    def submit(self, **kwargs) -> object:
        self.submits.append(kwargs)
        return kwargs

    def cancel_all(self) -> None:
        self.cancelled += 1

    def close(self) -> None:
        self.closed = True


def _window(qtbot, monkeypatch, config_dir=None):
    """MainWindow isolée pour les tests de scopes.

    ``config_dir`` permet de réutiliser le même répertoire de
    préférences entre deux fenêtres (test de persistance).
    """
    import tempfile


    from ui.main_window import MainWindow

    monkeypatch.setenv(
        "KUT_STUDIO_CONFIG_DIR",
        str(config_dir or tempfile.mkdtemp(prefix="kut-scopes-test-")),
    )
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.information", lambda *_, **__: None
    )
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.critical", lambda *_, **__: None
    )
    window = MainWindow()
    qtbot.addWidget(window)
    if getattr(window, "timeline_timer", None) is not None:
        window.timeline_timer.stop()
    return window


def _scopes_window(qtbot, monkeypatch):
    """Fenêtre avec un faux analyseur et une commande d'extraction
    valide, pour tester le câblage sans lancer FFmpeg."""
    window = _window(qtbot, monkeypatch)
    analyzer = _FakeAnalyzer()
    window.scopes_analyzer.close()
    window.scopes_analyzer = analyzer
    # La commande est fabriquée par le thread de l'analyseur : la fenêtre ne lui remet qu'une fabrique.
    window._prepare_scopes_command = lambda playhead: (lambda: ([
        "ffmpeg", "-i", "clip.mp4", "-frames:v", "1",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ], ()))
    window._scopes_visible = True
    return window, analyzer


def test_scopes_splitter_holds_viewer_and_panel(qtbot, monkeypatch) -> None:
    window = _window(qtbot, monkeypatch)
    host = window._viewer_host
    assert host.objectName() == "viewer_with_scopes"
    assert host.count() == 2
    # La zone du haut est la pile du moniteur : visionneuse ordinaire ou moniteur Multicam (même emplacement).
    assert host.widget(0) is window._monitor_stack
    assert window._monitor_stack.widget(0) is window.preview_panel
    assert host.widget(1) is window.scopes_panel
    # Le panneau est redimensionnable : il ne doit pas être figé.
    assert host.childrenCollapsible() is False


def test_toggle_scopes_visible_updates_splitter_and_persists(
    qtbot, monkeypatch,
) -> None:
    from core.user_settings import load_user_settings
    from ui.scopes_panel import ScopeLayout

    window = _window(qtbot, monkeypatch)
    window._scopes_visible = False
    window.toggle_scopes_visible()
    assert window._scopes_visible is True
    assert window.scopes_action.isChecked() is True
    sizes = window._viewer_host.sizes()
    assert sizes[1] > 0, "le panneau visible doit occuper de la hauteur"

    window.toggle_scopes_visible()
    assert window._scopes_visible is False
    assert window.scopes_action.isChecked() is False
    assert window._viewer_host.sizes()[1] == 0

    # La préférence est bien écrite sur le disque.
    settings = load_user_settings()
    assert settings.scopes_visible is False
    assert settings.scopes_layout in ("quad", "single")
    assert ScopeLayout(settings.scopes_layout) is not None


def test_scopes_preferences_survive_a_restart(qtbot, monkeypatch, tmp_path) -> None:
    import os as _os

    from core.user_settings import load_user_settings
    from ui.scopes_panel import ScopeLayout, ScopeView

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    window = _window(qtbot, monkeypatch, config_dir=config_dir)
    window.scopes_panel.set_layout_mode(ScopeLayout.SINGLE)
    window.scopes_panel.set_single_view(ScopeView.VECTORSCOPE)
    window.scopes_panel.set_levels(VideoLevels.FULL)
    window.close()

    # Une nouvelle fenêtre repart de l'état persisté.
    second = _window(qtbot, monkeypatch, config_dir=config_dir)
    assert second.scopes_panel.layout_mode() is ScopeLayout.SINGLE
    assert second.scopes_panel.single_view() is ScopeView.VECTORSCOPE
    assert second.scopes_panel.levels() is VideoLevels.FULL
    # Le fichier n'a pas été réécrit avec les valeurs par défaut.
    assert load_user_settings(config_dir).scopes_view == "vectorscope"
    assert _os.path.exists(config_dir / "user_settings.json")


def test_request_scopes_analysis_submits_to_analyzer(
    qtbot, monkeypatch,
) -> None:
    window, analyzer = _scopes_window(qtbot, monkeypatch)
    window.playhead_seconds = 3.0
    window._request_scopes_analysis(force=True)
    assert len(analyzer.submits) == 1
    submit = analyzer.submits[0]
    assert submit["playhead"] == 3.0
    assert submit["levels"] is VideoLevels.VIDEO
    assert submit["force"] is True
    command, temporary = submit["command_factory"]()
    assert command[0] == "ffmpeg" and temporary == ()


def test_request_scopes_analysis_skips_identical_playhead(
    qtbot, monkeypatch,
) -> None:
    window, analyzer = _scopes_window(qtbot, monkeypatch)
    window.playhead_seconds = 3.0
    window._request_scopes_analysis()
    window.playhead_seconds = 3.0005
    window._request_scopes_analysis()
    assert len(analyzer.submits) == 1
    # Un déplacement réel passe, même sans ``force``.
    window.playhead_seconds = 5.0
    window._request_scopes_analysis()
    assert len(analyzer.submits) == 2


def test_request_scopes_analysis_is_noop_when_hidden(
    qtbot, monkeypatch,
) -> None:
    window, analyzer = _scopes_window(qtbot, monkeypatch)
    window._scopes_visible = False
    window.playhead_seconds = 3.0
    window._request_scopes_analysis(force=True)
    assert analyzer.submits == []


def test_color_change_forces_immediate_scopes_refresh(
    qtbot, monkeypatch,
) -> None:
    """En pause, un changement couleur relance les scopes sans attendre."""
    window, analyzer = _scopes_window(qtbot, monkeypatch)
    window.playhead_seconds = 2.0
    assert window.is_playing is False
    window._refresh_color_monitor("clip-1")
    assert len(analyzer.submits) == 1
    assert analyzer.submits[0]["force"] is True


def test_playback_tick_requests_throttled_scopes(
    qtbot, monkeypatch,
) -> None:
    """Pendant la lecture, le tick demande une analyse mais sans forcer."""
    window, analyzer = _scopes_window(qtbot, monkeypatch)
    window.playhead_seconds = 2.0
    # On force l'état « en lecture » sans démarrer le transport réel.
    window._scopes_visible = True
    window.is_playing = True
    try:
        window._request_scopes_analysis()
        assert len(analyzer.submits) == 1
        assert analyzer.submits[0]["force"] is False
    finally:
        window.is_playing = False


def test_scopes_result_is_applied_in_the_gui_thread(
    qtbot, monkeypatch,
) -> None:
    """Le résultat arrive depuis le thread de travail mais le panneau
    n'est touché qu'après rebasculement dans la boucle Qt."""
    window, _ = _scopes_window(qtbot, monkeypatch)
    result = analyze_frame(_ramp_frame(16, 16), columns=16,
                           vectorscope_bins=16)
    analysis = ScopeAnalysis(
        request=ScopeRequest(
            request_id=1, playhead=0.0, ffmpeg_command=("fake",)
        ),
        result=result,
        elapsed_seconds=0.01,
        stale=False,
    )
    # Appelé depuis un VRAI thread de travail, comme en production : l'ancienne version l'appelait depuis le
    # thread principal et ne vérifiait donc pas la bascule (QTimer.singleShot posté d'un thread ne s'exécute jamais).
    import threading

    worker = threading.Thread(target=lambda: window._on_scopes_analysis_ready(analysis))
    worker.start()
    worker.join()
    qtbot.waitUntil(lambda: window.scopes_panel.result() is not None,
                    timeout=3000)
    assert window.scopes_panel.result() is result


def test_stale_scopes_result_is_ignored(qtbot, monkeypatch) -> None:
    window, _ = _scopes_window(qtbot, monkeypatch)
    stale = ScopeAnalysis(
        request=ScopeRequest(
            request_id=1, playhead=0.0, ffmpeg_command=("fake",)
        ),
        result=analyze_frame(_ramp_frame(16, 16), columns=16,
                             vectorscope_bins=16),
        elapsed_seconds=0.01,
        stale=True,
    )
    window._on_scopes_analysis_ready(stale)
    qtbot.wait(50)
    assert window.scopes_panel.result() is None


def test_scopes_extraction_error_is_silent(qtbot, monkeypatch) -> None:
    """Pas de trace parasite quand la tête de lecture est hors média."""
    window, _ = _scopes_window(qtbot, monkeypatch)
    # Ne doit rien lever ni écrire sur stdout.
    window._on_scopes_analysis_failed(
        ScopeRequest(request_id=1, playhead=0.0, ffmpeg_command=()),
        ScopeExtractionError("hors média"),
    )


def test_scopes_ffmpeg_command_extracts_a_single_png_frame(
    qtbot, monkeypatch,
) -> None:
    window = _window(qtbot, monkeypatch)
    captured: dict = {}

    class _Plan:
        video_layers = (object(),)
        fps = 30.0

    def fake_plan(at=None):
        captured["plan_at"] = at
        return _Plan()

    def fake_build_frame_command(_engine, request, playhead):
        captured["playhead"] = playhead
        captured["request"] = request
        return [
            "ffmpeg", "-y", "-ss", f"{playhead:.3f}", "-i", "clip.mp4",
            "-filter_complex", "[0:v]eq=1[v]", "-map", "[v]",
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
        ]

    monkeypatch.setattr(window, "get_render_plan", fake_plan)
    monkeypatch.setattr(
        "ui.main_window.ExportEngine.build_frame_command", fake_build_frame_command
    )

    command = window._build_scopes_ffmpeg_command(12.5)
    assert command is not None
    # Le plan est ramené à l'origine à la tête de lecture : l'image voulue est la première du rendu.
    assert captured["plan_at"] == 12.5
    assert captured["playhead"] == 0.0
    # La commande analyse une image unique sur stdout, sans audio.
    assert "-map" in command
    assert command[-7:] == [
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
    ]


def test_scopes_ffmpeg_command_returns_none_without_video(
    qtbot, monkeypatch,
) -> None:
    """Aucun média vidéo au projet : rien à analyser, et surtout pas
    d'appel à ``build_frame_command``."""
    window = _window(qtbot, monkeypatch)

    class _EmptyPlan:
        video_layers = ()

    monkeypatch.setattr(window, "get_render_plan", lambda at=None: _EmptyPlan())
    called = []
    monkeypatch.setattr(
        window.export_engine,
        "build_frame_command",
        lambda *a, **k: called.append(a) or [],
    )
    assert window._build_scopes_ffmpeg_command(1.0) is None
    assert called == []


def test_scopes_ffmpeg_command_returns_none_without_plan(
    qtbot, monkeypatch,
) -> None:
    window = _window(qtbot, monkeypatch)

    def boom(at=None):
        raise RuntimeError("projet non exportable")

    monkeypatch.setattr(window, "get_render_plan", boom)
    assert window._build_scopes_ffmpeg_command(1.0) is None


def test_build_frame_command_matches_export_filter_graph(tmp_path) -> None:
    """La commande d'analyse partage le graphe de filtres de l'export.

    C'est le point critique de la tâche : sans cela, les scopes
    analyseraient la source brute au lieu de l'image étalonnée.
    """
    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.render_plan import build_render_plan

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"\x00" * 16)
    asset = MediaAsset(
        id="a1", name="clip.mp4", path=str(source),
        duration=4.0, width=640, height=360, fps=30.0,
        media_type="video",
    )
    track = Track(
        id="V1", name="V1", type="video",
        clips=[
            Clip(
                id="c1", asset_id="a1", track_id="V1",
                timeline_start=0.0, source_in=0.0, source_out=3.0,
            )
        ],
    )
    project = Project(
        name="Scopes", width=640, height=360, fps=30.0,
        media_assets=[asset], tracks=[track],
    )
    plan = build_render_plan(project)

    engine = ExportEngine()
    request = ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "out.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset("HD", (640, 360), 18, "192k"),
        fps=30,
    )
    full = engine._build_command(request)
    single = engine.build_frame_command(request, 5.0)

    def graph(command: list[str]) -> str:
        return command[command.index("-filter_complex") + 1]

    # Même graphe ; l'audio, inutile pour une image, part dans un puits
    # (FFmpeg refuse une sortie de graphe non reliée), puis la sortie composée est rognée à la tête de
    # lecture : une image à 5 s à 30 i/s est la n° 150, visée une demi-image avant son horodatage.
    # L'export ajoute en plus l'étape de conversion BT.709 (YUV) ; l'extraction PNG lit du RVB et s'en passe.
    common = graph(single).split(";[aout]anullsink")[0]
    assert graph(full).startswith(common)
    assert graph(full)[len(common):].startswith(";[") and "out_color_matrix=bt709" in graph(full)[len(common):]
    assert ";[aout]anullsink;" in graph(single)
    assert graph(single).endswith("trim=start=4.983333,setpts=PTS-STARTPTS[kut_frame]")
    # Plus de ``-ss`` sur une entrée : il ignorait la position, la vitesse et le point d'entrée du clip.
    assert "-ss" not in single
    assert single[single.index("-map") + 1] == "[kut_frame]"
    assert single[-1] == "-"
    assert "-c:v" not in single
    # À l'origine du plan (cas des scopes) : aucun rognage, la première image est la bonne.
    assert "kut_frame" not in graph(engine.build_frame_command(request, 0.0))


def test_closing_window_stops_the_scopes_worker(qtbot, monkeypatch) -> None:
    window = _window(qtbot, monkeypatch)
    analyzer = window.scopes_analyzer
    window.close()
    assert analyzer._closed is True


def test_accumulate_matches_the_individual_computations() -> None:
    """Parité entre l'accumulateur unique et les ``compute_*``.

    ``analyze_frame`` n'appelle plus les cinq fonctions séparées (cinq
    parcours de l'image coûtent cinq fois plus). Ce test verrouille
    l'équivalence, pour que les deux chemins ne puissent pas diverger.
    """
    frame = _ramp_frame(32, 32)
    result = analyze_frame(frame, columns=16, vectorscope_bins=32)
    hist_luma, hist_r, hist_g, hist_b = compute_histogram(frame)
    assert result.histogram_luma == hist_luma
    assert result.histogram_r == hist_r
    assert result.histogram_g == hist_g
    assert result.histogram_b == hist_b
    assert result.waveform == compute_waveform(frame, columns=16)
    assert result.parade == compute_parade(frame, columns=16)
    assert result.alerts == compute_alerts(frame, levels=VideoLevels.VIDEO)
    # Sur une image de taille normale, le vectorscope reste exact.
    assert result.vectorscope == compute_vectorscope(
        frame, bins=32, levels=VideoLevels.VIDEO
    )


def test_large_frames_subsample_the_vectorscope_only() -> None:
    """Au-delà du budget, seul le vectorscope est sous-échantillonné.

    Les trois autres scopes restent spatialement exacts : la waveform
    et la parade décrivent *où* se trouve l'information, on ne peut
    donc pas la sous-échantillonner sans déformer l'image du signal.
    Le vectorscope ne montre qu'une distribution de teinte.
    """
    from core.scopes import _accumulate

    pixels = []
    state = 4242
    for _ in range(1920 * 1080):
        state = (1103515245 * state + 12345) % (1 << 31)
        pixels.append((state % 256, (state >> 8) % 256, (state >> 16) % 256))
    frame = ScopeFrame(width=1920, height=1080, pixels=tuple(pixels))
    stride = 8
    result = _accumulate(
        frame, columns=64, bins=16, color_space=ColorSpace.REC709,
        levels=VideoLevels.VIDEO, tolerance=0, stride=stride,
        vector_step=stride * 4,
    )
    # Histogrammes et alertes portent sur les échantillons, la
    # waveform sur la grille : rien n'indique un pixels manquant.
    assert sum(result.histogram_luma) == (1920 * 1080) // stride
    assert result.columns == 64
    assert all(
        any(value > 0.0 for value in column) for column in result.waveform
    )
