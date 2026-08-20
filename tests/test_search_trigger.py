"""検索を「入力のたび」ではなく「明示的な操作」で実行することのテスト。

実機フィードバック（2026-08-05）: ヒット件数が多いと、1 文字打つたびに
全タブ検索 + ハイライト再構築が走って動作が重くなる。そのため検索欄への
入力では検索せず、Enter か「検索」ボタンで初めて検索するようにした。

あわせて、ヒットが極端に多いときにハイライトだけを間引く上限
(``MAX_HIGHLIGHTED_MATCHES``) も確認する。**間引くのはハイライトだけで、
件数表示・ジャンプ・置換は全件が対象のまま**であることが重要。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from editor_app.main_window import MainWindow
from editor_app.search_replace import MAX_HIGHLIGHTED_MATCHES
from editor_app.search_panel import PENDING_QUERY_NOTICE


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def make_tabs(window: MainWindow, *texts: str) -> None:
    for text in texts:
        window.new_file()
        edit_text(window.current_editor(), text)


def count_searches(window: MainWindow, monkeypatch) -> list[str]:
    """``search_all_tabs()`` が呼ばれた検索語を記録するリストを返す。"""
    calls: list[str] = []
    real = window.search_all_tabs

    def counting(query: str):
        calls.append(query)
        return real(query)

    monkeypatch.setattr(window, "search_all_tabs", counting)
    return calls


def type_query(window: MainWindow, query: str) -> None:
    """検索欄に文字を入れる（検索は実行しない）。"""
    window.search_panel._input.setPlainText(query)


# ----------------------------------------------------------------------
# (a) 入力しただけでは検索されない
# ----------------------------------------------------------------------
def test_typing_does_not_run_a_search(window: MainWindow, monkeypatch) -> None:
    make_tabs(window, "foo foo foo\n")
    window.show_search_panel()
    calls = count_searches(window, monkeypatch)

    type_query(window, "f")
    type_query(window, "fo")
    type_query(window, "foo")

    assert calls == []
    assert window.search_panel.matches() == []


def test_typing_does_not_build_highlights(window: MainWindow) -> None:
    make_tabs(window, "foo foo foo\n")
    window.show_search_panel()

    type_query(window, "foo")

    assert window.current_editor().extraSelections() == []


def test_typing_shows_a_notice_instead_of_a_stale_count(window: MainWindow) -> None:
    """打ち替えている間、古い件数を残さず「まだ検索していない」と伝える。"""
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    type_query(window, "foo")
    window.search_panel.run_search()
    assert window.search_panel._count_label.text() == "2 件"

    type_query(window, "fo")

    assert window.search_panel._count_label.text() == PENDING_QUERY_NOTICE


def test_editing_the_text_does_not_search_an_untriggered_query(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    """本文編集への追随も、打ちかけの語ではなく検索済みの語で行う。"""
    make_tabs(window, "foo bar\n")
    window.show_search_panel()
    type_query(window, "foo")
    window.search_panel.run_search()

    calls = count_searches(window, monkeypatch)
    type_query(window, "bar")  # まだ検索していない語
    window.current_editor().insertPlainText("X")
    qtbot.wait(400)

    assert calls == ["foo"]


def test_refresh_does_not_search_before_any_query_was_run(
    window: MainWindow, monkeypatch
) -> None:
    """一度も検索していないうちに再検索の合図（タブの増減など）が来ても、
    ``searched_query()`` が ``None`` のうちは全タブ検索を走らせない。

    再検索は「最後に実際に検索した語」で行うため、まだ何も検索していない
    段階では走らせる語が無い。ここを踏み越えると ``None`` を検索語として
    全タブ検索に渡してしまう（``_refresh_search_results`` の防御分岐）。
    パネルは開いているが検索前、という状態を作るために、通常の
    ``show_search_panel()``（開いた時点で 1 回検索する）ではなく、パネルを
    直接 ``show()`` して分岐そのものを呼ぶ。
    """
    make_tabs(window, "foo bar\n")
    window.search_panel.show()
    assert window.search_panel.isVisible()
    assert window.search_panel.searched_query() is None

    calls = count_searches(window, monkeypatch)
    window._refresh_search_results()

    assert calls == []


# ----------------------------------------------------------------------
# (b) Enter / 「検索」ボタンで初めて検索される
# ----------------------------------------------------------------------
def test_enter_runs_the_search(window: MainWindow, qtbot, monkeypatch) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    calls = count_searches(window, monkeypatch)

    type_query(window, "foo")
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    assert calls == ["foo"]
    assert len(window.search_panel.matches()) == 2


def test_enter_jumps_to_the_first_match_after_searching(
    window: MainWindow, qtbot
) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    type_query(window, "foo")

    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    assert window.search_panel._count_label.text() == "1/2 件"
    assert window.current_editor().textCursor().selectedText() == "foo"


def test_search_button_runs_the_search(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    calls = count_searches(window, monkeypatch)

    type_query(window, "foo")
    window.search_panel._search_button.click()

    assert calls == ["foo"]
    assert window.search_panel._count_label.text() == "1/2 件"


def test_shift_enter_runs_the_search_too(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    calls = count_searches(window, monkeypatch)

    type_query(window, "foo")
    qtbot.keyClick(
        window.search_panel._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )

    assert calls == ["foo"]
    # 検索した直後はまだどのマッチも指していないので、「前へ」でも従来
    # どおり 1 件目から始まる（2 回目以降が本当に「前へ」になる）。
    assert window.search_panel._count_label.text() == "1/2 件"


def test_search_builds_highlights(window: MainWindow, qtbot) -> None:
    make_tabs(window, "foo foo\n")
    window.show_search_panel()
    type_query(window, "foo")

    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    assert len(window.current_editor().extraSelections()) == 2


# ----------------------------------------------------------------------
# (c)(d) 同じ語なら再検索しない / 語を変えたら再検索する
# ----------------------------------------------------------------------
def test_enter_with_the_same_query_only_navigates(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    make_tabs(window, "foo foo foo\n")
    window.show_search_panel()
    type_query(window, "foo")
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    calls = count_searches(window, monkeypatch)
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    assert calls == []
    assert window.search_panel._count_label.text() == "3/3 件"


def test_enter_after_changing_the_query_searches_again(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    make_tabs(window, "foo foobar\n")
    window.show_search_panel()
    type_query(window, "foo")
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    calls = count_searches(window, monkeypatch)
    type_query(window, "foobar")
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    assert calls == ["foobar"]
    assert len(window.search_panel.matches()) == 1


def test_searched_query_tracks_the_last_executed_search(window: MainWindow) -> None:
    window.show_search_panel()
    assert window.search_panel.searched_query() == ""  # 開いた時点で 1 回だけ検索

    type_query(window, "foo")
    assert window.search_panel.is_query_pending()

    window.search_panel.run_search()
    assert window.search_panel.searched_query() == "foo"
    assert not window.search_panel.is_query_pending()


# ----------------------------------------------------------------------
# 置換は「明示的な操作」として扱い、必要なら直前に検索する
# ----------------------------------------------------------------------
def test_replace_buttons_stay_usable_before_searching(window: MainWindow) -> None:
    make_tabs(window, "foo foo\n")
    window.show_replace_panel()

    type_query(window, "foo")

    assert window.search_panel._replace_button.isEnabled()
    assert window.search_panel._replace_all_button.isEnabled()


def test_replace_all_without_pressing_enter_still_works(window: MainWindow) -> None:
    make_tabs(window, "foo bar foo\n")
    window.show_replace_panel()
    type_query(window, "foo")
    window.search_panel._replace_input.setPlainText("BAZ")

    window._replace_all_matches()

    assert window.current_editor().toPlainText() == "BAZ bar BAZ\n"


def test_replace_one_without_pressing_enter_still_works(window: MainWindow) -> None:
    make_tabs(window, "foo bar foo\n")
    window.show_replace_panel()
    type_query(window, "foo")
    window.search_panel._replace_input.setPlainText("BAZ")

    window._replace_current_match()

    assert window.current_editor().toPlainText() == "BAZ bar foo\n"


# ----------------------------------------------------------------------
# (e) ハイライトの上限（件数・置換には影響しない）
# ----------------------------------------------------------------------
def _many_matches_text(count: int) -> str:
    return "x\n" * count


def test_highlights_are_capped_but_the_count_is_exact(window: MainWindow) -> None:
    total = MAX_HIGHLIGHTED_MATCHES + 25
    make_tabs(window, _many_matches_text(total))
    window.show_search_panel()
    type_query(window, "x")
    window.search_panel.run_search()

    editor = window.current_editor()
    assert len(window.search_panel.matches()) == total
    assert window.search_panel._count_label.text() == f"{total} 件"
    assert len(editor.extraSelections()) <= MAX_HIGHLIGHTED_MATCHES + 1


def test_capped_highlights_still_mark_the_current_match(
    window: MainWindow, qtbot
) -> None:
    """上限を超えていても、今指している 1 件は必ずハイライトされる。"""
    total = MAX_HIGHLIGHTED_MATCHES + 25
    make_tabs(window, _many_matches_text(total))
    window.show_search_panel()
    type_query(window, "x")
    qtbot.keyClick(window.search_panel._input, Qt.Key.Key_Return)

    selections = window.current_editor().extraSelections()
    colors = {sel.format.background().color().name() for sel in selections}
    # 「現在」と「それ以外」で 2 色使われている＝現在のマッチが光っている。
    assert len(colors) == 2


def test_replacing_all_is_not_limited_by_the_highlight_cap(
    window: MainWindow,
) -> None:
    total = MAX_HIGHLIGHTED_MATCHES + 25
    make_tabs(window, _many_matches_text(total))
    window.show_replace_panel()
    type_query(window, "x")
    window.search_panel._replace_input.setPlainText("y")

    window._replace_all_matches()

    assert window.current_editor().toPlainText() == "y\n" * total
    assert window.search_panel._count_label.text() == f"{total} 件を置換しました"
