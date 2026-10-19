"""ネットワーク/FUSE 上のファイルの外部変更を「自分から見に行く」仕組み（引き継ぎ ⑭）.

背景（実機で起きたこと、2026-10-12）
------------------------------------
NAS (CIFS) 上の Dropbox 同期フォルダのファイルを開いている最中に、**別の
マシンがそのファイルを書き換えても「再読み込みしますか？」が出なかった**。
原因は ``QFileSystemWatcher``（Linux では inotify）の原理的な限界で、
**カーネルがこのマシンで観測した変更しか拾えない**ためだった
（`file_watch.py` の作りの問題ではない）。

そこで ``file_watch.py`` に「ネットワーク/FUSE 上のファイルだけを、
:data:`~editor_app.file_watch.NETWORK_POLL_INTERVAL_MS` ごとに ``stat`` して
見に行く」道を足した。ここで見張るのは次の 2 つである。

1. **ちゃんと気づくか** — ネットワーク上のファイルが書き換わったら、
   既存の再読み込み確認の流れ（``_pending_reload_paths`` →
   ``_reload_check_timer`` → ``_maybe_reload_changed_file``）に
   **合流する**こと。新しい確認の道を作っていないこと。
2. **ローカルを巻き込まないか** — ここがいちばんの勘所。``stat`` を
   増やすと「タブが増えるほどファイルを開くのが遅くなる」という過去の
   不具合（`docs/performance-notes.md`、`tests/test_open_filesystem_cost.py`）を
   繰り返す。**ローカルのタブしか無いときは ``stat`` が 1 回も走らない**
   ことを、実際に ``os.stat`` を数えて確かめる。

ヘッドレスの CI に NAS は無いので、「このパスはネットワーク上」は
:meth:`~editor_app.file_watch.FileWatchMixin._is_network_path` を差し替えて
偽装する（**偽装できるようにメソッドへ切り出してある**）。判定そのもの
（``/proc/mounts`` の読み方）は、作ったマウント表を食わせて別に見る。

タイマーの発火そのもの（45 秒待つ）は試さず、:meth:`_poll_network_files` を
直接呼ぶ。待つテストは遅いうえに不安定で、見張りたいのは「発火したときに
何をするか」だからである（`tests/test_file_reload.py` と同じ方針）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox

from editor_app import file_watch
from editor_app.fileio import file_signature
from editor_app.main_window import MainWindow


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "共有メモ.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    return path


def _pretend_network(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """渡したパスだけを「ネットワーク/FUSE 上」として扱わせる。"""
    wanted = {str(path) for path in paths}
    monkeypatch.setattr(
        MainWindow, "_is_network_path", lambda self, path: str(path) in wanted
    )


def _pretend_network_mount(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """そのフォルダを「CIFS のマウント点」として扱わせる。

    :func:`_pretend_network` との違いが大事。あちらは
    :meth:`~editor_app.file_watch.FileWatchMixin._is_network_path` ごと
    差し替えてしまうので、**判定の中身は 1 行も通らない**（「どのパスも
    ネットワークと見ない」という変異が生き残る＝実機の不具合そのものを
    見逃す）。こちらは**マウント表だけ**を差し替えるので、
    ``/proc/mounts`` の読み方から確認ダイアログまで、本物の道が通る。
    """
    monkeypatch.setattr(
        file_watch,
        "_mount_table_cache",
        ((str(directory), True), ("/", False)),
    )


def _patch_prompt(
    monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton
) -> list[str]:
    """再読み込みの確認ダイアログを差し替え、出た本文を記録する。

    差し替え先は ``editor_app.file_watch``（中核がそこにあるため）。
    **ここを間違えると本物のモーダルダイアログが開き、応答する人のいない
    ヘッドレスのテストがそのまま固まる。**
    """
    messages: list[str] = []

    def fake(_parent, _icon, _title, message, *_args, **_kwargs):
        messages.append(message)
        return answer

    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)
    return messages


def _rewrite(path: Path, text: str) -> None:
    """外部のマシンが書き換えた、という状況を作る。

    中身の**長さも変える**ようにしている。見分け札は
    ``(更新日時 ns, サイズ)`` の対なので、長さが変わっていれば
    更新日時の分解能が粗い環境でも必ず違いが出る。
    """
    path.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------
# 1. ネットワーク上のファイルの変更に気づき、既存の流れへ合流する
# ----------------------------------------------------------------------
def test_poll_detects_change_and_joins_the_existing_reload_flow(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ポーリングで気づいた変更が、既存の再読み込み確認からそのまま読み直される。"""
    editor = window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    _rewrite(sample, "別のマシンが書き換えた、もっと長い内容\n")
    window._poll_network_files()

    # 「自分で確認ダイアログを出す」のではなく、既存の入口に積むこと。
    assert str(sample) in window._pending_reload_paths
    assert window._reload_check_timer.isActive()
    assert messages == []  # まだ何も訊いていない（デバウンスの手前）

    window._process_pending_reloads()

    assert len(messages) == 1
    assert "外部で変更されました" in messages[0]
    assert editor.toPlainText() == "別のマシンが書き換えた、もっと長い内容\n"
    assert editor.is_modified is False


