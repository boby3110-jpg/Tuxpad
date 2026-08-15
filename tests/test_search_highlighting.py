"""検索・置換でヒットした箇所を本文中にハイライトする機能のテスト。

実機フィードバックで追加。``QTextEdit.ExtraSelection`` を使うので本文自体は
変わらない。ハイライトの色までは検証せず、「対象範囲が正しく選ばれているか」
「現在指している箇所とそれ以外が分けられているか」を中心に確認する。
"""

from __future__ import annotations

from editor_app.main_window import MainWindow


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
    （Enter か「検索」ボタンに相当する明示的な実行が必要）。テストでは
    ``run_search()`` を直接呼んで、その「明示的な実行」だけを行う
    （Enter と違ってマッチへのジャンプはしない）。
    """
    window.search_panel._input.setPlainText(query)
    window.search_panel.run_search()


def test_search_highlights_all_matches_in_current_tab(window: MainWindow) -> None:
    make_tabs(window, "aXaXa\n")
    window.show_search_panel()
    set_query(window, "a")

    editor = window.current_editor()
    selections = editor.extraSelections()

    assert len(selections) == 3
    positions = sorted(sel.cursor.selectionStart() for sel in selections)
    assert positions == [0, 2, 4]


def test_search_highlights_span_across_tabs(window: MainWindow) -> None:
    make_tabs(window, "foo\n", "foo again\n")
    window.show_search_panel()
    set_query(window, "foo")

    editors = window.editors()
    assert len(editors[1].extraSelections()) == 1
    assert len(editors[2].extraSelections()) == 1


def test_navigating_marks_current_match_distinctly(window: MainWindow, qtbot) -> None:
    from PySide6.QtCore import Qt

    make_tabs(window, "aXaXa\n")
    window.show_search_panel()
    set_query(window, "a")

    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)  # 1件目へ

    editor = window.current_editor()
    selections = editor.extraSelections()
    current_formats = {sel.format.background().color().name() for sel in selections}
    # 「現在」と「それ以外」で異なる背景色が使われている（2色以上ある）こと。
    assert len(current_formats) == 2


def test_close_search_panel_clears_highlights(window: MainWindow) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    set_query(window, "foo")
    assert window.current_editor().extraSelections()

    window.close_search_panel()

    assert window.current_editor().extraSelections() == []


def test_empty_query_clears_highlights(window: MainWindow) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    set_query(window, "foo")
    assert window.current_editor().extraSelections()

    set_query(window, "")

    assert window.current_editor().extraSelections() == []


def test_replace_all_highlights_the_replacement_text(window: MainWindow) -> None:
    """全置換の後は、検索語ではなく置換後の文字列がハイライトされる。"""
    make_tabs(window, "foo bar foo\n")
    window.show_replace_panel()
    set_query(window, "foo")
    window.search_panel._replace_input.setPlainText("BAZ")

    window._replace_all_matches()

    editor = window.current_editor()
    selections = editor.extraSelections()
    assert len(selections) == 2
    texts = {editor.toPlainText()[s.cursor.selectionStart():s.cursor.selectionEnd()] for s in selections}
    assert texts == {"BAZ"}
