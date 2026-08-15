"""アプリ内から更新を確認して取り込むミックスイン.

git の実際の操作は `updater.py`（Qt 非依存）が持っていて、このモジュールは
**いつ確認するか・どう尋ねるか**だけを受け持つ（`file_watch.py` などと同じ
切り出し方）。

流れ:

1. :meth:`UpdateCheckMixin.check_for_updates_on_startup` … 起動時に 1 回。
   設定「起動時に更新を確認する」が OFF、または git 管理外なら何もしない。
2. :meth:`UpdateCheckMixin.check_for_updates_manually` … 「設定 → 更新 →
   更新を確認」から。**結果を必ず出す**（利用者が明示的に押したので沈黙しない）。
3. :meth:`UpdateCheckMixin._start_update_check` … ``git fetch`` は通信を伴い、
   相手が黙っていると数十秒かかりうるので**別スレッド**で走らせる。
   GUI を固めないための措置で、結果はシグナル経由で GUI スレッドに戻る。
4. :meth:`UpdateCheckMixin._handle_update_check_result` … 結果に応じて
   ダイアログを出す。**判断はここに全部集めてある**ので、テストはこれを
   直接呼べばよい（スレッドを動かさずに済む）。

**起動時と手動で沈黙の仕方を変える**のが肝。起動時に「確認できませんでした」
を毎回出すと、ネットワークの無い場所では起動のたびに邪魔になる。だから
起動時は「更新があるとき」だけ喋り、それ以外は黙る。

**取り込んだあとに勝手に再起動しない**。未保存のタブを巻き込む危険がある
ので、再起動の時機は利用者に委ねる（「再起動すると反映されます」と伝える
だけにする）。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from . import APP_NAME
from .dialogs import show_message_box
from .settings import load_check_updates_on_startup, save_check_updates_on_startup
from .updater import (
    STATE_DIRTY,
    STATE_FAILED,
    STATE_UNSUPPORTED,
    STATE_UP_TO_DATE,
    STATE_UPDATE_AVAILABLE,
    UpdateStatus,
    apply_update,
    check_for_updates,
    find_repository_root,
)


class _UpdateCheckSignals(QObject):
    """別スレッドの結果を GUI スレッドへ渡すための入れ物.

    ワーカースレッドから ``emit`` すると、受け手（GUI スレッドのウィンドウ）
    には Qt がキューイングして届けてくれる。**ウィジェットを直接触るコードを
    ワーカースレッドに書かないこと。**
    """

    finished = Signal(object, bool)  # (UpdateStatus, 手動で押されたか)


class UpdateCheckMixin:
    """「新しい版があります → 更新しますか？」をアプリ内で行う。

    ``MainWindow`` にミックスインとして混ぜて使う。
    """

    def _setup_update_check(self) -> None:
        """更新確認まわりを初期化する。``MainWindow.__init__`` から 1 回だけ呼ぶ。"""
        self._update_signals = _UpdateCheckSignals(self)
        self._update_signals.finished.connect(self._handle_update_check_result)
        #: 確認が走っている間は True（二重に走らせない）。
        self._update_check_running = False
        #: git 管理下の導入か。管理外なら更新機能は静かに無効にする。
        self._update_repo_root = find_repository_root()

    # ------------------------------------------------------------------
    # 入口（起動時 / 手動）
    # ------------------------------------------------------------------
    def check_for_updates_on_startup(self) -> None:
        """起動時の自動確認。設定が OFF・git 管理外なら何もしない。

        ``app.main()`` から、最初のウィンドウを出したあとに 1 回だけ呼ぶ
        （ウィンドウを作るたびに走らせない）。
        """
        if self._update_repo_root is None:
            return
        if not load_check_updates_on_startup():
            return
        self._start_update_check(manual=False)

    def check_for_updates_manually(self) -> None:
        """「設定 → 更新 → 更新を確認」。結果は必ず出す。"""
        if self._update_repo_root is None:
            self._show_update_message(
                QMessageBox.Icon.Information,
                "更新の確認",
                f"この {APP_NAME} は git で導入されていないため、"
                "アプリからの更新はできません。",
            )
            return
        if self._update_check_running:
            self._show_update_message(
                QMessageBox.Icon.Information, "更新の確認", "更新を確認しています。"
            )
            return
        self._start_update_check(manual=True)

    def set_check_updates_on_startup(self, enabled: bool) -> None:
        """「起動時に更新を確認する」を切り替える（アプリ全体で共有）。"""
        save_check_updates_on_startup(enabled)
        for window in self.open_windows():
            window._sync_check_updates_action_checked()

    def _sync_check_updates_action_checked(self) -> None:
        """チェック付きメニュー項目を、いまの設定に合わせる。"""
        self.action_check_updates_on_startup.setChecked(load_check_updates_on_startup())

    # ------------------------------------------------------------------
    # 確認（別スレッド）
    # ------------------------------------------------------------------
    def _start_update_check(self, *, manual: bool) -> None:
        """``git fetch`` を別スレッドで走らせる（GUI を固めないため）。"""
        self._update_check_running = True
        repo_root = self._update_repo_root
        signals = self._update_signals

        def worker() -> None:
            try:
                status = check_for_updates(repo_root)
            except Exception as exc:  # 予期しない失敗でもスレッドを黙って死なせない
                status = UpdateStatus(STATE_FAILED, detail=str(exc))
            signals.finished.emit(status, manual)

        thread = threading.Thread(target=worker, name="tuxpad-update-check", daemon=True)
        thread.start()
        #: 参照を残しておく（テストから join したいときのため）。
        self._update_check_thread = thread

    # ------------------------------------------------------------------
    # 結果の扱い（ここに全ての判断を集めてある＝テストはここを直接呼ぶ）
    # ------------------------------------------------------------------
    def _handle_update_check_result(self, status: UpdateStatus, manual: bool) -> None:
        """確認結果に応じてダイアログを出す。

        起動時 (``manual=False``) は、更新があるときだけ喋る。手動のときは
        「最新です」「確認に失敗：理由」も含めて必ず結果を返す。
        """
        self._update_check_running = False

        if status.state == STATE_UPDATE_AVAILABLE:
            self._prompt_apply_update(status)
            return

        if not manual:
            return  # 起動のたびに邪魔しない

        if status.state == STATE_UP_TO_DATE:
            self._show_update_message(
                QMessageBox.Icon.Information,
                "更新の確認",
                f"お使いの {APP_NAME} は最新です。",
            )
        elif status.state == STATE_DIRTY:
            self._show_update_message(
                QMessageBox.Icon.Information, "更新の確認", status.detail
            )
        elif status.state == STATE_UNSUPPORTED:
            self._show_update_message(
                QMessageBox.Icon.Information,
                "更新の確認",
                f"この {APP_NAME} は git で導入されていないため、"
                "アプリからの更新はできません。",
            )
        else:
            self._show_update_message(
                QMessageBox.Icon.Warning,
                "更新の確認",
                f"更新を確認できませんでした。\n\n{status.detail}",
            )

    def _prompt_apply_update(self, status: UpdateStatus) -> None:
        """「新しいバージョンがあります。更新しますか？」を尋ねて、取り込む。"""
        answer = self._show_update_message(
            QMessageBox.Icon.Question,
            "更新があります",
            f"新しいバージョンがあります（{status.count} 件の更新）。\n更新しますか？",
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button=QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        result = apply_update(self._update_repo_root, status.target)
        if not result.ok:
            self._show_update_message(
                QMessageBox.Icon.Warning,
                "更新できませんでした",
                f"更新を取り込めませんでした。\n\n{result.detail}",
            )
            return

        text = (
            f"更新を取り込みました（{status.count} 件）。\n"
            f"再起動すると反映されます。"
        )
        if result.dependencies_changed:
            # 依存パッケージやインストール手順が変わっていると、git の
            # 更新だけでは足りないことがある（PySide6 の版が上がった等）。
            text += (
                "\n\nこの更新には必要なパッケージやインストール手順の変更が"
                "含まれています。システム側の更新が要る可能性があるため、"
                "`./install.sh` の再実行を検討してください。"
            )
        self._show_update_message(QMessageBox.Icon.Information, "更新しました", text)

    def _show_update_message(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        """更新まわりのダイアログの出し口（テストはここを差し替える）。"""
        return show_message_box(self, icon, title, text, buttons, default_button)
