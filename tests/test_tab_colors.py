"""アクティブ / 非アクティブなタブの色を設定できることのテスト.

実機フィードバックによる追加機能。エディタ本文の配色と同じく
**アプリ全体で共有する設定**（全ウィンドウに即座に反映し、QSettings で
次回起動時にも復元する）。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from editor_app.settings import load_tab_colors, save_tab_colors
from editor_app.tab_bar import MultiRowTabBar
from editor_app.tab_widget import MultiRowTabWidget

ACTIVE = QColor("#ffcc00")
INACTIVE = QColor("#334455")


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    widget = MultiRowTabBar()
    qtbot.addWidget(widget)
    widget.show()
    widget.addTab("a.txt")
    widget.addTab("b.txt")
    return widget


# ----------------------------------------------------------------------
# MultiRowTabBar の色決定ロジック
# ----------------------------------------------------------------------


def test_default_colors_come_from_palette(bar):
    assert bar.tab_colors() == (None, None)

    palette = bar.palette()
    assert bar.tab_background_color(selected=True, hovered=False) == palette.base().color()
    assert bar.tab_background_color(selected=False, hovered=False) == palette.button().color()


def test_custom_active_color_is_used_for_selected_tab(bar):
    bar.set_tab_colors(ACTIVE, None)

    assert bar.tab_background_color(selected=True, hovered=False) == ACTIVE
    # 非アクティブ側は既定のまま。
    assert bar.tab_background_color(selected=False, hovered=False) == bar.palette().button().color()


def test_custom_inactive_color_is_used_for_unselected_tab(bar):
    bar.set_tab_colors(None, INACTIVE)

    assert bar.tab_background_color(selected=False, hovered=False) == INACTIVE
    assert bar.tab_background_color(selected=True, hovered=False) == bar.palette().base().color()


def test_hover_still_lightens_the_inactive_color(bar):
    bar.set_tab_colors(ACTIVE, INACTIVE)

    hovered = bar.tab_background_color(selected=False, hovered=True)
    assert hovered == INACTIVE.lighter(108)
    assert hovered != INACTIVE


def test_selected_tab_ignores_hover(bar):
    bar.set_tab_colors(ACTIVE, INACTIVE)

    assert bar.tab_background_color(selected=True, hovered=True) == ACTIVE


def test_reset_restores_palette_colors(bar):
    bar.set_tab_colors(ACTIVE, INACTIVE)
    bar.set_tab_colors(None, None)

    assert bar.tab_colors() == (None, None)
    assert bar.tab_background_color(selected=True, hovered=False) == bar.palette().base().color()


def test_painting_does_not_raise_with_custom_colors(bar, qtbot):
    """カスタム色でも描画が例外にならない（offscreen で paintEvent を回す）。"""
    bar.set_tab_colors(ACTIVE, INACTIVE)
    bar.repaint()
    QApplication.processEvents()


def test_tab_widget_delegates_colors(qtbot):
    from PySide6.QtWidgets import QWidget

    tabs = MultiRowTabWidget()
    qtbot.addWidget(tabs)
    tabs.addTab(QWidget(), "a.txt")

    assert tabs.tab_colors() == (None, None)
    tabs.set_tab_colors(ACTIVE, INACTIVE)
    assert tabs.tab_colors() == (ACTIVE, INACTIVE)
    assert tabs.tabBar().tab_colors() == (ACTIVE, INACTIVE)


# ----------------------------------------------------------------------
# 設定の保存・復元
# ----------------------------------------------------------------------


def test_settings_roundtrip(qapp):
    save_tab_colors(ACTIVE, INACTIVE)

    active, inactive = load_tab_colors()
    assert active == ACTIVE
    assert inactive == INACTIVE


def test_settings_default_is_none(qapp):
    assert load_tab_colors() == (None, None)


def test_settings_none_is_saved_as_unset(qapp):
    save_tab_colors(ACTIVE, INACTIVE)
    save_tab_colors(None, None)

    assert load_tab_colors() == (None, None)


# ----------------------------------------------------------------------
# MainWindow に組み込んだ状態
# ----------------------------------------------------------------------


def test_window_starts_with_default_colors(window):
    assert window.tab_colors() == (None, None)
    assert window.tabs.tab_colors() == (None, None)


def test_window_set_tab_colors_applies_and_saves(window):
    window.set_tab_colors(ACTIVE, INACTIVE)

    assert window.tab_colors() == (ACTIVE, INACTIVE)
    assert window.tabs.tabBar().tab_colors() == (ACTIVE, INACTIVE)
    assert load_tab_colors() == (ACTIVE, INACTIVE)


def test_change_applies_to_all_open_windows(make_window):
    first = make_window()
    second = make_window()

    first.set_tab_colors(ACTIVE, INACTIVE)

    assert second.tab_colors() == (ACTIVE, INACTIVE)
    assert second.tabs.tabBar().tab_colors() == (ACTIVE, INACTIVE)


def test_new_window_restores_saved_colors(make_window):
    make_window().set_tab_colors(ACTIVE, INACTIVE)

    later = make_window()

    assert later.tab_colors() == (ACTIVE, INACTIVE)
    assert later.tabs.tabBar().tab_colors() == (ACTIVE, INACTIVE)


def test_reset_action_restores_defaults(window):
    window.set_tab_colors(ACTIVE, INACTIVE)

    window.action_tab_color_reset.trigger()

    assert window.tab_colors() == (None, None)
    assert window.tabs.tabBar().tab_colors() == (None, None)
    assert load_tab_colors() == (None, None)


# ----------------------------------------------------------------------
# 色選択ダイアログ
# ----------------------------------------------------------------------


def test_prompt_active_color_applies_choice(window, monkeypatch):
    captured = {}

    def fake_get_color(initial, parent=None, title=""):
        captured["initial"] = QColor(initial)
        return ACTIVE

    monkeypatch.setattr("editor_app.dialogs.QColorDialog.getColor", fake_get_color)
    window.action_tab_active_color.trigger()

    assert window.tab_colors() == (ACTIVE, None)
    # 未設定のときの初期値はパレットの実際の色。
    assert captured["initial"] == window.tabs.tabBar().palette().color(QPalette.ColorRole.Base)


def test_prompt_inactive_color_applies_choice(window, monkeypatch):
    monkeypatch.setattr(
        "editor_app.dialogs.QColorDialog.getColor",
        lambda *args, **kwargs: INACTIVE,
    )
    window.action_tab_inactive_color.trigger()

    assert window.tab_colors() == (None, INACTIVE)


def test_prompt_cancel_keeps_current_colors(window, monkeypatch):
    """キャンセル（無効な色）なら何も変えない。"""
    window.set_tab_colors(ACTIVE, INACTIVE)
    monkeypatch.setattr(
        "editor_app.dialogs.QColorDialog.getColor",
        lambda *args, **kwargs: QColor(),
    )

    window.action_tab_active_color.trigger()
    window.action_tab_inactive_color.trigger()

    assert window.tab_colors() == (ACTIVE, INACTIVE)


# ----------------------------------------------------------------------
# 文字色の読みやすさ（カスタム色を選んだときだけ自動で白/黒にする）
# ----------------------------------------------------------------------


def test_text_color_follows_palette_by_default(bar):
    palette = bar.palette()
    assert bar.tab_text_color(selected=True) == palette.windowText().color()
    assert bar.tab_text_color(selected=False) == palette.buttonText().color()


def test_dark_custom_color_gets_white_text(bar):
    bar.set_tab_colors(QColor("#102030"), QColor("#203040"))

    assert bar.tab_text_color(selected=True) == QColor("white")
    assert bar.tab_text_color(selected=False) == QColor("white")


def test_light_custom_color_gets_black_text(bar):
    bar.set_tab_colors(QColor("#ffe680"), QColor("#f0f0f0"))

    assert bar.tab_text_color(selected=True) == QColor("black")
    assert bar.tab_text_color(selected=False) == QColor("black")


def test_only_the_customized_side_changes_text_color(bar):
    """片方だけカスタムしたら、もう片方の文字色はパレットのまま。"""
    bar.set_tab_colors(QColor("#102030"), None)

    assert bar.tab_text_color(selected=True) == QColor("white")
    assert bar.tab_text_color(selected=False) == bar.palette().buttonText().color()
