"""機能 1「新規作成」のテスト."""

from __future__ import annotations

from editor_app.editor import EditorWidget


def test_starts_with_one_empty_tab(window):
    """起動直後は空の新規タブが 1 つ開いている。"""
    assert window.tabs.count() == 1
    editor = window.current_editor()
    assert isinstance(editor, EditorWidget)
    assert editor.toPlainText() == ""
    assert editor.is_untitled
    assert not editor.is_modified
    assert editor.display_name == "無題-1"


def test_new_file_adds_tab_and_switches_to_it(window):
    """新規作成でタブが増え、そのタブがアクティブになる。"""
    created = window.new_file()
    assert window.tabs.count() == 2
    assert window.current_editor() is created
    assert created.display_name == "無題-2"
    assert window.tabs.tabText(window.tabs.currentIndex()) == "無題-2"


def test_untitled_numbers_increment(window):
    """無題の番号は 1 から順に振られる。"""
    window.new_file()
    window.new_file()
    names = [editor.display_name for editor in window.editors()]
    assert names == ["無題-1", "無題-2", "無題-3"]


def test_untitled_number_is_reused_after_close(window):
    """閉じたタブの番号は空き番号として再利用される。"""
    window.new_file()  # 無題-2
    window.new_file()  # 無題-3
    window.tabs.removeTab(1)  # 無題-2 を閉じる
    assert [e.display_name for e in window.editors()] == ["無題-1", "無題-3"]

    created = window.new_file()
    assert created.display_name == "無題-2"


def test_new_file_action_shortcut_is_ctrl_n(window):
    """新規作成は Ctrl+N に割り当てられている。"""
    assert window.action_new.shortcut().toString() == "Ctrl+N"


def test_new_file_action_creates_tab(window):
    """メニュー項目 (アクション) の起動でも新規タブが作られる。"""
    window.action_new.trigger()
    assert window.tabs.count() == 2


def test_new_tab_receives_focus(window):
    """新規タブのエディタにフォーカスが移る。"""
    created = window.new_file()
    assert created.hasFocus()


def test_window_title_follows_current_tab(window):
    """ウィンドウタイトルに現在のタブ名が出る。"""
    window.new_file()
    assert window.windowTitle().startswith("無題-2 - ")

    window.tabs.setCurrentIndex(0)
    assert window.windowTitle().startswith("無題-1 - ")


def test_empty_untitled_detection(window):
    """まっさらな新規タブは is_empty_untitled() が True。"""
    editor = window.current_editor()
    assert editor.is_empty_untitled()

    editor.setPlainText("あ")
    assert not editor.is_empty_untitled()


def test_saved_empty_file_is_not_an_empty_untitled_tab(window, tmp_path):
    """**中身が空のファイル**を開いているタブは「まっさらな無題タブ」ではない。

    ``is_empty_untitled()`` の 3 つの条件のうち ``self.is_untitled`` だけを
    落としても、2288 件すべてが緑のまま通り抜けていた（86 回目）。
    ここが抜けると、空のファイル（ファイルマネージャーで作ったばかりの
    ものや、中身を消して保存したもの）を開いているタブが
    :meth:`~editor_app.file_commands.FileCommandsMixin._reusable_editor`
    に「使い回してよい」と判定され、**次にファイルを開いた瞬間、そのタブが
    黙って消える**（下の
    :func:`test_opening_a_file_does_not_reuse_a_saved_empty_tab` で実際に
    確かめている）。
    """
    path = tmp_path / "からっぽ.txt"
    path.write_text("", encoding="utf-8")
    editor = window.open_path(path)

    assert editor.toPlainText() == ""
    assert not editor.is_modified
    assert not editor.is_untitled  # ファイルを持っている
    assert not editor.is_empty_untitled()


def test_opening_a_file_does_not_reuse_a_saved_empty_tab(window, tmp_path):
    """空のファイルのタブは、別のファイルを開いても消えないこと。

    上のテストが見ている判定が、実際にタブの増減へつながっていることを
    「タブの枚数と名前」で確かめる（判定だけ直しても、使い回しの経路が
    別の物差しを持っていたら意味がないため）。
    """
    empty = tmp_path / "からっぽ.txt"
    empty.write_text("", encoding="utf-8")
    other = tmp_path / "ほか.txt"
    other.write_text("本文\n", encoding="utf-8")

    window.open_path(empty)
    assert [editor.display_name for editor in window.editors()] == ["からっぽ.txt"]

    window.open_path(other)
    assert [editor.display_name for editor in window.editors()] == [
        "からっぽ.txt",
        "ほか.txt",
    ]


def test_edited_then_emptied_untitled_tab_is_not_reusable(window):
    """打った字を**消しただけ**の無題タブは、使い回してよい扱いにしない。

    ``not self.is_modified`` を落としても全件が緑のまま通り抜けていた
    （86 回目）。本文が空でも「打って消した」タブには undo で戻せる内容が
    あるので、ファイルを開いたときに使い回されると
    :meth:`~editor_app.editor.EditorWidget.set_content` が undo 履歴ごと
    捨ててしまい、**消した本文を戻せなくなる**。
    """
    editor = window.current_editor()
    # ``setPlainText()`` / ``clear()`` は変更の印を立てないので、実際に
    # 打ったときと同じようにカーソル経由で入れて消す。
    cursor = editor.textCursor()
    cursor.insertText("消す前の本文")
    cursor.select(cursor.SelectionType.Document)
    cursor.removeSelectedText()

    assert editor.toPlainText() == ""
    assert editor.document().isUndoAvailable()  # undo で戻せる内容がある
    assert editor.is_untitled
    assert editor.is_modified  # 打って消した跡は残っている
    assert not editor.is_empty_untitled()
