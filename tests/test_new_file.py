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
