"""終了時の保存確認ダイアログの「残りにも同じ操作を適用する」のテスト.

実機フィードバックによる追加機能。複数タブが未保存のままウィンドウを
閉じたとき、チェックを入れればそれ以降のタブは同じ回答で自動的に処理される。
チェックの記憶は 1 回の ``closeEvent()`` の間だけで、永続化はしない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import REMEMBER_CHOICE_TEXT

HELPER = "editor_app.main_window.show_message_box_with_checkbox"


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def make_unsaved_window(make_window, tmp_path: Path, count: int):
    """保存先が決まっている未保存タブを ``count`` 個持つウィンドウを作る。

    保存先が決まっていれば「保存」を選んでもファイルダイアログは出ない。
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
    # 起動時の空タブが残っていても未変更なので確認対象にはならない。
    assert sum(1 for e in win.editors() if e.is_modified) == count
    return win, paths


class DialogRecorder:
    """ダイアログの呼び出しを記録し、決めた答えを返すモック。"""

    def __init__(self, answer, remember: bool):
        self.answer = answer
        self.remember = remember
        self.calls: list[str | None] = []

    def __call__(self, parent, icon, title, text, buttons, default_button, checkbox_text):
        self.calls.append(checkbox_text)
        return self.answer, self.remember


# ----------------------------------------------------------------------
# チェック無し（従来の挙動）
# ----------------------------------------------------------------------


def test_without_check_every_tab_is_confirmed(make_window, tmp_path, monkeypatch):
    win, _ = make_unsaved_window(make_window, tmp_path, 3)
    recorder = DialogRecorder(QMessageBox.StandardButton.Discard, remember=False)
    monkeypatch.setattr(HELPER, recorder)

    assert win.close() is True
    assert len(recorder.calls) == 3


def test_checkbox_is_offered_while_more_unsaved_tabs_remain(make_window, tmp_path, monkeypatch):
    win, _ = make_unsaved_window(make_window, tmp_path, 3)
    recorder = DialogRecorder(QMessageBox.StandardButton.Discard, remember=False)
    monkeypatch.setattr(HELPER, recorder)

    win.close()

    # 最後の 1 件には「残り」が無いのでチェックボックスを出さない。
    assert recorder.calls == [REMEMBER_CHOICE_TEXT, REMEMBER_CHOICE_TEXT, None]


def test_checkbox_is_not_offered_for_a_single_unsaved_tab(make_window, tmp_path, monkeypatch):
    win, _ = make_unsaved_window(make_window, tmp_path, 1)
    recorder = DialogRecorder(QMessageBox.StandardButton.Discard, remember=False)
    monkeypatch.setattr(HELPER, recorder)

    win.close()

    assert recorder.calls == [None]


# ----------------------------------------------------------------------
# チェックあり
# ----------------------------------------------------------------------


def test_remembered_discard_closes_without_more_dialogs(make_window, tmp_path, monkeypatch):
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    recorder = DialogRecorder(QMessageBox.StandardButton.Discard, remember=True)
    monkeypatch.setattr(HELPER, recorder)

    assert win.close() is True
    assert win.isVisible() is False
    assert len(recorder.calls) == 1
    # 破棄なのでファイルは元のまま。
    assert all(p.read_text(encoding="utf-8") == "元の内容\n" for p in paths)


def test_remembered_save_saves_the_rest(make_window, tmp_path, monkeypatch):
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    recorder = DialogRecorder(QMessageBox.StandardButton.Save, remember=True)
    monkeypatch.setattr(HELPER, recorder)

    assert win.close() is True
    assert len(recorder.calls) == 1
    for i, path in enumerate(paths):
        assert path.read_text(encoding="utf-8") == f"変更 {i}\n"


def test_cancel_stops_even_with_check(make_window, tmp_path, monkeypatch):
    """キャンセルはチェックの有無に関わらず即中止。"""
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    recorder = DialogRecorder(QMessageBox.StandardButton.Cancel, remember=True)
    monkeypatch.setattr(HELPER, recorder)

    assert win.close() is False
    assert win.isVisible() is True
    assert len(recorder.calls) == 1
    assert all(p.read_text(encoding="utf-8") == "元の内容\n" for p in paths)


def test_remembered_choice_is_not_kept_for_the_next_close(make_window, tmp_path, monkeypatch):
    """覚えた回答は 1 回の closeEvent 限り。次に閉じるときはまた確認する。"""
    win, _ = make_unsaved_window(make_window, tmp_path, 3)
    cancel = DialogRecorder(QMessageBox.StandardButton.Cancel, remember=True)
    monkeypatch.setattr(HELPER, cancel)
    assert win.close() is False

    discard = DialogRecorder(QMessageBox.StandardButton.Discard, remember=False)
    monkeypatch.setattr(HELPER, discard)
    assert win.close() is True
    # 覚えていないので、また 3 件とも確認されている。
    assert len(discard.calls) == 3


def test_failed_save_stops_closing(make_window, tmp_path, monkeypatch):
    """「保存」を覚えていても、保存に失敗したらそこで中止する。"""
    win, _ = make_unsaved_window(make_window, tmp_path, 2)
    # 保存先の決まっていない未保存タブを足すと、保存にはダイアログが要る。
    extra = win.new_file()
    edit_text(extra, "無題の変更\n")

    monkeypatch.setattr(
        HELPER,
        DialogRecorder(QMessageBox.StandardButton.Save, remember=True),
    )
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Rejected)

    assert win.close() is False
    assert win.isVisible() is True


def test_unmodified_tabs_are_never_confirmed(make_window, tmp_path, monkeypatch):
    win, _ = make_unsaved_window(make_window, tmp_path, 2)
    win.new_file()  # 未変更のタブ
    recorder = DialogRecorder(QMessageBox.StandardButton.Discard, remember=False)
    monkeypatch.setattr(HELPER, recorder)

    assert win.close() is True
    assert len(recorder.calls) == 2


# ----------------------------------------------------------------------
# 1 タブだけ閉じるときは今まで通り（チェックボックス無し）
# ----------------------------------------------------------------------


def test_close_single_tab_still_uses_plain_dialog(window, monkeypatch):
    calls = []

    def fake_show(*args, **kwargs):
        calls.append(args)
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr("editor_app.main_window.show_message_box", fake_show)
    editor = window.current_editor()
    edit_text(editor, "未保存\n")

    assert window.close_editor(editor) is True
    assert len(calls) == 1


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Discard])
def test_helper_returns_button_and_check_state(window, monkeypatch, answer):
    """ヘルパー自体の戻り値の形（ボタン, チェック状態）を確認する。"""
    from editor_app import main_window as mw

    captured = {}

    def fake_exec(self):
        captured["checkbox"] = self.checkBox()
        if self.checkBox() is not None:
            self.checkBox().setChecked(True)
        return answer

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    result = mw.show_message_box_with_checkbox(
        window,
        QMessageBox.Icon.Warning,
        "保存の確認",
        "本文",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard,
        QMessageBox.StandardButton.Save,
        REMEMBER_CHOICE_TEXT,
    )

    assert result == (answer, True)
    assert captured["checkbox"] is not None
    assert captured["checkbox"].text() == REMEMBER_CHOICE_TEXT


def test_helper_without_checkbox_reports_false(window, monkeypatch):
    from editor_app import main_window as mw

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Discard)

    result = mw.show_message_box_with_checkbox(
        window,
        QMessageBox.Icon.Warning,
        "保存の確認",
        "本文",
        QMessageBox.StandardButton.Discard,
        QMessageBox.StandardButton.Discard,
        None,
    )

    assert result == (QMessageBox.StandardButton.Discard, False)
