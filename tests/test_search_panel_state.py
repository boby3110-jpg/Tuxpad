"""検索パネルが「今どういう状態か」を取り違えないことのテスト。

機能 6・7（Ctrl+F / Ctrl+H）の中身は 3 つに分かれている（`search_replace.py`
の冒頭を参照）。このファイルが見るのは、そのうち **パネル側が持っている状態**
——「最後に実際に検索した語」「今何件目を指しているか」「検索欄に何が
入っているか」——が、利用者の操作と食い違わないこと。

2026-08-18（35 回目）に、`search_replace.py`・`search_panel.py` をわざと
壊して確かめたところ（`tools/mutation_check.py`）、**壊しても全テストが緑の
まま**の箇所が 8 つ見つかった。ここはその 8 つを塞ぐために足したもので、
どのテストも「壊すと赤くなる」ことを確認してある。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from editor_app.main_window import MainWindow
from editor_app.search_panel import PENDING_QUERY_NOTICE
from editor_app.search_replace import SEARCH_REFRESH_DELAY_MS


def edit_text(editor, text: str) -> None:
    """本文を書き換える (setPlainText は変更フラグが立たないため使わない)。"""
    editor.selectAll()
    editor.insertPlainText(text)


def make_tabs(window: MainWindow, *texts: str) -> None:
    for text in texts:
        window.new_file()
        edit_text(window.current_editor(), text)


def append_text(editor, text: str) -> None:
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text)
    editor.setTextCursor(cursor)


def open_search(window: MainWindow, query: str) -> None:
    """検索パネルを開き、``query`` で実際に検索する。"""
    window.show_search_panel()
    window.search_panel._input.setPlainText(query)
    window.search_panel.run_search()


def type_query(window: MainWindow, query: str) -> None:
    """検索欄を打ち替えるだけ（検索はしない）。"""
    window.search_panel._input.setPlainText(query)


def clear_selection(editor) -> None:
    cursor = editor.textCursor()
    cursor.clearSelection()
    editor.setTextCursor(cursor)


def wait_for_refresh(qtbot) -> None:
    qtbot.wait(SEARCH_REFRESH_DELAY_MS + 150)


# ----------------------------------------------------------------------
# 1. 「最後に実際に検索した語」を、検索欄の内容で上書きしない
# ----------------------------------------------------------------------
def test_refresh_keeps_the_searched_word_separate_from_the_typed_one(
    window: MainWindow, qtbot
) -> None:
    """本文の編集で検索し直しても、打ちかけの語を「検索済み」にしない。

    「foo で検索 → bar と打ちかけ（まだ Enter を押していない）→ 本文を
    編集」という流れでは、追随の検索は **foo** で行われる。その結果を
    「bar の結果」として覚えてしまうと、**bar では 1 件も無いのに
    「3 件」と出る**——検索していない語の件数を、検索したかのように
    見せることになる。
    """
    make_tabs(window, "foo foo\n")
    editor = window.current_editor()
    open_search(window, "foo")
    type_query(window, "bar")
    assert window.search_panel._count_label.text() == PENDING_QUERY_NOTICE

    append_text(editor, "\nfoo")
    wait_for_refresh(qtbot)

    # 追随の検索は foo で行われたので、bar は「まだ検索していない」まま。
    assert len(window.search_panel.matches()) == 3
    assert window.search_panel.searched_query() == "foo"
    assert window.search_panel.is_query_pending()
    assert window.search_panel._count_label.text() == PENDING_QUERY_NOTICE


# ----------------------------------------------------------------------
# 2. 件数欄は「まだ検索していない」を「見つかりません」より先に伝える
# ----------------------------------------------------------------------
def test_typing_after_a_fruitless_search_says_not_searched_yet(
    window: MainWindow,
) -> None:
    """0 件だった語のあとに打ち替えても、「見つかりません」とは言わない。

    直前の検索が 0 件だと ``_matches`` は空のままなので、判定の順番を
    取り違えると **本文にちゃんとある語を打っている最中に「見つかりません」**
    と出てしまう（打ち間違えたのかと迷わせる）。
    """
    make_tabs(window, "foo foo\n")
    open_search(window, "zzz")
    assert window.search_panel._count_label.text() == "見つかりません"

    type_query(window, "foo")

    assert window.search_panel._count_label.text() == PENDING_QUERY_NOTICE


# ----------------------------------------------------------------------
# 3. パネルを開き直しても、前の検索語を消さない
# ----------------------------------------------------------------------
def test_reopening_without_a_selection_keeps_the_previous_query(
    window: MainWindow,
) -> None:
    """本文で何も選ばずに Ctrl+F すると、前回の検索語がそのまま残る。

    選択中の文字列を検索欄へ流し込むのは「選択がある」ときだけ。選択が
    無いのに流し込むと **空文字で検索欄が上書きされ、前回の検索語が消える**。
    """
    make_tabs(window, "foo foo\n")
    open_search(window, "foo")
    window.close_search_panel()
    clear_selection(window.current_editor())

    window.show_search_panel()

    assert window.search_panel.query() == "foo"


def test_reopening_with_a_selection_replaces_the_query(window: MainWindow) -> None:
    """選択があるときは、これまでどおりその文字列で検索欄を置き換える。"""
    make_tabs(window, "alpha bravo\n")
    open_search(window, "alpha")
    window.close_search_panel()

    editor = window.current_editor()
    cursor = editor.textCursor()
    cursor.setPosition(len("alpha "))
    cursor.setPosition(len("alpha bravo"), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    window.show_search_panel()

    assert window.search_panel.query() == "bravo"


def test_multiline_selection_goes_into_the_search_box_as_newlines(
    window: MainWindow,
) -> None:
    """複数行を選んで Ctrl+F すると、その複数行がそのまま検索語になる。

    ``QTextCursor.selectedText()`` は改行を U+2029（段落区切り）で返すため、
    :meth:`SearchPanel.set_initial_text` はこれを本文と同じ ``\\n`` に直して
    から検索欄へ入れている。**この置換自体は二重の備え**で、外しても
    ``QPlainTextEdit.setPlainText()`` が U+2029 を段落の切れ目として取り込む
    ので結果は変わらない（35 回目に確認）。ここで固定しているのは中の
    やり方ではなく、**選んだ範囲がそのまま検索語として通ること**。
    """
    make_tabs(window, "alpha\nbravo\ncharlie\n")
    editor = window.current_editor()
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("alpha\nbravo"), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)

    window.show_search_panel()

    assert window.search_panel.query() == "alpha\nbravo"
    assert len(window.search_panel.matches()) == 1


# ----------------------------------------------------------------------
# 4. 検索欄にフォーカスを移すとき、既にある語を選択しておく
# ----------------------------------------------------------------------
def test_focus_input_selects_the_existing_query(window: MainWindow) -> None:
    """開き直してそのまま打てば、前の検索語を置き換えられる。"""
    window.show_search_panel()
    type_query(window, "foo")

    window.search_panel.focus_input()

    assert window.search_panel._input.textCursor().selectedText() == "foo"


def test_focus_input_can_keep_the_existing_query_unselected(
    window: MainWindow,
) -> None:
    """``select_all=False`` のときは選択しない（続きから打てる）。"""
    window.show_search_panel()
    type_query(window, "foo")

    window.search_panel.focus_input(select_all=False)

    assert window.search_panel._input.textCursor().selectedText() == ""


# ----------------------------------------------------------------------
# 5. 置換欄でも Esc でパネルを閉じられる
# ----------------------------------------------------------------------
def test_escape_in_the_replace_box_closes_the_panel(
    window: MainWindow, qtbot
) -> None:
    """Ctrl+H のあと置換欄にカーソルがあっても、Esc で閉じられる。

    検索欄側は ``test_panel_escape_emits_closed`` が見ているが、置換欄は
    別のウィジェットなので、つなぎ忘れても検索欄のテストは緑のまま通る。
    """
    make_tabs(window, "foo\n")
    window.show_replace_panel()
    window.search_panel._replace_input.setFocus()

    closed_signals: list[None] = []
    window.search_panel.closed.connect(lambda: closed_signals.append(None))
    qtbot.keyClick(window.search_panel._replace_input, Qt.Key.Key_Escape)

    assert len(closed_signals) == 1
    assert not window.search_panel.isVisible()


# ----------------------------------------------------------------------
# 6. 全置換の件数は「実際に置換できた件数」
# ----------------------------------------------------------------------
def test_replace_all_counts_only_the_ones_actually_replaced(
    window: MainWindow, monkeypatch
) -> None:
    """置換を見送った件を「置換しました」に数えない。

    全置換は 1 件ごとに、その位置に本当に検索語があるかを確かめてから
    書き換える（`_apply_replacement` の ``expected`` 照合）。見送った件まで
    数えると、**本文は 1 文字も変わっていないのに「3 件を置換しました」**
    と出て、利用者は置換が済んだものとして先へ進んでしまう。
    """
    make_tabs(window, "foo foo foo\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("foo")
    window.search_panel._replace_input.setPlainText("bar")
    window.search_panel.run_search()
    assert len(window.search_panel.matches()) == 3

    monkeypatch.setattr(window, "_apply_replacement", lambda *a, **k: False)
    window._replace_all_matches()

    assert window.search_panel._count_label.text() == "0 件を置換しました"
    assert "foo foo foo" in window.current_editor().toPlainText()


# ----------------------------------------------------------------------
# 7. パネルを閉じたら、予約済みの検索し直しも取り消す
# ----------------------------------------------------------------------
def test_closing_the_panel_cancels_a_queued_refresh(
    window: MainWindow, qtbot
) -> None:
    """本文を打った直後に閉じても、あとから検索し直しが走らない。

    本文の編集による検索し直しは 200ms 待ってから走る。その待ち時間の
    最中にパネルを閉じると、**閉じたあとに全タブの走査が始まる**予約が
    残ったままになる。走ってしまっても `_refresh_search_results` の側で
    「パネルが見えていなければ何もしない」と受け止めてはいるが、
    **受け止める側だけに頼らない**（26 回目に、閉じたウィンドウが後から
    「再読み込みしますか？」と訊いてくる不具合が実際に出ている）。
    """
    make_tabs(window, "foo\n")
    editor = window.current_editor()
    open_search(window, "foo")

    append_text(editor, "\nfoo")
    assert window._search_refresh_timer.isActive()  # 予約された

    window.close_search_panel()

    assert not window._search_refresh_timer.isActive()  # 取り消された
    qtbot.wait(SEARCH_REFRESH_DELAY_MS + 150)
    assert len(window.search_panel.matches()) == 1  # 閉じた時点のまま
