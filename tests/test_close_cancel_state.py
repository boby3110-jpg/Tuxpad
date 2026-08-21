"""閉じるのを「キャンセル」したあとに残る状態のテスト.

未保存の確認ダイアログの 3 択（保存／破棄／キャンセル）のうち、
**キャンセル**を選んだ「あと」を見る。既存の
``test_close_remember_choice.py`` は「閉じずに済んだか」
（``close()`` が False・ウィンドウがまだ見えている・ファイルが変わって
いない）までは見ているが、**閉じる直前にだけやるべき後片付けが、
中止したのに走ってしまっていないか**は誰も見ていなかった。

``MainWindow.closeEvent`` は「閉じると決まった後」に

* 位置・サイズを次回起動用に覚える
* 保留中の「外部変更の再読み込み」を捨て、監視を解く
* 検索パネルを閉じる
* 開いているウィンドウ一覧 (:meth:`MainWindow.open_windows`) から自分を外す

の 4 つをやる。中止した側でこれらが走ると、**ウィンドウは残っているのに
中身だけが「閉じた後」になる**（外部で書き換わっても知らせてくれない、
他のウィンドウからタブを渡せない、など）。どれも画面を見ても分からない
種類の食い違いなので、テストで固定しておく。

なお「中止したのにタブが消える」「× で閉じた（どのボタンでもない）回答を
破棄として扱う」の 2 つは、**既存のテストが既に捕まえている**ことを
わざと壊して確かめたので、ここには重ねて書いていない。
"""

from __future__ import annotations

from pathlib import Path

import shiboken6
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MODIFIED_MARK, MainWindow
from editor_app.settings import load_window_geometry

HELPER = "editor_app.main_window.show_message_box_with_checkbox"
PLAIN = "editor_app.main_window.show_message_box"


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def make_unsaved_window(make_window, tmp_path: Path, count: int):
    """保存先が決まっている未保存タブを ``count`` 個持つウィンドウを作る。

    保存先が決まっていれば「保存」を選んでもファイルダイアログは出ない。
    起動時の空タブは未変更なので確認の対象にはならない。
    """
    win = make_window()
    paths = []
    for i in range(count):
        path = tmp_path / f"file{i}.txt"
        path.write_text("元の内容\n", encoding="utf-8")
        editor = win.open_path(path)
        assert editor is not None
        edit_text(editor, f"変更 {i}\n")
        paths.append(path)
    assert sum(1 for e in win.editors() if e.is_modified) == count
    return win, paths


def answer_with(monkeypatch, answer, *, remember: bool = False):
    """ウィンドウを閉じるときの確認ダイアログの答えを決め打ちする。"""
    asked = []

    def fake(parent, icon, title, text, buttons, default_button, checkbox_text):
        asked.append(text)
        return answer, remember

    monkeypatch.setattr(HELPER, fake)
    return asked


# ----------------------------------------------------------------------
# ウィンドウを閉じるのを中止したとき（closeEvent）
# ----------------------------------------------------------------------


