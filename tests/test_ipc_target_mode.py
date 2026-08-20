"""外部から開くファイルの読み込み先（最初/最後/最後にアクティブにしたウィンドウ）のテスト。

実機フィードバックにより追加。ウィンドウのアクティブ化は本番と同じ
``activateWindow()`` で行う（``tests/test_tab_switch_shortcut.py`` と同じ手法）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from editor_app.app import _handle_forwarded_paths
from editor_app.main_window import MainWindow
from editor_app.settings import (
    IPC_TARGET_ACTIVE,
    IPC_TARGET_FIRST,
    IPC_TARGET_LAST,
    load_ipc_target_mode,
)


def _activate(window: MainWindow) -> None:
    """ウィンドウをアクティブにする（offscreen でも実際に切り替わる）。

    ``activateWindow()`` は「アクティブにしてほしい」とプラットフォームへ
    頼むだけなので、``processEvents()`` でその通知を受け取るまで
    ``QApplication.activeWindow()`` は入れ替わらない。**2 つのうち
    どちらが欠けても効かない**ことは実測で確認済み。
    """
    window.activateWindow()
    QApplication.processEvents()


# ----------------------------------------------------------------------
# 設定の永続化
# ----------------------------------------------------------------------
def test_default_ipc_target_mode_is_first() -> None:
    assert load_ipc_target_mode() == IPC_TARGET_FIRST


def test_set_ipc_target_mode_persists_and_syncs_menu_across_windows(
    window: MainWindow, make_window
) -> None:
    other = make_window()

    window.set_ipc_target_mode(IPC_TARGET_ACTIVE)

    assert load_ipc_target_mode() == IPC_TARGET_ACTIVE
    assert window.action_open_target_active.isChecked()
    assert other.action_open_target_active.isChecked()


# ----------------------------------------------------------------------
# 「最後にアクティブにしたウィンドウ」の判定
# ----------------------------------------------------------------------
def test_most_recently_active_window_with_no_activation_is_oldest(
    window: MainWindow, make_window
) -> None:
    """一度もアクティブになっていなければ、生成順で一番古いものになる。"""
    make_window()
    assert MainWindow.most_recently_active_window() is window


def test_most_recently_active_window_follows_activation(
    window: MainWindow, make_window
) -> None:
    other = make_window()

    _activate(other)
    assert MainWindow.most_recently_active_window() is other

    _activate(window)
    assert MainWindow.most_recently_active_window() is window


# ----------------------------------------------------------------------
# _handle_forwarded_paths との統合
# ----------------------------------------------------------------------
def test_forward_opens_in_first_window_by_default(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    other = make_window()
    _activate(other)  # アクティブなのは other でも、既定は「最初」。

    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    _handle_forwarded_paths([str(sample)])

    assert window.current_editor().path == sample.resolve()
    assert other.find_editor_by_path(sample.resolve()) is None


def test_forward_opens_in_last_window_when_configured(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    other = make_window()
    window.set_ipc_target_mode(IPC_TARGET_LAST)

    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    _handle_forwarded_paths([str(sample)])

    assert other.current_editor().path == sample.resolve()
    assert window.find_editor_by_path(sample.resolve()) is None


def test_forward_opens_in_active_window_when_configured(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    other = make_window()
    window.set_ipc_target_mode(IPC_TARGET_ACTIVE)
    _activate(window)  # 最後にアクティブにしたのは最初のウィンドウの方。

    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    _handle_forwarded_paths([str(sample)])

    assert window.current_editor().path == sample.resolve()
    assert other.find_editor_by_path(sample.resolve()) is None


def test_forward_active_mode_picks_the_middle_window(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    """「最後にアクティブにしたウィンドウ」を、最初でも最後でもない所で確かめる。

    上の 3 件は**アクティブにしたウィンドウが「最初のウィンドウ」**だった
    ため、この設定が壊れて既定（最初のウィンドウ）に落ちても**全部緑のまま
    通ってしまった**（34 回目に変異 `app-target-active` が生き残って発覚）。
    利用者がわざわざ選べるようにした設定なので、**最初とも最後とも違う
    ウィンドウ**を選ばせて、3 つの動きを区別できるようにする。
    """
    middle = make_window()
    last = make_window()
    window.set_ipc_target_mode(IPC_TARGET_ACTIVE)
    _activate(middle)

    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    _handle_forwarded_paths([str(sample)])

    assert middle.current_editor().path == sample.resolve()
    # 既定（最初）にも「最後」にも落ちていないこと。
    assert window.find_editor_by_path(sample.resolve()) is None
    assert last.find_editor_by_path(sample.resolve()) is None
