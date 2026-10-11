"""Bancs de l'ADR-0003 (B1 et B2) : la logique pure est testée, les mesures ne tournent pas dans la suite."""

from __future__ import annotations

import pickle

import pytest

from tools.perf import decode_scaling, gil_scaling


def test_cpu_decode_command_has_no_hardware_option():
    command = decode_scaling.decode_command("ffmpeg", "clip.mp4")
    assert "-hwaccel" not in command and "-hwaccel_output_format" not in command
    assert command[command.index("-i") + 1] == "clip.mp4" and command[-1] == "-"


def test_hardware_decode_command_carries_both_options_when_asked():
    command = decode_scaling.decode_command("ffmpeg", "clip.mp4", "videotoolbox", "videotoolbox_vld")
    assert command[command.index("-hwaccel") + 1] == "videotoolbox"
    assert command[command.index("-hwaccel_output_format") + 1] == "videotoolbox_vld"


def test_an_output_format_without_a_backend_is_refused():
    with pytest.raises(ValueError):
        decode_scaling.decode_command("ffmpeg", "clip.mp4", None, "videotoolbox_vld")


def test_throughput_counts_every_stream():
    # 2 flux de 180 images en 3 s : 120 images/s au total, 60 par flux.
    assert decode_scaling.throughput(180, 2, 3.0) == {"aggregate_fps": 120.0, "per_stream_fps": 60.0}


def test_every_gil_job_is_picklable_for_the_process_pool():
    # Un travail envoyé à un processus doit être une fonction de module, sans fermeture.
    assert {"text_outline", "python_control"} <= set(gil_scaling.JOBS)
    for label, job, count in gil_scaling.JOBS.values():
        assert label and count > 0
        assert pickle.loads(pickle.dumps(job)) is job