def test_cancel_keeps_pending_reload(make_window, tmp_path, monkeypatch):
    """中止したなら、保留中の「外部変更の再読み込み」を捨ててはいけない。

    捨てると監視も一緒に解かれるので、**閉じずに使い続けているウィンドウ
    なのに、開いているファイルが外部で書き換わったことを二度と知らせて
    くれなくなる**。利用者が気づけるのは、実際に外部で書き換わって、
    それを見落とした後だけ。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 1)
    answer_with(monkeypatch, QMessageBox.StandardButton.Cancel)
    # デバウンスのタイマーが検査の途中で発火しても、本物の確認ダイアログを
    # 開かせない（ヘッドレスでは応答する人がいないので固まる）。
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    key = str(paths[0].resolve())
    win._pending_reload_paths.add(key)
    win._reload_check_timer.start()

    assert win.close() is False

    assert key in win._pending_reload_paths  # 保留は残っている
    assert win._reload_check_timer.isActive() is True  # タイマーも動いたまま
    assert key in win._file_watcher.files()  # 監視も解かれていない


def test_cancel_keeps_search_panel_open(make_window, tmp_path, monkeypatch):
    """中止したなら、開いていた検索パネルもそのまま残すこと。"""
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    win.show_search_panel()
    assert win.search_panel.isVisible() is True
    answer_with(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert win.close() is False

    assert win.search_panel.isVisible() is True


def test_cancel_keeps_window_in_open_windows(make_window, tmp_path, monkeypatch):
    """中止したなら、開いているウィンドウの一覧から外してはいけない。

    一覧はタブのウィンドウ間ドラッグや、別プロセスから渡されたファイルの
    引き受け先を決めるのに使う。外れたままだと、**見えているのに誰からも
    宛先として選ばれないウィンドウ**になる。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    answer_with(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert win.close() is False

    assert win in MainWindow.open_windows()


def test_cancel_does_not_save_geometry(make_window, tmp_path, monkeypatch):
    """中止したなら、そのときの位置・大きさを「閉じたときの状態」として
    覚えてはいけない。

    覚えるのは閉じると決まった後だけ。中止のたびに覚えると、次に開く
    ウィンドウの大きさが**閉じてもいないウィンドウの途中経過**で
    上書きされる。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    win.resize(640, 480)
    answer_with(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert win.close() is False

    assert load_window_geometry() is None


def test_cancel_keeps_every_tab_with_its_unsaved_mark(make_window, tmp_path, monkeypatch):
    """中止したなら、タブも未保存の印もそのまま残ること。"""
    win, _ = make_unsaved_window(make_window, tmp_path, 3)
    before = win.editors()
    answer_with(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert win.close() is False

    assert win.editors() == before
    assert all(shiboken6.isValid(editor) for editor in before)
    unsaved = [editor for editor in before if editor.path is not None]
    assert len(unsaved) == 3
    assert all(editor.is_modified for editor in unsaved)
    titles = [win.tabs.tabText(win.tabs.indexOf(editor)) for editor in unsaved]
    assert all(title.startswith(MODIFIED_MARK) for title in titles)


def test_tabs_saved_before_the_cancel_stay_saved(make_window, tmp_path, monkeypatch):
    """先に「保存」を選んだタブは、後のタブで中止しても保存済みのまま。

    「保存」はその場でディスクへ書くので取り消せない。中止をきっかけに
    未保存の印だけ元へ戻すと、**中身は新しいのに印は古いまま**という
    食い違いになり、次に閉じるときにもう一度確認されてしまう。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 2)
    answers = iter([QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel])

    def fake(parent, icon, title, text, buttons, default_button, checkbox_text):
        return next(answers), False

    monkeypatch.setattr(HELPER, fake)

    assert win.close() is False

    saved, still_unsaved = (e for e in win.editors() if e.path is not None)
    assert saved.is_modified is False
    assert paths[0].read_text(encoding="utf-8") == "変更 0\n"
    assert still_unsaved.is_modified is True
    assert paths[1].read_text(encoding="utf-8") == "元の内容\n"


def test_the_asked_tab_is_shown_while_closing_the_window(make_window, tmp_path, monkeypatch):
    """どのタブについて訊いているのかを、先に表示へ出してから訊くこと。

    切り替えないと、**画面に出ているのと違うファイルの名前で訊かれる**
    （タブが多いときほど、どれの話か分からない）。中止したあとも、
    訊かれたタブが表示されたまま残る。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 2)
    first, last = (e for e in win.editors() if e.path is not None)
    win.tabs.setCurrentWidget(last)  # 訊かれる順とは違うタブを表示しておく
    assert win.tabs.currentWidget() is not first
    shown = []

    def fake(parent, icon, title, text, buttons, default_button, checkbox_text):
        shown.append(win.tabs.currentWidget())
        return QMessageBox.StandardButton.Cancel, False

    monkeypatch.setattr(HELPER, fake)

    assert win.close() is False

    assert shown == [first]
    assert win.tabs.currentWidget() is first


# ----------------------------------------------------------------------
# タブを 1 つ閉じるのを中止したとき（close_editor）
# ----------------------------------------------------------------------


def test_the_asked_tab_is_shown_while_closing_one_tab(window, tmp_path, monkeypatch):
    """タブを 1 つ閉じるときも、訊く前にそのタブへ表示を切り替えること。"""
    path = tmp_path / "file.txt"
    path.write_text("元の内容\n", encoding="utf-8")
    editor = window.open_path(path)
    edit_text(editor, "変更\n")
    other = window.new_file()
    window.tabs.setCurrentWidget(other)
    shown = []

    def fake(*args, **kwargs):
        shown.append(window.tabs.currentWidget())
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(PLAIN, fake)

    assert window.close_editor(editor) is False

    assert shown == [editor]
    assert window.tabs.currentWidget() is editor
