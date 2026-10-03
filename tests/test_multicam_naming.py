"""Noms initiaux des angles : mots de rôle (FR / EN), bobine, caméra, nom de fichier nettoyé, repli, unicité du lot."""

from __future__ import annotations

import re

import pytest

from core.multicam_naming import MAX_NAME_LENGTH, suggest_angle_name, suggest_angle_names
from core.project_model import MediaAsset


def _video(name: str = "", path: str | None = None, **fields) -> MediaAsset:
    if path is None:
        path = f"/rushes/{name}" if name else ""
    return MediaAsset("v", path, name, 10.0, 1920, 1080, 25.0, "video", True, **fields)


def _audio(name: str = "", path: str | None = None, **fields) -> MediaAsset:
    if path is None:
        path = f"/rushes/{name}" if name else ""
    return MediaAsset("a", path, name, 10.0, 0, 0, 0.0, "audio", True, **fields)


def _name(asset: MediaAsset, index: int = 0, **words) -> str:
    return suggest_angle_name(asset, index, **words)


# --- Mots de rôle ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("concert_wide.mp4", "Wide"), ("Concert WIDE.mov", "Wide"), ("wide-shot-02.mp4", "Wide"), ("WideShot.mov", "Wide"),
        ("plan_large.mp4", "Wide"), ("Plan large scène.mov", "Wide"), ("PlanLarge.mov", "Wide"),
        ("plan d'ensemble.mp4", "Wide"), ("grand angle.mp4", "Wide"),
        ("close.mp4", "Close-up"), ("close-up.mp4", "Close-up"), ("Close Up 3.mov", "Close-up"),
        ("CloseUp.mov", "Close-up"), ("closeup_guitar.mp4", "Close-up"), ("gros plan.mp4", "Close-up"),
        ("Gros_Plan_chanteur.mov", "Close-up"), ("plan serré.mp4", "Close-up"),
        ("medium.mp4", "Medium"), ("plan moyen.mp4", "Medium"),
        ("handheld.mp4", "Handheld"), ("Hand_Held.mov", "Handheld"), ("caméra épaule.mp4", "Handheld"),
        ("Epaule gauche.mov", "Handheld"), ("à l'épaule.mp4", "Handheld"),
        ("drone.mp4", "Drone"), ("Drone_shot.mov", "Drone"), ("vue aérienne.mp4", "Drone"), ("aerial.mp4", "Drone"),
        ("gimbal.mp4", "Gimbal"), ("steadicam.mp4", "Steadicam"), ("overhead.mp4", "Overhead"),
        ("GoPro_stage.mp4", "GoPro"), ("Go Pro.mov", "GoPro"),
    ],
)
def test_a_role_or_position_word_gives_a_normalised_name(filename, expected):
    assert _name(_video(filename)) == expected


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("cam a.mp4", "Camera A"), ("Cam_A.mp4", "Camera A"), ("CamA_C0012.mp4", "Camera A"), ("camera b.mov", "Camera B"),
        ("Camera 2.mov", "Camera 2"), ("cam_3.mp4", "Camera 3"), ("cam1.mp4", "Camera 1"), ("CAM1_take2.mov", "Camera 1"),
        ("cam 02.mp4", "Camera 2"), ("cam-12.mp4", "Camera 12"), ("caméra 4.mp4", "Camera 4"), ("Concert_Cam_C_0007.mp4", "Camera C"),
    ],
)
def test_a_camera_label_is_normalised(filename, expected):
    assert _name(_video(filename)) == expected


@pytest.mark.parametrize(
    "filename",
    ["camp_fire.mp4", "camera_roll.mp4", "worldwide.mp4", "closer.mp4", "widespread.mov", "cameo.mp4", "campaign_2.mp4",
     "overheard.mp4", "droned_on.mp4", "shoulders.mp4"],
)
def test_words_that_merely_contain_a_role_word_are_not_roles(filename):
    name = _name(_video(filename))
    assert name not in {"Wide", "Close-up", "Handheld", "Drone", "Overhead"}
    assert not re.fullmatch(r"Camera ([A-Z]|\d+)", name)             # « Camera roll » est un nom, pas un libellé de caméra


def test_the_title_is_searched_before_the_file_name_and_the_folder():
    asset = _video("Gros plan guitare", path="/rushes/cam_b/C0012.mp4")
    assert _name(asset) == "Close-up"                                       # le titre gagne sur le dossier
    asset = _video("C0012.mp4", path="/rushes/Drone/C0012.mp4")
    assert _name(asset) == "Drone"                                          # le dossier de la carte sert de dernier recours
    asset = _video("C0012.mp4", path="/rushes/Camera B/C0012.mp4")
    assert _name(asset) == "Camera B"
    asset = _video("C0012.mp4", path="C:\\Rushes\\Camera 2\\C0012.mp4")
    assert _name(asset) == "Camera 2"


def test_a_position_word_wins_over_a_camera_label_when_both_are_present():
    assert _name(_video("cam1_wide.mp4")) == "Wide"
    assert _name(_video("CamB_gros_plan.mov")) == "Close-up"


