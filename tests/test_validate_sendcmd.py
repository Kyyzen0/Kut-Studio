"""Banc ``tools/validate_sendcmd.py`` (ADR-0008, étape 1) : seules ses fonctions pures sont testées, FFmpeg ne tourne pas."""

from __future__ import annotations

import types

import numpy as np
import pytest

from tools import validate_sendcmd as bench


def test_ramp_goes_from_start_to_end_and_is_clamped():
    assert bench.ramp_value(2.0, 4.0, 0, 5) == 2.0
    assert bench.ramp_value(2.0, 4.0, 4, 5) == 4.0
    assert bench.ramp_value(2.0, 4.0, 2, 5) == pytest.approx(3.0)
    assert bench.ramp_value(2.0, 4.0, 99, 5) == 4.0           # au-delà de la dernière image : bornée
    assert bench.ramp_value(2.0, 4.0, -3, 5) == 2.0
    assert bench.ramp_value(2.0, 4.0, 0, 1) == 2.0            # une seule image : le départ


def test_format_number_has_no_scientific_notation_or_trailing_zeros():
    assert bench.format_number(0.5) == "0.5"
    assert bench.format_number(2.0) == "2"
    assert bench.format_number(1e-9) == "0"
    assert bench.format_number(-0.25) == "-0.25"
    assert "e" not in bench.format_number(1.23456789e-05)


def test_frame_time_early_is_half_a_frame_before_the_frame_and_zero_stays_zero():
    assert bench.frame_time(0, 30, 1, "early") == 0.0
    assert bench.frame_time(3, 30, 1, "exact") == pytest.approx(0.1)
    assert bench.frame_time(3, 30, 1, "early") == pytest.approx(2.5 / 30)
    assert bench.frame_time(3, 30000, 1001, "exact") == pytest.approx(3 * 1001 / 30000)
    assert bench.frame_time(3, 30, 1, "early", shift=1.0) == pytest.approx(1.0 + 2.5 / 30)
    with pytest.raises(ValueError):
        bench.frame_time(1, 30, 1, "sometime")


def test_sendcmd_text_has_one_line_per_frame_with_named_targets():
    triples = [[("lutrgb@t", "r", "'clip(val,0,255)'"), ("lutrgb@t", "g", "5")], [("lutrgb@t", "r", "7")]]
    lines = bench.sendcmd_text(triples, 30, 1, timing="early").splitlines()
    assert lines[0] == "0.000000000 lutrgb@t r 'clip(val,0,255)', lutrgb@t g 5;"
    assert lines[1].endswith("lutrgb@t r 7;") and lines[1].startswith("0.016666667 ")
    assert len(lines) == 2


def test_sendcmd_text_exact_us_truncates_to_the_microsecond():
    text = bench.sendcmd_text([[("a@t", "x", "1")], [("a@t", "x", "2")]], 30, 1, timing="exact_us")
    assert text.splitlines()[1].startswith("0.033333 ")


def test_sendcmd_file_size_grows_linearly_with_the_frame_count():
    one = len(bench.sendcmd_text([[("eq@t", "contrast", "1.5")]] * 100, 30, 1))
    two = len(bench.sendcmd_text([[("eq@t", "contrast", "1.5")]] * 200, 30, 1))
    assert 1.9 < two / one < 2.2


def test_compare_arrays_reports_max_mean_and_share_over_one_level():
    left = np.array([10, 10, 10, 10], dtype=np.uint8)
    right = np.array([10, 11, 13, 10], dtype=np.uint8)
    stats = bench.compare_arrays(left, right)
    assert stats["max"] == 3.0
    assert stats["mean"] == pytest.approx(1.0)
    assert stats["pct_gt1"] == pytest.approx(25.0)             # seul l'écart de 3 dépasse un niveau
    assert bench.compare_arrays(left, left)["max"] == 0.0
    assert bench.compare_arrays(left, right[:3])["max"] == 255.0     # tailles différentes : jamais validé


def test_is_valid_needs_every_frame_within_one_level_and_at_least_one_frame():
    good = {"max": 1.0, "mean": 0.1, "pct_gt1": 0.0}
    bad = {"max": 2.0, "mean": 0.1, "pct_gt1": 1.0}
    assert bench.is_valid([good, good])
    assert not bench.is_valid([good, bad])
    assert not bench.is_valid([])


def test_sample_indices_cover_the_ends_and_stay_inside_the_ramp():
    assert bench.sample_indices(90) == [0, 1, 2, 15, 30, 59, 89]
    assert bench.sample_indices(60) == [0, 1, 2, 15, 30, 59]
    assert bench.sample_indices(60, short=True) == [0, 1, 30, 59]
    assert bench.sample_indices(1) == [0]


