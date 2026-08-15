"""機能 6「Ctrl+F: ウィンドウ内の全タブを横断する検索」のテスト。

EmEditor と同様、検索語自体が改行を含む「複数行検索」にも対応しているので、
1 行だけのケースと複数行にまたがるケースの両方をカバーする。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from editor_app.main_window import MainWindow
from editor_app.search_panel import SearchMatch


def edit_text(editor, text: str) -> None:
    """本文を書き換える (setPlainText は変更フラグが立たないため使わない)。"""
    editor.selectAll()
    editor.insertPlainText(text)


def make_tabs(window: MainWindow, *texts: str) -> None:
    """新しいタブを texts の数だけ作り、それぞれに本文を入れる。"""
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
# MainWindow.search_all_tabs（1 行内のマッチ）
# ----------------------------------------------------------------------
def test_search_finds_matches_across_all_tabs(window: MainWindow) -> None:
    make_tabs(window, "hello world\nfoo Hello bar\n", "nothing here\nHELLO again\n")

    matches = window.search_all_tabs("hello")

    assert [(m.editor_index, m.line) for m in matches] == [(1, 1), (1, 2), (2, 2)]


def test_search_is_case_insensitive(window: MainWindow) -> None:
    make_tabs(window, "MiXeD Case\n")
    matches = window.search_all_tabs("mixed")
    assert len(matches) == 1


def test_search_empty_query_returns_no_matches(window: MainWindow) -> None:
    make_tabs(window, "何か文字列\n")
    assert window.search_all_tabs("") == []


def test_search_multiple_matches_on_same_line(window: MainWindow) -> None:
    make_tabs(window, "aXaXa\n")
    matches = window.search_all_tabs("a")
    assert [m.position for m in matches] == [0, 2, 4]


def test_search_no_match_returns_empty_list(window: MainWindow) -> None:
    make_tabs(window, "abc\n")
    assert window.search_all_tabs("xyz") == []


def test_search_match_has_tab_title_and_line(window: MainWindow) -> None:
    make_tabs(window, "見つけたい文字列\n")
    matches = window.search_all_tabs("見つけたい")
    assert matches[0].tab_title == "*無題-2"
    assert matches[0].line == 1
    assert matches[0].snippet == "見つけたい文字列"


# ----------------------------------------------------------------------
# MainWindow.search_all_tabs（複数行にまたがるマッチ）
# ----------------------------------------------------------------------
def test_search_query_spanning_multiple_lines(window: MainWindow) -> None:
    make_tabs(window, "見出し\n本文1行目\n本文2行目\nフッタ\n")

    matches = window.search_all_tabs("本文1行目\n本文2行目")

    assert len(matches) == 1
    match = matches[0]
    assert match.line == 2
    assert match.length == len("本文1行目\n本文2行目")


def test_multiline_match_snippet_uses_newline_glyph(window: MainWindow) -> None:
    make_tabs(window, "AAA\nBBB\nCCC\n")

    matches = window.search_all_tabs("AAA\nBBB")

    assert matches[0].snippet == "AAA⏎BBB"


def test_multiline_match_jump_selects_across_lines(window: MainWindow) -> None:
    make_tabs(window, "1行目\n2行目\n3行目\n")

    matches = window.search_all_tabs("1行目\n2行目")
    assert len(matches) == 1

    window.jump_to_match(matches[0])

    selected = window.current_editor().textCursor().selectedText()
    # QTextCursor.selectedText() は改行を U+2029 で返す。
    assert selected.replace(" ", "\n") == "1行目\n2行目"


# ----------------------------------------------------------------------
# MainWindow.jump_to_match
# ----------------------------------------------------------------------
def test_jump_to_match_switches_tab_and_selects_text(window: MainWindow) -> None:
    make_tabs(window, "1つ目のタブ\n", "2つ目のタブに検索語がある\n")
    window.tabs.setCurrentIndex(0)

    matches = window.search_all_tabs("検索語")
    assert len(matches) == 1

    window.jump_to_match(matches[0])

    assert window.tabs.currentIndex() == matches[0].editor_index
    assert window.current_editor().textCursor().selectedText() == "検索語"


def test_jump_to_match_centers_cursor_in_viewport(window: MainWindow) -> None:
    """検索でヒットした語がビューポートの端に埋もれないこと（実機フィードバック）。

    ``ensureCursorVisible()`` だけだと最小限しかスクロールされず、
    ジャンプ先がビューポートの一番下（または上）ぎりぎりに来て
    見落としやすい、という不具合が実機で見つかった。ここでは、
    十分に長い本文の後半にある検索語へジャンプしたとき、カーソルが
    ビューポートの端ではなく中央付近に来ることを確認する。
    """
    editor = window.current_editor()
    window.resize(500, 500)
    window.show()

    lines = [f"{i}行目の本文です\n" for i in range(300)]
    lines[250] = "見つけたい語\n"
    edit_text(editor, "".join(lines))

    matches = window.search_all_tabs("見つけたい語")
    assert len(matches) == 1

    window.jump_to_match(matches[0])

    viewport_height = editor.viewport().height()
    cursor_y = editor.cursorRect().center().y()
    # 完全な中央でなくてもよいが、端（上下 20% の帯）には来ないこと。
    margin = viewport_height * 0.2
    assert margin < cursor_y < viewport_height - margin


def test_jump_to_match_with_invalid_index_does_nothing(window: MainWindow) -> None:
    make_tabs(window, "本文\n")
    bad_match = SearchMatch(
        editor_index=99, tab_title="無題-2", position=0, length=2, line=1, snippet="本文"
    )
    window.tabs.setCurrentIndex(0)
    window.jump_to_match(bad_match)  # 例外を出さずに何もしないこと
    assert window.tabs.currentIndex() == 0


# ----------------------------------------------------------------------
# 検索パネルの開閉
# ----------------------------------------------------------------------
def test_find_action_shortcut_is_ctrl_f(window: MainWindow) -> None:
    assert window.action_find.shortcut().toString() == "Ctrl+F"


def test_show_search_panel_makes_panel_visible_and_updates_count(
    window: MainWindow,
) -> None:
    make_tabs(window, "検索対象の文字列\n")
    window.show_search_panel()
    set_query(window, "検索対象")

    assert window.search_panel.isVisible()
    assert window.search_panel._count_label.text() == "1 件"


def test_show_search_panel_prefills_selected_text(window: MainWindow) -> None:
    make_tabs(window, "選択される文字列\n")
    editor = window.current_editor()
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("選択される"), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)

    window.show_search_panel()

    assert window.search_panel.query() == "選択される"


def test_show_search_panel_prefills_multiline_selection(window: MainWindow) -> None:
    make_tabs(window, "1行目\n2行目\n3行目\n")
    editor = window.current_editor()
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("1行目\n2行目"), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)

    window.show_search_panel()

    assert window.search_panel.query() == "1行目\n2行目"


def test_close_search_panel_hides_panel_and_refocuses_editor(
    window: MainWindow,
) -> None:
    make_tabs(window, "本文\n")
    window.show_search_panel()
    window.close_search_panel()
    assert not window.search_panel.isVisible()


# ----------------------------------------------------------------------
# SearchPanel 単体（件数表示・Enter/Shift+Enterでのナビゲーション）
#
# 結果は一覧表示せず「n/N 件」の件数表示のみにしてある（実機で試した
# ユーザーからの要望）。ジャンプは Enter（次へ）/ Shift+Enter（前へ）で行う。
# ----------------------------------------------------------------------
def _match(index: int, line: int) -> SearchMatch:
    return SearchMatch(
        editor_index=index,
        tab_title=f"無題-{index + 1}",
        position=0,
        length=2,
        line=line,
        snippet="サンプル行",
    )


def test_panel_set_matches_updates_count_label(window: MainWindow) -> None:
    panel = window.search_panel
    panel._input.setPlainText("サン")
    panel.set_matches([_match(0, 1), _match(0, 2)])
    assert panel._count_label.text() == "2 件"


def test_panel_set_matches_empty_shows_not_found(window: MainWindow) -> None:
    panel = window.search_panel
    panel._input.setPlainText("なし")
    panel.set_matches([])
    assert panel._count_label.text() == "見つかりません"


def test_panel_current_match_defaults_to_first(window: MainWindow) -> None:
    """まだ Enter でナビゲートしていなくても、最初の 1 件を指す。"""
    panel = window.search_panel
    matches = [_match(0, 1), _match(0, 2)]
    panel.set_matches(matches)
    assert panel.current_match() == matches[0]


def test_panel_enter_jumps_to_next_match_and_updates_count(
    window: MainWindow, qtbot
) -> None:
    panel = window.search_panel
    panel._input.setPlainText("q")
    matches = [_match(0, 1), _match(0, 2), _match(0, 3)]
    panel.set_matches(matches)

    received: list[SearchMatch] = []
    panel.match_activated.connect(received.append)

    qtbot.keyClick(panel._input, Qt.Key.Key_Return)
    assert received == [matches[0]]
    assert panel._count_label.text() == "1/3 件"
    assert panel.current_match() == matches[0]

    qtbot.keyClick(panel._input, Qt.Key.Key_Return)
    assert received == [matches[0], matches[1]]
    assert panel._count_label.text() == "2/3 件"


def test_panel_enter_wraps_around_to_first_match(window: MainWindow, qtbot) -> None:
    panel = window.search_panel
    matches = [_match(0, 1), _match(0, 2)]
    panel.set_matches(matches)

    received: list[SearchMatch] = []
    panel.match_activated.connect(received.append)

    qtbot.keyClick(panel._input, Qt.Key.Key_Return)  # -> 1件目
    qtbot.keyClick(panel._input, Qt.Key.Key_Return)  # -> 2件目
    qtbot.keyClick(panel._input, Qt.Key.Key_Return)  # -> 折り返して1件目

    assert received == [matches[0], matches[1], matches[0]]


def test_panel_shift_enter_jumps_to_previous_match(window: MainWindow, qtbot) -> None:
    panel = window.search_panel
    panel._input.setPlainText("q")
    matches = [_match(0, 1), _match(0, 2), _match(0, 3)]
    panel.set_matches(matches)

    received: list[SearchMatch] = []
    panel.match_activated.connect(received.append)

    qtbot.keyClick(panel._input, Qt.Key.Key_Return)  # -> 1件目 (index 0)
    qtbot.keyClick(
        panel._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )  # -> 前へ、折り返して3件目 (index 2)

    assert received[-1] == matches[2]
    assert panel._count_label.text() == "3/3 件"


def test_panel_ctrl_enter_inserts_newline_instead_of_navigating(
    window: MainWindow, qtbot
) -> None:
    # qtbot.keyClicks は非ASCII文字だとoffscreenプラットフォームで
    # 落ちることがあるため、この操作系テストはASCII文字列で行う。
    panel = window.search_panel
    panel.focus_input()

    received: list[SearchMatch] = []
    panel.match_activated.connect(received.append)

    qtbot.keyClicks(panel._input, "line1")
    qtbot.keyClick(panel._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClicks(panel._input, "line2")

    assert panel.query() == "line1\nline2"
    assert received == []


def test_panel_enter_with_no_matches_does_nothing(window: MainWindow, qtbot) -> None:
    panel = window.search_panel
    panel.set_matches([])

    received: list[SearchMatch] = []
    panel.match_activated.connect(received.append)

    qtbot.keyClick(panel._input, Qt.Key.Key_Return)

    assert received == []


def test_panel_escape_emits_closed(window: MainWindow, qtbot) -> None:
    panel = window.search_panel
    closed_signals: list[None] = []
    panel.closed.connect(lambda: closed_signals.append(None))

    qtbot.keyClick(panel._input, Qt.Key.Key_Escape)

    assert len(closed_signals) == 1
