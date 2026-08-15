"""本文フォント（ファミリー・サイズ）の設定のテスト。実機フィードバックで追加。"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFontDialog

from editor_app.editor import (
    DEFAULT_FONT_FAMILY,
    DEFAULT_FONT_SIZE,
    TAB_STOP_SPACES,
    EditorWidget,
)
from editor_app.main_window import MainWindow
from editor_app.settings import load_font_settings


def test_editor_default_font_settings(window: MainWindow) -> None:
    editor = window.current_editor()
    assert editor.font_family() == DEFAULT_FONT_FAMILY
    assert editor.font_size() == DEFAULT_FONT_SIZE
    assert editor.font().pointSize() == DEFAULT_FONT_SIZE


def test_editor_default_font_keeps_monospace_style_hint(window: MainWindow) -> None:
    """既定の総称名 "monospace" には従来通り Monospace ヒントを付ける。"""
    editor = window.current_editor()
    assert editor.font().styleHint() == QFont.StyleHint.Monospace


def test_editor_set_font_settings_applies_to_widget_font(window: MainWindow) -> None:
    editor = window.current_editor()

    editor.set_font_settings("Courier", 18)

    assert editor.font_family() == "Courier"
    assert editor.font_size() == 18
    assert editor.font().family() == "Courier"
    assert editor.font().pointSize() == 18


def test_editor_set_font_settings_recomputes_tab_stop_distance(
    window: MainWindow,
) -> None:
    """タブ幅は文字幅に依存するので、フォント変更時に再計算されること。"""
    editor = window.current_editor()

    editor.set_font_settings(DEFAULT_FONT_FAMILY, 10)
    small = editor.tabStopDistance()

    editor.set_font_settings(DEFAULT_FONT_FAMILY, 30)
    large = editor.tabStopDistance()

    expected = TAB_STOP_SPACES * editor.fontMetrics().horizontalAdvance(" ")
    assert large == expected
    assert large > small


def test_editor_set_font_settings_rejects_non_positive_size() -> None:
    editor = EditorWidget(untitled_name="無題-1")

    editor.set_font_settings("Courier", 0)

    assert editor.font_size() == DEFAULT_FONT_SIZE


def test_window_set_font_settings_applies_to_all_existing_tabs(
    window: MainWindow,
) -> None:
    window.new_file()
    window.new_file()

    window.set_font_settings("Courier", 14)

    for editor in window.editors():
        assert editor.font_family() == "Courier"
        assert editor.font_size() == 14


def test_new_tab_inherits_window_font_settings(window: MainWindow) -> None:
    window.set_font_settings("Courier", 13)

    window.new_file()

    assert window.current_editor().font_family() == "Courier"
    assert window.current_editor().font_size() == 13


def test_adopted_tab_inherits_target_window_font_settings(
    window: MainWindow, make_window
) -> None:
    """他のウィンドウから D&D で移動してきたタブにも、移動先の設定が効く。"""
    other = make_window()
    other.set_font_settings("Courier", 20)
    editor = window.current_editor()

    window.tabs.removeTab(window.tabs.indexOf(editor))
    other._adopt_editor(editor)

    assert editor.font_family() == "Courier"
    assert editor.font_size() == 20


def test_window_font_settings_getter(window: MainWindow) -> None:
    window.set_font_settings("Courier", 15)

    assert window.font_settings() == ("Courier", 15)


def test_prompt_font_applies_chosen_font(window: MainWindow, monkeypatch) -> None:
    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (True, QFont("Courier", 16)))
    )

    window._prompt_font()

    assert window.current_editor().font_family() == "Courier"
    assert window.current_editor().font_size() == 16


def test_prompt_font_cancelled_keeps_previous_font(window: MainWindow, monkeypatch) -> None:
    window.set_font_settings("Courier", 16)
    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (False, QFont("Serif", 30)))
    )

    window._prompt_font()

    assert window.font_settings() == ("Courier", 16)
    assert window.current_editor().font_family() == "Courier"


def test_prompt_font_keeps_size_when_dialog_returns_pixel_font(
    window: MainWindow, monkeypatch
) -> None:
    """ピクセル指定のフォント (pointSize() == -1) でも壊れないこと。"""
    window.set_font_settings("Courier", 16)

    pixel_font = QFont("Serif")
    pixel_font.setPixelSize(24)
    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (True, pixel_font))
    )

    window._prompt_font()

    assert window.font_settings() == ("Serif", 16)


# ----------------------------------------------------------------------
# 永続化（前回起動時の状態を復元）
# ----------------------------------------------------------------------
def test_load_font_settings_defaults_when_nothing_saved() -> None:
    assert load_font_settings() == (DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE)


def test_set_font_settings_saved_value_survives_reload(window: MainWindow) -> None:
    window.set_font_settings("Courier", 17)

    assert load_font_settings() == ("Courier", 17)


def test_set_font_settings_persists_across_new_windows(
    window: MainWindow, make_window
) -> None:
    """あるウィンドウで変更した設定が、後から作る別ウィンドウにも引き継がれる
    （= アプリを再起動したのと同じ経路で読み込まれることの確認）。"""
    window.set_font_settings("Courier", 19)

    other = make_window()

    assert other.font_settings() == ("Courier", 19)
    assert other.current_editor().font_family() == "Courier"
    assert other.current_editor().font_size() == 19
