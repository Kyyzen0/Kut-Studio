COLORS = {
    "background": "#111318",
    "panel": "#191C22",
    "panel_alt": "#15181D",
    "surface": "#20242C",
    "surface_hover": "#282D37",
    "border": "#2B303A",
    "text": "#F4F4F5",
    "muted": "#9298A5",
    "accent": "#7C5CFC",
    "accent_hover": "#8B70FF",
    "accent_dark": "#30245F",
    "success": "#67D6A3",
    "danger": "#F27686",
}


def global_stylesheet():
    return f"""
    QWidget {{
        color: {COLORS['text']};
        font-family: 'Avenir Next', 'SF Pro Display', sans-serif;
        font-size: 12px;
    }}
    QMainWindow {{ background: {COLORS['background']}; }}
    QToolTip {{
        background: {COLORS['surface']}; color: {COLORS['text']};
        border: 1px solid {COLORS['border']}; padding: 5px 8px;
    }}
    QSplitter::handle {{ background: {COLORS['background']}; }}
    QSplitter::handle:horizontal {{ width: 6px; }}
    QSplitter::handle:vertical {{ height: 6px; }}
    QPushButton {{
        background: {COLORS['surface']}; color: {COLORS['text']};
        border: 1px solid {COLORS['border']}; border-radius: 6px;
        padding: 7px 11px;
    }}
    QPushButton:hover {{ background: {COLORS['surface_hover']}; border-color: #454B58; }}
    QPushButton:pressed {{ background: {COLORS['accent_dark']}; }}
    QPushButton:disabled {{ color: #626875; background: {COLORS['panel_alt']}; }}
    QLineEdit, QTextEdit, QComboBox, QListWidget {{
        background: {COLORS['panel_alt']}; color: {COLORS['text']};
        border: 1px solid {COLORS['border']}; border-radius: 6px;
        selection-background-color: {COLORS['accent_dark']};
    }}
    QComboBox {{ padding: 6px 8px; }}
    QComboBox QAbstractItemView {{ background: {COLORS['surface']}; color: {COLORS['text']}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {COLORS['border']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 12px; margin: -4px 0; background: {COLORS['accent']}; border-radius: 6px; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {COLORS['border']}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QMenuBar {{ background: {COLORS['panel']}; color: {COLORS['muted']}; border-bottom: 1px solid {COLORS['border']}; padding: 3px 8px; }}
    QMenuBar::item {{ padding: 5px 9px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {COLORS['surface_hover']}; color: {COLORS['text']}; }}
    QMenu {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; }}
    QMenu::item {{ padding: 7px 22px; }}
    QMenu::item:selected {{ background: {COLORS['accent_dark']}; }}
    QGroupBox {{
        color: {COLORS['muted']}; border: 1px solid {COLORS['border']};
        border-radius: 6px; margin-top: 10px; padding-top: 10px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {COLORS['muted']}; }}
    """


def label_style(size=12, color="text", weight=400):
    return f"color: {COLORS[color]}; font-size: {size}px; font-weight: {weight};"
