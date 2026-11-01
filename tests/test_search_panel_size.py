"""検索・置換パネルの大きさを固定すること（2026-10-25 実機フィードバック）.

利用者からの要望は 2 つ。

1. **パネルの大きさを完全に固定したい**（うっかり最大化・リサイズしない）。
   最小と最大を同じ大きさにしてあるので、Wayland でもウィンドウマネージャー
   （KWin）が最大化・リサイズを受け付けない。
2. **検索結果が出ると入力欄の大きさが変わる**のを防ぎたい。件数欄が
   文字の長さに合わせて横に伸び、そのぶん入力欄が押し縮められていた
   （「1/222000 件」「本文が変わったため検索し直しました」などで、入力欄が
   256px → 147px → 123px と縮んでいた）。件数欄の幅を固定し、長い案内は
   欄の中で折り返す。

表示行数の設定（検索欄の高さ）を変えたときだけ、新しい高さで固定し直す。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from editor_app import search_panel as search_panel_module
from editor_app.main_window import MainWindow
from editor_app.search_panel import PENDING_QUERY_NOTICE, STALE_RESULT_NOTICE


@pytest.fixture
def panel(window: MainWindow):
    window.new_file()
    window.current_editor().setPlainText("返信 " * 50)
    window.show_search_panel()
    QApplication.processEvents()
    return window.search_panel


def _shape(panel) -> tuple[QSize, int, int, int]:
    QApplication.processEvents()
    return (
        panel.size(),
        panel._input.width(),
        panel._replace_input.width(),
        panel._count_label.width(),
    )


def test_panel_size_is_fixed(panel) -> None:
    """最小と最大が同じ＝マウスでも最大化でも大きさを変えられない。"""
    assert panel.minimumSize() == panel.maximumSize()
    assert panel.size() == panel.minimumSize()


def test_resizing_the_panel_is_refused(panel) -> None:
    before = panel.size()

    panel.resize(before.width() + 300, before.height() + 300)
    QApplication.processEvents()

    assert panel.size() == before


def test_search_results_do_not_change_any_size(panel) -> None:
    """件数欄の文が変わっても、パネルも入力欄も 1px も動かない。"""
    opened = _shape(panel)

    panel._input.setPlainText("返信")
    assert panel._count_label.text() == PENDING_QUERY_NOTICE
    pending = _shape(panel)

    panel.run_search()
    assert panel._count_label.text() == "50 件"
    searched = _shape(panel)

    panel.show_stale_notice()
    stale = _shape(panel)

    panel.show_replacement_summary(222000, canceled=True)
    summary = _shape(panel)

    assert opened == pending == searched == stale == summary


def test_long_query_text_does_not_widen_the_panel(panel) -> None:
    """検索欄に長い語を入れても、欄は横スクロールするだけで広がらない。"""
    before = _shape(panel)

    panel._input.setPlainText("とても長い検索語" * 40)
    panel._replace_input.setPlainText("とても長い置換後の文字列" * 40)

    assert _shape(panel) == before


def test_search_and_replace_inputs_stay_the_same_width(panel) -> None:
    assert panel._input.width() == panel._replace_input.width()


def test_input_width_is_the_configured_number_of_characters(panel) -> None:
    """入力欄は全角 INPUT_WIDTH_CHARS 文字ぶん（＋余白）の幅。

    最初は案内文が丸ごと収まる幅（パネル全体で約 611px）にしたが、
    「大きすぎるので 6 割くらいに」との要望（2026-10-26）で狭めた。
    """
    expected = (
        panel._input.fontMetrics().horizontalAdvance(
            "あ" * search_panel_module.INPUT_WIDTH_CHARS
        )
        + search_panel_module.INPUT_WIDTH_PADDING
    )
    assert panel._input.width() == expected
    assert 10 <= search_panel_module.INPUT_WIDTH_CHARS <= 20


@pytest.mark.parametrize("field", ["_input", "_replace_input"])
def test_input_is_wide_enough_for_its_hint(panel, field: str) -> None:
    """狭めても、空のときに出る案内文は切れずに収まる。"""
    widget = getattr(panel, field)
    hint = widget.placeholderText()
    assert hint
    assert widget.width() >= widget.fontMetrics().horizontalAdvance(hint)


def test_key_help_moved_to_tooltips(panel) -> None:
    """案内文を短くしたぶん、キー操作の説明はマウスを乗せると出るようにした。"""
    assert "Shift+Enter" in panel._input.toolTip()
    assert "Ctrl+Enter" in panel._input.toolTip()
    assert "Ctrl+Enter" in panel._replace_input.toolTip()


def test_count_label_is_fixed_and_wraps_long_notices(panel) -> None:
    """件数欄は見本の件数表示と「Enter か…」の広い方の幅で固定、長い案内は折り返す。"""
    label = panel._count_label
    metrics = label.fontMetrics()
    expected = max(
        metrics.horizontalAdvance(search_panel_module.COUNT_LABEL_SAMPLE),
        metrics.horizontalAdvance(PENDING_QUERY_NOTICE),
    )
    assert label.minimumWidth() == label.maximumWidth() == expected
    assert label.wordWrap()
    # 長い案内も、折り返せば入力欄 1 つぶんの高さに収まる（はみ出して
    # 行の高さを押し広げない）。
    label.setText(STALE_RESULT_NOTICE)
    needed = label.heightForWidth(label.width())
    assert needed <= panel._input.height()


def test_changing_the_visible_lines_refixes_the_height(window: MainWindow, panel) -> None:
    """表示行数の設定を変えたときは、新しい高さで固定し直す（幅はそのまま）。"""
    before = panel.size()
    lines = panel.input_visible_lines()

    panel.set_input_visible_lines(lines + 4)
    QApplication.processEvents()

    assert panel.height() > before.height()
    assert panel.width() == before.width()
    assert panel.minimumSize() == panel.maximumSize() == panel.size()