def test_poll_detects_a_change_through_the_real_mount_check(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**判定を偽装せず**、マウント表だけを差し替えて端から端まで通す。

    他の多くのテストは ``_is_network_path`` ごと差し替えて「見回りの
    振る舞い」を見ているが、それでは**判定そのものが黙っていても緑**に
    なる（「どのパスもネットワーク上と見ない」＝今回の不具合に戻る変異が
    生き残る）。ここだけは ``/proc/mounts`` の読み方からダイアログまで、
    本物の道を通す。
    """
    _pretend_network_mount(monkeypatch, sample.parent)
    editor = window.open_path(sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    # 本物の判定で「ネットワーク上」と分かるので、見回りが始まっている。
    assert window._network_poll_timer.isActive() is True

    _rewrite(sample, "別のマシンが書き換えた、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()

    assert len(messages) == 1
    assert editor.toPlainText() == "別のマシンが書き換えた、もっと長い内容\n"


def test_local_file_is_left_alone_by_the_real_mount_check(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**対照**: 同じ道で、ローカル扱いのフォルダは見回らない。

    上のテストと対にしてある。上だけだと「常にネットワークと見る」実装
    （＝遅いフォルダを無駄に stat する）でも緑になってしまう。
    """
    monkeypatch.setattr(file_watch, "_mount_table_cache", (("/", False),))
    path = tmp_path / "ローカル.txt"
    path.write_text("ローカルのファイル\n", encoding="utf-8")
    window.open_path(path)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    assert window._network_poll_timer.isActive() is False

    _rewrite(path, "書き換わったが、inotify に任せる内容\n")
    window._poll_network_files()
    window._process_pending_reloads()

    assert messages == []


# ----------------------------------------------------------------------
# タイマーが実際に見回りへつながっているか
# ----------------------------------------------------------------------
def test_the_poll_timer_is_wired_to_the_check(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """タイマーが発火したら、本当に見回りが走ること。

    45 秒待つ代わりに ``timeout`` を自分で出す。結線は 1 行きりなので、
    **外しても他のテストは全部緑のまま通る**（＝「45 秒ごとに何もしない」
    状態に黙って戻る）。
    """
    _pretend_network_mount(monkeypatch, sample.parent)
    window.open_path(sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    _rewrite(sample, "タイマーの発火で気づくべき、もっと長い内容\n")
    window._network_poll_timer.timeout.emit()
    window._process_pending_reloads()

    assert len(messages) == 1


def test_the_poll_interval_is_between_30_and_60_seconds(window: MainWindow) -> None:
    """見回りの間隔が、利用者と決めた 30〜60 秒の範囲にあること。

    短すぎれば遅いフォルダを叩きすぎ、長すぎれば「気づかない」に近づく。
    定数で切り出してあるので、実機で重ければここを伸ばせる。
    """
    assert 30_000 <= file_watch.NETWORK_POLL_INTERVAL_MS <= 60_000
    assert window._network_poll_timer.interval() == file_watch.NETWORK_POLL_INTERVAL_MS
    # 繰り返し発火すること（単発だと 1 回見たら終わってしまう）。
    assert window._network_poll_timer.isSingleShot() is False


def test_poll_is_quiet_when_the_file_has_not_changed(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """何も変わっていなければ、ポーリングは何もしない（毎分ダイアログを出さない）。"""
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    window._poll_network_files()
    window._poll_network_files()

    assert window._pending_reload_paths == set()
    assert window._reload_check_timer.isActive() is False
    assert messages == []


def test_poll_notices_a_change_made_while_the_editor_was_untouched(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ大きさでも、更新日時が動いていれば気づく（札は対で見ている）。"""
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    # 中身の長さは変えず、更新日時だけを 10 秒先へ動かす。
    stat = sample.stat()
    os.utime(sample, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    window._poll_network_files()

    assert str(sample) in window._pending_reload_paths


# ----------------------------------------------------------------------
# 2. ローカルのタブしか無いときは stat が 1 回も走らない（性能の肝）
# ----------------------------------------------------------------------
class _StatCounter:
    """``os.stat`` / ``os.lstat`` の呼び出し回数を数える。

    数え方は `tests/test_open_filesystem_cost.py` と同じにしてある
    （あちらは「開くときに増えないこと」、ここは「ポーリングで 1 回も
    走らないこと」を見る）。
    """

    def __init__(self) -> None:
        self.count = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_stat, real_lstat = os.stat, os.lstat

        def counted_stat(*args, **kwargs):
            self.count += 1
            return real_stat(*args, **kwargs)

        def counted_lstat(*args, **kwargs):
            self.count += 1
            return real_lstat(*args, **kwargs)

        monkeypatch.setattr(os, "stat", counted_stat)
        monkeypatch.setattr(os, "lstat", counted_lstat)


def test_local_only_tabs_are_never_stat_ed_by_polling(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ローカルのタブだけのときは、ポーリングが ``stat`` を 1 回も呼ばない。

    **この機能でいちばん壊してはいけないところ。** ローカルは inotify で
    足りているので、見に行くだけ無駄——しかも rclone/FUSE のような遅い
    ファイルシステムでは ``stat`` 1 回がそのまま体感の遅さになる。

    判定は**本物**（``/proc/mounts`` を読む
    :meth:`~editor_app.file_watch.FileWatchMixin._is_network_path`）を
    使う。偽装してしまうと、「本物の判定でローカルがローカルと分かるか」
    という肝心のところが見張れない。
    """
    for index in range(5):
        path = tmp_path / f"local{index}.txt"
        path.write_text(f"{index} 番目\n", encoding="utf-8")
        assert window.open_path(path) is not None

    counter = _StatCounter()
    with monkeypatch.context() as patch:
        counter.install(patch)
        window._poll_network_files()

    assert counter.count == 0
    assert window._pending_reload_paths == set()


def test_polling_timer_stays_off_while_only_local_files_are_open(
    window: MainWindow, tmp_path: Path
) -> None:
    """ローカルのタブだけなら、そもそもタイマーが動いていない。

    「対象が空ならタイマーごと止める」ことで、ローカルだけで使っている
    利用者にはこの機能による負荷が**まったく掛からない**ようにしてある。
    """
    path = tmp_path / "ローカル.txt"
    path.write_text("ローカルのファイル\n", encoding="utf-8")
    window.open_path(path)

    assert window._network_poll_timer.isActive() is False


def test_polling_timer_starts_and_stops_with_the_network_tab(
    window: MainWindow, sample: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ネットワーク上のタブを開けばタイマーが動き、閉じれば止まる。"""
    _pretend_network(monkeypatch, sample)
    local = tmp_path / "ローカル.txt"
    local.write_text("ローカル\n", encoding="utf-8")

    window.open_path(local)
    assert window._network_poll_timer.isActive() is False

    editor = window.open_path(sample)
    assert window._network_poll_timer.isActive() is True

    window.tabs.removeTab(window.tabs.indexOf(editor))
    window._refresh_file_watches()
    assert window._network_poll_timer.isActive() is False


# ----------------------------------------------------------------------
# 3. ウィンドウがアクティブになったときにも 1 回走る
# ----------------------------------------------------------------------
def test_activating_the_window_runs_one_check(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """他のアプリから戻ってきた瞬間にも見に行く（45 秒待たせないため）。"""
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    _rewrite(sample, "他のアプリで直してきた、もっと長い内容\n")
    window.activateWindow()
    assert window.isActiveWindow()
    window.changeEvent(QEvent(QEvent.Type.ActivationChange))

    assert str(sample) in window._pending_reload_paths


def test_other_change_events_do_not_run_a_check(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """アクティブ化**以外**の ``changeEvent`` では見に行かない。

    ``changeEvent`` はテーマ変更・フォント変更・有効無効の切り替えなど
    さまざまな理由で飛んでくる。そのすべてで ``stat`` していては、
    「間隔を空けて見に行く」という設計が崩れる。
    """
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    _rewrite(sample, "書き換わっているが、まだ気づかなくてよい内容\n")

    window.changeEvent(QEvent(QEvent.Type.StyleChange))
    window.changeEvent(QEvent(QEvent.Type.FontChange))

    assert window._pending_reload_paths == set()


# ----------------------------------------------------------------------
# 4. 閉じたウィンドウは、もう何も訊いてこない
# ----------------------------------------------------------------------
def test_closing_the_window_stops_the_polling_timer(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """閉じたウィンドウが 45 秒後に「再読み込みしますか？」と言い出さない。

    デバウンスのタイマーで同じ踏み方を既にしている（閉じたはずの
    ウィンドウが後からダイアログを出す）。ポーリングのタイマーは
    **一度きりではなく何度でも**発火するので、止め忘れたときの壊れ方は
    そちらより悪い。
    """
    _pretend_network(monkeypatch, sample)
    window.open_path(sample)
    assert window._network_poll_timer.isActive() is True

    window.close()

    assert window._network_poll_timer.isActive() is False
    assert window._pending_reload_paths == set()
    assert window._declined_reload_signatures == {}


def test_polling_a_closed_window_does_not_ask(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """閉じたあとに発火してしまっても、確認は出ない（取りこぼしへの保険）。"""
    _pretend_network(monkeypatch, sample)
    window.open_path(sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    window.close()
    _rewrite(sample, "閉じた後に書き換わった、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()

    assert messages == []


def test_minimized_window_still_polls(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**最小化しているだけ**のウィンドウでは、見張りを止めない。

    上の 2 件（閉じたら黙る）とこれは対になっている。「閉じた」の判定を
    ``isVisible()`` で書くと最小化まで巻き込み、**タスクバーに入れている
    あいだ外部変更に気づかない**——実機でいちばん起こりやすい形で
    不具合そのものに戻る。``isHidden()`` は最小化では ``False`` になる。
    """
    _pretend_network(monkeypatch, sample)
    window.open_path(sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)

    window.showMinimized()
    assert window.isHidden() is False
    _rewrite(sample, "最小化中に書き換わった、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()

    assert len(messages) == 1


# ----------------------------------------------------------------------
# 5. 未保存の変更があるタブでも、既存の流れに乗る
# ----------------------------------------------------------------------
def test_modified_tab_goes_through_the_existing_confirmation(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存のタブでは、既存の「編集は破棄されます」の確認がそのまま出る。

    ポーリング用に新しい分岐を作っていないことの確認。断れば本文は
    そのままで、未保存マークも落ちない。
    """
    editor = window.open_path(sample)
    editor.setPlainText("編集中の内容\n")
    editor.set_modified(True)  # 未保存の編集がある状態にする
    assert editor.is_modified is True
    _pretend_network(monkeypatch, sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    _rewrite(sample, "別のマシンが書き換えた、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()

    assert len(messages) == 1
    assert "未保存の変更があります" in messages[0]
    assert editor.toPlainText() == "編集中の内容\n"
    assert editor.is_modified is True


# ----------------------------------------------------------------------
# 断られた確認を、45 秒ごとに訊き直さない（ただし保存の見張りは緩めない）
# ----------------------------------------------------------------------
def test_declined_reload_is_not_asked_again_for_the_same_disk_state(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「いいえ」と答えたら、ディスクがそのままの間は訊き直さない。

    監視 (inotify) は「変わった瞬間」に 1 回来るので断れば終わりだが、
    ポーリングは「今のディスクが本文と違う」ことを根拠にするため、
    覚えておかないと **45 秒ごとに同じ確認を出し続ける**。
    """
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    _rewrite(sample, "別のマシンが書き換えた、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()
    assert len(messages) == 1

    window._poll_network_files()
    window._process_pending_reloads()
    assert len(messages) == 1  # 2 度目は訊かない


def test_declined_reload_asks_again_after_another_change(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一度断っても、**さらに書き換わったら**もう一度訊く。

    「断られた」を覚えるのは、あくまで**その状態のディスク**について。
    覚えたまま黙り続けると、不具合そのもの（外部変更に気づかない）に戻る。
    """
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    _rewrite(sample, "1 回目の書き換え\n")
    window._poll_network_files()
    window._process_pending_reloads()
    assert len(messages) == 1

    _rewrite(sample, "2 回目の書き換え。長さも変えてある\n")
    window._poll_network_files()
    window._process_pending_reloads()
    assert len(messages) == 2


def test_declining_a_reload_does_not_advance_the_disk_signature(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """断っても、**保存直前のチェックは外部変更を見逃さない**。

    ここは安全にかかわる。「もう訊かない」を
    :meth:`~editor_app.editor.EditorWidget.remember_disk_state` で実現すると、
    その札は保存直前のチェック
    (:meth:`~editor_app.file_commands.FileCommandsMixin._check_external_change`)
    も見ているため、**断っただけの利用者が他のマシンの編集を黙って
    上書きしてしまう**。ポーリングを黙らせる札は、タブが知っている
    ディスクの札とは別に持たなければならない。
    """
    editor = window.open_path(sample)
    opened_signature = editor.disk_signature
    _pretend_network(monkeypatch, sample)
    _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    _rewrite(sample, "別のマシンが書き換えた、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()

    # タブが知っているディスクの状態は、開いたときのまま（進んでいない）。
    assert editor.disk_signature == opened_signature
    assert editor.disk_signature != file_signature(sample)


def test_declined_state_is_forgotten_when_the_tab_closes(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """タブを閉じれば「断られた」も忘れる（開き直したらまた訊いてよい）。"""
    editor = window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    _rewrite(sample, "別のマシンが書き換えた、もっと長い内容\n")
    window._poll_network_files()
    window._process_pending_reloads()
    assert str(sample) in window._declined_reload_signatures

    window.tabs.removeTab(window.tabs.indexOf(editor))
    window._refresh_file_watches()

    assert window._declined_reload_signatures == {}


# ----------------------------------------------------------------------
# 長い処理の最中は見に行かない
# ----------------------------------------------------------------------
def test_polling_is_deferred_during_a_long_operation(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全置換のような長い処理の最中は、ポーリングも控える。

    途中で本文を丸ごと入れ替えると、置換の残りが別の本文に対して走る
    （引き継ぎ ⑨ でバッチ化したときに起こりうるようになった）。
    """
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)
    _rewrite(sample, "処理中に書き換わった、もっと長い内容\n")

    window._long_operation_active = True
    try:
        window._poll_network_files()
        assert window._pending_reload_paths == set()
    finally:
        window._long_operation_active = False

    window._poll_network_files()
    assert str(sample) in window._pending_reload_paths


def test_polling_does_not_stat_during_a_long_operation(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """長い処理の最中は ``stat`` もしない（止めるのは入口であること）。"""
    window.open_path(sample)
    _pretend_network(monkeypatch, sample)

    counter = _StatCounter()
    window._long_operation_active = True
    try:
        with monkeypatch.context() as patch:
            counter.install(patch)
            window._poll_network_files()
    finally:
        window._long_operation_active = False

    assert counter.count == 0


# ----------------------------------------------------------------------
# 無題タブはポーリングの対象にならない
# ----------------------------------------------------------------------
def test_untitled_tabs_are_not_polled(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """まだ保存していないタブは見に行かない（見に行く先が無い）。"""
    monkeypatch.setattr(MainWindow, "_is_network_path", lambda self, path: True)

    counter = _StatCounter()
    with monkeypatch.context() as patch:
        counter.install(patch)
        window._poll_network_files()

    assert counter.count == 0
    assert window._network_poll_timer.isActive() is False


# ----------------------------------------------------------------------
# マウント種別の判定（/proc/mounts の読み方）
# ----------------------------------------------------------------------
#: ``/proc/mounts`` の体裁をなぞった作りもの。利用者の実機に合わせて
#: ``/mnt/online`` を CIFS、``/mnt/gdrive`` を rclone (FUSE) にしてある。
FAKE_MOUNTS = """\
proc /proc proc rw,nosuid 0 0
/dev/sda2 / ext4 rw,relatime 0 0
/dev/sda1 /boot/efi vfat rw 0 0
systemd-1 /mnt/online autofs rw,relatime,fd=60,direct 0 0
//100.93.179.89/LAMP /mnt/online cifs rw,actimeo=5 0 0
rclone /mnt/gdrive fuse.rclone rw,nosuid,nodev 0 0
server:/export /srv/nfs nfs4 rw 0 0
/dev/sdb1 /mnt/online/local-disk ext4 rw 0 0
//nas/share /mnt/my\\040disk cifs rw 0 0
ntfs-3g /mnt/windows fuseblk rw 0 0
"""


@pytest.fixture
def mount_table(tmp_path: Path) -> tuple[tuple[str, bool], ...]:
    path = tmp_path / "mounts"
    path.write_text(FAKE_MOUNTS, encoding="utf-8")
    return file_watch.read_mount_table(path)


def _is_network(table, path: str, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setattr(file_watch, "_mount_table_cache", table)
    return file_watch.path_is_on_network_mount(path)


@pytest.mark.parametrize(
    ("path", "expected", "why"),
    [
        ("/mnt/online/仕事/メモ.txt", True, "CIFS (NAS)。実機で不具合が出た形"),
        ("/mnt/online", True, "マウント点そのもの"),
        ("/mnt/gdrive/Drive/メモ.txt", True, "rclone (FUSE)"),
        ("/srv/nfs/共有/メモ.txt", True, "NFS"),
        ("/home/someone/メモ.txt", False, "ローカル (ext4)。inotify で足りる"),
        ("/boot/efi/x", False, "ローカル (vfat)"),
        ("/mnt/windows/x.txt", False, "fuseblk はローカルのブロックデバイス"),
        (
            "/mnt/online/local-disk/x.txt",
            False,
            "NAS の中に重ねたローカル（長い方のマウント点が勝つ）",
        ),
        ("/mnt/onlineXYZ/x.txt", False, "名前が前方一致するだけの別フォルダ"),
        (
            "/mnt/my disk/x.txt",
            True,
            "空白を含むマウント点 (\\040) を復元できている。復元しないと "
            "このフォルダだけ判定が外れ、NAS なのに見回られない",
        ),
        (
            "/mnt/my\\040disk/x.txt",
            False,
            "エスケープのまま書いたパスは別物（復元後の名前で比べている）",
        ),
    ],
)
def test_network_mount_detection(
    mount_table, path: str, expected: bool, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/proc/mounts`` からの判定が、ネットワークとローカルを取り違えない。

    取り違えたときの壊れ方は左右で違う。**ネットワークをローカルと見れば、
    外部変更に気づかない**（今回の不具合そのもの）。**ローカルを
    ネットワークと見れば、無駄な ``stat`` で遅くなる**。
    """
    assert _is_network(mount_table, path, monkeypatch) is expected, why


def test_longest_mount_point_wins(mount_table) -> None:
    """マウント表は長いマウント点から順に並ぶ（前方一致の判定がそれに依る）。"""
    lengths = [len(mount_point) for mount_point, _ in mount_table]
    assert lengths == sorted(lengths, reverse=True)


def test_automount_placeholder_does_not_hide_the_real_mount(mount_table) -> None:
    """同じマウント点が 2 行あるときは、**後の行（実体）**を採る。

    `x-systemd.automount` を付けた NAS は ``/proc/mounts`` に 2 行出る：
    受け口の ``autofs`` が先、実際に繋がっている ``cifs`` が後。
    **先勝ちにすると autofs を見てローカルと判定し、NAS のファイルが
    見回りから外れる**（2026-10-19 に実機で発生。外部変更に気づかず、
    「再読み込みしますか？」が出なかった）。長さで並べ替えるだけでは
    同じ長さなので直らない、というのがこの穴の正体。
    """
    assert [entry for entry in mount_table if entry[0] == "/mnt/online"] == [
        ("/mnt/online", True)
    ]


def test_unreadable_mount_table_falls_back_to_local(tmp_path: Path) -> None:
    """``/proc/mounts`` が読めない環境でも落ちず、「全部ローカル」に倒れる。

    倒れた先が「ローカル」なら、動きは今までどおり（inotify だけ）になる。
    """
    assert file_watch.read_mount_table(tmp_path / "ありません") == ()


def test_malformed_mount_lines_are_skipped(tmp_path: Path) -> None:
    """列の足りない行は読み飛ばす（1 行壊れていても判定全体を失わない）。"""
    path = tmp_path / "mounts"
    path.write_text(
        "壊れた行\n\n//nas/share /mnt/nas cifs rw 0 0\n", encoding="utf-8"
    )
    assert file_watch.read_mount_table(path) == (("/mnt/nas", True),)


def test_mount_table_is_read_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """マウント表は控えから使う（タブを開くたびに ``/proc/mounts`` を読まない）。"""
    reads: list[int] = []

    def counted(*_args, **_kwargs):
        reads.append(1)
        return ()

    monkeypatch.setattr(file_watch, "_mount_table_cache", None)
    monkeypatch.setattr(file_watch, "read_mount_table", counted)

    file_watch.mount_table()
    file_watch.mount_table()
    file_watch.mount_table()

    assert len(reads) == 1


def test_real_mount_table_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """本物の ``/proc/mounts`` も読めること（作りものだけで満足しない）。

    この環境にネットワークマウントは無いので、中身までは縛れない。
    **ここで見るのは「読めて、`/` が載っていて、落ちない」こと**だけ。
    """
    monkeypatch.setattr(file_watch, "_mount_table_cache", None)
    table = file_watch.mount_table()

    assert table  # 1 行も読めなければ、判定は常にローカルへ倒れる
    assert any(mount_point == "/" for mount_point, _ in table)
    assert file_watch.path_is_on_network_mount(__file__) is False


def test_poll_after_qt_teardown_is_ignored(
    qtbot, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qt 側 (C++) が片付いた後に 45 秒の見回りが発火しても、落ちないこと。

    ``closeEvent`` を通らずにウィンドウが片付けられる経路（テストの後始末
    など）では、タイマーを止める手が回らない。そのまま ``self.isHidden()``
    へ触ると ``Internal C++ object (MainWindow) already deleted`` の
    ``RuntimeError`` になる。

    **デバウンスの側で実際に踏んだ形**（``tests/test_file_reload.py`` の
    ``test_pending_reload_after_teardown_is_ignored``）だが、**こちらの
    壊れ方はそちらより悪い**。あちらは「変更が届いたとき 1 回」だけだが、
    こちらは :data:`~editor_app.file_watch.NETWORK_POLL_INTERVAL_MS`
    ごとに**何度でも**発火するので、片付け漏れが 1 つあるだけで
    45 秒おきに例外が出続ける。
    """
    import shiboken6
    from PySide6.QtCore import QCoreApplication

    # 自分で片付けるので ``window``（pytest-qt に後始末を任せるフィクスチャ）は
    # 使わない。先に消してあると、pytest-qt の後始末が落ちる。
    window = MainWindow()
    window.show()
    _pretend_network(monkeypatch, sample)
    window.open_path(sample)
    messages = _patch_prompt(monkeypatch, QMessageBox.StandardButton.Yes)
    _rewrite(sample, "片付けた後に書き換わった、もっと長い内容\n")

    # pytest-qt の後始末と同じ形で Qt 側だけ消す（Python 側の窓口は残る）。
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert shiboken6.isValid(window) is False

    window._poll_network_files()  # 例外が飛ばないことが見張りたいこと

    assert messages == []


def test_an_empty_mount_table_decides_everything_is_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """マウント表が空のときは、**判定そのもの**が「ローカル」に倒れること。

    ``/proc`` が無い環境では :func:`~editor_app.file_watch.read_mount_table`
    が空を返す。そこから先、``path_is_on_network_mount`` が
    「どのマウント点にも前方一致しなかった」ときに何を返すかまでが
    この機能の倒れ方を決める。

    **倒れる先を取り違えると、倒れ方は「今までどおり」では済まない**。
    ここで ``True`` に倒れると、``/proc`` の無い環境では**開いている
    すべてのタブ**が 45 秒ごとに ``stat`` され、
    `docs/performance-notes.md` の「タブが増えるほど重くなる」不具合を
    そのまま呼び戻す。

    上の ``test_unreadable_mount_table_falls_back_to_local`` が見ているのは
    「表が空になること」までで、**そこから先の判定**はこちらで見る。
    """
    assert _is_network((), "/mnt/online/仕事/メモ.txt", monkeypatch) is False


def test_a_relative_path_is_not_taken_for_a_network_file(
    mount_table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相対パスは、どのマウント点にも前方一致しない＝ローカル扱いになること。

    タブの ``path`` は :func:`~editor_app.fileio.resolve_path` を通した
    絶対パスなので、普段ここへは来ない。それでも倒れる先を固定しておく
    のは、``/`` が必ず前方一致する本物の ``/proc/mounts`` では
    「どれにも当たらなかったときの戻り値」が**一度も通らない**ため
    （＝取り違えても誰も気づけない）。
    """
    assert _is_network(mount_table, "メモ.txt", monkeypatch) is False
