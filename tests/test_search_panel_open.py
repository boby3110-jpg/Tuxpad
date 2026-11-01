"""検索パネルを開くとき・タブの並びが変わったときに、重い検索で固まらないこと.

背景（2026-10-20、実機）
------------------------
「Ctrl+F を押してすぐ Ctrl+V を押すと、検索欄ではなく元のタブの本文に
貼り付いてしまう」という報告。Ctrl+F は検索欄に残っていた**前回の語**で
全タブをその場で検索しており、ありふれた語だと 1 秒近く画面が止まって
いた（15.6 万行＋22 タブ・22 万件ヒットで 942ms を実測）。パネルを前面に
出す依頼は後から効くので、止まっている間に打った Ctrl+V は元のタブへ
届く。

そこで次のように変えた。ここではそれを見張る。

1. 開いたときは**検索しない**。前回の語は検索欄に残し、結果だけ捨てて
   「まだ検索していない」状態に戻す。Enter で検索できる。
   選択中の文字列を検索欄へ流し込むこともしない（同日、利用者の判断で
   廃止。選んでから Ctrl+F → Ctrl+V で検索欄へ貼る操作の邪魔になる）。
2. パネルが既に開いているときに Ctrl+F を押しても、今の結果は捨てない
   （開いている間の結果は本文の編集に追随しているので古くない）。
3. タブを開く・閉じる・並べ替えるときの検索し直しは、**前回の検索が
   重かったら行わず**、結果を捨てる（残すと別のタブを指してしまう）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from editor_app import search_replace
from editor_app.main_window import MainWindow
from editor_app.search_panel import PENDING_QUERY_NOTICE


def _tab_with(window: MainWindow, text: str):
    window.new_file()
    editor = window.current_editor()
    editor.setPlainText(text)
    return editor


def _count_searches(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = window.search_all_tabs

    def counting(query: str):
        calls.append(query)
        return real(query)

    monkeypatch.setattr(window, "search_all_tabs", counting)
    return calls


def _search(window: MainWindow, qtbot, query: str) -> None:
    window.search_panel._input.setPlainText(query)
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)


def _clear_selection(window: MainWindow) -> None:
    """選択を外す（検索で飛んだ先は選択状態になっているので）。"""
    for editor in window.editors():
        cursor = editor.textCursor()
        cursor.clearSelection()
        editor.setTextCursor(cursor)


def _select(editor, start: int, end: int) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def _highlight_count(window: MainWindow) -> int:
    return sum(len(editor.extraSelections()) for editor in window.editors())


# ----------------------------------------------------------------------
# 1・2. 開くとき
# ----------------------------------------------------------------------
def test_reopening_with_a_leftover_query_does_not_search(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window.close_search_panel()
    _clear_selection(window)
    calls = _count_searches(window, monkeypatch)

    window.show_search_panel()

    assert calls == [], "前回の語で、開いた瞬間に検索してしまっている"
    panel = window.search_panel
    assert panel.query() == "返信", "前回の語は検索欄に残す（Enter で使える）"
    assert panel.searched_query() is None
    assert panel.matches() == []
    assert panel._count_label.text() == PENDING_QUERY_NOTICE
    assert _highlight_count(window) == 0


def test_enter_after_reopening_searches_the_leftover_query(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window.close_search_panel()
    _clear_selection(window)
    window.show_search_panel()
    calls = _count_searches(window, monkeypatch)

    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    assert calls == ["返信"]
    assert len(window.search_panel.matches()) == 3
    assert window.search_panel._count_label.text() == "1/3 件"


def test_reopening_forgets_results_that_went_stale_while_closed(
    window: MainWindow, qtbot
) -> None:
    """閉じている間に本文が変わっていたら、前回の結果は別の場所を指す。

    検索し直さない代わりに、古い結果を**残さない**ことが大事（残すと
    Enter で見当違いの位置へ飛ぶ）。
    """
    editor = _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window.close_search_panel()
    _clear_selection(window)
    editor.setPlainText("前置きの文章 返信\n")

    window.show_search_panel()
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    match = window.search_panel.current_match()
    assert len(window.search_panel.matches()) == 1
    assert match.position == len("前置きの文章 ")


def test_opening_with_a_selection_neither_searches_nor_takes_the_selection(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """選択していても、検索欄はそのまま・検索もしない。"""
    editor = _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window.close_search_panel()
    _select(editor, 3, 5)
    calls = _count_searches(window, monkeypatch)

    window.show_search_panel()
    qtbot.wait(50)

    assert calls == []
    assert window.search_panel.query() == "返信"
    assert window.search_panel.searched_query() is None


def test_ctrl_f_while_the_panel_is_open_keeps_the_results(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """開いている間の結果は本文に追随しているので、捨てずに使い続ける。"""
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    _clear_selection(window)
    calls = _count_searches(window, monkeypatch)

    window.show_search_panel()

    assert calls == []
    assert len(window.search_panel.matches()) == 3
    assert window.search_panel.searched_query() == "返信"
    assert _highlight_count(window) > 0


def test_ctrl_h_with_a_leftover_query_does_not_search_either(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window.close_search_panel()
    _clear_selection(window)
    calls = _count_searches(window, monkeypatch)

    window.show_replace_panel()

    assert calls == []
    assert window.search_panel.searched_query() is None


def test_replace_all_after_reopening_searches_first(
    window: MainWindow, qtbot
) -> None:
    """検索し直さずに開いても、全置換はその場で検索してから行う。"""
    editor = _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window.close_search_panel()
    _clear_selection(window)
    window.show_replace_panel()
    window.search_panel._replace_input.setPlainText("回答")

    window.search_panel.replace_all_requested.emit()

    assert editor.toPlainText() == "回答 回答 回答\n"


# ----------------------------------------------------------------------
# 3. タブの並びが変わったとき
# ----------------------------------------------------------------------
def test_tab_change_after_a_slow_search_does_not_search_again(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前回の検索が重かったら、タブを開いても検索し直さず、結果を捨てる。"""
    monkeypatch.setattr(search_replace, "SLOW_SEARCH_REFRESH_MS", -1)  # 何でも重い扱い
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    assert window._auto_refresh_on_edit is False
    calls = _count_searches(window, monkeypatch)

    _tab_with(window, "返信\n")

    assert calls == []
    panel = window.search_panel
    assert panel.matches() == [], "古い結果は別のタブを指すので残さない"
    assert panel.searched_query() is None
    assert panel._count_label.text() == PENDING_QUERY_NOTICE
    assert _highlight_count(window) == 0


def test_tab_change_after_a_slow_search_cancels_a_queued_refresh(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_replace, "SLOW_SEARCH_REFRESH_MS", -1)
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    window._search_refresh_timer.start()

    _tab_with(window, "返信\n")

    assert not window._search_refresh_timer.isActive()


def test_tab_change_after_a_fast_search_still_searches_again(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**対照**: 軽い検索なら今までどおり、その場で検索し直す。"""
    _tab_with(window, "返信 返信 返信\n")
    window.show_search_panel()
    _search(window, qtbot, "返信")
    assert window._auto_refresh_on_edit is True
    calls = _count_searches(window, monkeypatch)

    _tab_with(window, "返信\n")

    assert calls == ["返信"]
