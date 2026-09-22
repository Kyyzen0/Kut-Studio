import os

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect


def apply_color_effect(effect, brightness, contrast, saturation):
    base_color = QColor("#ffffff")
    brightness_target = QColor("#203050" if brightness < 0 else "#fff1c2")
    saturation_target = QColor("#aeb7c4" if saturation < 0 else "#ffb84d")
    brightness_ratio = min(1.0, abs(brightness) / 100.0)
    saturation_ratio = min(1.0, abs(saturation) / 100.0)

    channels = []
    for channel in range(3):
        base_value = base_color.getRgbF()[channel]
        brightness_delta = brightness_target.getRgbF()[channel] - base_value
        saturation_delta = saturation_target.getRgbF()[channel] - base_value
        value = base_value + brightness_delta * brightness_ratio + saturation_delta * saturation_ratio
        channels.append(max(0.0, min(1.0, value)))
    color = QColor.fromRgbF(*channels)
    strength = min(1.0, (abs(brightness) + abs(contrast) + abs(saturation)) / 300.0)
    effect.setColor(color)
    effect.setStrength(strength)


def set_volume(audio_output, value):
    audio_output.setVolume(value / 100.0)


def format_srt_time(seconds):
    total_milliseconds = max(0, int(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def save_subtitles(clips, file_path):
    subtitle_clips = [clip for clip in clips if clip["track"] == 2]
    with open(file_path, "w", encoding="utf-8") as subtitle_file:
        for index, clip in enumerate(subtitle_clips, 1):
            subtitle_file.write(f"{index}\n")
            subtitle_file.write(
                f"{format_srt_time(clip['start'])} --> {format_srt_time(clip['end'])}\n"
            )
            subtitle_file.write(f"{clip.get('text', '').strip()}\n\n")


def play_crossfade_preview(overlay, parent):
    overlay.show()
    opacity_effect = QGraphicsOpacityEffect(overlay)
    overlay.setGraphicsEffect(opacity_effect)
    animation = QPropertyAnimation(opacity_effect, b"opacity", parent)
    animation.setDuration(900)
    animation.setStartValue(0.0)
    animation.setKeyValueAt(0.35, 1.0)
    animation.setKeyValueAt(0.65, 1.0)
    animation.setEndValue(0.0)
    animation.setEasingCurve(QEasingCurve.InOutQuad)
    animation.finished.connect(overlay.hide)
    animation.start()
    return animation
