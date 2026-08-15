"""機能 7「Ctrl+H: ウィンドウ内の全タブを横断する検索・置換」のテスト。

機能 6 (`tests/test_cross_tab_search.py`) の `search_all_tabs()` /
`SearchMatch` をそのまま流用しているので、複数行にまたがる置換も対象になる。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from editor_app.main_window import MainWindow
from editor_app.search_panel import SearchMatch


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def make_tabs(window: MainWindow, *texts: str) -> None:
    for text in texts:
        window.new_file()
        edit_text(window.current_editor(), text)


def set_query(window: MainWindow, query: str) -> None:
    """検索欄に検索語を入れて、実際に検索する。

    検索はインクリメンタルサーチではないので、入力しただけでは検索されない
    （Enter か「検索」ボタンに相当する明示的な実行が必要）。
    """
    window.search_panel._input.setPlainText(query)
    window.search_panel.run_search()


# ----------------------------------------------------------------------
# MainWindow._apply_replacement（1 件だけの低レベル置換）
# ----------------------------------------------------------------------
def test_apply_replacement_replaces_text_at_position(window: MainWindow) -> None:
    make_tabs(window, "foo bar foo\n")
    editor = window.current_editor()
    match = window.search_all_tabs("bar")[0]

    window._apply_replacement(match, "BAZ")

    assert editor.toPlainText() == "foo BAZ foo\n"
    assert editor.is_modified


def test_apply_replacement_with_invalid_editor_index_does_nothing(
    window: MainWindow,
) -> None:
    make_tabs(window, "本文\n")
    bad_match = SearchMatch(
        editor_index=99, tab_title="無題-2", position=0, length=2, line=1, snippet="本文"
    )
    window._apply_replacement(bad_match, "置換後")  # 例外を出さないこと


# ----------------------------------------------------------------------
# MainWindow._replace_current_match（検索パネルの「置換」）
# ----------------------------------------------------------------------
def test_replace_current_match_replaces_selected_result(window: MainWindow) -> None:
    make_tabs(window, "hello hello\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("hello")
    window.search_panel._replace_input.setPlainText("hi")

    window._replace_current_match()

    assert window.current_editor().toPlainText() == "hi hello\n"


def test_replace_current_match_refreshes_result_count(window: MainWindow) -> None:
    make_tabs(window, "aaa\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("aaa")
    window.search_panel._replace_input.setPlainText("bbb")

    window._replace_current_match()

    assert window.search_panel._count_label.text() == "見つかりません"


def test_replace_current_match_with_no_selection_does_nothing(
    window: MainWindow,
) -> None:
    make_tabs(window, "本文\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("見つからない語")
    window.search_panel._replace_input.setPlainText("なにか")

    window._replace_current_match()  # 例外を出さずに何もしないこと

    assert window.current_editor().toPlainText() == "本文\n"


# ----------------------------------------------------------------------
# MainWindow._replace_all_matches（「全置換」・確認ダイアログなし、件数表示あり）
# ----------------------------------------------------------------------
def test_replace_all_replaces_every_match_across_tabs(window: MainWindow) -> None:
    make_tabs(window, "foo bar foo\n", "another foo here\nsecond foo line\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("foo")
    window.search_panel._replace_input.setPlainText("BAZ")

    window._replace_all_matches()

    editors = window.editors()
    assert editors[1].toPlainText() == "BAZ bar BAZ\n"
    assert editors[2].toPlainText() == "another BAZ here\nsecond BAZ line\n"
    assert window.search_all_tabs("foo") == []


def test_replace_all_shows_replacement_count(window: MainWindow) -> None:
    make_tabs(window, "foo bar foo\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("foo")
    window.search_panel._replace_input.setPlainText("BAZ")

    window._replace_all_matches()

    assert window.search_panel._count_label.text() == "2 件を置換しました"


def test_replace_all_is_a_single_undo_step_per_tab(window: MainWindow) -> None:
    """1 タブ内で複数件置換しても、Ctrl+Z (undo) 1 回でまとめて戻せる。"""
    make_tabs(window, "foo bar foo baz foo\n")
    editor = window.current_editor()
    editor.document().setModified(False)
    window.show_replace_panel()
    window.search_panel._input.setPlainText("foo")
    window.search_panel._replace_input.setPlainText("BAZ")

    window._replace_all_matches()
    assert editor.toPlainText() == "BAZ bar BAZ baz BAZ\n"

    editor.undo()

    assert editor.toPlainText() == "foo bar foo baz foo\n"


def test_replace_all_handles_replacement_length_difference(window: MainWindow) -> None:
    """置換後の文字列が検索語より長くても短くても、後続マッチがずれずに置換される。"""
    make_tabs(window, "a-a-a-a\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("a")
    window.search_panel._replace_input.setPlainText("XYZ")

    window._replace_all_matches()

    assert window.current_editor().toPlainText() == "XYZ-XYZ-XYZ-XYZ\n"


def test_replace_all_with_no_matches_does_nothing(window: MainWindow) -> None:
    make_tabs(window, "本文\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("見つからない語")
    window.search_panel._replace_input.setPlainText("なにか")

    window._replace_all_matches()  # 例外を出さずに何もしないこと

    assert window.current_editor().toPlainText() == "本文\n"


def test_replace_all_multiline_match_with_multiline_replacement(
    window: MainWindow,
) -> None:
    make_tabs(window, "見出し\n1行目\n2行目\nフッタ\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("1行目\n2行目")
    window.search_panel._replace_input.setPlainText("差し替え後1\n差し替え後2")

    window._replace_all_matches()

    assert window.current_editor().toPlainText() == (
        "見出し\n差し替え後1\n差し替え後2\nフッタ\n"
    )


# ----------------------------------------------------------------------
# パネルの検索/置換モード切り替え
# ----------------------------------------------------------------------
def test_replace_action_shortcut_is_ctrl_h(window: MainWindow) -> None:
    assert window.action_replace.shortcut().toString() == "Ctrl+H"


def test_show_replace_panel_shows_replace_row(window: MainWindow) -> None:
    make_tabs(window, "本文\n")
    window.show_replace_panel()
    assert window.search_panel.is_replace_mode()
    assert window.search_panel._replace_input.isVisible()
    assert window.search_panel._replace_button.isVisible()


def test_show_search_panel_hides_replace_row(window: MainWindow) -> None:
    make_tabs(window, "本文\n")
    window.show_replace_panel()
    window.show_search_panel()
    assert not window.search_panel.is_replace_mode()
    assert not window.search_panel._replace_input.isVisible()
    assert not window.search_panel._replace_button.isVisible()


def test_replace_buttons_disabled_without_matches(window: MainWindow) -> None:
    make_tabs(window, "本文\n")
    window.show_replace_panel()
    set_query(window, "見つからない語")

    assert not window.search_panel._replace_button.isEnabled()
    assert not window.search_panel._replace_all_button.isEnabled()


def test_replace_buttons_enabled_with_matches(window: MainWindow) -> None:
    make_tabs(window, "本文\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("本文")

    assert window.search_panel._replace_button.isEnabled()
    assert window.search_panel._replace_all_button.isEnabled()


# ----------------------------------------------------------------------
# SearchPanel 単体（置換欄の操作）
# ----------------------------------------------------------------------
def test_panel_replace_input_enter_emits_replace_one_requested(
    window: MainWindow, qtbot
) -> None:
    panel = window.search_panel
    panel.set_replace_mode(True)

    received = []
    panel.replace_one_requested.connect(lambda: received.append(True))

    panel._replace_input.setFocus()
    qtbot.keyClick(panel._replace_input, Qt.Key.Key_Return)

    assert received == [True]


def test_panel_replace_input_shift_enter_inserts_newline(
    window: MainWindow, qtbot
) -> None:
    panel = window.search_panel
    panel.set_replace_mode(True)
    panel._replace_input.setFocus()

    received = []
    panel.replace_one_requested.connect(lambda: received.append(True))

    qtbot.keyClicks(panel._replace_input, "line1")
    qtbot.keyClick(
        panel._replace_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )
    qtbot.keyClicks(panel._replace_input, "line2")

    assert panel.replace_text() == "line1\nline2"
    assert received == []


def test_panel_replace_all_button_emits_signal(window: MainWindow, qtbot) -> None:
    panel = window.search_panel
    panel.set_matches(
        [SearchMatch(editor_index=0, tab_title="無題-1", position=0, length=1, line=1, snippet="a")]
    )

    received = []
    panel.replace_all_requested.connect(lambda: received.append(True))
    qtbot.mouseClick(panel._replace_all_button, Qt.MouseButton.LeftButton)

    assert received == [True]
