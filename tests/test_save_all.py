"""「すべて保存」（引き継ぎ ⑬）のテスト.

変更済みのタブが何枚も溜まったときに、1 枚ずつ ``Ctrl+S`` せずに
**そのウィンドウの変更済みタブだけ**をまとめて保存する項目。

ここで縛っているのは 6 つ:

1. 変更済みのタブが**全部**保存される
2. 変更の無いタブには**書き込まない**（引き継ぎ ⑧ に乗っているだけだが、
   「すべて」の名前のとおりに全タブを書きに行くと、rclone / NAS の同期に
   中身の同じ無駄な差分が上がる。⑧ を入れた意味が消えるので見張る）
3. 無題タブには保存先を尋ねる
4. 途中でキャンセル・失敗しても**残りのタブへ進む**
5. **他のウィンドウのタブには手を出さない**
6. ショートカットが**付いていない**（利用者の明確な指定）

加えて、保存のたびにそのタブへ切り替えること（ダイアログが「どのタブの
話か」見えるようにするため）と、終わったら元のタブへ戻ること
（失敗があればその最初の 1 枚を出すこと）も見ている。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from editor_app import file_commands
from editor_app.main_window import MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（``test_save_file`` と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def mock_save_dialog(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    """「名前を付けて保存」ダイアログをモックする（``test_save_file`` と同じ）。"""
    if path is None:
        monkeypatch.setattr(
            QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Rejected
        )
    else:
        monkeypatch.setattr(
            QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted
        )
        monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(path)])


def open_tab(window: MainWindow, tmp_path: Path, name: str, text: str):
    """``text`` を書いたファイルを作り、タブとして開いて返す。"""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    window.open_path(path)
    return window.editors()[-1]


# ----------------------------------------------------------------------
# 1. 変更済みのタブが全部保存される
# ----------------------------------------------------------------------
def test_all_modified_tabs_are_written(window, tmp_path) -> None:
    first = open_tab(window, tmp_path, "a.txt", "もとのA\n")
    second = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(first, "新しいA\n")
    edit_text(second, "新しいB\n")

    assert window.save_all_editors() == 2

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "新しいA\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "新しいB\n"
    assert [editor.is_modified for editor in window.editors()] == [False, False]


def test_returns_zero_when_nothing_is_modified(window, tmp_path) -> None:
    open_tab(window, tmp_path, "a.txt", "もとのA\n")

    assert window.save_all_editors() == 0


def test_status_bar_reports_how_many_were_saved(window, tmp_path) -> None:
    first = open_tab(window, tmp_path, "a.txt", "もとのA\n")
    second = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(first, "新しいA\n")
    edit_text(second, "新しいB\n")

    window.save_all_editors()

    assert "2 件" in window.statusBar().currentMessage()


def test_status_bar_says_so_when_there_is_nothing_to_save(window, tmp_path) -> None:
    """「押したのに何も起きない」に見えないよう、無かったことも知らせる。"""
    open_tab(window, tmp_path, "a.txt", "もとのA\n")

    window.save_all_editors()

    assert window.statusBar().currentMessage() == "保存が必要なタブはありません"


# ----------------------------------------------------------------------
# 2. 変更の無いタブには書き込まない（引き継ぎ ⑧ を壊さない）
# ----------------------------------------------------------------------
def test_unmodified_tabs_are_not_written(window, tmp_path, monkeypatch) -> None:
    """更新日時すら動かさないこと。

    利用者は rclone / NAS で同期しているので、中身が同じなのに更新日時
    だけが動くと無駄な差分が上がる（引き継ぎ ⑧ の理由そのもの）。
    """
    untouched = open_tab(window, tmp_path, "keep.txt", "そのまま\n")
    modified = open_tab(window, tmp_path, "edit.txt", "もとの\n")
    edit_text(modified, "編集した\n")

    written: list[Path] = []
    original = file_commands.FileCommandsMixin._write_editor

    def spy(self, editor, path, *args, **kwargs):
        written.append(path)
        return original(self, editor, path, *args, **kwargs)

    # 差し替え先は **ミックスイン**（``MainWindow`` に直接生やすと、
    # ミックスインを差し替える他のテストの見張りが素通りする）。
    monkeypatch.setattr(file_commands.FileCommandsMixin, "_write_editor", spy)

    window.save_all_editors()

    assert written == [tmp_path / "edit.txt"]
    assert untouched.is_modified is False


# ----------------------------------------------------------------------
# 3. 無題タブには保存先を尋ねる
# ----------------------------------------------------------------------
def test_untitled_tab_is_asked_for_a_destination(window, tmp_path, monkeypatch) -> None:
    untitled = window.editors()[0]
    edit_text(untitled, "無題の中身\n")
    target = tmp_path / "untitled.txt"
    mock_save_dialog(monkeypatch, target)

    assert window.save_all_editors() == 1

    assert target.read_text(encoding="utf-8") == "無題の中身\n"
    assert untitled.path == target


# ----------------------------------------------------------------------
# 4. 途中でキャンセル・失敗しても残りのタブへ進む
# ----------------------------------------------------------------------
def test_cancelling_one_destination_still_saves_the_others(
    window, tmp_path, monkeypatch
) -> None:
    """保存先のキャンセルは「そのタブは今はいい」であって「全部やめる」ではない。"""
    untitled = window.editors()[0]
    edit_text(untitled, "名前を決めない\n")
    named = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(named, "新しいB\n")
    mock_save_dialog(monkeypatch, None)  # ダイアログをキャンセル

    assert window.save_all_editors() == 1

    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "新しいB\n"
    assert untitled.is_modified is True


def test_a_failed_write_does_not_stop_the_rest(window, tmp_path, monkeypatch) -> None:
    failing = open_tab(window, tmp_path, "a.txt", "もとのA\n")
    other = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(failing, "新しいA\n")
    edit_text(other, "新しいB\n")

    # 1 枚目だけ書き込みが失敗する状況を作る。
    monkeypatch.setattr(
        "editor_app.file_commands.write_encoded_file",
        lambda path, data: (_ for _ in ()).throw(OSError("書けません"))
        if path == tmp_path / "a.txt"
        else path.write_bytes(data),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda *args, **kwargs: shown.append(args[2]),
    )

    assert window.save_all_editors() == 1

    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "新しいB\n"
    assert failing.is_modified is True
    assert shown  # 失敗はその場で知らせている


def test_status_bar_counts_the_ones_left_unsaved(window, tmp_path, monkeypatch) -> None:
    untitled = window.editors()[0]
    edit_text(untitled, "名前を決めない\n")
    named = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(named, "新しいB\n")
    mock_save_dialog(monkeypatch, None)

    window.save_all_editors()

    message = window.statusBar().currentMessage()
    assert "1 件のタブを保存しました" in message
    assert "1 件は保存していません" in message


# ----------------------------------------------------------------------
# 5. 他のウィンドウのタブには手を出さない
# ----------------------------------------------------------------------
def test_other_windows_are_left_alone(make_window, tmp_path) -> None:
    first = make_window()
    second = make_window()
    mine = open_tab(first, tmp_path, "mine.txt", "もとの\n")
    theirs = open_tab(second, tmp_path, "theirs.txt", "もとの\n")
    edit_text(mine, "こっちは保存する\n")
    edit_text(theirs, "あっちは触らない\n")

    assert first.save_all_editors() == 1

    assert (tmp_path / "mine.txt").read_text(encoding="utf-8") == "こっちは保存する\n"
    assert (tmp_path / "theirs.txt").read_text(encoding="utf-8") == "もとの\n"
    assert theirs.is_modified is True


# ----------------------------------------------------------------------
# 6. ショートカットは付けない（利用者の明確な指定）
# ----------------------------------------------------------------------
def test_save_all_has_no_shortcut(window) -> None:
    """押し間違いで全タブが書き込まれるのを避けるため、メニューからのみ。"""
    assert window.action_save_all.shortcut().isEmpty()
    assert window.action_save_all.shortcuts() == []


# ----------------------------------------------------------------------
# 保存するタブを見せる / 終わったら元のタブへ戻る
# ----------------------------------------------------------------------
def test_each_tab_is_shown_while_it_is_saved(window, tmp_path, monkeypatch) -> None:
    """保存先や文字化けのダイアログは、そのタブが見えていないと答えられない。"""
    first = open_tab(window, tmp_path, "a.txt", "もとのA\n")
    second = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(first, "新しいA\n")
    edit_text(second, "新しいB\n")
    window.tabs.setCurrentWidget(window.editors()[0])

    seen: list[object] = []
    original = file_commands.FileCommandsMixin.save_editor

    def spy(self, editor):
        seen.append(self.tabs.currentWidget())
        return original(self, editor)

    monkeypatch.setattr(file_commands.FileCommandsMixin, "save_editor", spy)

    window.save_all_editors()

    assert seen == [first, second]


def test_the_original_tab_comes_back_when_everything_was_saved(
    window, tmp_path
) -> None:
    first = open_tab(window, tmp_path, "a.txt", "もとのA\n")
    edit_text(first, "新しいA\n")
    # 「開く」はまっさらな無題タブを使い回すので、居場所にするタブは後から作る。
    untouched = window.new_file()

    window.save_all_editors()

    assert window.tabs.currentWidget() is untouched


def test_the_first_unsaved_tab_is_shown_when_something_failed(
    window, tmp_path, monkeypatch
) -> None:
    """直すものが目の前に残るように、失敗したタブを出したままにする。"""
    failing = open_tab(window, tmp_path, "a.txt", "もとのA\n")
    other = open_tab(window, tmp_path, "b.txt", "もとのB\n")
    edit_text(failing, "新しいA\n")
    edit_text(other, "新しいB\n")
    window.new_file()  # 元居たタブ（ここへ戻らないことを見る）

    monkeypatch.setattr(
        "editor_app.file_commands.write_encoded_file",
        lambda path, data: (_ for _ in ()).throw(OSError("書けません"))
        if path == tmp_path / "a.txt"
        else path.write_bytes(data),
    )
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box", lambda *args, **kwargs: None
    )

    window.save_all_editors()

    assert window.tabs.currentWidget() is failing
