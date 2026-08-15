"""アプリ全体のライト/ダークテーマ切り替え（実機フィードバックにより追加）.

**なぜパレットだけでは駄目なのか（2026-08-06 の作り直しの経緯）**

当初は ``QApplication.setPalette()`` にスタイル ``Fusion`` を組み合わせる、
Qt では定番のやり方で実装していた。ところが実機（KDE Plasma / Wayland）では
「ライトを選んでいるのに起動直後からずっとダークのまま、切り替えても
一切変わらない」という症状になった。KDE のネイティブなプラットフォームテーマ
統合（``KDEPlasmaPlatformTheme6.so`` / plasma-integration）が、アプリ側の
``setPalette()`` を常に自分のパレットで上書きしてしまうためと考えられる。

そこで本モジュールでは **QSS（``QApplication.setStyleSheet()``）を主体**に
切り替えた。QSS はスタイル／パレットより後段で効くため、プラットフォームテーマ
統合による上書きの影響を受けにくい。パレットの設定も従来どおり併用し、
さらに各ウィンドウにも明示的にパレットを設定する（後述）。

**QSS を書くときの厳守事項**

- ``QTextEdit`` / ``QPlainTextEdit`` / ``QAbstractScrollArea`` を対象にした
  セレクタは**絶対に書かない**。エディタ本文の配色（``EditorWidget``）は
  すでに実機で動いている ``set_editor_colors()``（``QPalette`` 直接指定）が
  担当しており、QSS を当てると必ず衝突して壊れる。
- ``QWidget`` のような包括的な型セレクタも使わない。QSS の型セレクタは
  派生クラスにもマッチするため、``QWidget`` は ``QPlainTextEdit`` にも効いて
  しまう。対象はメニュー・ボタン等の「chrome（本文以外の枠組み）」に
  相当するウィジェット種別だけを個別に列挙する。
- ``QScrollBar`` は **サブコントロール一式をまとめて**指定する
  （2026-08-05 追加。それまでは対象外にしてパレット任せにしていた）。
  QSS で溝とつまみだけを指定すると、両端の矢印ボタンが「押せるのに何も
  描かれない空白」になってしまうことを offscreen の描画結果で確認した
  （下の「スクロールバーだけ QSS にした理由」を参照）。部分指定は禁止。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

THEME_LIGHT = "light"
THEME_DARK = "dark"
DEFAULT_THEME = THEME_LIGHT

VALID_THEMES = {THEME_LIGHT, THEME_DARK}


def normalize_theme(theme: str) -> str:
    """不正・未知の値を既定テーマに丸める。"""
    return theme if theme in VALID_THEMES else DEFAULT_THEME


# ----------------------------------------------------------------------
# 配色の定義（パレットと QSS の両方がここを参照する）
# ----------------------------------------------------------------------

#: テーマごとの色。キーの意味は QPalette のロール名に合わせてある。
_COLORS: dict[str, dict[str, str]] = {
    THEME_LIGHT: {
        "window": "#efefef",
        "window_text": "#1a1a1a",
        "base": "#ffffff",
        "alternate_base": "#e8e8e8",
        "text": "#1a1a1a",
        "button": "#efefef",
        "button_text": "#1a1a1a",
        "button_hover": "#e2e2e2",
        "button_pressed": "#d4d4d4",
        "border": "#b4b4b4",
        "highlight": "#308cc6",
        "highlighted_text": "#ffffff",
        "disabled_text": "#9a9a9a",
        "tooltip_base": "#ffffdc",
        "tooltip_text": "#1a1a1a",
        # スクロールバー。溝 (groove) とつまみ (handle) の明度をはっきり
        # 離す（実機フィードバック: ダークでつまみを見つけづらい）。
        "scrollbar_groove": "#e4e4e4",
        "scrollbar_handle": "#a6a6a6",
        "scrollbar_handle_hover": "#8d8d8d",
        "scrollbar_handle_pressed": "#767676",
    },
    THEME_DARK: {
        "window": "#353535",
        "window_text": "#f0f0f0",
        "base": "#232323",
        "alternate_base": "#353535",
        "text": "#f0f0f0",
        "button": "#353535",
        "button_text": "#f0f0f0",
        "button_hover": "#454545",
        "button_pressed": "#2a2a2a",
        "border": "#5a5a5a",
        "highlight": "#2a82da",
        "highlighted_text": "#000000",
        "disabled_text": "#7f7f7f",
        "tooltip_base": "#454545",
        "tooltip_text": "#f0f0f0",
        # 溝は本文の背景 (base #232323) とウィンドウ (#353535) の中間、
        # つまみはそこから 2〜3 段階明るいグレーにして、暗い本文の隣でも
        # 位置がすぐ分かるようにする。
        "scrollbar_groove": "#2b2b2b",
        "scrollbar_handle": "#6e6e6e",
        "scrollbar_handle_hover": "#868686",
        "scrollbar_handle_pressed": "#9c9c9c",
    },
}


def colors_for(theme: str) -> dict[str, str]:
    """指定したテーマの色表を返す（不正な値は既定テーマ扱い）。"""
    return dict(_COLORS[normalize_theme(theme)])


def _palette_from_colors(colors: dict[str, str]) -> QPalette:
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(colors["window"]))
    palette.setColor(role.WindowText, QColor(colors["window_text"]))
    palette.setColor(role.Base, QColor(colors["base"]))
    palette.setColor(role.AlternateBase, QColor(colors["alternate_base"]))
    palette.setColor(role.Text, QColor(colors["text"]))
    palette.setColor(role.PlaceholderText, QColor(colors["disabled_text"]))
    palette.setColor(role.Button, QColor(colors["button"]))
    palette.setColor(role.ButtonText, QColor(colors["button_text"]))
    palette.setColor(role.BrightText, QColor("#ff5a5a"))
    palette.setColor(role.ToolTipBase, QColor(colors["tooltip_base"]))
    palette.setColor(role.ToolTipText, QColor(colors["tooltip_text"]))
    palette.setColor(role.Link, QColor(colors["highlight"]))
    palette.setColor(role.Highlight, QColor(colors["highlight"]))
    palette.setColor(role.HighlightedText, QColor(colors["highlighted_text"]))

    disabled = QColor(colors["disabled_text"])
    palette.setColor(group.Disabled, role.Text, disabled)
    palette.setColor(group.Disabled, role.ButtonText, disabled)
    palette.setColor(group.Disabled, role.WindowText, disabled)
    return palette


def palette_for(theme: str) -> QPalette:
    """指定したテーマのパレットを返す（不正な値は既定テーマ扱い）。"""
    return _palette_from_colors(colors_for(theme))


# ----------------------------------------------------------------------
# QSS（本命）
# ----------------------------------------------------------------------

#: QSS の対象にしてはいけないウィジェット型（テストからも参照する）。
#: エディタ本文の配色機能と衝突するため。
EXCLUDED_QSS_SELECTORS = (
    "QWidget",
    "QAbstractScrollArea",
    "QPlainTextEdit",
    "QTextEdit",
)

#: QScrollBar の QSS で必ず定義しておくサブコントロール（テストからも参照する）。
#: 一部だけ指定すると、指定しなかったサブコントロールが「場所は取るのに
#: 何も描かれない」状態になる（offscreen で描画結果を確認済み）。
REQUIRED_SCROLLBAR_SUBCONTROLS = (
    "handle",
    "add-line",
    "sub-line",
    "add-page",
    "sub-page",
)

_QSS_TEMPLATE = """\
/* Tuxpad のテーマ用スタイルシート。
   エディタ本文 (QPlainTextEdit / QTextEdit) は意図的に対象外にしている
   （editor_app/theme.py の説明を参照）。 */

