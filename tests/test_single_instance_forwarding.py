"""他プロセスから転送されたファイルパスの処理 (_handle_forwarded_paths) のテスト。

Dolphin 等で別のファイルをダブルクリックしたときに新しいウィンドウが
増えるのではなく、起動中のウィンドウで開く／既に開いていればそのタブ・
ウィンドウを前面に出すだけにする、という実機フィードバックへの対応。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from editor_app.app import _handle_forwarded_paths
from editor_app.main_window import MainWindow


def _activate(window: MainWindow) -> None:
    """ウィンドウをアクティブにする（offscreen でも実際に切り替わる）。

    ``tests/test_ipc_target_mode.py`` と同じ手法。``activateWindow()`` は
    プラットフォームへ頼むだけなので、``processEvents()`` で通知を受け取る
    まで ``QApplication.activeWindow()`` は入れ替わらない。
    """
    window.activateWindow()
    QApplication.processEvents()


def test_new_file_opens_in_primary_window(window: MainWindow, tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    _handle_forwarded_paths([str(sample)])

    assert window.current_editor().path == sample.resolve()


def test_already_open_file_switches_to_existing_tab_without_duplicating(
    window: MainWindow, tmp_path: Path
) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")
    window.open_path(sample)
    window.new_file()  # 別タブへ移動しておく
    before_count = window.tabs.count()

    _handle_forwarded_paths([str(sample)])

    assert window.tabs.count() == before_count  # 重複して開かれていない
    assert window.current_editor().path == sample.resolve()


def test_already_open_file_in_other_window_is_focused_there(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")
    other = make_window()
    other.open_path(sample)
    other_editor_count = other.tabs.count()

    _handle_forwarded_paths([str(sample)])

    assert other.tabs.count() == other_editor_count  # 重複なし
    assert other.current_editor().path == sample.resolve()
    # 元々あったウィンドウ側には新しいタブが増えていない。
    assert window.find_editor_by_path(sample.resolve()) is None


def test_already_open_file_in_other_window_raises_that_window(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    """既に開いているファイルなら、**そのウィンドウ自体**を前面に出す。

    上のテストは「そのタブが選ばれること」しか見ていないため、前面に出す
    相手を取り違えて**別のウィンドウが手前に来ても緑のまま**通っていた
    （34 回目に変異 `app-dedup-raise-owner` が生き残って発覚）。
    利用者から見ると「ダブルクリックしたのに、そのファイルが見えている
    ウィンドウが出てこない」という形で現れる。
    """
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")
    other = make_window()
    other.open_path(sample)
    _activate(window)  # 直前に手前にあったのは、ファイルを持っていない方。

    _handle_forwarded_paths([str(sample)])
    # `raise_window()` の `activateWindow()` も、通知を受け取るまでは効かない。
    QApplication.processEvents()

    assert QApplication.activeWindow() is other


# ----------------------------------------------------------------------
# ファイル指定なしでの再起動（アイコンの再クリック / `tuxpad` の素の実行）
#
# 既存ウィンドウを前面に出すのではなく、新しい空のウィンドウを開く
# （利用者の要望、2026-08-09）。
# ----------------------------------------------------------------------
def test_empty_paths_opens_new_empty_window(window: MainWindow) -> None:
    before_count = window.tabs.count()
    before_windows = MainWindow.open_windows()

    _handle_forwarded_paths([])

    after_windows = MainWindow.open_windows()
    assert len(after_windows) == len(before_windows) + 1
    # 元のウィンドウにはタブが増えていない。
    assert window.tabs.count() == before_count

    new_window = after_windows[-1]
    assert new_window is not window
    assert new_window.isVisible()
    # 増えたウィンドウは無題タブ 1 つだけ。
    assert new_window.tabs.count() == 1
    assert new_window.current_editor().path is None


def test_empty_paths_new_window_follows_app_settings(window: MainWindow) -> None:
    """新しいウィンドウにもアプリ全体の設定（メニューバー表示）が効くこと。"""
    window.set_menu_bar_visible(False)

    _handle_forwarded_paths([])

    new_window = MainWindow.open_windows()[-1]
    assert new_window is not window
    assert not new_window.menuBar().isVisible()


def test_forwarded_file_does_not_add_a_window(window: MainWindow, tmp_path: Path) -> None:
    """ファイル付きの転送は従来どおり（ウィンドウを増やさない）。"""
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")
    before_windows = len(MainWindow.open_windows())

    _handle_forwarded_paths([str(sample)])

    assert len(MainWindow.open_windows()) == before_windows
    assert window.current_editor().path == sample.resolve()


def test_one_unopenable_path_does_not_stop_the_rest(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """開けないパスが混ざっても、後ろのファイルはちゃんと開く。

    ここは**受け取ったパスを順に開くループ**なので、途中の 1 つで例外が
    漏れると残りが黙って捨てられる（ファイルマネージャで複数選択して
    「Tuxpad で開く」したときに、一部しか開かない形で出る）。
    輪になったシンボリックリンクは、パスの正規化の時点で
    ``RuntimeError`` を投げうる代表例。
    """
    loop_a = tmp_path / "loop_a"
    loop_b = tmp_path / "loop_b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    monkeypatch.setattr("editor_app.file_commands.show_message_box", lambda *a, **k: None)

    _handle_forwarded_paths([str(loop_a), str(sample)])

    assert window.current_editor().path == sample.resolve()
