"""機能 4「タブにファイル名を表示 (未保存マーク・閉じるボタン付き)」のテスト."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app import APP_NAME
from editor_app.main_window import MODIFIED_MARK, MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする。

    ``setPlainText()`` は変更フラグを立てないため、利用者の入力と同じ扱いに
    なる ``insertPlainText()`` を使う (tests/test_save_file.py と同じ理由)。
    """
    editor.selectAll()
    editor.insertPlainText(text)


def write_sample(directory: Path, name: str = "sample.txt", text: str = "あいう\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# タブ名と未保存マーク
# ----------------------------------------------------------------------
def test_tab_shows_file_name(window: MainWindow, tmp_path: Path) -> None:
    """タブにはファイル名 (ディレクトリを除いた名前) が出る。"""
    window.open_path(write_sample(tmp_path))
    assert window.tabs.tabText(0) == "sample.txt"


def test_modified_mark_appears_when_edited(window: MainWindow, tmp_path: Path) -> None:
    """編集すると未保存マークがタブ名の先頭に付く。"""
    editor = window.open_path(write_sample(tmp_path))
    assert window.tabs.tabText(0) == "sample.txt"

    edit_text(editor, "編集した")
    assert editor.is_modified
    assert window.tabs.tabText(0) == f"{MODIFIED_MARK}sample.txt"


def test_modified_mark_disappears_after_save(
    window: MainWindow, tmp_path: Path
) -> None:
    """保存すると未保存マークが消える。"""
    editor = window.open_path(write_sample(tmp_path))
    edit_text(editor, "編集した")
    assert window.tabs.tabText(0).startswith(MODIFIED_MARK)

    assert window.save_editor(editor) is True
    assert window.tabs.tabText(0) == "sample.txt"


def test_modified_mark_on_untitled_tab(window: MainWindow) -> None:
    """無題タブでも編集すればマークが付く。"""
    editor = window.current_editor()
    assert window.tabs.tabText(0) == "無題-1"

    edit_text(editor, "あ")
    assert window.tabs.tabText(0) == f"{MODIFIED_MARK}無題-1"


def test_window_title_shows_modified_mark(window: MainWindow) -> None:
    """ウィンドウタイトルにも未保存マークが出る。"""
    editor = window.current_editor()
    edit_text(editor, "あ")
    assert window.windowTitle().startswith(f"{MODIFIED_MARK}無題-1 - ")


def test_tab_name_follows_save_as(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """名前を付けて保存するとタブ名がそのファイル名に変わる。"""
    from PySide6.QtWidgets import QFileDialog

    target = tmp_path / "保存先.txt"
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(target)])
    editor = window.current_editor()
    edit_text(editor, "本文")

    assert window.save_editor_as(editor) is True
    assert window.tabs.tabText(0) == "保存先.txt"


# ----------------------------------------------------------------------
# ツールチップ (同名ファイルの区別)
# ----------------------------------------------------------------------
def test_tooltip_shows_full_path(window: MainWindow, tmp_path: Path) -> None:
    """タブのツールチップにはフルパスが出る。"""
    path = write_sample(tmp_path)
    window.open_path(path)
    assert window.tabs.tabToolTip(0) == str(path.resolve())


def test_tooltip_of_untitled_tab(window: MainWindow) -> None:
    """無題タブのツールチップには未保存であることを書く。"""
    assert "無題-1" in window.tabs.tabToolTip(0)
    assert "未保存" in window.tabs.tabToolTip(0)


def test_same_name_files_are_distinguished_by_tooltip(
    window: MainWindow, tmp_path: Path
) -> None:
    """同名ファイルを 2 つ開いてもツールチップで区別できる。"""
    first = write_sample(tmp_path / "a", "同じ名前.txt")
    second = write_sample(tmp_path / "b", "同じ名前.txt")

    window.open_path(first)
    window.open_path(second)

    assert [window.tabs.tabText(i) for i in range(2)] == ["同じ名前.txt", "同じ名前.txt"]
    assert window.tabs.tabToolTip(0) != window.tabs.tabToolTip(1)