QMainWindow, QDialog, QMessageBox {{
    background-color: {window};
    color: {window_text};
}}

QMenuBar {{
    background-color: {window};
    color: {window_text};
    border: none;
}}
QMenuBar::item {{
    background: transparent;
    color: {window_text};
    padding: 4px 8px;
}}
QMenuBar::item:selected {{
    background-color: {highlight};
    color: {highlighted_text};
}}
QMenuBar::item:disabled {{
    color: {disabled_text};
}}

QMenu {{
    background-color: {window};
    color: {window_text};
    border: 1px solid {border};
}}
QMenu::item {{
    padding: 4px 24px 4px 24px;
    background: transparent;
}}
QMenu::item:selected {{
    background-color: {highlight};
    color: {highlighted_text};
}}
QMenu::item:disabled {{
    color: {disabled_text};
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 8px;
}}

QToolBar {{
    background-color: {window};
    color: {window_text};
    border: none;
}}
QStatusBar {{
    background-color: {window};
    color: {window_text};
}}
QStatusBar::item {{
    border: none;
}}

QLabel {{
    background: transparent;
    color: {window_text};
}}
QLabel:disabled {{
    color: {disabled_text};
}}

QGroupBox {{
    background-color: {window};
    color: {window_text};
    border: 1px solid {border};
    border-radius: 3px;
    margin-top: 8px;
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 3px;
    color: {window_text};
}}

