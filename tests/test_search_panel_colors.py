"""検索欄・置換欄の配色（実機フィードバックにより追加）のテスト。

ダークテーマだと検索欄の背景が暗く、選択ハイライトも見えづらいとの
報告を受けて、テーマとは無関係に白背景・黒文字・見やすいハイライト色に
固定した。
"""

from __future__ import annotations

from PySide6.QtGui import QPalette

from editor_app.main_window import MainWindow
from editor_app.search_panel import (
    _INPUT_BASE_COLOR,
    _INPUT_HIGHLIGHT_COLOR,
    _INPUT_HIGHLIGHTED_TEXT_COLOR,
    _INPUT_TEXT_COLOR,
)
from editor_app.theme import THEME_DARK, THEME_LIGHT


def _assert_fixed_colors(widget) -> None:
    palette = widget.palette()
    assert palette.color(QPalette.ColorRole.Base) == _INPUT_BASE_COLOR
    assert palette.color(QPalette.ColorRole.Text) == _INPUT_TEXT_COLOR
    assert palette.color(QPalette.ColorRole.Highlight) == _INPUT_HIGHLIGHT_COLOR
    assert (
        palette.color(QPalette.ColorRole.HighlightedText)
        == _INPUT_HIGHLIGHTED_TEXT_COLOR
    )
    # QPlainTextEdit は QAbstractScrollArea なので、実際に描画しているのは
    # viewport() 側。そちらにも同じ背景色が設定されていること。
    assert widget.viewport().palette().color(QPalette.ColorRole.Base) == _INPUT_BASE_COLOR


def test_search_input_has_fixed_colors_by_default(window: MainWindow) -> None:
    _assert_fixed_colors(window.search_panel._input)
    _assert_fixed_colors(window.search_panel._replace_input)


def test_search_input_colors_survive_dark_theme(window: MainWindow) -> None:
    window.set_theme(THEME_DARK)

    _assert_fixed_colors(window.search_panel._input)
    _assert_fixed_colors(window.search_panel._replace_input)

    window.set_theme(THEME_LIGHT)


def test_search_input_colors_survive_repeated_theme_switching(
    window: MainWindow,
) -> None:
    for theme in (THEME_DARK, THEME_LIGHT, THEME_DARK, THEME_LIGHT):
        window.set_theme(theme)
        _assert_fixed_colors(window.search_panel._input)
        _assert_fixed_colors(window.search_panel._replace_input)


def test_changeevent_does_not_reapply_while_already_reapplying(
    window: MainWindow,
) -> None:
    """配色を貼り直している最中の ``changeEvent`` は、貼り直しを繰り返さない。

    ``_apply_fixed_palette()`` の ``setPalette()`` が自分自身への
    ``changeEvent`` を呼び戻すため、この再入ガードが無いと無限再帰で落ちる。
    「貼り直し中」の印を立てた状態で配色変更イベントを送り、貼り直しが
    重ねて呼ばれないことを確かめる（``changeEvent`` の防御分岐）。
    """
    from PySide6.QtCore import QEvent

    field = window.search_panel._input
    field._reapplying_fixed_palette = True
    applied: list[int] = []
    field._apply_fixed_palette = lambda: applied.append(1)

    field.changeEvent(QEvent(QEvent.Type.StyleChange))

    assert applied == []
