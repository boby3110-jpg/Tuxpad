"""開いているファイルが外部で変わったときの再読み込み（実機フィードバックにより追加）。

QFileSystemWatcher / QTimer による監視・デバウンスの配線そのものは
非同期で不安定なのでテストせず、確認と再読み込みの中核ロジック
(_maybe_reload_changed_file) を直接呼んで検証する。確認ダイアログは
show_message_box を monkeypatch して「はい/いいえ」を注入する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MainWindow


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    return path


def _patch_prompt(monkeypatch, answer: QMessageBox.StandardButton) -> list[int]:
    """再読み込み確認ダイアログをモックし、呼ばれた回数を記録するリストを返す。"""
    calls: list[int] = []

    def fake(*_args, **_kwargs):
        calls.append(1)
        return answer

    # 監視・再読み込みの中核は file_watch.py が持っているので、
    # 差し替える先もそちらのモジュール。**ここを間違えると本物のモーダル
    # ダイアログが開き、ヘッドレスのテストがそのまま固まる**（応答する人が
    # いないため）ので、パッチ先はコードの引っ越しに合わせること。
    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)
    return calls


def test_reload_yes_replaces_content(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert calls  # 確認ダイアログが出た
    assert editor.toPlainText() == "外部で書き換えた内容\n"
    assert editor.is_modified is False


def test_reload_no_keeps_content(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert calls  # 確認は出たが
    assert editor.toPlainText() == "最初の内容\n"  # 再読み込みしていない


def test_no_prompt_when_disk_matches_editor(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """ディスクの内容が本文と同じ（＝自分で保存した直後等）なら確認しない。"""
    window.open_path(sample)
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    # 内容は変えずに書き直す（mtime は変わるが中身は同じ）。
    sample.write_text("最初の内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert calls == []  # ダイアログは出ない


def test_unsaved_edits_still_prompt_and_reload_on_yes(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    editor.setPlainText("未保存の編集")
    editor.set_modified(True)  # 未保存の編集がある状態にする
    assert editor.is_modified is True
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert calls  # 未保存でも必ず確認する
    assert editor.toPlainText() == "外部で書き換えた内容\n"  # 「はい」なら編集は破棄


def test_unsaved_edits_no_keeps_local_edits(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    editor.setPlainText("未保存の編集")
    editor.set_modified(True)
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert calls
    assert editor.toPlainText() == "未保存の編集"  # 編集を保つ


def test_deleted_file_does_not_prompt(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    window.open_path(sample)
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    sample.unlink()
    window._maybe_reload_changed_file(sample)

    assert calls == []  # 消えたファイルは再読み込みしようがないので何もしない


def test_file_not_open_here_is_ignored(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    other = tmp_path / "not_open.txt"
    other.write_text("開いていない\n", encoding="utf-8")
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    window._maybe_reload_changed_file(other)

    assert calls == []


def test_change_event_for_closed_file_is_dropped(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """閉じたファイルへの変更イベントが来ても、監視を再登録せず確認も出さない。

    実機報告:「ファイルを閉じた後に外部で上書きすると、閉じたファイルにまで
    再読み込みの確認が出た」。QFileSystemWatcher は removePaths の直後でも
    カーネルが積んだ変更イベントを届けることがあり、以前はそのとき閉じた
    ファイルを監視に再登録してしまっていた。
    """
    window.new_file()  # ウィンドウが閉じないよう 2 タブにする
    editor = window.open_path(sample)
    window.close_tab(window.tabs.indexOf(editor))
    assert window.find_editor_by_path(sample.resolve()) is None
    key = str(sample.resolve())

    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    sample.write_text("閉じた後に外部で上書き\n", encoding="utf-8")

    # 閉じたファイルへの変更イベントが（遅れて）届いた状況を再現する。
    window._on_watched_file_changed(key)

    assert key not in window._file_watcher.files()  # 再登録しない
    assert key not in window._pending_reload_paths  # 保留にもしない
    window._process_pending_reloads()
    assert calls == []  # 確認ダイアログは出ない


def test_change_event_cleans_up_leftover_watch(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """開いていないファイルの監視が残っていたら、通知をきっかけに取り消す。

    ここは「閉じたのに監視だけが残ってしまった」状態からの復帰口。通常の
    経路（タブを閉じる・別ウィンドウへ移す・保存し直す）では
    ``_refresh_file_watches`` が監視を外すので、この状態になるのは
    ``removePaths`` が空振りした・閉じる直前に届いた通知で
    ``addPath`` し直された、といった競合のとき。ここでの再現も
    「閉じたあとに監視だけが残っている」状態を直接作って通知を届ける形にした。

    取り消せないと、**閉じたファイルの監視が動いたまま残り続ける**。
    ``QFileSystemWatcher`` は 1 パスにつき inotify の枠を 1 つ使い、この枠は
    利用者ごとの上限（既定 8192／ディストロによってはもっと少ない）がある。
    開いては閉じるたびに漏れていくと、いずれ**今開いているファイルの監視が
    張れなくなり、外部の書き換えに気づけなくなる**（黙って古い本文のまま
    上書き保存してしまう）。
    """
    window.new_file()  # ウィンドウが閉じないよう 2 タブにする
    editor = window.open_path(sample)
    window.close_tab(window.tabs.indexOf(editor))
    assert window.find_editor_by_path(sample.resolve()) is None

    key = str(sample.resolve())
    window._file_watcher.addPath(key)  # 監視だけが残ってしまった状態
    assert key in window._file_watcher.files()

    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    sample.write_text("閉じた後に外部で上書き\n", encoding="utf-8")
    window._on_watched_file_changed(key)

    assert key not in window._file_watcher.files()  # 残っていた監視を外す
    assert key not in window._pending_reload_paths
    window._process_pending_reloads()
    assert calls == []


def test_change_during_prompt_does_not_stack_dialogs(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """確認ダイアログを出している最中に同じファイルの変更が来ても、二重に出さない。

    確認ダイアログはモーダルだが、その間も Qt はイベントを回し続けるので、
    デバウンス用のタイマー（``_reload_check_timer``）は**ダイアログを出したまま
    発火する**。同期ソフト (rclone/NAS) が数秒おきに書き戻すファイルでは
    実際にこうなる。二重に出すと、利用者は**同じ問いに何度も答えさせられ**、
    しかも奥のダイアログに答えたときには手前で既に再読み込みが済んでいる
    （＝古い方の答えで上書きされる）。
    """
    editor = window.open_path(sample)
    calls: list[int] = []

    def fake(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            # ダイアログを出している間に、次の変更通知のデバウンスが明けた。
            sample.write_text("さらに外部で書き換え\n", encoding="utf-8")
            window._pending_reload_paths.add(str(sample.resolve()))
            window._process_pending_reloads()
        return QMessageBox.StandardButton.No

    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert len(calls) == 1  # 出るのは 1 回だけ
    assert editor.toPlainText() == "最初の内容\n"  # 「いいえ」なので本文は変わらない
    # 出し終わったら次の確認は出せる状態に戻っていること。
    assert str(sample.resolve()) not in window._reload_prompt_active


def test_partially_written_file_is_skipped(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """書き込み途中で読めないファイルは、確認も再読み込みもせず見送る。

    別のエディタや同期ソフトが書いている最中のファイルは、**中途半端な
    バイト列として読めてしまう**。ここでは UTF-16 のファイルが 1 バイト
    足りない状態（＝文字の途中で切れている）で届いた場合を再現している。
    読めないまま進むと**例外がそのまま上まで抜ける**。この経路は
    デバウンス用タイマーのスロットなので、抜けると
    「開いているファイルが外で変わっても、以後まったく気づかない」形になりうる。
    次の変更通知でまた来るので、ここは黙って見送るのが正しい。
    """
    path = tmp_path / "utf16.txt"
    path.write_bytes(b"\xff\xfe" + "最初の内容\n".encode("utf-16-le"))
    editor = window.open_path(path)
    assert editor.encoding == "utf-16-le"

    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    # 最後の 1 バイトが欠けた＝まだ書き終わっていない状態。
    path.write_bytes(b"\xff\xfe" + "外部で書き換えた内容\n".encode("utf-16-le")[:-1])
    window._maybe_reload_changed_file(path)

    assert calls == []  # 確認は出さない
    assert editor.toPlainText() == "最初の内容\n"  # 本文もそのまま


def test_unreadable_file_does_not_stop_other_pending_reloads(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """読めないファイルが 1 つ混じっても、他のファイルの確認は出る。

    デバウンス後の処理は溜まったパスをまとめて回すので、途中で例外が
    抜けると**その後ろのファイルが丸ごと処理されない**（順番は set 由来で
    決まらないため、どちらが先でも困る）。
    """
    broken = tmp_path / "broken.txt"
    broken.write_bytes(b"\xff\xfe" + "壊れる前\n".encode("utf-16-le"))
    good = tmp_path / "good.txt"
    good.write_text("最初の内容\n", encoding="utf-8")

    window.open_path(broken)
    good_editor = window.open_path(good)
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    broken.write_bytes(b"\xff\xfe" + "書き込み途中\n".encode("utf-16-le")[:-1])
    good.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._pending_reload_paths.update({str(broken.resolve()), str(good.resolve())})
    window._process_pending_reloads()

    assert len(calls) == 1  # 読めた方だけ確認が出る
    assert good_editor.toPlainText() == "最初の内容\n"  # 「いいえ」なので変わらない


def test_reload_preserves_cursor_position(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "lines.txt"
    path.write_text("あ\nい\nう\nえ\nお\n", encoding="utf-8")
    editor = window.open_path(path)

    cursor = editor.textCursor()
    cursor.setPosition(4)  # 3 行目あたり
    editor.setTextCursor(cursor)

    _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    path.write_text("か\nき\nく\nけ\nこ\n", encoding="utf-8")
    window._maybe_reload_changed_file(path)

    assert editor.textCursor().position() == 4


def test_reload_adopts_new_encoding(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """再読み込みしたら、ディスク側の文字コードを覚え直すこと。

    機能 8（文字コードの保持）は「開いたときの文字コードで保存し直す」
    ことで成り立っている。外部で**別の文字コードに書き換えられた**
    ファイルを再読み込みしたのに古い文字コードを覚えたままだと、
    **次の上書き保存でファイル全体が化ける**（本文は新しい内容なのに、
    保存だけ前の文字コードで行われるため）。ここでは Shift-JIS で
    書き換えられた場面を再現している。
    """
    path = tmp_path / "recoded.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    editor = window.open_path(path)
    assert editor.encoding == "utf-8"

    _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    # 別のエディタが Shift-JIS で保存し直した、という状況。
    path.write_bytes("外部で書き換えた内容\n".encode("cp932"))
    window._maybe_reload_changed_file(path)

    assert editor.toPlainText() == "外部で書き換えた内容\n"
    assert editor.encoding == "cp932"
    # 覚え直せていれば、そのまま保存してもディスクの文字コードが変わらない。
    window.save_editor(editor)
    assert path.read_bytes() == "外部で書き換えた内容\n".encode("cp932")


def test_reload_clamps_cursor_when_file_shrinks(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """縮んだファイルを再読み込みしても、カーソルは末尾に留まること。

    ``QTextCursor.setPosition()`` は**範囲外の位置を渡すと何もしない**
    （警告を出して黙って無視する）。丸めを外すと、カーソルは元の位置でも
    末尾でもなく**文書の先頭 (0)** に飛ばされる。長いファイルを開いて
    下の方を編集しているときに、外部でファイルが短く書き換わると
    「再読み込みしたら画面が先頭に戻った」形で効く。
    """
    path = tmp_path / "shrinking.txt"
    path.write_text("あ\nい\nう\nえ\nお\n", encoding="utf-8")
    editor = window.open_path(path)

    cursor = editor.textCursor()
    cursor.setPosition(8)  # 末尾寄り
    editor.setTextCursor(cursor)

    _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    path.write_text("か\n", encoding="utf-8")  # ぐっと短くなる
    window._maybe_reload_changed_file(path)

    assert editor.toPlainText() == "か\n"
    # 丸めていれば末尾（本文の長さ）。丸めを外すと 0 になる。
    assert editor.textCursor().position() == len("か\n")