def test_parse_version_reads_the_banner():
    assert bench.parse_version("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023") == "6.1.1-3ubuntu5"
    assert bench.parse_version("ffmpeg version 9.0.2 Copyright") == "9.0.2"
    assert bench.parse_version("rien") == "inconnue"


def test_every_case_builds_a_static_filter_and_matching_named_commands():
    for case in bench.build_cases():
        middle = case.body(case.start, False)
        assert "@" not in middle, case.key                      # statique : aucun nom
        named = case.body(case.start, True)
        assert "@" in named, case.key
        targets = {target for target, _option, _value in case.commands(case.end)}
        assert targets, case.key
        for target in targets:
            assert target in named, (case.key, target)         # chaque cible de commande existe dans le graphe
        if case.expr_body is not None:
            assert "eval=frame" in case.expr_body(case.start, case.end, 90, "n", 30.0) or case.key.startswith(("hue", "geq")), case.key


def test_scenarios_are_unique_and_the_failing_ones_are_the_unshifted_ones():
    scenarios = bench.build_scenarios()
    keys = [s.key for s in scenarios]
    assert len(keys) == len(set(keys)) and "base" in keys
    expected_failures = {s.key for s in scenarios if s.expect == "fail"}
    assert expected_failures == {"trim_noreset_unshifted", "seek_copyts_unshifted"}
    assert all(s.shift == 0.0 for s in scenarios if s.expect == "fail")


def test_integer_cases_round_their_ramp():
    case = next(c for c in bench.build_cases() if c.key == "rgbashift.shift")
    values = bench._values(case, bench.Scenario("t", count=10))
    assert all(v == int(v) for v in values) and values[0] == 0.0 and values[9] == 8.0


def test_cube_text_has_eight_rows_and_the_inverse_is_complementary():
    plain, inverse = bench._cube_text(False).splitlines(), bench._cube_text(True).splitlines()
    assert plain[0] == "LUT_3D_SIZE 2" and len(plain) == 9
    assert plain[1] == "0 0 0" and inverse[1] == "1 1 1" and plain[8] == "1 1 1" and inverse[8] == "0 0 0"


def test_the_bench_is_standalone():
    text = open(bench.__file__, encoding="utf-8").read()
    for forbidden in ("from core", "import core", "from ui", "import ui", "eval(", "exec(", "pickle", "importlib"):
        assert forbidden not in text.replace("eval=frame", ""), forbidden


def _runner(tmp_path, cpu0):
    return bench.Runner("ffmpeg", tmp_path, cpu0=cpu0)


def test_the_cost_command_honors_cpuflags0_before_the_input():
    for cpu0 in (False, True):
        cmd = _runner(None, cpu0).cost_command("testsrc2=s=64x36", "eq=contrast=1")
        assert ("-cpuflags" in cmd) is cpu0
        if cpu0:
            assert cmd[cmd.index("-cpuflags") + 1] == "0"
            assert cmd.index("-cpuflags") < cmd.index("-i"), "option globale : avant l'entrée"


def test_last_line_keeps_the_last_non_empty_line_and_caps_its_length():
    assert bench.last_line(b"a\n\nb option inconnue \n\n") == "b option inconnue"
    assert bench.last_line("") == "" and bench.last_line(None) == ""
    assert len(bench.last_line("x" * 500)) == 200


def test_a_failed_cost_render_records_the_error_and_no_timing(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        return types.SimpleNamespace(returncode=1, stderr=b"Unrecognized option 'sigmaV'.\n")

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    case = next(c for c in bench.build_cases() if c.key == "eq.contrast")
    (entry,) = bench.run_cost(_runner(tmp_path, True), [case], repeat=2)
    assert "animated_s" not in entry and "static_s" not in entry, "une durée d'échec n'est jamais un résultat"
    assert "sigmaV" in entry["animated_s_error"] and "static_s_error" in entry
    assert all("-cpuflags" in cmd for cmd in seen)
    assert len(seen) == 2, "un échec arrête les répétitions de ce rendu"


def test_a_successful_cost_render_records_the_best_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(bench.subprocess, "run", lambda cmd, **_k: types.SimpleNamespace(returncode=0, stderr=b""))
    case = next(c for c in bench.build_cases() if c.key == "eq.contrast")
    (entry,) = bench.run_cost(_runner(tmp_path, False), [case], repeat=2)
    assert isinstance(entry["animated_s"], float) and isinstance(entry["static_s"], float)
    assert "animated_s_error" not in entry
