"""外部変更の監視 (`file_watch.py`) の「裏方の後始末」を縛るテスト。

`test_file_reload.py` は「訊いてくるか」「読み直すか」を見ているが、
2026-08-19（39 回目）の変異検査で、その周りの**目に見えない後始末**が
壊しても全部緑のままだと分かったので、ここで別に縛る。

いずれも実機では「同じ問いが何度も出る」「閉じたはずのファイルの問いが
後から出る」「外部変更に気づかない」という形で出るもので、どれも
2026-08-06〜08-08 に実機報告として上がった種類の不具合と同じ性質を持つ。
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
    """再読み込みの確認ダイアログを差し替え、出た回数を数えるリストを返す。"""
    calls: list[int] = []

    def fake(*_args, **_kwargs):
        calls.append(1)
        return answer

    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)
    return calls


def test_watched_path_through_a_symlink_finds_the_tab(
    window: MainWindow, tmp_path: Path
) -> None:
    """``QFileSystemWatcher`` が正規化前のパスで通知してきても、タブを見つける。

    タブが持つパスは必ず :func:`~editor_app.fileio.resolve_path` を通した
    形（シンボリックリンクを解いた実体）になっている。Qt 側が登録したときの
    文字列をそのまま返してくれる限りは単純比較で当たるが、**当たらなかった
    ときの解決し直し**が無いと、リンク越しに開いたファイルの外部変更を
    まるごと取り逃がす。
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = real_dir / "sample.txt"
    target.write_text("最初の内容\n", encoding="utf-8")
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    through_link = link_dir / "sample.txt"
    editor = window.open_path(through_link)
    assert editor is not None
    # open_path はリンクを解いた実体をタブのパスにする（＝単純比較では当たらない）。
    assert editor.path != through_link

    assert window._find_editor_for_watched_path(str(through_link)) is editor