QPushButton, QToolButton {{
    background-color: {button};
    color: {button_text};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 4px 10px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {button_hover};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {button_pressed};
}}
QPushButton:default {{
    border: 1px solid {highlight};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {disabled_text};
    border-color: {disabled_text};
}}

QCheckBox, QRadioButton {{
    background: transparent;
    color: {window_text};
    spacing: 6px;
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {disabled_text};
}}

QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 3px 5px;
    selection-background-color: {highlight};
    selection-color: {highlighted_text};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {disabled_text};
}}

QComboBox {{
    background-color: {button};
    color: {button_text};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 3px 5px;
}}
QComboBox:disabled {{
    color: {disabled_text};
}}
QComboBox QAbstractItemView {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {highlight};
    selection-color: {highlighted_text};
}}

QToolTip {{
    background-color: {tooltip_base};
    color: {tooltip_text};
    border: 1px solid {border};
}}

/* スクロールバー。
   サブコントロールを一式まとめて定義すること（部分指定は禁止。
   モジュール冒頭の説明を参照）。両端の矢印ボタンは、QSS では矢印そのものを
   画像なしで描けず「空白のボタン」になってしまうため、高さ・幅 0 にして
   無くしてある（つまみのドラッグ・溝のクリック・ホイールで操作する）。 */
QScrollBar:vertical {{
    background: {scrollbar_groove};
    width: 14px;
    margin: 0;
    border: none;
}}
QScrollBar:horizontal {{
    background: {scrollbar_groove};
    height: 14px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {scrollbar_handle};
    min-height: 28px;
    border: none;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {scrollbar_handle};
    min-width: 28px;
    border: none;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {scrollbar_handle_hover};
}}
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {{
    background: {scrollbar_handle_pressed};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    width: 0px;
    background: none;
    border: none;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    height: 0px;
    width: 0px;
    background: none;
    border: none;
}}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,
QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {{
    background: none;
    border: none;
    width: 0px;
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}
"""


def stylesheet_for(theme: str) -> str:
    """指定したテーマの QSS を返す（不正な値は既定テーマ扱い）。

    ライト・ダークとも必ず明示的な QSS を返す。「ライトは QSS を空にして
    ネイティブに任せる」とすると、KDE 側の既定がダークの環境で
    ライトを選んでもダークに見えてしまう（今回直した不具合そのもの）。
    """
    return _QSS_TEMPLATE.format(**colors_for(theme))


# ----------------------------------------------------------------------
# 適用
# ----------------------------------------------------------------------


def apply_theme_to_window(widget: QWidget, theme: str) -> None:
    """ウィンドウ 1 枚にテーマのパレットを明示的に適用する。

    ``QApplication.setPalette()`` はプラットフォームテーマ統合に上書き
    されうるが、ウィジェットへ直接設定したパレットは上書きされない
    （かつ子ウィジェットへ伝播する）。自前で描画しているタブバー
    （``MultiRowTabBar``）のように QSS が効かない要素も、これで
    テーマに追従させられる。

    エディタ本文のカスタム配色（``EditorWidget.set_editor_colors()``）は
    ``WA_SetPalette`` 付きで個別に設定されているため、親からの伝播では
    上書きされない（Qt の仕様）。
    """
    widget.setPalette(palette_for(theme))


def apply_theme(app: QApplication, theme: str) -> None:
    """アプリ全体にテーマを適用する（実行中に切り替えても即座に反映される）。

    1. ``Fusion`` スタイルにする（プラットフォームスタイルの独自描画を避ける）。
    2. アプリのパレットを設定する（QSS の対象外にした要素のための土台）。
    3. **QSS を設定する（本命）**。プラットフォームテーマ統合による
       パレット上書きの影響を受けにくい。
    4. 既に開いている各ウィンドウにもパレットを直接設定する。
    """
    theme = normalize_theme(theme)

    # QSS を設定していると ``app.style()`` は QStyleSheetStyle の包み込みに
    # なり、元のスタイル名が取れない（＝「もう Fusion かどうか」を判定
    # できない）ため、毎回そのまま設定し直す。実測で数 ms なので問題ない。
    app.setStyle("Fusion")

    app.setPalette(palette_for(theme))

    # スタイルシートの設定は全ウィジェットの polish をやり直させるので、
    # 中身が変わらないときは呼ばない（同じテーマを選び直したときに
    # 画面全体が再描画されるのを避ける）。
    stylesheet = stylesheet_for(theme)
    if app.styleSheet() != stylesheet:
        app.setStyleSheet(stylesheet)

    for widget in app.topLevelWidgets():
        if widget.isWindow():
            apply_theme_to_window(widget, theme)
