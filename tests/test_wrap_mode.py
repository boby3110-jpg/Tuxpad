"""折り返しモード（なし/ウィンドウ幅/指定文字数）のテスト。実機フィードバックで追加。"""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QTextEdit

from editor_app.editor import DEFAULT_WRAP_COLUMN, DEFAULT_WRAP_MODE, WRAP_FIXED, WRAP_NONE, WRAP_WINDOW
from editor_app.main_window import MainWindow
from editor_app.settings import load_wrap_settings


def test_editor_default_wrap_mode_is_none(window: MainWindow) -> None:
    editor = window.current_editor()
    assert editor.wrap_mode() == WRAP_NONE
    assert editor.lineWrapMode() == QTextEdit.LineWrapMode.NoWrap


def test_editor_set_wrap_window(window: MainWindow) -> None:
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_WINDOW)
    assert editor.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth


def test_editor_set_wrap_fixed_column(window: MainWindow) -> None:
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_FIXED, column=42)
    assert editor.lineWrapMode() == QTextEdit.LineWrapMode.FixedColumnWidth
    assert editor.lineWrapColumnOrWidth() == 42
    assert editor.wrap_column() == 42


def test_window_set_wrap_mode_applies_to_all_existing_tabs(window: MainWindow) -> None:
    window.new_file()
    window.new_file()

    window.set_wrap_mode(WRAP_WINDOW)

    for editor in window.editors():
        assert editor.wrap_mode() == WRAP_WINDOW


def test_new_tab_inherits_window_wrap_mode(window: MainWindow) -> None:
    window.set_wrap_mode(WRAP_FIXED, column=60)

    window.new_file()

    assert window.current_editor().wrap_mode() == WRAP_FIXED
    assert window.current_editor().wrap_column() == 60


def test_set_wrap_mode_checks_matching_action(window: MainWindow) -> None:
    window.set_wrap_mode(WRAP_WINDOW)
    assert window.action_wrap_window.isChecked()
    assert not window.action_wrap_none.isChecked()

    window.set_wrap_mode(WRAP_NONE)
    assert window.action_wrap_none.isChecked()
    assert not window.action_wrap_window.isChecked()


def test_prompt_wrap_fixed_column_applies_entered_value(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (55, True)))

    window._prompt_wrap_fixed_column()

    assert window.current_editor().wrap_mode() == WRAP_FIXED
    assert window.current_editor().wrap_column() == 55
    assert window.action_wrap_fixed.isChecked()


def test_prompt_wrap_fixed_column_cancelled_keeps_previous_mode(
    window: MainWindow, monkeypatch
) -> None:
    window.set_wrap_mode(WRAP_WINDOW)
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (55, False)))

    window._prompt_wrap_fixed_column()

    assert window.current_editor().wrap_mode() == WRAP_WINDOW
    assert window.action_wrap_window.isChecked()
    assert not window.action_wrap_fixed.isChecked()


# ----------------------------------------------------------------------
# 折り返しモードの永続化（前回起動時の状態を復元、実機フィードバックで追加）
# ----------------------------------------------------------------------
def test_load_wrap_settings_defaults_when_nothing_saved() -> None:
    assert load_wrap_settings() == (DEFAULT_WRAP_MODE, DEFAULT_WRAP_COLUMN)


def test_set_wrap_mode_persists_across_new_windows(window: MainWindow, make_window) -> None:
    """あるウィンドウで変更した設定が、後から作る別ウィンドウにも引き継がれる
    （= アプリを再起動したのと同じ経路で読み込まれることの確認）。"""
    window.set_wrap_mode(WRAP_FIXED, column=37)

    other = make_window()

    assert other.current_editor().wrap_mode() == WRAP_FIXED
    assert other.current_editor().wrap_column() == 37


def test_set_wrap_mode_saved_value_survives_reload(window: MainWindow) -> None:
    window.set_wrap_mode(WRAP_WINDOW)

    assert load_wrap_settings() == (WRAP_WINDOW, DEFAULT_WRAP_COLUMN)


def test_prompt_wrap_fixed_column_also_persists(window: MainWindow, monkeypatch) -> None:
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (12, True)))

    window._prompt_wrap_fixed_column()

    assert load_wrap_settings() == (WRAP_FIXED, 12)
