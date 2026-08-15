"""保存確認ダイアログをウィンドウの × で閉じるとクラッシュする不具合の回帰テスト.

実機のクラッシュダンプ (coredumpctl) で、KDE のネイティブなメッセージ
ダイアログ統合が ``QDialogPrivate::setNativeDialogVisible()`` 内で
SIGSEGV することを確認した。``show_message_box()``
(``dialogs.py``) が ``QMessageBox.Option.DontUseNativeDialog`` を
必ず設定していることを確認する。

offscreen 環境ではそもそも KDE のネイティブダイアログ統合プラグインが
読み込まれないため、この回帰そのもの（実機の SIGSEGV）はここでは
再現できない。ここで検証できるのはあくまで「回避策のオプションが
確実に設定されているか」「× で閉じた（＝閉じるボタン以外での終了）
場合に例外を出さずキャンセル相当として扱えるか」まで。
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MainWindow, show_message_box


def test_show_message_box_disables_native_dialog(window: MainWindow, monkeypatch) -> None:
    captured: list[QMessageBox] = []

    def spy_exec(self):
        captured.append(self)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", spy_exec)

    show_message_box(
        window,
        QMessageBox.Icon.Warning,
        "タイトル",
        "本文",
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save,
    )

    assert len(captured) == 1
    assert captured[0].testOption(QMessageBox.Option.DontUseNativeDialog) is True


def test_show_message_box_closed_via_window_close_behaves_like_cancel(
    window: MainWindow, qtbot
) -> None:
    """ウィンドウを閉じる（× ボタン相当）の操作は Cancel 相当の値を返す。"""
    from PySide6.QtCore import QTimer

    box_holder: list[QMessageBox] = []
    original_init = QMessageBox.__init__

    def capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        box_holder.append(self)

    # show_message_box が作った実際の QMessageBox を捕まえて、表示された
    # 直後に close() する（実機での × クリックに相当する操作）。
    import editor_app.main_window as main_window_module

    def close_soon():
        if box_holder:
            box_holder[-1].close()

    QTimer.singleShot(0, close_soon)

    from unittest.mock import patch

    with patch.object(QMessageBox, "__init__", capturing_init):
        answer = main_window_module.show_message_box(
            window,
            QMessageBox.Icon.Warning,
            "タイトル",
            "本文",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    assert answer == QMessageBox.StandardButton.Cancel
