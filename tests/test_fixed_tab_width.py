"""「タブの幅を固定値に設定できるようにする」のテスト.

ファイル名の長さでタブ幅がバラバラになるのを避けるための機能
（実機フィードバックにより追加）。設定は **ウィンドウごとに独立** で、
QSettings には保存しない（新しいウィンドウは自動幅から始まる）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from editor_app.tab_bar import MAX_TAB_WIDTH, MIN_TAB_WIDTH, MultiRowTabBar
from editor_app.tab_widget import MultiRowTabWidget


# ----------------------------------------------------------------------
# 補助
# ----------------------------------------------------------------------


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    widget = MultiRowTabBar()
    widget.setTabsClosable(True)
    qtbot.addWidget(widget)
    widget.show()
    widget.resize(800, widget.height())
    QApplication.processEvents()
    return widget


def widths(bar: MultiRowTabBar) -> list[int]:
    return [bar.tabRect(i).width() for i in range(bar.count())]


# ----------------------------------------------------------------------
# MultiRowTabBar 単体
# ----------------------------------------------------------------------


def test_default_is_auto_width(bar):
    """既定は自動幅（従来通り、名前の長さでタブ幅が変わる）。"""
    bar.addTab("a.txt")
    bar.addTab("とても長いファイル名のテキストファイル.txt")

    assert bar.fixed_tab_width() is None
    assert len(set(widths(bar))) == 2


def test_fixed_width_makes_all_tabs_identical(bar):
    """固定幅を指定すると、名前の長さに関係なく全タブが同じ幅になる。"""
    bar.addTab("a.txt")
    bar.addTab("とても長いファイル名のテキストファイル.txt")
    bar.addTab("b.md")

    bar.set_fixed_tab_width(120)

    assert bar.fixed_tab_width() == 120
    assert widths(bar) == [120, 120, 120]


def test_fixed_width_is_not_clamped_by_min_max(bar):
    """MIN_TAB_WIDTH / MAX_TAB_WIDTH では丸めず、指定値をそのまま使う。"""
    bar.addTab("a.txt")

    bar.set_fixed_tab_width(MIN_TAB_WIDTH - 20)
    assert widths(bar) == [MIN_TAB_WIDTH - 20]

    bar.set_fixed_tab_width(MAX_TAB_WIDTH + 100)
    assert widths(bar) == [MAX_TAB_WIDTH + 100]


def test_fixed_width_never_exceeds_available_width(bar):
    """ウィンドウ幅より広い固定幅を指定しても、はみ出さない。"""
    bar.addTab("a.txt")
    bar.resize(200, bar.height())
    QApplication.processEvents()

    bar.set_fixed_tab_width(500)

    assert widths(bar) == [200]


def test_none_restores_auto_width(bar):
    """None を渡すと自動幅に戻る。"""
    bar.addTab("a.txt")
    bar.addTab("とても長いファイル名のテキストファイル.txt")
    auto = widths(bar)

    bar.set_fixed_tab_width(140)
    assert widths(bar) == [140, 140]

    bar.set_fixed_tab_width(None)
    assert bar.fixed_tab_width() is None
    assert widths(bar) == auto


def test_long_name_is_elided_in_fixed_width(bar):
    """固定幅に収まらない長い名前は … で省略表示される。"""
    long_name = "とても長いファイル名のテキストファイル.txt"
    bar.addTab(long_name)
    bar.set_fixed_tab_width(80)

    metrics = bar.fontMetrics()
    text_rect = bar._text_rect(0)
    assert metrics.horizontalAdvance(long_name) > text_rect.width()

    elided = metrics.elidedText(long_name, Qt.TextElideMode.ElideMiddle, text_rect.width())
    assert elided != long_name
    assert "…" in elided


def test_fixed_width_applies_to_tabs_added_later(bar):
    """固定幅を設定したあとに追加したタブにも効く。"""
    bar.addTab("a.txt")
    bar.set_fixed_tab_width(100)
    bar.addTab("とても長いファイル名のテキストファイル.txt")

    assert widths(bar) == [100, 100]


def test_fixed_width_changes_row_count(bar):
    """固定幅を広げると、折り返しの段数が増える。"""
    for i in range(8):
        bar.addTab(f"f{i}.txt")
    bar.resize(400, bar.height())
    QApplication.processEvents()

    bar.set_fixed_tab_width(60)
    narrow_rows = bar.rowCount()

    bar.set_fixed_tab_width(200)
    assert bar.rowCount() > narrow_rows


# ----------------------------------------------------------------------
# MultiRowTabWidget への委譲
# ----------------------------------------------------------------------


def test_tab_widget_delegates_to_bar(qtbot):
    from PySide6.QtWidgets import QWidget

    tabs = MultiRowTabWidget()
    qtbot.addWidget(tabs)
    tabs.show()
    tabs.addTab(QWidget(), "a.txt")

    assert tabs.fixed_tab_width() is None
    tabs.set_fixed_tab_width(130)
    assert tabs.fixed_tab_width() == 130
    assert tabs.tabBar().fixed_tab_width() == 130


# ----------------------------------------------------------------------
# MainWindow に組み込んだ状態
# ----------------------------------------------------------------------


def test_window_starts_with_auto_width(window):
    assert window.fixed_tab_width() is None
    assert window.action_tab_width_auto.isChecked()
    assert not window.action_tab_width_fixed.isChecked()


def test_window_set_fixed_tab_width(window):
    window.new_file()
    window.new_file()
    window.set_fixed_tab_width(110)
    QApplication.processEvents()

    bar = window.tabs.tabBar()
    assert widths(bar) == [110] * bar.count()
    assert window.fixed_tab_width() == 110
    assert window.action_tab_width_fixed.isChecked()
    assert not window.action_tab_width_auto.isChecked()


def test_window_back_to_auto_width(window):
    window.new_file()
    window.set_fixed_tab_width(110)
    window.set_fixed_tab_width(None)
    QApplication.processEvents()

    assert window.fixed_tab_width() is None
    assert window.action_tab_width_auto.isChecked()
    assert len(set(widths(window.tabs.tabBar()))) >= 1


def test_fixed_width_applies_to_new_tab_in_same_window(window):
    window.set_fixed_tab_width(90)
    window.new_file()
    window.new_file()
    QApplication.processEvents()

    bar = window.tabs.tabBar()
    assert bar.count() >= 3
    assert widths(bar) == [90] * bar.count()


def test_fixed_width_is_per_window(make_window):
    """別ウィンドウには引き継がれない（ウィンドウごとに独立した設定）。"""
    first = make_window()
    first.set_fixed_tab_width(120)

    second = make_window()

    assert first.fixed_tab_width() == 120
    assert second.fixed_tab_width() is None
    assert second.action_tab_width_auto.isChecked()


def test_fixed_width_is_not_persisted(make_window):
    """QSettings に保存されない（次に作るウィンドウも自動幅から始まる）。"""
    win = make_window()
    win.set_fixed_tab_width(150)
    win.close()

    assert make_window().fixed_tab_width() is None


def test_torn_off_window_starts_with_auto_width(window, qtbot):
    """タブを切り離した先の新しいウィンドウも自動幅から始まる。"""
    from PySide6.QtCore import QPoint

    window.set_fixed_tab_width(120)
    editor = window.new_file()

    new_window = window._tear_off_tab_to_new_window(editor, QPoint(300, 300))
    qtbot.addWidget(new_window)

    assert new_window is not None
    assert new_window.fixed_tab_width() is None
    assert window.fixed_tab_width() == 120


# ----------------------------------------------------------------------
# メニューから開くダイアログ
# ----------------------------------------------------------------------


def test_prompt_applies_entered_width(window, monkeypatch):
    """ダイアログで入力した値が固定幅として適用される。"""
    captured = {}

    def fake_get_int(parent, title, label, value, minimum, maximum, step):
        captured.update(value=value, minimum=minimum, maximum=maximum)
        return 160, True

    monkeypatch.setattr("editor_app.dialogs.QInputDialog.getInt", fake_get_int)
    window.action_tab_width_fixed.trigger()

    assert window.fixed_tab_width() == 160
    # 初期値は「いま表示されているタブ幅」。閉じるボタンが消えない範囲に限る。
    assert captured["value"] > 0
    assert captured["minimum"] == MIN_TAB_WIDTH


def test_prompt_cancel_keeps_current_setting(window, monkeypatch):
    """キャンセルすると設定もメニューのチェック状態も変わらない。"""
    monkeypatch.setattr(
        "editor_app.dialogs.QInputDialog.getInt",
        lambda *args, **kwargs: (0, False),
    )
    window.action_tab_width_fixed.trigger()

    assert window.fixed_tab_width() is None
    assert window.action_tab_width_auto.isChecked()
    assert not window.action_tab_width_fixed.isChecked()


def test_auto_action_restores_auto_width(window):
    """「自動」を選ぶと固定幅が解除される。"""
    window.set_fixed_tab_width(120)
    window.action_tab_width_auto.trigger()

    assert window.fixed_tab_width() is None
    assert window.action_tab_width_auto.isChecked()