def test_the_earliest_role_word_wins():
    assert _name(_video("close_then_wide.mp4")) == "Close-up"
    assert _name(_video("wide_then_close.mp4")) == "Wide"


def test_audio_recorders_get_audio_roles_and_never_video_ones():
    assert _name(_audio("boom_take3.wav")) == "Boom"
    assert _name(_audio("Perche.wav")) == "Boom"
    assert _name(_audio("lav_01.wav")) == "Lavalier"
    assert _name(_audio("cravate HF.wav")) == "Lavalier"
    assert _name(_audio("Zoom recorder.wav")) == "Recorder"
    assert _name(_audio("mixdown.wav")) == "Mix"
    assert _name(_audio("wide_ambiance.wav")) == "Wide ambiance"           # « wide » n'est pas un rôle audio
    assert _name(_video("boom.mp4")) == "Boom"                              # un fichier vidéo reste un angle vidéo : nom nettoyé
    assert _name(_audio("cam_a_scratch.wav")) == "Camera A"


# --- Ordre de priorité -----------------------------------------------------------------------------------------------


def test_priority_is_role_then_reel_then_camera_then_file_name_then_fallback():
    full = _video("interview_C0012.mp4", reel="A001", camera="Sony A7S III")
    assert _name(full) == "A001"
    assert _name(_video("interview_C0012.mp4", camera="Sony A7S III")) == "Sony A7S III"
    assert _name(_video("interview_C0012.mp4")) == "Interview"
    assert _name(_video("C0012.mp4"), 4) == "Angle 5"
    assert _name(_video("wide.mp4", reel="A001", camera="Sony A7S III")) == "Wide"
    assert _name(_video("cam_b_C0012.mp4", reel="A001")) == "Camera B"


def test_reel_and_camera_names_are_kept_as_written_but_tidied():
    assert _name(_video("C0001.mp4", reel="  B002   ")) == "B002"
    assert _name(_video("C0001.mp4", camera="  Canon   EOS  C70 ")) == "Canon EOS C70"
    assert _name(_video("C0001.mp4", reel="---")) == "Angle 1"             # sans lettre ni chiffre : rien d'utile
    assert len(_name(_video("C0001.mp4", camera="x" * 200))) <= MAX_NAME_LENGTH


# --- Nom de fichier nettoyé ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("Concert_C0012.mp4", "Concert"), ("my_band_live_0042.mov", "My band live"), ("interview 01.mov", "Interview 1"),
        ("Interview_2.mp4", "Interview 2"), ("Shoot-02.mov", "Shoot-2"), ("CONCERT_HALL.mov", "Concert hall"),
        ("DJI_0042.MP4", "DJI"), ("Guitariste.mp4", "Guitariste"), ("guitariste.mp4", "Guitariste"),
        ("Soirée Été 2024.mp4", "Soirée Été"), ("Bal masqué 20260314_093000.mov", "Bal masqué"),
        ("scène finale.mp4", "Scène finale"), ("Concert0012.mp4", "Concert"), ("A7S_Concert.mp4", "A7S Concert"),
        ("日本語のカメラ.mp4", "日本語のカメラ"), ("Ünïcode_Ñame.mov", "Ünïcode Ñame"),
        ("Interview.with.the.director.mp4", "Interview.with.the.director"),
        ("Take 12 - guitare.mp4", "Take 12 - guitare"),
    ],
)
def test_a_file_name_is_cleaned_into_a_readable_name(filename, expected):
    assert _name(_video(filename)) == expected


@pytest.mark.parametrize(
    "filename",
    ["C0012.mp4", "MVI_1234.MP4", "IMG_0042.MOV", "DSC00123.MP4", "DSC_0042.mov", "A001_C002.mov", "A001_C002_0101AB.mov",
     "VID_20260314_093000.mp4", "PXL_20260314_093000123.mp4", "20260314.mp4", "0042.mov", "2.mp4", "---.mov", "_.mp4",
     "MVI 1234.mp4", "img-0042.mov"],
)
def test_pure_camera_counters_fall_back_to_the_generic_name(filename):
    assert _name(_video(filename), 2) == "Angle 3"


@pytest.mark.parametrize("filename", ["GX010123.MP4", "GH010456.mp4", "GOPR0123.MP4", "gx010123.mp4"])
def test_a_gopro_counter_names_the_camera(filename):
    assert _name(_video(filename)) == "GoPro"


def test_an_extension_is_only_removed_when_it_is_a_media_extension():
    assert _name(_video("Cam 1.5 Hz.mp4")) == "Camera 1"                    # « 1.5 » : pas une extension
    assert _name(_video("notes.final")) == "Notes.final"
    assert _name(_video("take.MP4")) == "Take"


def test_a_long_name_is_cut_at_a_word_and_never_exceeds_the_limit():
    name = _name(_video("interview with the director in the studio at night during the rain storm.mp4"))
    assert name.startswith("Interview with the director") and len(name) <= MAX_NAME_LENGTH
    assert not name.endswith(" ")
    assert len(_name(_video("x" * 300 + ".mp4"))) <= MAX_NAME_LENGTH


