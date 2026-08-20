"""pytest 共通設定.

このリポジトリの CI / 開発環境にはディスプレイが無いため、Qt は必ず
offscreen プラットフォームで動かす。QApplication が生成される前に
環境変数を設定する必要があるので、import 時点で設定している。
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import shiboken6  # noqa: E402

from editor_app.main_window import MainWindow  # noqa: E402


def _discard_unsaved_changes(win: MainWindow) -> None:
    """テスト終了時に、未保存の確認ダイアログが出ないようにする。

    ``MainWindow.closeEvent`` は未保存のタブがあると確認ダイアログを出す。
    pytest-qt はテスト後に登録されたウィジェットを閉じるので、そのままだと
    ヘッドレス環境では誰も応答できずテストが固まってしまう。閉じる直前に
    変更フラグを落としておくことで、確認せずに閉じられるようにする。

    テストが自分で ``deleteLater()`` したウィンドウもここへ回ってくる。
    Qt 側 (C++) が消えたウィンドウに触ると
    ``Internal C++ object ... already deleted`` になるので、先に確かめる。
    """
    if not shiboken6.isValid(win):
        return
    for editor in win.editors():
        editor.set_modified(False)


@pytest.fixture
def make_window(qtbot):
    """MainWindow を作って表示するファクトリ。"""

    def factory() -> MainWindow:
        win = MainWindow()
        qtbot.addWidget(win, before_close_func=_discard_unsaved_changes)
        win.show()
        return win

    return factory


@pytest.fixture
def window(make_window) -> MainWindow:
    """表示済みの MainWindow を返すフィクスチャ。"""
    return make_window()


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """折り返しモード等の永続設定 (QSettings) を、テスト用の一時ファイルへ
    リダイレクトする。これが無いと、テストがユーザー本人の実際の設定ファイル
    （``~/.config/Tuxpad/Tuxpad.conf``）を読み書きしてしまう。
    """
    from PySide6.QtCore import QSettings

    ini_path = str(tmp_path / "test-settings.ini")
    monkeypatch.setattr(
        "editor_app.settings.QSettings",
        lambda *args, **kwargs: QSettings(ini_path, QSettings.Format.IniFormat),
    )


@pytest.fixture(autouse=True)
def _isolated_backup_cache(tmp_path, monkeypatch):
    """上書き前の控え（``editor_app.backups``）の置き場を一時ディレクトリへ移す。

    本番のままだと、テストが利用者本人の ``~/.cache/tuxpad/backups`` に
    テスト用の一時ファイルの控えを積み上げ、**本物の控えを容量の掃除で
    押し出しかねない**。``XDG_CACHE_HOME`` は本番の実装がそのまま見る
    環境変数なので、差し替えても本番と違う経路にはならない。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _isolated_ipc_server_name(monkeypatch):
    """IPC (シングルインスタンス化) のソケット名を、テストごとに一意な
    名前へ差し替える。

    本番と同じ名前 (``tuxpad-ipc``) のままテストを動かすと、この
    マシンで実際に起動中の Tuxpad インスタンスに、テスト用の一時
    ファイルパスを誤って転送してしまう恐れがある（実務データが開いて
    いる可能性のある共有デスクトップのため、これは絶対に避けること）。
    """
    monkeypatch.setattr("editor_app.ipc.SERVER_NAME", f"tuxpad-ipc-test-{uuid.uuid4()}")


@pytest.fixture(autouse=True)
def _close_stray_windows():
    """テストごとに MainWindow.open_windows() を空の状態に戻す。

    タブのウィンドウ間ドラッグ&ドロップ（複数ウィンドウ対応）のテストでは、
    ``make_window`` フィクスチャを経由せずに ``MainWindow()`` を直接
    生成すること（タブの切り離しで新規ウィンドウが作られる）があるため、
    それらが次のテストに残らないよう明示的に閉じておく。

    あわせて、クラス変数で持っているドラッグ状態（ドラッグ元・引き取り
    フラグ）も、前のテストの残りが次に漏れないようリセットしておく。
    """
    from editor_app.tab_bar import MultiRowTabBar

    MultiRowTabBar.set_drag_source(None)
    MultiRowTabBar._drag_accepted = False
    yield
    for win in MainWindow.open_windows():
        for editor in win.editors():
            editor.set_modified(False)
        win.close()
