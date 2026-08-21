"""ウィンドウを閉じるとき、「保存」の保存に**失敗**した「あと」に残る状態のテスト.

未保存の確認ダイアログの 3 択のうち「保存」を選び、その保存が失敗した経路を見る。
:mod:`test_close_cancel_state` が見ている「キャンセル」枝と、``event.ignore()``
に至るところは同じだが、**そこへ至る道が別**なので、後片付けが中止側に
漏れていないかは改めて縛らないと誰も見ていない。実際、既存の
``test_close_remember_choice.py::test_failed_save_stops_closing`` は
**ウィンドウが残ること**（``close()`` が False・まだ見えている）までしか
見ていなかった。

``MainWindow.closeEvent`` は「閉じると決まった後」に

* 位置・サイズを次回起動用に覚える
* 保留中の「外部変更の再読み込み」を捨て、監視を解く
* 検索パネルを閉じる
* 開いているウィンドウ一覧 (:meth:`MainWindow.open_windows`) から自分を外す

の 4 つをやる。保存に失敗して閉じるのをやめた側でこれらが走ると、
**ウィンドウは残っているのに中身だけが「閉じた後」になる**。しかも
こちらの経路は「保存しようとして失敗した」直後——利用者がいちばん
**中身を失いたくない**場面なので、外部変更を知らせてくれなくなるのは
キャンセル枝より重い。

保存が失敗する道は 2 本あり、両方を見ている:

1. **保存先を訊かれてキャンセルした**（無題タブ → 「名前を付けて保存」を中止）
2. **書き込みそのものが失敗した**（権限が無い・NAS が切れた・ディスクが一杯）

さらに、その失敗が起きるのが

* **直接訊かれたタブ**（``remembered`` がまだ ``None``）
* **「残りにも同じ操作」で覚えた回答が適用されたタブ**（``remembered`` あり）

のどちらでも止まることを見ている（前者は今まで誰も見ていなかった)。

なお「保存に失敗してもそのまま閉じてしまう」変異は、**既存のテストが
既に捕まえている**ことをわざと壊して確かめたので、ここには重ねて書いていない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import shiboken6
from PySide6.QtWidgets import QFileDialog, QMessageBox

from editor_app.main_window import MODIFIED_MARK, MainWindow
from editor_app.settings import load_window_geometry

HELPER = "editor_app.main_window.show_message_box_with_checkbox"


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def quiet_errors(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """「保存できません」等のお知らせダイアログを捕まえる。

    ヘッドレスでは誰も応答できないため、モーダルを開いたままにするとテスト
    全体が固まる。既存の :mod:`test_save_as_failure_state` と同じ差し替え。
    """
    calls: list[tuple[str, str]] = []

    def fake(_parent, _icon, title, text, *_args, **_kwargs):
        calls.append((title, text))
        return None

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return calls


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


def answer_save(monkeypatch, *, remember: bool = False) -> list[str]:
    """確認ダイアログで必ず「保存」を選ぶ。"""
    asked: list[str] = []

    def fake(parent, icon, title, text, buttons, default_button, checkbox_text):
        asked.append(text)
        return QMessageBox.StandardButton.Save, remember

    monkeypatch.setattr(HELPER, fake)
    return asked


def refuse_writes(monkeypatch) -> None:
    """ディスクへの書き込みを必ず失敗させる（権限が無い・NAS が切れた等）。"""

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)


def cancel_save_dialog(monkeypatch) -> None:
    """「名前を付けて保存」のファイルダイアログをキャンセルしたことにする。"""
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Rejected
    )


# ----------------------------------------------------------------------
# 書き込みに失敗して閉じるのをやめたあと（保存先は決まっているタブ）
# ----------------------------------------------------------------------


def test_failed_save_keeps_pending_reload(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、保留中の再読み込みを捨ててはいけない。

    捨てると監視も一緒に解かれるので、**閉じずに使い続けているウィンドウ
    なのに、開いているファイルが外部で書き換わったことを二度と知らせて
    くれなくなる**。保存に失敗した直後——つまり**まだディスクに書けていない
    変更を抱えたまま**の状態でこれが起きるので、キャンセル枝よりも重い。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 1)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)
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


def test_failed_save_keeps_search_panel_open(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、検索パネルもそのまま残すこと。"""
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    win.show_search_panel()
    assert win.search_panel.isVisible() is True
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close() is False

    assert win.search_panel.isVisible() is True


def test_failed_save_keeps_window_in_open_windows(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、ウィンドウ一覧から外してはいけない。

    一覧はタブのウィンドウ間ドラッグや、別プロセスから渡されたファイルの
    引き受け先を決めるのに使う。外れたままだと、**見えているのに誰からも
    宛先として選ばれないウィンドウ**になる。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close() is False

    assert win in MainWindow.open_windows()


def test_failed_save_does_not_save_geometry(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、位置・大きさを覚えてはいけない。

    覚えるのは閉じると決まった後だけ。失敗のたびに覚えると、次に開く
    ウィンドウの大きさが**閉じてもいないウィンドウの途中経過**で
    上書きされる。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    win.resize(640, 480)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close() is False

    assert load_window_geometry() is None


def test_failed_save_keeps_every_tab_with_its_unsaved_mark(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、タブも未保存の印もそのまま残ること。

    書けていないのだから、印が消えてはいけない（消えると、次に閉じるときに
    **確認もされずに捨てられる**）。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    before = win.editors()
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close() is False

    assert win.editors() == before
    assert all(shiboken6.isValid(editor) for editor in before)
    unsaved = [editor for editor in before if editor.path is not None]
    assert len(unsaved) == 3
    assert all(editor.is_modified for editor in unsaved)
    titles = [win.tabs.tabText(win.tabs.indexOf(editor)) for editor in unsaved]
    assert all(title.startswith(MODIFIED_MARK) for title in titles)
    # 1 つも書けていないので、ファイルは 3 つとも元のまま。
    assert all(p.read_text(encoding="utf-8") == "元の内容\n" for p in paths)


def test_tabs_saved_before_the_failure_stay_saved(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """先に保存できたタブは、後のタブの保存が失敗しても保存済みのまま。

    「保存」はその場でディスクへ書くので取り消せない。後のタブが失敗したのを
    きっかけに未保存の印だけ元へ戻すと、**中身は新しいのに印は古いまま**と
    いう食い違いになり、次に閉じるときにもう一度確認されてしまう。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 2)
    first, second = (e for e in win.editors() if e.path is not None)
    answer_save(monkeypatch)

    # 2 つ目を保存しようとした時点から書き込みを失敗させる。
    from editor_app import file_commands

    real_write = file_commands.write_encoded_file

    def refuse_second(path, *args, **kwargs):
        if Path(path).resolve() == paths[1].resolve():
            raise PermissionError("書き込めません")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse_second)

    assert win.close() is False

    # 1 つ目は本当に書けている。印も落ちたまま戻らない。
    assert first.is_modified is False
    assert paths[0].read_text(encoding="utf-8") == "変更 0\n"
    # 2 つ目は書けていないので、印も中身も未保存のまま。
    assert second.is_modified is True
    assert paths[1].read_text(encoding="utf-8") == "元の内容\n"


# ----------------------------------------------------------------------
# 保存先を訊かれてキャンセルしたあと（無題タブ）
# ----------------------------------------------------------------------


def test_cancelled_save_dialog_keeps_window_intact(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存先の指定をキャンセルしても、後片付けは 1 つも走らせないこと。

    こちらは ``write_encoded_file`` まで行かず、「名前を付けて保存」の
    ファイルダイアログを閉じた時点で失敗する道。既存の
    ``test_failed_save_stops_closing`` と同じ入り口だが、あちらは
    ウィンドウが残ることまでしか見ていない。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    extra = win.new_file()
    edit_text(extra, "無題の変更\n")
    win.show_search_panel()
    win.resize(640, 480)
    answer_save(monkeypatch, remember=True)
    cancel_save_dialog(monkeypatch)

    assert win.close() is False

    assert win.isVisible() is True
    assert win in MainWindow.open_windows()
    assert win.search_panel.isVisible() is True
    assert load_window_geometry() is None
    # 保存先が決まらなかったので、無題のままで印も落ちていない。
    assert extra.path is None
    assert extra.is_modified is True


# ----------------------------------------------------------------------
# 「直接訊かれたタブ」で失敗した道（remembered がまだ None）
# ----------------------------------------------------------------------


def test_failure_on_the_directly_asked_tab_stops_closing(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """いちばん最初に訊かれたタブの保存が失敗しても、そこで止まること。

    既存の ``test_failed_save_stops_closing`` は「残りにも同じ操作」を
    覚えた**あと**のタブで失敗する道しか通っていない（覚える前に失敗する
    道は誰も通していなかった）。**覚えたかどうかに関わらず**止まる。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 2)
    asked = answer_save(monkeypatch, remember=False)
    refuse_writes(monkeypatch)

    assert win.close() is False

    assert win.isVisible() is True
    # 1 つ目で止まるので、2 つ目は訊かれもしない。
    assert len(asked) == 1
    assert all(p.read_text(encoding="utf-8") == "元の内容\n" for p in paths)
    assert win in MainWindow.open_windows()
    assert load_window_geometry() is None


def test_failure_on_a_remembered_tab_stops_closing(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """覚えた回答が適用されたタブで失敗した場合も、後片付けは走らせない。"""
    win, paths = make_unsaved_window(make_window, tmp_path, 2)
    first, second = (e for e in win.editors() if e.path is not None)
    asked = answer_save(monkeypatch, remember=True)
    win.show_search_panel()

    from editor_app import file_commands

    real_write = file_commands.write_encoded_file

    def refuse_second(path, *args, **kwargs):
        if Path(path).resolve() == paths[1].resolve():
            raise PermissionError("書き込めません")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse_second)

    assert win.close() is False

    # 覚えたので訊かれたのは 1 回だけ。2 つ目は訊かずに保存しようとして失敗した。
    assert len(asked) == 1
    assert first.is_modified is False
    assert second.is_modified is True
    assert win in MainWindow.open_windows()
    assert win.search_panel.isVisible() is True
    assert load_window_geometry() is None


# ----------------------------------------------------------------------
# 失敗したあと、もう一度閉じられること
# ----------------------------------------------------------------------


def test_the_window_can_still_be_closed_after_a_failed_save(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """一度保存に失敗しても、閉じ直せば今までどおり確認から始まること。

    後片付けが中止側に漏れていると、2 回目の closeEvent は
    **既に「閉じた後」の状態**から始まってしまう。ここまでの各テストは
    漏れを 1 つずつ見ているが、これは「使い続けられるか」を通しで見る。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 2)
    answer_save(monkeypatch)
    from editor_app import file_commands

    real_write = file_commands.write_encoded_file
    refuse_writes(monkeypatch)
    assert win.close() is False

    # 書き込みが直った（NAS がつながった）ことにして、閉じ直す。
    # ここで monkeypatch.undo() を使わないこと。monkeypatch は関数スコープで
    # conftest の autouse フィクスチャ（設定と控えの置き場の隔離）と**同じ
    # インスタンスを共有している**ため、undo() すると隔離ごと外れて
    # 利用者本人の ~/.config・~/.cache を触りに行ってしまう。
    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", real_write)
    asked = answer_save(monkeypatch)

    assert win.close() is True
    # 覚えていないので 2 件とも確認され、2 件とも書けている。
    assert len(asked) == 2
    for i, path in enumerate(paths):
        assert path.read_text(encoding="utf-8") == f"変更 {i}\n"