# --- Repli, langue, cas limites ------------------------------------------------------------------------------------


def test_the_fallback_uses_the_given_words_and_the_index():
    assert _name(_video(""), 0) == "Angle 1"
    assert _name(_video(""), 6) == "Angle 7"
    assert _name(_audio(""), 1) == "Audio 2"
    assert _name(_video("C0001.mp4"), 2, angle_word="Ángulo") == "Ángulo 3"
    assert _name(_video("C0001.mp4"), 1, angle_word="Angle") == "Angle 2"
    assert _name(_audio("0001.wav"), 0, audio_word="Son") == "Son 1"
    assert _name(_video(""), -3) == "Angle 1"                                # un rang négatif n'écrit jamais « Angle -2 »


def test_empty_and_odd_inputs_never_raise_and_never_return_an_empty_name():
    for asset in (_video(""), _video("", path=""), _video("   ", path="   "), _video(".mp4"), _video("..."),
                  _video("", path="/"), _video("", path="C:\\"), _audio(""), _video("\u0000\u200b")):
        assert _name(asset, 0).strip()


def test_the_name_is_taken_from_the_path_when_the_title_is_empty():
    assert _name(_video("", path="/rushes/guitariste.mp4")) == "Guitariste"
    assert _name(_video("", path="C:\\Rushes\\Cam B\\C0001.mp4")) == "Camera B"


# --- Lot : unicité --------------------------------------------------------------------------------------------------


def test_a_batch_gets_distinct_names_with_numeric_suffixes():
    assets = [_video("wide.mp4"), _video("wide_2.mp4"), _video("WIDE.mov"), _video("close.mp4")]
    assert suggest_angle_names(assets) == ["Wide", "Wide 2", "Wide 3", "Close-up"]


def test_uniqueness_ignores_case_and_never_collides_with_an_explicit_suffixed_name():
    names = suggest_angle_names([_video("Guitare.mp4"), _video("guitare.mp4"), _video("Guitare 2.mp4")])
    assert len({name.casefold() for name in names}) == 3
    assert names[0] == "Guitare" and names[2] == "Guitare 2"
    assert names[1] == "Guitare 3"


def test_two_cameras_of_the_same_model_are_told_apart_by_a_suffix():
    assets = [_video("C0001.mp4", camera="Sony A7S III"), _video("C0002.mp4", camera="Sony A7S III")]
    assert suggest_angle_names(assets) == ["Sony A7S III", "Sony A7S III 2"]


def test_a_shared_role_falls_back_to_the_camera_labels_when_they_tell_the_angles_apart():
    assets = [_video("cam1_wide.mp4"), _video("cam2_wide.mp4"), _video("cam3_wide.mp4")]
    assert suggest_angle_names(assets) == ["Camera 1", "Camera 2", "Camera 3"]


def test_a_shared_role_with_distinct_roles_elsewhere_keeps_the_roles():
    assets = [_video("cam1_wide.mp4"), _video("cam2_close.mp4")]
    assert suggest_angle_names(assets) == ["Wide", "Close-up"]


def test_a_shared_reel_prefix_does_not_hide_a_better_name_from_the_file():
    assets = [_video("guitare_C0001.mp4", reel="A001"), _video("batterie_C0002.mp4", reel="A001")]
    assert suggest_angle_names(assets) == ["Guitare", "Batterie"]


def test_the_fallback_is_numbered_per_kind_across_a_mixed_batch():
    assets = [_video("C0001.mp4"), _audio("0001.wav"), _video("C0002.mp4"), _audio("0002.wav"), _video("C0003.mp4")]
    assert suggest_angle_names(assets) == ["Angle 1", "Audio 1", "Angle 2", "Audio 2", "Angle 3"]
    assert suggest_angle_names(assets, angle_word="Ángulo", audio_word="Son") == [
        "Ángulo 1", "Son 1", "Ángulo 2", "Son 2", "Ángulo 3"]


def test_the_generic_fallback_never_repeats_even_if_a_file_is_named_like_it():
    names = suggest_angle_names([_video("Angle 2.mp4"), _video("C0001.mp4"), _video("C0002.mp4")])
    assert len({name.casefold() for name in names}) == 3


def test_an_empty_batch_and_a_single_asset():
    assert suggest_angle_names([]) == []
    assert suggest_angle_names([_video("wide.mp4")]) == ["Wide"]


def test_a_large_batch_is_distinct_and_stable():
    assets = [_video(f"C{index:04d}.mp4") for index in range(64)]
    names = suggest_angle_names(assets)
    assert names == [f"Angle {index + 1}" for index in range(64)]
    assert suggest_angle_names(assets) == names


def test_the_batch_names_match_the_single_asset_name_when_nothing_collides():
    assets = [_video("wide.mp4"), _video("C0002.mp4", reel="B7"), _video("Guitariste.mov")]
    assert suggest_angle_names(assets) == ["Wide", "B7", "Guitariste"]
    assert [suggest_angle_name(asset, index) for index, asset in enumerate(assets)] == suggest_angle_names(assets)
