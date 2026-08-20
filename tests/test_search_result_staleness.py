"""検索結果が古くなったまま置換されないことのテスト。

機能 6・7（Ctrl+F / Ctrl+H）の検索結果 :class:`SearchMatch` は
「そのウィンドウの何番目のタブの、文書内の何文字目か」を持っている。
検索パネルはフローティングウィンドウで開きっぱなしにできるため、
検索したあとに

- 本文を編集する
- タブを閉じる・並べ替える・別のウィンドウへ移す

といった操作をすると、その位置は別の場所を指してしまう。その状態で
「置換」を押すと、本文の関係ない場所が静かに書き換わってしまう
（2026-08-07 に修正した「文字位置の数え方のずれ」と同じ種類の壊れ方）。

このテストは
1. 本文・タブの構成が変わったら検索し直すこと
2. それでも古い結果で置換しようとしたら、置換せずに見送ること
の 2 段構えを固定する。
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor

from editor_app.main_window import MainWindow
from editor_app.search_replace import (
    SEARCH_REFRESH_DELAY_MS,
    SLOW_SEARCH_REFRESH_MS,
)
from editor_app.search_panel import STALE_RESULT_NOTICE, SearchMatch


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def make_tabs(window: MainWindow, *texts: str) -> list:
    """本文入りのタブを作り、作ったエディタを順に返す。

    ``MainWindow`` は起動時に空の無題タブを 1 つ持っているので、
    ``window.editors()[0]`` はここで作ったタブではない点に注意
    （タブの並び順が絡むテストでは、必ずこの戻り値の方を使う）。
    """
    created = []
    for text in texts:
        window.new_file()
        editor = window.current_editor()
        edit_text(editor, text)
        created.append(editor)
    return created


def append_text(editor, text: str) -> None:
    """本文の末尾に 1 行追加する（EditorWidget は QTextEdit 派生）。"""
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text)
    editor.setTextCursor(cursor)


def open_replace_panel(window: MainWindow, query: str, replacement: str) -> None:
    window.show_replace_panel()
    window.search_panel._input.setPlainText(query)
    window.search_panel._replace_input.setPlainText(replacement)
    # 検索欄への入力では検索されないので、明示的に検索を実行する。
    window.search_panel.run_search()


def wait_for_refresh(qtbot) -> None:
    """本文編集後の検索し直し（少し待ってから実行される）を待つ。"""
    qtbot.wait(SEARCH_REFRESH_DELAY_MS + 150)


# ----------------------------------------------------------------------
# 1. 本文が編集されたら検索し直す
# ----------------------------------------------------------------------
def test_editing_text_refreshes_match_positions(window: MainWindow, qtbot) -> None:
    make_tabs(window, "あいうえお テスト かきくけこ")
    editor = window.current_editor()
    open_replace_panel(window, "テスト", "置換後")
    before = window.search_panel.matches()[0].position

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("XXXXX")
    wait_for_refresh(qtbot)

    after = window.search_panel.matches()[0].position
    assert after == before + len("XXXXX")


def test_replace_after_editing_replaces_the_right_place(
    window: MainWindow, qtbot
) -> None:
    """先頭に文字を打ってから「置換」を押しても、検索語の場所だけが変わる。"""
    make_tabs(window, "あいうえお テスト かきくけこ")
    editor = window.current_editor()
    open_replace_panel(window, "テスト", "置換後")

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("XXXXX")
    wait_for_refresh(qtbot)

    window._replace_current_match()

    assert editor.toPlainText() == "XXXXXあいうえお 置換後 かきくけこ"


def test_editing_text_refreshes_match_count(window: MainWindow, qtbot) -> None:
    make_tabs(window, "foo\n")
    editor = window.current_editor()
    open_replace_panel(window, "foo", "bar")
    assert len(window.search_panel.matches()) == 1

    append_text(editor, "\nfoo foo")
    wait_for_refresh(qtbot)

    assert len(window.search_panel.matches()) == 3
    assert window.search_panel._count_label.text() == "3 件"


def test_no_refresh_while_panel_is_closed(window: MainWindow, qtbot) -> None:
    """パネルを閉じている間は検索し直さない（無駄に全タブを走査しない）。"""
    make_tabs(window, "foo\n")
    editor = window.current_editor()
    open_replace_panel(window, "foo", "bar")
    window.close_search_panel()

    append_text(editor, "\nfoo foo")
    wait_for_refresh(qtbot)

    assert len(window.search_panel.matches()) == 1  # 閉じた時点のまま


# ----------------------------------------------------------------------
# 2. それでも古い結果だったら、置換せずに見送る
# ----------------------------------------------------------------------
def test_replace_before_refresh_does_not_corrupt_text(window: MainWindow) -> None:
    """検索し直しが走る前に「置換」を押しても、本文が壊れない。

    ``wait_for_refresh()`` を挟まないので、検索結果は編集前の古い位置の
    まま。この状態で置換すると、以前は本文の別の場所
    （``"あいうえお"`` の途中）が書き換わっていた。
    """
    make_tabs(window, "あいうえお テスト かきくけこ")
    editor = window.current_editor()
    open_replace_panel(window, "テスト", "置換後")

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("XXXXX")

    window._replace_current_match()

    assert editor.toPlainText() == "XXXXXあいうえお テスト かきくけこ"


def test_replace_before_refresh_tells_the_user(window: MainWindow) -> None:
    make_tabs(window, "あいうえお テスト かきくけこ")
    editor = window.current_editor()
    open_replace_panel(window, "テスト", "置換後")

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("XXXXX")

    window._replace_current_match()

    assert window.search_panel._count_label.text() == STALE_RESULT_NOTICE


def test_replace_before_refresh_leaves_usable_results(window: MainWindow) -> None:
    """見送ったあとは検索し直されているので、もう一度押せば正しく置換できる。"""
    make_tabs(window, "あいうえお テスト かきくけこ")
    editor = window.current_editor()
    open_replace_panel(window, "テスト", "置換後")

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("XXXXX")
    window._replace_current_match()  # 1 回目は見送られる

    window._replace_current_match()  # 2 回目

    assert editor.toPlainText() == "XXXXXあいうえお 置換後 かきくけこ"


def test_apply_replacement_skips_when_expected_text_is_gone(
    window: MainWindow,
) -> None:
    make_tabs(window, "foo bar foo\n")
    editor = window.current_editor()
    match = window.search_all_tabs("bar")[0]
    edit_text(editor, "まったく別の本文\n")

    replaced = window._apply_replacement(match, "BAZ", expected="bar")

    assert replaced is False
    assert editor.toPlainText() == "まったく別の本文\n"


def test_apply_replacement_still_replaces_when_expected_text_is_there(
    window: MainWindow,
) -> None:
    make_tabs(window, "foo bar foo\n")
    editor = window.current_editor()
    match = window.search_all_tabs("bar")[0]

    replaced = window._apply_replacement(match, "BAZ", expected="bar")

    assert replaced is True
    assert editor.toPlainText() == "foo BAZ foo\n"


def test_apply_replacement_expected_ignores_case_like_the_search(
    window: MainWindow,
) -> None:
    """検索は大小文字を区別しないので、確認も区別しない。"""
    make_tabs(window, "Hello world\n")
    editor = window.current_editor()
    match = window.search_all_tabs("hello")[0]

    replaced = window._apply_replacement(match, "やあ", expected="hello")

    assert replaced is True
    assert editor.toPlainText() == "やあ world\n"


def test_apply_replacement_expected_handles_multiline_query(
    window: MainWindow,
) -> None:
    """複数行にまたがる検索語でも、確認が誤って弾かないこと。"""
    make_tabs(window, "見出し\n1行目\n2行目\nフッタ\n")
    editor = window.current_editor()
    match = window.search_all_tabs("1行目\n2行目")[0]

    replaced = window._apply_replacement(match, "差し替え", expected="1行目\n2行目")

    assert replaced is True
    assert editor.toPlainText() == "見出し\n差し替え\nフッタ\n"


def test_apply_replacement_with_stale_editor_index_is_skipped(
    window: MainWindow,
) -> None:
    make_tabs(window, "本文\n")
    stale = SearchMatch(
        editor_index=99, tab_title="無題-2", position=0, length=2, line=1, snippet="本文"
    )

    assert window._apply_replacement(stale, "置換後", expected="本文") is False


def test_replace_all_skips_results_pointing_at_a_missing_tab(
    window: MainWindow, monkeypatch
) -> None:
    """全置換に「もう無いタブ」を指す結果が混じっても、残りは普通に置換する。

    全置換は :meth:`search_all_tabs` の結果をタブごとにまとめてから回すので、
    そのタブ番号がタブの本数を超えていると ``editors[editor_index]`` が
    ``IndexError`` になる（＝**置換の途中で例外が飛び、そこまでの書き換えだけが
    残った半端な状態**になる）。ここでは 1 つ目のタブぶんだけを範囲外にして、

    - 範囲外を指すぶんは何も書き換えないこと
    - **そこで打ち切らず**、残りのタブはちゃんと置換されること

    の両方を見る（見送りを ``continue`` ではなく ``break``/``return`` に
    してしまうと、後ろのタブが丸ごと置換されないまま「置換しました」と出る）。
    """
    tab_a, tab_b = make_tabs(window, "タブA テスト", "タブB テスト")
    open_replace_panel(window, "テスト", "ZZZ")

    real_search = window.search_all_tabs

    def search_with_tab_a_gone(query: str) -> list[SearchMatch]:
        # 検索し終えた直後に tab_a が閉じられ、そのぶんの結果だけが
        # 古いタブ番号のまま残っている状態を作る。
        stale_index = len(window.editors())
        results = []
        for match in real_search(query):
            if window.editors()[match.editor_index] is tab_a:
                match = replace(match, editor_index=stale_index)
            results.append(match)
        return results

    monkeypatch.setattr(window, "search_all_tabs", search_with_tab_a_gone)
    window._replace_all_matches()

    assert tab_a.toPlainText() == "タブA テスト"
    assert tab_b.toPlainText() == "タブB ZZZ"
    assert window.search_panel._count_label.text() == "1 件を置換しました"


# ----------------------------------------------------------------------
# 3. タブの構成が変わったときも検索し直す
# ----------------------------------------------------------------------
def test_closing_a_tab_refreshes_results(window: MainWindow) -> None:
    tab_a, tab_b = make_tabs(window, "タブA テスト", "タブB テスト")
    open_replace_panel(window, "テスト", "ZZZ")
    assert len(window.search_panel.matches()) == 2

    tab_a.set_modified(False)  # 未保存の確認ダイアログを出さない
    window.close_editor(tab_a)

    matches = window.search_panel.matches()
    assert len(matches) == 1
    assert window.editors()[matches[0].editor_index] is tab_b


def test_replace_after_closing_a_tab_hits_the_right_tab(window: MainWindow) -> None:
    """タブを閉じたあとに置換しても、別のタブの関係ない場所を書き換えない。"""
    tab_a, tab_b, tab_c = make_tabs(
        window, "タブA テスト", "タブB テスト", "タブC テスト"
    )
    open_replace_panel(window, "テスト", "ZZZ")
    # タブB のマッチを指した状態にする（Enter を 2 回）。
    window.search_panel._go_to_next_match()
    window.search_panel._go_to_next_match()

    tab_a.set_modified(False)  # 未保存の確認ダイアログを出さない
    window.close_editor(tab_a)
    window._replace_current_match()

    # 閉じたことで index はずれるが、置換されるのは検索語のあった場所だけ。
    texts = [tab_b.toPlainText(), tab_c.toPlainText()]
    assert texts.count("タブB ZZZ") + texts.count("タブC ZZZ") == 1
    assert all(text in ("タブB テスト", "タブC テスト", "タブB ZZZ", "タブC ZZZ")
               for text in texts)


def test_adding_a_tab_refreshes_results(window: MainWindow, qtbot) -> None:
    make_tabs(window, "タブA テスト")
    open_replace_panel(window, "テスト", "ZZZ")
    assert len(window.search_panel.matches()) == 1

    make_tabs(window, "タブB テスト")
    wait_for_refresh(qtbot)

    assert len(window.search_panel.matches()) == 2


def test_moving_a_tab_refreshes_results(window: MainWindow) -> None:
    """タブを並べ替えると editor_index がずれるので、検索し直す。"""
    target, _other = make_tabs(window, "あ テスト", "い")
    open_replace_panel(window, "テスト", "ZZZ")

    window.tabs.moveTab(window.tabs.indexOf(target), window.tabs.count() - 1)

    match = window.search_panel.matches()[0]
    assert window.editors()[match.editor_index] is target


def test_replace_after_moving_a_tab_hits_the_right_tab(window: MainWindow) -> None:
    target, other = make_tabs(window, "あ テスト", "い ダミー")
    open_replace_panel(window, "テスト", "ZZZ")

    window.tabs.moveTab(window.tabs.indexOf(target), window.tabs.count() - 1)
    window._replace_current_match()

    assert target.toPlainText() == "あ ZZZ"
    assert other.toPlainText() == "い ダミー"


def test_moving_a_tab_to_another_window_refreshes_both(
    make_window, qtbot
) -> None:
    source = make_window()
    target = make_window()
    make_tabs(source, "移動するタブ テスト")
    moved = source.current_editor()
    open_replace_panel(source, "テスト", "ZZZ")
    open_replace_panel(target, "テスト", "ZZZ")
    assert len(source.search_panel.matches()) == 1
    assert len(target.search_panel.matches()) == 0

    source._move_tab_to_window(moved, target)

    assert len(source.search_panel.matches()) == 0
    assert len(target.search_panel.matches()) == 1


def test_tearing_a_tab_off_refreshes_the_source_window(make_window) -> None:
    """タブを新しいウィンドウへ切り離したら、元の検索結果も作り直す。

    「別のウィンドウへ移す」経路
    (:func:`test_moving_a_tab_to_another_window_refreshes_both`) は固定して
    あったが、**新しいウィンドウへ切り離す経路は同じ後始末を誰も見て
    いなかった**。:class:`SearchMatch` は「このウィンドウの何番目のタブか」
    で位置を覚えているので、切り離しで並びが詰まると**残ったタブの
    関係ない場所を指したまま**になる。その状態で「置換」を押されると、
    2026-08-07 に直した「文字位置のずれ」と同じ壊れ方をする。
    """
    source = make_window()
    torn_off, _remaining = make_tabs(source, "切り離すタブ テスト", "残るタブ")
    open_replace_panel(source, "テスト", "ZZZ")
    assert len(source.search_panel.matches()) == 1

    new_window = source._tear_off_tab_to_new_window(torn_off, QPoint(300, 300))

    assert new_window is not None
    assert len(source.search_panel.matches()) == 0


def test_moved_tab_refreshes_its_new_window_only(make_window, qtbot) -> None:
    """移動したあとの本文編集は、移動先のウィンドウの検索結果に反映される。"""
    source = make_window()
    target = make_window()
    make_tabs(source, "移動するタブ\n")
    moved = source.current_editor()
    open_replace_panel(source, "テスト", "ZZZ")
    open_replace_panel(target, "テスト", "ZZZ")

    source._move_tab_to_window(moved, target)
    append_text(moved, "\nテスト テスト")
    wait_for_refresh(qtbot)

    assert len(target.search_panel.matches()) == 2
    assert len(source.search_panel.matches()) == 0


# ----------------------------------------------------------------------
# 4. 全置換との組み合わせ
# ----------------------------------------------------------------------
def test_replace_all_still_replaces_everything(window: MainWindow) -> None:
    """全置換は自分で検索し直してから置換するので、確認で弾かれない。"""
    make_tabs(window, "foo bar foo\n", "foo\n")
    open_replace_panel(window, "foo", "BAZ")

    window._replace_all_matches()

    editors = window.editors()
    assert editors[1].toPlainText() == "BAZ bar BAZ\n"
    assert editors[2].toPlainText() == "BAZ\n"
    assert window.search_panel._count_label.text() == "3 件を置換しました"


def test_replace_all_after_editing_replaces_current_text(
    window: MainWindow, qtbot
) -> None:
    make_tabs(window, "foo\n")
    editor = window.current_editor()
    open_replace_panel(window, "foo", "BAZ")

    append_text(editor, "\nfoo foo")
    window._replace_all_matches()

    assert editor.toPlainText() == "BAZ\n\nBAZ BAZ"
    assert window.search_panel._count_label.text() == "3 件を置換しました"


def test_replace_all_counts_only_actual_replacements(window: MainWindow) -> None:
    """件数表示は「実際に置換した件数」（見送った件は数えない）。"""
    make_tabs(window, "foo foo\n")
    open_replace_panel(window, "foo", "BAZ")

    window._replace_all_matches()

    assert window.search_panel._count_label.text() == "2 件を置換しました"
    assert window.current_editor().toPlainText() == "BAZ BAZ\n"


# ----------------------------------------------------------------------
# 5. 重い検索では本文編集への追随をやめる（入力が固まらないための安全弁）
# ----------------------------------------------------------------------
def test_fast_search_keeps_following_edits(window: MainWindow) -> None:
    make_tabs(window, "foo\n")
    open_replace_panel(window, "foo", "bar")

    assert window._auto_refresh_on_edit is True


def test_slow_search_stops_following_edits(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    """検索し直しに時間がかかったら、本文編集ごとの検索し直しをやめる。"""
    make_tabs(window, "foo\n")
    editor = window.current_editor()
    open_replace_panel(window, "foo", "bar")

    real_search = window.search_all_tabs

    def slow_search(query: str):
        import time as _time

        _time.sleep((SLOW_SEARCH_REFRESH_MS + 100) / 1000)
        return real_search(query)

    monkeypatch.setattr(window, "search_all_tabs", slow_search)
    window._refresh_search_results()
    assert window._auto_refresh_on_edit is False

    # 以降、本文を編集しても検索し直さない（＝入力が固まらない）。
    monkeypatch.setattr(window, "search_all_tabs", real_search)
    append_text(editor, "\nfoo foo")
    wait_for_refresh(qtbot)
    assert len(window.search_panel.matches()) == 1


def test_slow_search_still_protects_the_text(
    window: MainWindow, qtbot, monkeypatch
) -> None:
    """追随をやめている間でも、古い結果での置換は見送られる。"""
    make_tabs(window, "あいうえお テスト かきくけこ")
    editor = window.current_editor()
    open_replace_panel(window, "テスト", "置換後")
    window._auto_refresh_on_edit = False

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("XXXXX")
    wait_for_refresh(qtbot)
    window._replace_current_match()

    assert editor.toPlainText() == "XXXXXあいうえお テスト かきくけこ"
    assert window.search_panel._count_label.text() == STALE_RESULT_NOTICE


def test_query_change_can_re_enable_following_edits(window: MainWindow) -> None:
    """検索語を打ち直せば、そのときの速さで測り直して追随を再開する。"""
    make_tabs(window, "foo\n")
    open_replace_panel(window, "foo", "bar")
    window._auto_refresh_on_edit = False

    window.search_panel._input.setPlainText("fo")
    window.search_panel.run_search()

    assert window._auto_refresh_on_edit is True
