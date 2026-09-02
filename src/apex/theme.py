"""Apex visual system, aligned with the IBMF1 post-session review.

The team viewer uses warm graphite surfaces, restrained instrument amber and
quiet borders. Apex keeps its useful motorsport semantics on top: purple is a
session best/reference, green is gained, and red is lost or risky.
"""

# Warm-graphite surfaces -------------------------------------------------------

BG = "#171613"
SURFACE_1 = "#211f1b"
SURFACE_2 = "#2a2823"
SURFACE_3 = "#35322c"
BORDER = "#454139"
BORDER_STRONG = "#5a554b"

# Type ------------------------------------------------------------------------

TEXT = "#f3f0e9"
TEXT_DIM = "#b9b3a8"
TEXT_MUTED = "#827c72"

# Accent + retained telemetry semantics --------------------------------------

ACCENT = "#e3a64a"  # instrument amber, matching the team review viewer
ACCENT_HOVER = "#efb75d"
BLUE = "#70a9c6"  # current-lap trace / neutral telemetry action
PURPLE = "#b9a0da"  # session best / reference
GREEN = "#64b99a"
YELLOW = ACCENT
RED = "#df7062"
NEUTRAL = SURFACE_3
GRID_ALPHA = 0.10

# One stylesheet owns the shell and ordinary Qt controls. Page modules only
# add semantic object names; they do not each invent another visual language.
STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI Variable Text", "Inter", "Segoe UI", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{ background-color: {BG}; }}
QLabel {{ background: transparent; }}

QLabel#brand {{
    color: {TEXT};
    font-size: 16px;
    font-weight: 750;
    letter-spacing: 2px;
    padding: 0 12px 0 6px;
}}
QLabel#modeBadge, QLabel#chip {{
    background: {SURFACE_2};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 1px;
}}
QLabel#pageEyebrow {{
    color: {ACCENT};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.4px;
}}
QLabel#pageTitle {{
    color: {TEXT};
    font-size: 22px;
    font-weight: 650;
}}
QLabel#sectionTitle {{
    color: {TEXT};
    font-size: 14px;
    font-weight: 650;
}}
QLabel#pageDescription, QLabel#secondary {{ color: {TEXT_DIM}; }}
QLabel#tertiary {{ color: {TEXT_MUTED}; font-size: 11px; }}
QLabel#metric {{
    background: {SURFACE_2};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    font-family: "Cascadia Mono", "SFMono-Regular", monospace;
    font-size: 11px;
}}
QLabel#bestMetric {{
    background: {SURFACE_2};
    color: {PURPLE};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    font-family: "Cascadia Mono", "SFMono-Regular", monospace;
    font-size: 11px;
}}

QFrame#pageHeader {{
    background: {SURFACE_1};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#panel, QFrame#summaryCard, QWidget#sidePanel {{
    background: {SURFACE_1};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QMenuBar {{
    background: {BG};
    color: {TEXT_DIM};
    border-bottom: 1px solid {BORDER};
    padding: 2px 6px;
}}
QMenuBar::item {{ padding: 5px 9px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QMenu {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    padding: 5px;
}}
QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

QToolBar {{
    background: {SURFACE_1};
    border: none;
    border-bottom: 1px solid {BORDER};
    spacing: 3px;
    padding: 7px 9px;
}}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 6px 9px; }}
QToolButton {{
    background: transparent;
    color: {TEXT_DIM};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 11px;
    font-weight: 600;
}}
QToolButton:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QToolButton:checked {{
    background: {SURFACE_3};
    color: {TEXT};
    border-color: {BORDER_STRONG};
}}
QToolButton:disabled {{ color: {TEXT_MUTED}; }}

QStatusBar {{
    background: {SURFACE_1};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
    min-height: 24px;
}}
QStatusBar::item {{ border: none; }}

QPushButton {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 7px 13px;
    min-height: 18px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {TEXT_MUTED}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:disabled {{
    background: {SURFACE_1};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QPushButton#primary {{
    background: {ACCENT};
    color: {BG};
    border-color: {ACCENT};
    font-weight: 700;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#danger {{ color: {RED}; }}
QPushButton#quiet, QPushButton:flat {{
    background: transparent;
    color: {TEXT_DIM};
    border-color: transparent;
}}
QPushButton#quiet:hover, QPushButton:flat:hover {{ background: {SURFACE_2}; color: {TEXT}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 6px 10px;
    min-height: 20px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {SURFACE_3};
    selection-color: {TEXT};
    outline: none;
}}

QListWidget, QTableWidget, QTreeView, QPlainTextEdit {{
    background: {SURFACE_1};
    alternate-background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: none;
    selection-background-color: {SURFACE_3};
    selection-color: {TEXT};
}}
QListWidget::item {{ padding: 9px 10px; border-radius: 5px; margin: 2px 4px; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{
    background: {SURFACE_3};
    color: {TEXT};
    border-left: 2px solid {ACCENT};
}}
QTableWidget {{ gridline-color: transparent; }}
QTableWidget::item {{
    border-bottom: 1px solid {BORDER};
    padding: 7px 9px;
}}
QTableWidget::item:hover {{ background: {SURFACE_2}; }}
QTableWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE_2};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER_STRONG};
    padding: 8px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.7px;
}}
QTableCornerButton::section {{ background: {SURFACE_2}; border: none; }}

QGroupBox {{
    background: {SURFACE_1};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px 12px 10px;
    font-weight: 650;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {TEXT_DIM};
}}

QCheckBox {{ spacing: 8px; background: transparent; padding: 3px 0; }}
QCheckBox::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator:unchecked {{
    background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 4px;
}}

QProgressBar {{
    background: {SURFACE_2};
    border: none;
    border-radius: 3px;
    color: {TEXT_DIM};
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}

QToolTip {{
    background: {SURFACE_3};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 5px;
    padding: 6px;
}}
"""