# ----------------------------------------------------------------------
# 閉じるボタン
# ----------------------------------------------------------------------
def test_tabs_are_closable(window: MainWindow) -> None:
    """タブに閉じるボタンが表示される設定になっている。"""
    assert window.tabs.tabsClosable() is True


def test_close_tab_removes_tab(window: MainWindow) -> None:
    """未編集のタブは確認なしで閉じられる。"""
    window.new_file()
    assert window.tabs.count() == 2

    assert window.close_tab(0) is True
    assert window.tabs.count() == 1
    assert [e.display_name for e in window.editors()] == ["無題-2"]


def test_close_tab_signal_from_close_button(window: MainWindow) -> None:
    """タブバーの × (tabCloseRequested) でタブが閉じる。"""
    window.new_file()
    window.tabs.tabCloseRequested.emit(1)
    assert window.tabs.count() == 1
    assert [e.display_name for e in window.editors()] == ["無題-1"]


def test_close_current_tab_shortcut_is_ctrl_w(window: MainWindow) -> None:
    """主のショートカットは Ctrl+W (Ctrl+F4 も受け付ける)。"""
    shortcuts = [s.toString() for s in window.action_close_tab.shortcuts()]
    assert shortcuts[0] == "Ctrl+W"
    assert "Ctrl+F4" in shortcuts


def test_quit_action_has_no_shortcut(window: MainWindow) -> None:
    """Ctrl+Q は誤操作しやすいとの実機フィードバックにより割り当てない。

    「終了」はメニューからのみ実行できる（アクション自体は残す）。
    """
    assert window.action_quit.shortcut().isEmpty()


def test_close_current_tab_action(window: MainWindow) -> None:
    """Ctrl+W のアクションで現在のタブが閉じる。"""
    window.new_file()  # 無題-2 が現在のタブ
    window.action_close_tab.trigger()
    assert [e.display_name for e in window.editors()] == ["無題-1"]


def test_closing_last_tab_leaves_no_tab(window: MainWindow) -> None:
    """最後のタブを閉じるとタブが 0 個になる (ウィンドウは残る)。"""
    assert window.close_current_tab() is True
    assert window.tabs.count() == 0
    assert window.current_editor() is None
    assert window.windowTitle() == APP_NAME


def test_close_current_tab_without_tabs_returns_false(window: MainWindow) -> None:
    """タブが無い状態で閉じても落ちない。"""
    window.close_current_tab()
    assert window.close_current_tab() is False


def test_untitled_number_reused_after_closing_tab(window: MainWindow) -> None:
    """閉じたタブの無題番号は次の新規作成で再利用される。"""
    window.new_file()  # 無題-2
    window.new_file()  # 無題-3
    window.close_tab(1)  # 無題-2 を閉じる
    assert window.new_file().display_name == "無題-2"


# ----------------------------------------------------------------------
# 閉じるときの未保存確認
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("button", "closed"),
    [
        (QMessageBox.StandardButton.Discard, True),
        (QMessageBox.StandardButton.Cancel, False),
    ],
)
def test_close_modified_tab_asks_before_closing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, button, closed: bool
) -> None:
    """未保存のタブを閉じるときは確認する。キャンセルなら閉じない。"""
    asked: list[str] = []
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda _p, _icon, title, *a, **k: (asked.append(title), button)[1],
    )
    editor = window.current_editor()
    edit_text(editor, "編集した")

    assert window.close_editor(editor) is closed
    assert asked  # 確認ダイアログが出ている
    assert window.tabs.count() == (0 if closed else 1)


def test_close_modified_tab_can_save(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確認ダイアログで「保存」を選ぶと保存してから閉じる。"""
    path = write_sample(tmp_path, text="元の内容\n")
    editor = window.open_path(path)
    edit_text(editor, "新しい内容\n")

    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Save,
    )
    assert window.close_editor(editor) is True
    assert window.tabs.count() == 0
    assert path.read_text(encoding="utf-8") == "新しい内容\n"


def test_close_unmodified_tab_does_not_ask(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未編集のタブでは確認ダイアログを出さない。"""
    asked: list[int] = []
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Cancel,
    )
    window.new_file()
    assert window.close_tab(0) is True
    assert asked == []
