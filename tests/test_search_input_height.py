"""検索欄・置換欄の表示行数（実機フィードバックにより追加）のテスト。"""

from __future__ import annotations

from unittest.mock import patch

from editor_app.main_window import MainWindow
from editor_app.settings import DEFAULT_SEARCH_INPUT_LINES, load_search_input_lines


def test_default_search_input_lines(window: MainWindow) -> None:
    assert load_search_input_lines() == DEFAULT_SEARCH_INPUT_LINES
    assert window.search_panel.input_visible_lines() == DEFAULT_SEARCH_INPUT_LINES


def test_default_search_input_lines_is_six() -> None:
    """既定は 6 行（元の 3 行の 2 倍）。

    「検索欄が狭くて打ちにくい」という実機フィードバックへの対応で、
    利用者の求めに応じて **3 行から 2 倍の 6 行**にした値。上の
    ``test_default_search_input_lines`` は定数どうしを見比べているだけ
    なので、**定数を 3 に戻しても緑のまま通る**（2026-08-18（37 回目）に
    変異 `se-search-lines-default` が生き残って発覚）。利用者が頼んだ
    値そのものなので、数字で固定しておく。
    """
    assert DEFAULT_SEARCH_INPUT_LINES == 6


def test_set_input_visible_lines_resizes_both_input_and_replace_boxes(
    window: MainWindow,
) -> None:
    panel = window.search_panel
    before_input_height = panel._input.height()
    before_replace_height = panel._replace_input.height()

    panel.set_input_visible_lines(12)

    assert panel.input_visible_lines() == 12
    assert panel._input.height() > before_input_height
    assert panel._replace_input.height() > before_replace_height
    # 検索欄・置換欄は常に同じ高さに揃える。
    assert panel._input.height() == panel._replace_input.height()


def test_set_search_input_lines_persists_and_applies_to_all_windows(
    window: MainWindow, make_window
) -> None:
    other = make_window()

    window.set_search_input_lines(10)

    assert load_search_input_lines() == 10
    assert window.search_panel.input_visible_lines() == 10
    assert other.search_panel.input_visible_lines() == 10


def test_set_search_input_lines_persists_for_new_windows(
    window: MainWindow, make_window
) -> None:
    window.set_search_input_lines(9)

    new_window = make_window()

    assert new_window.search_panel.input_visible_lines() == 9


def test_prompt_search_input_lines_applies_entered_value(
    window: MainWindow,
) -> None:
    with patch(
        "editor_app.search_replace.prompt_int", return_value=15
    ) as mock_prompt:
        window._prompt_search_input_lines()

    assert mock_prompt.called
    assert window.search_panel.input_visible_lines() == 15


def test_prompt_search_input_lines_cancelled_keeps_previous_value(
    window: MainWindow,
) -> None:
    original = window.search_panel.input_visible_lines()

    with patch("editor_app.search_replace.prompt_int", return_value=None):
        window._prompt_search_input_lines()

    assert window.search_panel.input_visible_lines() == original


# ----------------------------------------------------------------------
# 検索欄と置換欄の幅をそろえる / 検索欄の × ボタンを無くす
# （実機フィードバックにより追加。QGridLayout で列を共有して幅を合わせた）
# ----------------------------------------------------------------------
def test_search_and_replace_inputs_have_same_width(window: MainWindow) -> None:
    panel = window.search_panel
    panel.set_replace_mode(True)
    panel.resize(500, 200)
    panel.show()
    panel.updateGeometry()
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()

    assert panel._input.width() == panel._replace_input.width()
    assert panel._input.x() == panel._replace_input.x()


def test_search_panel_has_no_close_button(window: MainWindow) -> None:
    """検索欄の右の × ボタンは削除済み（Esc かウィンドウの × で閉じる）。"""
    from PySide6.QtWidgets import QPushButton

    labels = [b.text() for b in window.search_panel.findChildren(QPushButton)]
    assert "×" not in labels
    # 検索・置換・全置換のボタンは残っていること。
    assert "検索" in labels
    assert "置換" in labels
    assert "全置換" in labels
