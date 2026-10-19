"""同じファイルを 2 つのタブで開かせない見張りの、**利用者に見える側**のテスト.

このファイルが見張るのは「探し当てたあと、利用者にどう伝えるか」である。
**探す側**（同じファイルのタブをどう突き合わせるか・他のウィンドウまで見るか・
保存を本当に止めるか）は `test_open_file.py` / `test_save_file.py` が既に
固めてあり、変異を 11 通り当てて全て赤くなることを確かめた（69 回目）。
一方で**伝える側**は誰も見ていなかった:

- 「名前を付けて保存」で止めたときの文面から、**どのファイルか**（名前）と
  **どこで開いているか**（このウィンドウか別のウィンドウか）が落ちても緑だった。
  この警告には「それでも保存する」が用意されていないので、**文面が頼りの
  唯一の出口**（先に開いている方のタブを閉じてもらう）になる。別ウィンドウで
  開いているのに「このウィンドウの別のタブ」と言われた利用者は、このウィンドウの
  タブをいくら探しても見つけられず、そのファイルを保存できないまま詰む。
- 既に開いているファイルを開き直したとき、タブは切り替わるのに
  **キーボードの入力先が本文へ戻らない**形にしても緑だった（タブバーに
  カーソルが残る）。

どちらも「ファイルが壊れて開けなくなる」の一歩手前——**保存が黙って消える**
状況から利用者が抜け出すための案内なので、字面を直接書いて固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from PySide6.QtWidgets import QFileDialog

from editor_app.main_window import MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（`test_save_file.py` と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def mock_save_dialog(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """「名前を付けて保存」ダイアログをモックする（`test_save_file.py` と同じ）。"""
    monkeypatch.setattr(QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(path)])


@pytest.fixture
def warnings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """「保存できません」の警告を (題名, 本文) で受け取る。"""
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, text, *a, **k: shown.append((title, text)),
    )
    return shown


def _prepare_target(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.write_text("元の内容\n", encoding="utf-8")
    return target


# ----------------------------------------------------------------------
# 「名前を付けて保存」で止めたときの文面
# ----------------------------------------------------------------------
def test_the_warning_names_the_file_that_is_already_open(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings
) -> None:
    """どのファイルの話なのかが文面に出る（タブが何枚も開いていると、
    名前が無い警告では利用者がどれを閉じればよいか決められない）。"""
    target = _prepare_target(tmp_path, "共有メモ.txt")
    window.open_path(target)
    other = window.new_file()
    edit_text(other, "別の内容\n")
    mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(other) is False
    (title, text), = warnings
    assert title == "保存できません"
    assert "共有メモ.txt" in text


def test_the_warning_says_the_tab_is_in_this_window(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings
) -> None:
    target = _prepare_target(tmp_path, "同じ窓.txt")
    window.open_path(target)
    other = window.new_file()
    edit_text(other, "別の内容\n")
    mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(other) is False
    _title, text = warnings[0]
    assert "このウィンドウの別のタブ" in text
    assert "別のウィンドウのタブ" not in text


def test_the_warning_says_the_tab_is_in_another_window(
    make_window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings
) -> None:
    """別ウィンドウで開いているときは、そう伝える。

    ここが「このウィンドウの別のタブ」に化けると、利用者は目の前の
    ウィンドウのタブを探し続けることになる（止めるだけの警告なので、
    探し当てられなければそのファイルは保存できないまま）。
    """
    target = _prepare_target(tmp_path, "別の窓.txt")
    win_a = make_window()
    win_b = make_window()
    win_a.open_path(target)

    editor = win_b.current_editor()
    edit_text(editor, "別ウィンドウの内容\n")
    mock_save_dialog(monkeypatch, target)

    assert win_b.save_editor_as(editor) is False
    _title, text = warnings[0]
    assert "別のウィンドウのタブ" in text
    assert "このウィンドウの別のタブ" not in text


def test_the_two_places_are_told_apart(
    make_window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings
) -> None:
    """同じウィンドウの場合と別ウィンドウの場合で、文面が違う。

    上の 2 件は「その語が入っているか」を見ているので、両方の語を並べて
    しまう実装でも通る。ここで 2 つの文面そのものを見比べて、
    **どちらの場合も同じ案内になってしまう**形を塞ぐ。
    """
    same = _prepare_target(tmp_path, "同じ窓.txt")
    other_file = _prepare_target(tmp_path, "別の窓.txt")

    win_a = make_window()
    win_a.open_path(same)
    tab = win_a.new_file()
    edit_text(tab, "x\n")
    mock_save_dialog(monkeypatch, same)
    assert win_a.save_editor_as(tab) is False

    win_b = make_window()
    win_b.open_path(other_file)
    win_c = make_window()
    editor = win_c.current_editor()
    edit_text(editor, "y\n")
    mock_save_dialog(monkeypatch, other_file)
    assert win_c.save_editor_as(editor) is False

    assert len(warnings) == 2
    in_this_window = warnings[0][1].replace("同じ窓.txt", "F")
    in_other_window = warnings[1][1].replace("別の窓.txt", "F")
    assert in_this_window != in_other_window


def test_the_warning_explains_what_would_be_lost(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings
) -> None:
    """「なぜ止めたのか」と「どうすればよいか」が文面に残っている。"""
    target = _prepare_target(tmp_path, "説明.txt")
    window.open_path(target)
    other = window.new_file()
    edit_text(other, "別の内容\n")
    mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(other) is False
    _title, text = warnings[0]
    assert "先の変更が消えてしまいます" in text
    assert "閉じてから保存してください" in text


# ----------------------------------------------------------------------
# 既に開いているファイルを開き直したときの、キーボードの入力先
# ----------------------------------------------------------------------
def test_reopening_a_file_puts_the_cursor_back_in_the_text(
    window: MainWindow, tmp_path: Path
) -> None:
    """タブを切り替えるだけでなく、本文に入力できる状態にして返す。

    タブバーにカーソルが残っていると、利用者が続けて打った文字は本文に
    入らない（タブの切り替えだけが起きる）。``setCurrentWidget`` では
    カーソルは移らないことを実測で確かめてある（69 回目）。
    """
    target = _prepare_target(tmp_path, "開き直し.txt")
    first = window.open_path(target)
    window.new_file()
    window.tabs.tabBar().setFocus()
    assert first.hasFocus() is False

    assert window.open_path(target) is first
    assert first.hasFocus() is True


def test_reopening_a_file_in_another_window_puts_the_cursor_in_its_text(
    window: MainWindow, make_window, tmp_path: Path
) -> None:
    """別ウィンドウで見つかった場合も、そのウィンドウの本文に入力先を置く。

    ここで見るのは ``hasFocus()`` ではなく**そのウィンドウの
    ``focusWidget()``**。``hasFocus()`` は「OS が今どのウィンドウを
    アクティブと見ているか」まで含むが、offscreen では
    ``activateWindow()`` が効かない（"This plugin does not support
    raise()" が出る）ため、実機では入力先になっていても False になる。
    ウィンドウごとの入力先の記録は offscreen でも本番と同じに動く。
    """
    target = _prepare_target(tmp_path, "別窓の開き直し.txt")
    other = make_window()
    existing = other.open_path(target)
    other.tabs.tabBar().setFocus()
    assert other.focusWidget() is not existing

    assert window.open_path(target) is existing
    assert other.focusWidget() is existing


def test_a_file_found_in_this_window_does_not_re_raise_it(
    window: MainWindow, tmp_path: Path
) -> None:
    """自分のウィンドウで見つかった場合は、前面に出す処理を走らせない。

    `open_path` の説明にそう書いてあるのに、誰も確かめていなかった
    （69 回目）。最小化したままかどうかで見る——`raise_window` は
    最小化を元に戻すので、走れば必ずここに出る。
    """
    target = _prepare_target(tmp_path, "最小化.txt")
    window.open_path(target)
    window.showMinimized()
    assert window.isMinimized() is True

    window.open_path(target)

    assert window.isMinimized() is True


# ----------------------------------------------------------------------
# 別のフォルダにある「同じ名前」のファイルを混同しないか
# ----------------------------------------------------------------------
def test_two_files_with_the_same_name_in_different_folders_get_their_own_tabs(
    window: MainWindow, tmp_path: Path
) -> None:
    """同名でもフォルダが違えば別のファイル——タブも別々に開く。

    突き合わせをファイル名だけで決める形（`sfg-match-by-name`）にしても、
    69 回目の時点では**どのテストも赤くならなかった**。日付ごとのフォルダに
    同じ名前のファイルを置く使い方（`2026-09/メモ.txt` と
    `2026-10/メモ.txt`）では、2 つ目を開いたときに 1 つ目のタブへ
    黙って切り替わり、**利用者は別のファイルだと思ったまま書き換える**。
    """
    september = tmp_path / "2026-09"
    october = tmp_path / "2026-10"
    september.mkdir()
    october.mkdir()
    old_memo = september / "メモ.txt"
    new_memo = october / "メモ.txt"
    old_memo.write_text("9 月の内容\n", encoding="utf-8")
    new_memo.write_text("10 月の内容\n", encoding="utf-8")

    first = window.open_path(old_memo)
    second = window.open_path(new_memo)

    assert second is not first
    assert window.tabs.count() == 2
    assert first.toPlainText() == "9 月の内容\n"
    assert second.toPlainText() == "10 月の内容\n"
    assert {editor.path for editor in window.editors()} == {
        old_memo.resolve(),
        new_memo.resolve(),
    }


def test_saving_onto_a_same_named_file_in_another_folder_is_allowed(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings
) -> None:
    """同名のタブが開いているだけでは、「名前を付けて保存」を止めない。

    止めるのは**同じファイル**を 2 つのタブで開くときだけ。名前だけで
    突き合わせると、別フォルダの同名ファイルへ保存できなくなる
    （止めるだけで「それでも保存する」が無いので、逃げ道も無い）。
    """
    september = tmp_path / "2026-09"
    october = tmp_path / "2026-10"
    september.mkdir()
    october.mkdir()
    opened = september / "メモ.txt"
    target = october / "メモ.txt"
    opened.write_text("9 月の内容\n", encoding="utf-8")
    target.write_text("10 月の内容\n", encoding="utf-8")

    window.open_path(opened)
    editor = window.new_file()
    edit_text(editor, "書き足した\n")
    mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(editor) is True
    assert warnings == []
    assert target.read_text(encoding="utf-8") == "書き足した\n"
    assert opened.read_text(encoding="utf-8") == "9 月の内容\n"
