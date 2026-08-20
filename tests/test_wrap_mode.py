"""折り返しモード（なし/ウィンドウ幅/指定文字数）のテスト。実機フィードバックで追加。"""

from __future__ import annotations

import pytest
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


def test_switching_wrap_mode_without_a_column_keeps_the_chosen_column(
    window: MainWindow,
) -> None:
    """文字数を指定せずにモードだけ変えても、決めてある文字数は消さない。

    「40 文字で折り返す」に設定 → 「折り返さない」へ切り替え → もう一度
    「指定文字数で折り返す」に戻す、という普通の操作で 40 が残ること。
    `set_wrap_mode()` の `if column is not None:` がこれを守っているが、
    34 回目まで、この行を外して**文字数を None で上書きしても全テストが
    緑のまま**だった（変異 `vs-wrap-column-guard`）。
    """
    window.set_wrap_mode(WRAP_FIXED, column=40)

    window.set_wrap_mode(WRAP_NONE)  # 文字数は指定しない

    assert window._wrap_column == 40
    assert load_wrap_settings() == (WRAP_NONE, 40)

    window.set_wrap_mode(WRAP_FIXED)  # 戻したときも 40 のまま
    assert load_wrap_settings() == (WRAP_FIXED, 40)


def test_prompt_wrap_fixed_column_also_persists(window: MainWindow, monkeypatch) -> None:
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (12, True)))

    window._prompt_wrap_fixed_column()

    assert load_wrap_settings() == (WRAP_FIXED, 12)


def test_unknown_wrap_mode_is_refused_instead_of_ignored(window: MainWindow) -> None:
    """知らない折り返しモードは、黙って素通りさせずその場で失敗させる。

    素通りさせると「設定したのに何も変わらない」だけの症状になり、
    どこで取り違えたのかを追えなくなる（設定ファイルから読んだ値の
    受け止めは ``settings.load_wrap_settings()`` 側で既定へ倒している
    ので、ここまで来る時点で呼び出し側の取り違え）。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_WINDOW)

    with pytest.raises(ValueError):
        editor.set_wrap_mode("うずまき")

    # 画面の折り返しは、失敗する前のまま動かない。
    assert editor.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth
