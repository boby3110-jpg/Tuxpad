"""タブを **1 つだけ** 閉じるとき、保存に失敗した「あと」に残る状態のテスト.

:mod:`test_close_failed_save_state` は「ウィンドウを閉じる」道
(:meth:`MainWindow.closeEvent`) の失敗経路を見ている。こちらは
**タブの × ボタン / Ctrl+W** の道 (:meth:`MainWindow.close_editor` →
:meth:`MainWindow.maybe_save`) を見る。中止して ``False`` を返すところは
似ているが、**そこへ至る道が別**（``event.ignore()`` ではなく
``close_editor`` の早期 ``return``）なので、後片付けが中止側に漏れて
いないかは改めて縛らないと誰も見ていない。

実際、既存のテストは

* ``test_tab_display.py`` … 中止したらタブが残ること
* ``test_close_cancel_state.py`` … 訊く前にそのタブへ表示を切り替えること

までしか見ていなかった。中止のあとに **やってはいけないこと** を
足す変異を 9 通り試したところ、**7 通りが生き残った**（残り 2 通り
——「中身を親から外す」「ウィンドウを閉じる」——は既存のテストが捕捉）。

:meth:`MainWindow.close_editor` は「閉じると決まった後」に

* タブを外し、本文ウィジェットを捨てる
* ウィンドウのタイトルとタブの並びの追従
* 監視の張り直し
* タブが 0 個になったらウィンドウも閉じる

をやる。さらに、このウィンドウには「閉じると決まった後」にだけやる
後片付け（位置の記憶・保留中の再読み込みの破棄・検索パネル・開いている
ウィンドウ一覧）もある。**1 タブを閉じるのをやめただけ**でこれらが走ると、
**ウィンドウもタブも残っているのに中身だけが「閉じた後」になる**。
しかもこの経路は「保存しようとして失敗した」直後——利用者がいちばん
**中身を失いたくない**場面である。

保存が失敗する道は 2 本あり、両方を通している:

1. **保存先を訊かれてキャンセルした**（無題タブ → 「名前を付けて保存」を中止）
2. **書き込みそのものが失敗した**（権限が無い・NAS が切れた・ディスクが一杯）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox

from editor_app.editor import document_text
from editor_app.main_window import MODIFIED_MARK, MainWindow
from editor_app.settings import load_window_geometry

PLAIN = "editor_app.main_window.show_message_box"


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def quiet_errors(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """「保存できません」等のお知らせダイアログを捕まえる。

    ヘッドレスでは誰も応答できないため、モーダルを開いたままにするとテスト
    全体が固まる。既存の :mod:`test_close_failed_save_state` と同じ差し替え。
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


def answer_save(monkeypatch) -> list[str]:
    """1 タブを閉じるときの確認ダイアログで、必ず「保存」を選ぶ。

    こちらの道が使うのはチェックボックス付きではない素の
    ``show_message_box``（「残りにも同じ操作」はウィンドウを閉じるとき専用）。
    """
    asked: list[str] = []

    def fake(_parent, _icon, _title, text, *_args, **_kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.Save

    monkeypatch.setattr(PLAIN, fake)
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


def closing_editor(win):
    """閉じようとする（＝保存先が決まっている未保存の）タブを返す。"""
    return next(e for e in win.editors() if e.path is not None)


# ----------------------------------------------------------------------
# 書き込みに失敗して、タブを閉じるのをやめたあと
# ----------------------------------------------------------------------


def test_failed_save_keeps_the_tab_and_its_editor_alive(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、本文ウィジェットを捨ててはいけない。

    ``deleteLater()`` は「制御が Qt へ戻った時点」で効くので、**その場では
    まだ生きているように見える**のがたちが悪い。捨ててしまうと、閉じずに
    残っているはずのタブの中身が後から消え、**まだディスクに書けていない
    変更ごと失われる**。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 1)
    editor = closing_editor(win)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(editor) is False

    # 保留中の deleteLater をここで消化させてから確かめる（そうしないと
    # 「捨てた」変異でも、その場ではまだ生きて見えてしまう）。
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert shiboken6.isValid(editor)
    assert editor in win.editors()
    assert win.tabs.indexOf(editor) >= 0
    assert document_text(editor) == "変更 0\n"
    assert paths[0].read_text(encoding="utf-8") == "元の内容\n"


def test_failed_save_keeps_the_unsaved_mark(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、未保存の印を消してはいけない。

    書けていないのだから印が消えてはいけない。消えると、次に閉じるときに
    **確認もされずに捨てられる**（``maybe_save`` は未編集ならそのまま
    通してしまう）。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    editor = closing_editor(win)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(editor) is False

    assert editor.is_modified is True
    assert win.tabs.tabText(win.tabs.indexOf(editor)).startswith(MODIFIED_MARK)


def test_failed_save_keeps_pending_reload(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、保留中の再読み込みを捨ててはいけない。

    捨てると監視も一緒に解かれるので、**閉じずに使い続けているウィンドウ
    なのに、開いているファイルが外部で書き換わったことを二度と知らせて
    くれなくなる**。しかも 1 タブを閉じ損ねただけで、**そのウィンドウの
    全タブ**の監視が黙って止まる。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 1)
    editor = closing_editor(win)
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

    assert win.close_editor(editor) is False

    assert key in win._pending_reload_paths  # 保留は残っている
    assert win._reload_check_timer.isActive() is True  # タイマーも動いたまま
    assert key in win._file_watcher.files()  # 監視も解かれていない


def test_failed_save_keeps_search_panel_open(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、検索パネルもそのまま残すこと。"""
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    editor = closing_editor(win)
    win.show_search_panel()
    assert win.search_panel.isVisible() is True
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(editor) is False

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
    editor = closing_editor(win)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(editor) is False

    assert win in MainWindow.open_windows()


def test_failed_save_does_not_save_geometry(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存に失敗して閉じるのをやめたなら、位置・大きさを覚えてはいけない。

    覚えるのは**ウィンドウを閉じると決まった後**だけ。タブ 1 つを閉じ
    損ねただけで覚えると、次に開くウィンドウの大きさが**閉じてもいない
    ウィンドウの途中経過**で上書きされる。
    """
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    editor = closing_editor(win)
    win.resize(640, 480)
    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(editor) is False

    assert load_window_geometry() is None


def test_failed_save_leaves_the_other_tabs_alone(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """1 タブの保存が失敗しても、他のタブの印を触ってはいけない。

    「保存」はその場でディスクへ書くので取り消せない。失敗をきっかけに
    **既に保存し終えたタブまで未保存の印へ戻す**と、中身は新しいのに印は
    古いままという食い違いになり、次に閉じるときにもう一度確認される。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    first, second, third = (e for e in win.editors() if e.path is not None)
    # 先の 2 つは書けている状態にしておく（＝印が落ちている）。
    assert win.save_editor(first) is True
    assert win.save_editor(second) is True
    assert first.is_modified is False
    assert second.is_modified is False

    answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(third) is False

    assert first.is_modified is False
    assert second.is_modified is False
    assert third.is_modified is True
    # 先に書けた 2 つは、後の失敗では巻き戻らない。
    assert paths[0].read_text(encoding="utf-8") == "変更 0\n"
    assert paths[1].read_text(encoding="utf-8") == "変更 1\n"
    assert paths[2].read_text(encoding="utf-8") == "元の内容\n"


# ----------------------------------------------------------------------
# 保存先を訊かれてキャンセルした道（無題タブ）
# ----------------------------------------------------------------------


def test_cancelled_save_dialog_keeps_the_tab_intact(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """保存先の指定をやめた場合も、閉じるのをやめて何も片付けないこと。

    「保存」を選んだのに保存先を訊かれて閉じた（＝やめた）道。上の
    書き込み失敗とは **``save_editor`` が False を返すまでの経路が違う**
    ので、両方通しておく。
    """
    win = make_window()
    editor = win.new_file()
    edit_text(editor, "まだ名前の無い文章\n")
    assert editor.path is None
    win.show_search_panel()
    win.resize(640, 480)

    answer_save(monkeypatch)
    cancel_save_dialog(monkeypatch)

    assert win.close_editor(editor) is False

    assert shiboken6.isValid(editor)
    assert editor in win.editors()
    assert editor.is_modified is True
    assert editor.path is None
    assert win.search_panel.isVisible() is True
    assert win in MainWindow.open_windows()
    assert load_window_geometry() is None
    # 保存先を決めなかったのだから、どこにも書かれていない。
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------
# 失敗したあと、もう一度閉じられること
# ----------------------------------------------------------------------


def test_the_tab_can_still_be_closed_after_a_failed_save(
    make_window, tmp_path, monkeypatch, quiet_errors
):
    """一度保存に失敗しても、閉じ直せば今までどおり確認から始まること。

    中止の副作用でウィンドウの中身が「閉じた後」になっていると、2 回目は
    そもそも確認が出ない・タブが見つからない、といった形で壊れる。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 2)
    editor = closing_editor(win)
    asked = answer_save(monkeypatch)
    refuse_writes(monkeypatch)

    assert win.close_editor(editor) is False
    assert len(asked) == 1

    # 2 回目は「破棄」を選ぶ。今度はちゃんと閉じられること。
    monkeypatch.setattr(
        PLAIN, lambda *args, **kwargs: QMessageBox.StandardButton.Discard
    )

    assert win.close_editor(editor) is True
    assert win.tabs.count() == 1
    # 破棄したのだから、ファイルは元のまま。
    assert paths[0].read_text(encoding="utf-8") == "元の内容\n"
