"""Ctrl+Tab / Ctrl+Shift+Tab でウィンドウ内のタブを切り替えるテスト.

実機フィードバックによる追加機能。Tab キーは Qt のフォーカス移動に
先取りされやすいので、「メソッドを呼べば動く」だけでなく
**実際にキーを押して発火すること**まで確認する
（過去に Ctrl+H / Ctrl+W で同じ問題が起きた。PROGRESS.md 参照）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def add_tabs(window, count: int) -> None:
    while window.tabs.count() < count:
        window.new_file()


# ----------------------------------------------------------------------
# メソッド単体
# ----------------------------------------------------------------------


def test_activate_next_tab(window):
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(0)

    window.activate_next_tab()
    assert window.tabs.currentIndex() == 1

    window.activate_next_tab()
    assert window.tabs.currentIndex() == 2


def test_activate_next_tab_wraps_to_first(window):
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(2)

    window.activate_next_tab()
    assert window.tabs.currentIndex() == 0


def test_activate_previous_tab(window):
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(2)

    window.activate_previous_tab()
    assert window.tabs.currentIndex() == 1


def test_activate_previous_tab_wraps_to_last(window):
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(0)

    window.activate_previous_tab()
    assert window.tabs.currentIndex() == 2


def test_single_tab_does_not_raise(window):
    assert window.tabs.count() == 1

    window.activate_next_tab()
    window.activate_previous_tab()

    assert window.tabs.currentIndex() == 0


def test_switching_moves_focus_to_editor(window):
    add_tabs(window, 2)
    window.tabs.setCurrentIndex(0)

    window.activate_next_tab()

    assert window.current_editor().hasFocus() or not window.isActiveWindow()


# ----------------------------------------------------------------------
# 実際のキー入力（エディタにフォーカスがある状態で押す）
# ----------------------------------------------------------------------


def test_ctrl_tab_key_switches_tab(window, qtbot):
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(0)
    window.current_editor().setFocus()

    QTest.keyClick(window.current_editor(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)

    assert window.tabs.currentIndex() == 1


def test_ctrl_tab_key_wraps(window, qtbot):
    add_tabs(window, 2)
    window.tabs.setCurrentIndex(1)
    window.current_editor().setFocus()

    QTest.keyClick(window.current_editor(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)

    assert window.tabs.currentIndex() == 0


def test_ctrl_shift_tab_key_switches_backwards(window, qtbot):
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(2)
    window.current_editor().setFocus()

    QTest.keyClick(
        window.current_editor(),
        Qt.Key.Key_Backtab,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )

    assert window.tabs.currentIndex() == 1


def test_ctrl_tab_does_not_insert_tab_character(window, qtbot):
    """Ctrl+Tab を押しても本文にタブ文字が入らない。"""
    add_tabs(window, 2)
    window.tabs.setCurrentIndex(0)
    editor = window.current_editor()
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)

    assert editor.toPlainText() == ""


def test_plain_tab_still_inserts_tab_character(window, qtbot):
    """修飾キーなしの Tab は今まで通り本文に入る（横取りしていない）。"""
    editor = window.current_editor()
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key.Key_Tab)

    assert "\t" in editor.toPlainText()


def test_ctrl_tab_is_per_window(make_window, qtbot):
    """別ウィンドウのタブには影響しない。"""
    first = make_window()
    add_tabs(first, 3)
    second = make_window()
    add_tabs(second, 3)
    first.tabs.setCurrentIndex(0)
    second.tabs.setCurrentIndex(0)

    # ショートカットは「アクティブなウィンドウ」を対象に解決されるので、
    # キーを送る前に 1 つ目のウィンドウをアクティブに戻しておく。
    # activateWindow() は「アクティブにしてほしい」とプラットフォームへ
    # 頼むだけなので、processEvents() でその通知を受け取るまで
    # 実際には切り替わらない（どちらが欠けても効かないことは実測済み）。
    first.activateWindow()
    QApplication.processEvents()
    first.current_editor().setFocus()
    QTest.keyClick(first.current_editor(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)

    assert first.tabs.currentIndex() == 1
    assert second.tabs.currentIndex() == 0


def test_ctrl_shift_tab_as_plain_tab_key_also_works(window, qtbot):
    """環境によっては Shift+Tab が Key_Tab のまま届く。その場合も動く。"""
    add_tabs(window, 3)
    window.tabs.setCurrentIndex(0)
    window.current_editor().setFocus()

    QTest.keyClick(
        window.current_editor(),
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )

    assert window.tabs.currentIndex() == 2


# ----------------------------------------------------------------------
# アプリのショートカットに割り当てたキーを、本文の編集操作に食われない
# （EditorWidget.event の ShortcutOverride 処理）
#
# X11/KDE には「Ctrl+H = 1 文字削除」のような Emacs 風の編集キー
# バインドがあり、QTextEdit はこれを ShortcutOverride で自分の編集操作
# として先取りする。譲らないと、**本文にフォーカスがある間だけ**
# Ctrl+H（置換）や Ctrl+F（検索）が一切効かなくなる（実機で実際に
# 起きた。PROGRESS.md 参照）。
# ----------------------------------------------------------------------


def test_ctrl_h_opens_replace_panel_from_editor(window, qtbot):
    """本文にフォーカスがあっても Ctrl+H で置換が開く。"""
    editor = window.current_editor()
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)

    assert window.search_panel.isVisible()
    assert window.search_panel.is_replace_mode()


def test_ctrl_h_does_not_edit_the_text(window, qtbot):
    """Ctrl+H が「1 文字削除」として本文に効いてしまわない。"""
    editor = window.current_editor()
    editor.setPlainText("あいうえお")
    cursor = editor.textCursor()
    cursor.setPosition(5)  # 末尾
    editor.setTextCursor(cursor)
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)

    assert editor.toPlainText() == "あいうえお"


def test_reserved_shortcut_is_not_accepted_by_the_editor(window, qtbot):
    """予約キーの ShortcutOverride を、エディタが受理せず譲ること。

    ShortcutOverride は「このキーは自分が編集操作として使うので、
    ショートカットは発火させないでほしい」という申し出で、**受理
    (accept) すると申し出が通る**。予約キーでは受理しないのが正しい。
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    editor = window.current_editor()
    for key in (Qt.Key.Key_H, Qt.Key.Key_F, Qt.Key.Key_W, Qt.Key.Key_S):
        event = QKeyEvent(
            QEvent.Type.ShortcutOverride,
            key,
            Qt.KeyboardModifier.ControlModifier,
        )
        event.accept()  # 実機では受理済みの状態で届くことがある
        editor.event(event)
        assert not event.isAccepted(), f"Ctrl+{key} をエディタが横取りしている"
