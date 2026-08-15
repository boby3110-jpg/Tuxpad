"""アプリ内アップデート確認の GUI 側 (editor_app/update_check.py) のテスト.

git の操作そのものは `test_updater.py` が本物のリポジトリで見ているので、
ここでは **「結果に応じて、どう尋ねるか」** だけを見る。判断は
``_handle_update_check_result`` に集めてあるので、スレッドを動かさずに
それを直接呼べる。

ダイアログは ``_show_update_message`` を差し替えて注入する
（**本物のモーダルを開くと、答える人のいないヘッドレスでは固まる**）。

ここで押さえたいのは、実機でしか気づけない類の振る舞い:

- 起動時は黙る（毎回「確認できませんでした」が出ると邪魔なので）
- 手動は必ず何か返す（押したのに無反応だと壊れて見える）
- 「後で」を選んだら git を触らない
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MainWindow
from editor_app.settings import (
    load_check_updates_on_startup,
    save_check_updates_on_startup,
)
from editor_app.updater import (
    STATE_DIRTY,
    STATE_FAILED,
    STATE_UNSUPPORTED,
    STATE_UP_TO_DATE,
    STATE_UPDATE_AVAILABLE,
    UpdateResult,
    UpdateStatus,
)


@pytest.fixture
def shown(window: MainWindow, monkeypatch):
    """出たダイアログを記録し、常に「はい」を返すようにする。

    戻り値は ``(icon, title, text)`` が溜まるリスト。
    """
    calls: list[tuple] = []

    def fake(icon, title, text, buttons=None, default_button=None):
        calls.append((icon, title, text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(window, "_show_update_message", fake)
    return calls


def status(state: str, **kwargs) -> UpdateStatus:
    return UpdateStatus(state, **kwargs)


# ----------------------------------------------------------------------
# 起動時は黙る / 手動は必ず答える
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [STATE_UP_TO_DATE, STATE_DIRTY, STATE_FAILED, STATE_UNSUPPORTED],
)
def test_startup_check_stays_silent_without_update(
    window: MainWindow, shown, state: str
) -> None:
    """起動時は、更新が無いとき・失敗したときは何も出さないこと。"""
    window._handle_update_check_result(status(state, detail="理由"), False)
    assert shown == []


@pytest.mark.parametrize(
    "state",
    [STATE_UP_TO_DATE, STATE_DIRTY, STATE_FAILED, STATE_UNSUPPORTED],
)
def test_manual_check_always_reports(window: MainWindow, shown, state: str) -> None:
    """手動のときは、どの結果でも必ず 1 つ出すこと。"""
    window._handle_update_check_result(status(state, detail="理由"), True)
    assert len(shown) == 1


def test_manual_check_up_to_date_says_latest(window: MainWindow, shown) -> None:
    window._handle_update_check_result(status(STATE_UP_TO_DATE), True)
    assert "最新" in shown[0][2]


def test_manual_check_failure_shows_reason(window: MainWindow, shown) -> None:
    """失敗の理由をそのまま見せること（利用者が対処できるように）。"""
    window._handle_update_check_result(
        status(STATE_FAILED, detail="could not read Username"), True
    )
    icon, _title, text = shown[0]
    assert icon == QMessageBox.Icon.Warning
    assert "could not read Username" in text


def test_manual_check_dirty_explains_skip(window: MainWindow, shown) -> None:
    window._handle_update_check_result(
        status(STATE_DIRTY, detail="未コミットの変更があるため、更新を見送りました。"), True
    )
    assert "見送りました" in shown[0][2]


# ----------------------------------------------------------------------
# 更新があるとき
# ----------------------------------------------------------------------


def test_update_available_prompts_on_startup_too(window: MainWindow, shown, monkeypatch) -> None:
    """起動時でも、更新があるときだけは尋ねること。"""
    monkeypatch.setattr(
        "editor_app.update_check.apply_update", lambda *a, **k: UpdateResult(True)
    )
    window._handle_update_check_result(
        status(STATE_UPDATE_AVAILABLE, count=3, target="abc123"), False
    )
    assert "3 件" in shown[0][2]


def test_accepting_update_applies_it(window: MainWindow, shown, monkeypatch) -> None:
    applied: list[tuple] = []

    def fake_apply(repo_root, target):
        applied.append((repo_root, target))
        return UpdateResult(True)

    monkeypatch.setattr("editor_app.update_check.apply_update", fake_apply)
    window._update_repo_root = Path("/どこか/tuxpad")

    window._handle_update_check_result(
        status(STATE_UPDATE_AVAILABLE, count=1, target="abc123"), True
    )

    assert applied == [(Path("/どこか/tuxpad"), "abc123")]
    # 取り込んだあとの案内に「再起動」が入っていること（勝手に再起動しない）。
    assert "再起動" in shown[-1][2]


def test_declining_update_does_not_touch_git(window: MainWindow, monkeypatch) -> None:
    """「後で」を選んだら、git を一切触らないこと。"""
    monkeypatch.setattr(
        window,
        "_show_update_message",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )
    called: list[int] = []
    monkeypatch.setattr(
        "editor_app.update_check.apply_update",
        lambda *a, **k: called.append(1) or UpdateResult(True),
    )

    window._handle_update_check_result(
        status(STATE_UPDATE_AVAILABLE, count=1, target="abc123"), True
    )

    assert called == []


def test_failed_apply_reports_reason(window: MainWindow, shown, monkeypatch) -> None:
    """早送りできなかったときは、理由を出して終わること（force しない）。"""
    monkeypatch.setattr(
        "editor_app.update_check.apply_update",
        lambda *a, **k: UpdateResult(False, detail="Not possible to fast-forward"),
    )

    window._handle_update_check_result(
        status(STATE_UPDATE_AVAILABLE, count=1, target="abc123"), True
    )

    icon, _title, text = shown[-1]
    assert icon == QMessageBox.Icon.Warning
    assert "Not possible to fast-forward" in text


def test_dependency_change_suggests_reinstall(window: MainWindow, shown, monkeypatch) -> None:
    """requirements.txt 等が変わったら、install.sh の再実行を勧めること。"""
    monkeypatch.setattr(
        "editor_app.update_check.apply_update",
        lambda *a, **k: UpdateResult(True, dependencies_changed=True),
    )

    window._handle_update_check_result(
        status(STATE_UPDATE_AVAILABLE, count=1, target="abc123"), True
    )

    assert "install.sh" in shown[-1][2]


# ----------------------------------------------------------------------
# 起動時チェックの入口
# ----------------------------------------------------------------------


def test_startup_check_skipped_when_setting_off(window: MainWindow, monkeypatch) -> None:
    started: list[bool] = []
    monkeypatch.setattr(
        window, "_start_update_check", lambda **kwargs: started.append(kwargs["manual"])
    )
    save_check_updates_on_startup(False)

    window.check_for_updates_on_startup()

    assert started == []


def test_startup_check_runs_when_setting_on(window: MainWindow, monkeypatch) -> None:
    started: list[bool] = []
    monkeypatch.setattr(
        window, "_start_update_check", lambda **kwargs: started.append(kwargs["manual"])
    )
    window._update_repo_root = Path("/どこか/tuxpad")
    save_check_updates_on_startup(True)

    window.check_for_updates_on_startup()

    assert started == [False]


def test_startup_check_skipped_outside_git(window: MainWindow, monkeypatch) -> None:
    """git 管理外の導入では、起動時に何もしないこと（静かに無効）。"""
    started: list[bool] = []
    monkeypatch.setattr(
        window, "_start_update_check", lambda **kwargs: started.append(kwargs["manual"])
    )
    window._update_repo_root = None

    window.check_for_updates_on_startup()

    assert started == []


def test_manual_check_outside_git_explains(window: MainWindow, shown) -> None:
    """git 管理外でも、手動で押されたら黙らずに理由を返すこと。"""
    window._update_repo_root = None
    window.check_for_updates_manually()
    assert "git" in shown[0][2]


def test_manual_check_starts_the_check(window: MainWindow, monkeypatch) -> None:
    started: list[bool] = []
    monkeypatch.setattr(
        window, "_start_update_check", lambda **kwargs: started.append(kwargs["manual"])
    )
    window._update_repo_root = Path("/どこか/tuxpad")

    window.check_for_updates_manually()

    assert started == [True]


def test_second_manual_check_while_running_is_refused(
    window: MainWindow, shown, monkeypatch
) -> None:
    """確認中にもう一度押されても、二重に走らせないこと。"""
    started: list[bool] = []
    monkeypatch.setattr(
        window, "_start_update_check", lambda **kwargs: started.append(kwargs["manual"])
    )
    window._update_repo_root = Path("/どこか/tuxpad")
    window._update_check_running = True

    window.check_for_updates_manually()

    assert started == []
    assert shown  # 「確認しています」と返すこと


def test_result_clears_running_flag(window: MainWindow, shown) -> None:
    """結果が返ったら、次の確認ができる状態に戻ること。"""
    window._update_check_running = True
    window._handle_update_check_result(status(STATE_UP_TO_DATE), True)
    assert window._update_check_running is False


# ----------------------------------------------------------------------
# 設定「起動時に更新を確認する」
# ----------------------------------------------------------------------


def test_check_on_startup_defaults_to_on(window: MainWindow) -> None:
    assert load_check_updates_on_startup() is True
    assert window.action_check_updates_on_startup.isChecked() is True


def test_toggling_setting_persists_and_syncs_all_windows(
    window: MainWindow, make_window
) -> None:
    """テーマ等と同じく、アプリ全体で共有し、全ウィンドウに反映すること。"""
    other = make_window()

    window.set_check_updates_on_startup(False)

    assert load_check_updates_on_startup() is False
    assert window.action_check_updates_on_startup.isChecked() is False
    assert other.action_check_updates_on_startup.isChecked() is False


def test_new_window_reflects_saved_setting(make_window) -> None:
    save_check_updates_on_startup(False)
    win = make_window()
    assert win.action_check_updates_on_startup.isChecked() is False


# ----------------------------------------------------------------------
# 別スレッドで走らせる配線（GUI を固めないこと）
# ----------------------------------------------------------------------


def test_check_runs_off_the_gui_thread(window: MainWindow, qtbot, monkeypatch) -> None:
    """``git fetch`` が GUI スレッド以外で動き、結果はシグナルで戻ること。

    確認が同期だと、通信が沈黙している間ウィンドウが固まる（実機で
    「起動が固まる」として出る類の不具合）。実際に別スレッドで呼ばれて
    いることを、スレッド ID の違いで確かめる。
    """
    import threading

    gui_thread = threading.get_ident()
    ran_on: list[int] = []

    def fake_check(_repo_root):
        ran_on.append(threading.get_ident())
        return UpdateStatus(STATE_UP_TO_DATE)

    monkeypatch.setattr("editor_app.update_check.check_for_updates", fake_check)
    received: list[tuple] = []
    monkeypatch.setattr(
        window,
        "_handle_update_check_result",
        lambda status_, manual: received.append((status_.state, manual)),
    )
    # シグナルの接続先は _setup_update_check で結んであるので、差し替えた
    # メソッドが呼ばれるよう繋ぎ直す。
    window._update_signals.finished.disconnect()
    window._update_signals.finished.connect(window._handle_update_check_result)

    window._start_update_check(manual=True)

    qtbot.waitUntil(lambda: bool(received), timeout=5000)
    assert ran_on and ran_on[0] != gui_thread
    assert received == [(STATE_UP_TO_DATE, True)]


def test_worker_failure_becomes_a_failed_status(window: MainWindow, qtbot, monkeypatch) -> None:
    """予期しない例外でも、スレッドが黙って死なずに結果が返ること。"""

    def boom(_repo_root):
        raise RuntimeError("想定外")

    monkeypatch.setattr("editor_app.update_check.check_for_updates", boom)
    received: list[tuple] = []
    monkeypatch.setattr(
        window,
        "_handle_update_check_result",
        lambda status_, manual: received.append((status_.state, status_.detail)),
    )
    window._update_signals.finished.disconnect()
    window._update_signals.finished.connect(window._handle_update_check_result)

    window._start_update_check(manual=True)

    qtbot.waitUntil(lambda: bool(received), timeout=5000)
    assert received == [(STATE_FAILED, "想定外")]


def test_show_update_message_asks_dialogs_with_this_window_as_parent(
    window: MainWindow, monkeypatch
) -> None:
    """ダイアログの出し口が、**このウィンドウを親にして**共通の出し方へ渡すこと。

    他のテストはここを差し替えて注入するので、**この 1 本だけが本体を通る**。
    親を渡し損ねると、複数ウィンドウを開いているときに
    **更新のダイアログが関係ないウィンドウの前に出る**（実機でしか
    気づけない類の崩れ）。既定ボタンの指定も、そのまま渡らないと
    「うっかり Enter で更新してしまう」に化けるので一緒に縛る。
    """
    calls: list[tuple] = []
    monkeypatch.setattr(
        "editor_app.update_check.show_message_box",
        lambda *args: calls.append(args) or QMessageBox.StandardButton.Yes,
    )

    answer = window._show_update_message(
        QMessageBox.Icon.Question,
        "更新があります",
        "取り込みますか",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )

    assert answer == QMessageBox.StandardButton.Yes
    assert calls == [
        (
            window,  # 親がこのウィンドウであること
            QMessageBox.Icon.Question,
            "更新があります",
            "取り込みますか",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
    ]