def test_change_notice_after_closing_drops_the_pending_entry(
    window: MainWindow, tmp_path: Path
) -> None:
    """閉じたファイルの通知が遅れて届いたら、保留も取り消すこと。

    ``QFileSystemWatcher`` は ``removePaths`` の直後でも、既にカーネルが
    積んだ変更イベントを届けてくることがある。ここで保留を残すと、
    **閉じたはずのファイルについて「再読み込みしますか？」が後から出る**
    （2026-08-06 の実機報告と同じ形）。
    """
    first = tmp_path / "first.txt"
    first.write_text("あ\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("い\n", encoding="utf-8")

    closing = window.open_path(first)
    window.open_path(second)
    assert closing is not None
    path = str(closing.path)

    window._pending_reload_paths.add(path)
    assert window.close_editor(closing) is True

    window._on_watched_file_changed(path)

    assert path not in window._pending_reload_paths


def test_processing_empties_the_pending_list(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """一度処理した保留は消すこと（同じ問いが何度も出ないように）。

    消さないと、次に別のファイルが変わってタイマーが起きたときに、
    **もう答えたはずのファイルまでもう一度訊いてくる**。
    """
    editor = window.open_path(sample)
    assert editor is not None
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._pending_reload_paths.add(str(editor.path))
    window._process_pending_reloads()

    assert len(calls) == 1
    assert window._pending_reload_paths == set()

    # 保留が空なので、次にタイマーが起きても何も訊いてこない。
    window._process_pending_reloads()
    assert len(calls) == 1


def test_changed_file_tab_comes_to_the_front(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """外部変更を訊く前に、そのタブを前面に出すこと。

    出さないと、**どのファイルの話なのか分からないまま**「再読み込み
    しますか？」に答えることになる（本文が見えている別のタブの話だと
    勘違いして「はい」を押すと、編集がその場で消える）。
    """
    first = tmp_path / "first.txt"
    first.write_text("あ\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("い\n", encoding="utf-8")

    changed = window.open_path(first)
    front = window.open_path(second)
    assert changed is not None and front is not None
    assert window.tabs.currentWidget() is front

    _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)
    first.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(changed.path)

    assert window.tabs.currentWidget() is changed


def _capture_prompt_text(monkeypatch, answer: QMessageBox.StandardButton) -> list[str]:
    """再読み込みの確認ダイアログを差し替え、**出た本文**を集めるリストを返す。"""
    messages: list[str] = []

    def fake(_parent, _icon, _title, text, *_args, **_kwargs):
        messages.append(text)
        return answer

    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)
    return messages


def test_prompt_warns_that_unsaved_edits_will_be_discarded(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """未保存の編集があるタブでは、「その編集は破棄されます」と伝えること。

    同じ「再読み込みしますか？」でも、**未保存の編集があるかどうかで
    失うものがまるで違う**。伝えないまま「はい」を押させると、書きかけの
    本文が黙って消える。
    """
    editor = window.open_path(sample)
    assert editor is not None
    editor.setPlainText("書きかけの本文")
    editor.set_modified(True)

    messages = _capture_prompt_text(monkeypatch, QMessageBox.StandardButton.No)
    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert len(messages) == 1
    assert "破棄されます" in messages[0]


def test_prompt_without_unsaved_edits_does_not_mention_discarding(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """逆に、失うものが無いときに「破棄されます」と脅さないこと。"""
    editor = window.open_path(sample)
    assert editor is not None
    assert editor.is_modified is False

    messages = _capture_prompt_text(monkeypatch, QMessageBox.StandardButton.No)
    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert len(messages) == 1
    assert "破棄されます" not in messages[0]


# ----------------------------------------------------------------------
# 監視の「配線」そのもの（`_setup_file_watching`）
#
# 上のテストはどれも ``_on_watched_file_changed`` / ``_process_pending_reloads``
# を**直接呼んで**いる。だから 2026-09-20（62 回目）の変異検査では、
# `_setup_file_watching` の配線を外しても全部緑のままだった——
# **外部変更の監視が丸ごと黙っていても、誰も気づかない**状態だった。
#
# これは利用者の使い方（rclone/NAS で同じファイルを別の PC からも触る）で
# いちばん困る形で出る: 別の PC で直したファイルを、こちらは古い本文のまま
# 開いていて、保存した瞬間に相手の変更を静かに上書きする。
# ここでは中身ではなく**配線が生きていること**だけを、シグナルを自分で
# 発火させて確かめる（実ファイルシステムの通知を待たないので速い）。
# ----------------------------------------------------------------------


def test_watcher_change_signal_reaches_the_handler(
    window: MainWindow, sample: Path
) -> None:
    """``QFileSystemWatcher.fileChanged`` が受け手につながっていること。

    つながっていないと、外部変更に**一度も気づかない**（ダイアログが
    出ないので、利用者からは「監視が付いている」ように見えたまま）。
    """
    editor = window.open_path(sample)
    assert editor is not None
    path = str(editor.path)
    window._pending_reload_paths.clear()
    window._reload_check_timer.stop()

    # 実際のファイルシステムの通知は環境によって届き方が違うので、
    # 監視役が出すシグナルそのものを発火させて配線だけを見る。
    window._file_watcher.fileChanged.emit(path)

    assert path in window._pending_reload_paths  # 受け手が動いた
    assert window._reload_check_timer.isActive() is True  # デバウンスも起きた


def test_debounce_timeout_runs_the_pending_reloads(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """デバウンス用タイマーの ``timeout`` が処理につながっていること。

    つながっていないと、変更を溜めるだけで**一生訊いてこない**
    （``_pending_reload_paths`` に積まれたまま誰も処理しない）。
    """
    editor = window.open_path(sample)
    assert editor is not None
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._pending_reload_paths.add(str(editor.path))
    window._reload_check_timer.timeout.emit()

    assert len(calls) == 1
    assert window._pending_reload_paths == set()


def test_debounce_timer_stops_after_firing_once(
    window: MainWindow, qtbot
) -> None:
    """デバウンス用タイマーは 1 回きり (``setSingleShot``) であること。

    繰り返しになると、一度変更を拾ったあと**ウィンドウを閉じるまで
    ずっと 1.2 秒ごとに起き続ける**（保留が空でも止まらない）。
    ノート PC では電池を削るだけで、誰の得にもならない。
    """
    assert window._reload_check_timer.isSingleShot() is True

    # 実際に 1 回発火させて、止まっていることまで見る（待ち時間は 0 に
    # してあるので、本来の 1.2 秒を待たずに済む）。
    window._reload_check_timer.setInterval(0)
    window._reload_check_timer.start()
    qtbot.waitUntil(lambda: window._reload_check_timer.isActive() is False, timeout=1000)


# ----------------------------------------------------------------------
# タブがウィンドウ間を移ったときの後片付け
#
# タブを閉じたとき (`MainWindow.close_editor`) と、ウィンドウ間で移したとき
# (`tab_transfer.py`) は、**同じ後片付けが要る**（自分のタブに無くなった
# ファイルの監視を解く）。閉じる側は `_refresh_file_watches()` を呼んで
# いたが、**移す側は呼び忘れていた**（2026-10-29（85 回目）に発見）。
#
# 残っても「閉じたファイルの問いが出る」までは至らない
# （`_on_watched_file_changed` が開いていないパスで手を引く安全網がある）。
# 実害はその手前で、**自分のタブには無いファイルを見張り続ける**こと——
# ネットワーク上のファイルなら 45 秒おきの見回り（引き継ぎ ⑭⑮）が、
# そのウィンドウで次に開く・保存する・閉じるまで止まらない。
# ----------------------------------------------------------------------
def test_moving_a_tab_to_another_window_moves_the_watch(
    window: MainWindow, make_window, sample: Path, tmp_path: Path
) -> None:
    """別のウィンドウへ移したら、監視も移る（元には残らない）。"""
    other = tmp_path / "残るほう.txt"
    other.write_text("残る\n", encoding="utf-8")
    window.open_path(other)
    editor = window.open_path(sample)
    target = make_window()
    assert str(sample) in window._file_watcher.files()

    window._move_tab_to_window(editor, target)

    assert str(sample) not in window._file_watcher.files()
    assert str(sample) in target._file_watcher.files()
    # 元のウィンドウに残したタブの監視は、巻き込まれて外れたりしない。
    assert str(other) in window._file_watcher.files()


def test_tearing_off_a_tab_moves_the_watch(
    window: MainWindow, sample: Path, tmp_path: Path
) -> None:
    """新しいウィンドウへ切り離したときも同じ（対の確認）。"""
    other = tmp_path / "残るほう.txt"
    other.write_text("残る\n", encoding="utf-8")
    window.open_path(other)
    editor = window.open_path(sample)
    assert str(sample) in window._file_watcher.files()

    new_window = window._tear_off_tab_to_new_window(editor, window.pos())
    assert new_window is not None

    assert str(sample) not in window._file_watcher.files()
    assert str(sample) in new_window._file_watcher.files()
    assert str(other) in window._file_watcher.files()
    new_window.close()


def test_moving_the_last_network_tab_stops_the_polling_in_the_source(
    window: MainWindow, make_window, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ネットワーク上のタブを渡したら、元のウィンドウの見回りは止まる。

    45 秒おきの ``stat`` は、見るべきタブが無ければ 1 回も走らせない
    （`tests/test_network_file_polling.py` の方針）。渡したあとも回り
    続けると、そのウィンドウは**もう持っていないファイル**のために
    起き続ける。
    """
    monkeypatch.setattr(
        MainWindow, "_is_network_path", lambda self, path: str(path) == str(sample)
    )
    editor = window.open_path(sample)
    target = make_window()
    assert window._network_poll_timer.isActive()

    window._move_tab_to_window(editor, target)

    assert not window._network_poll_timer.isActive()
    assert target._network_poll_timer.isActive()
