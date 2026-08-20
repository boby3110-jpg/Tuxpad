"""引き継ぎ ⑧「変更が無ければ上書き保存でディスクへ書き込まない」のテスト.

VS Code・Notepad++・gedit などの主要なエディタと同じく、上書き保存
(Ctrl+S) は変更が無ければファイルに触らない。利用者は rclone / NAS の
同期を使っており、無用な書き込みはタイムスタンプだけが動いた無駄な差分に
なってしまうため。

このガードが掛かるのは**上書き保存だけ**で、「名前を付けて保存」と
「文字コードを指定して保存」は変更が無くても従来どおり書き込む。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from editor_app import file_commands
from editor_app.main_window import MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（test_save_file.py と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def count_writes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """``_write_editor`` が呼ばれた保存先を記録するリストを返す。

    ディスクへ書くのは ``_write_editor`` 1 か所だけなので、ここを見張れば
    「本当にディスクに触っていないか」を確かめられる。本来の処理はその
    まま行わせる（保存できることも同時に確かめたいため）。
    """
    calls: list[Path] = []
    original = file_commands.FileCommandsMixin._write_editor

    def spy(self, editor, path, *args, **kwargs):
        calls.append(path)
        return original(self, editor, path, *args, **kwargs)

    monkeypatch.setattr(file_commands.FileCommandsMixin, "_write_editor", spy)
    return calls


# ----------------------------------------------------------------------
# 1. 無変更の上書き保存はディスクに触らない
# ----------------------------------------------------------------------
def test_save_unmodified_does_not_write(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """開いただけで何も変えていないタブの Ctrl+S は完全な no-op。"""
    path = tmp_path / "doc.txt"
    path.write_text("そのまま\n", encoding="utf-8")

    editor = window.open_path(path)
    assert editor.is_modified is False

    writes = count_writes(monkeypatch)
    assert window.save_file() is True

    assert writes == []
    assert path.read_text(encoding="utf-8") == "そのまま\n"


def test_save_unmodified_keeps_mtime(window: MainWindow, tmp_path: Path) -> None:
    """タイムスタンプが動かない（rclone / NAS の同期で差分にならない）。"""
    path = tmp_path / "doc.txt"
    path.write_text("そのまま\n", encoding="utf-8")

    # 「読み込み直後の保存」と区別が付くよう、十分古い時刻に寄せておく。
    old = 1_000_000_000
    os.utime(path, (old, old))

    window.open_path(path)
    assert window.save_file() is True

    assert path.stat().st_mtime == old


def test_save_after_saving_once_is_noop(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 回保存した直後にもう一度 Ctrl+S しても、2 回目は書かない。"""
    path = tmp_path / "doc.txt"
    path.write_text("最初\n", encoding="utf-8")

    editor = window.open_path(path)
    edit_text(editor, "書き換えた\n")

    writes = count_writes(monkeypatch)
    assert window.save_file() is True
    assert len(writes) == 1  # 1 回目は変更があるので書く

    assert window.save_file() is True
    assert len(writes) == 1  # 2 回目は変更が無いので書かない
    assert path.read_text(encoding="utf-8") == "書き換えた\n"


# ----------------------------------------------------------------------
# 2. 変更があれば従来どおり書く（ガードが効きすぎていないこと）
# ----------------------------------------------------------------------
def test_save_modified_still_writes(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "doc.txt"
    path.write_text("最初\n", encoding="utf-8")

    editor = window.open_path(path)
    edit_text(editor, "書き換えた\n")
    assert editor.is_modified

    writes = count_writes(monkeypatch)
    assert window.save_file() is True

    assert writes == [path.resolve()]
    assert path.read_text(encoding="utf-8") == "書き換えた\n"
    assert editor.is_modified is False


def test_save_writes_again_after_reediting(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """no-op のあとに編集し直せば、また書けること（フラグの取りこぼし防止）。"""
    path = tmp_path / "doc.txt"
    path.write_text("最初\n", encoding="utf-8")

    editor = window.open_path(path)
    writes = count_writes(monkeypatch)

    assert window.save_file() is True  # 無変更 → 書かない
    edit_text(editor, "あとから編集\n")
    assert window.save_file() is True  # 編集後 → 書く

    assert len(writes) == 1
    assert path.read_text(encoding="utf-8") == "あとから編集\n"


# ----------------------------------------------------------------------
# 3. ガードを掛けない 2 つの保存
# ----------------------------------------------------------------------
def test_save_with_encoding_writes_even_when_unmodified(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「文字コードを指定して保存」は、本文が無変更でも書き直す。

    文字コードを選び直すこと自体が意味のある操作なので、⑧ のガードの
    対象外にしてある。
    """
    path = tmp_path / "doc.txt"
    path.write_text("あいう\n", encoding="utf-8")

    editor = window.open_path(path)
    assert editor.is_modified is False
    assert editor.encoding == "utf-8"

    # 文字コード選択ダイアログで Shift-JIS を選ぶ。
    choices = file_commands.encoding_choices("utf-8")
    monkeypatch.setattr(
        file_commands, "prompt_choice", lambda *a, **k: choices.index("cp932")
    )

    writes = count_writes(monkeypatch)
    assert window.save_file_with_encoding() is True

    assert writes == [path.resolve()]
    assert path.read_bytes() == "あいう\n".encode("cp932")
    assert editor.encoding == "cp932"


def test_save_as_writes_even_when_unmodified(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「名前を付けて保存」は、本文が無変更でも新しい保存先へ書く。"""
    source = tmp_path / "doc.txt"
    source.write_text("あいう\n", encoding="utf-8")
    target = tmp_path / "コピー.txt"

    editor = window.open_path(source)
    assert editor.is_modified is False

    monkeypatch.setattr(QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(target)])

    writes = count_writes(monkeypatch)
    assert window.save_file_as() is True

    assert writes == [target.resolve()]
    assert target.read_text(encoding="utf-8") == "あいう\n"
    assert editor.path == target.resolve()


# ----------------------------------------------------------------------
# 4. 無題タブは従来どおり「名前を付けて保存」に回る
# ----------------------------------------------------------------------
def test_untitled_unmodified_still_goes_to_save_as(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存先が無いタブは、無変更でもガードに掛からず保存先を尋ねる。

    ``editor.path is None`` の判定が ``is_modified`` の判定より手前に
    無いと、まっさらな新規タブの Ctrl+S が黙って何もしなくなってしまう。
    """
    target = tmp_path / "新規.txt"
    monkeypatch.setattr(QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(target)])

    editor = window.current_editor()
    assert editor.path is None
    assert editor.is_modified is False

    assert window.save_file() is True
    assert target.exists()
    assert editor.path == target.resolve()


# ----------------------------------------------------------------------
# 5. 保存前の関門・副作用が一切起きないこと
# ----------------------------------------------------------------------
def test_noop_save_skips_external_change_check(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """③④ の「保存直前の外部変更チェック」まで届かない。

    外部で書き換えられたファイルでも、こちらが無変更なら Ctrl+S は
    ダイアログを出さずに何もしない（そもそも書くものが無いため）。
    ダイアログが出てしまうとヘッドレスでは応答できず固まるので、
    出ないこと自体をここで縛っておく。
    """
    path = tmp_path / "doc.txt"
    path.write_text("最初\n", encoding="utf-8")
    window.open_path(path)

    # 外部から書き換える。
    path.write_text("外部で書き換えた\n", encoding="utf-8")

    asked: list[str] = []
    monkeypatch.setattr(
        file_commands,
        "prompt_external_change",
        lambda *a, **k: asked.append("shown"),
    )
    monkeypatch.setattr(
        file_commands,
        "show_message_box",
        lambda *a, **k: asked.append("shown"),
    )
    writes = count_writes(monkeypatch)

    assert window.save_file() is True
    assert asked == []
    assert writes == []
    assert path.read_text(encoding="utf-8") == "外部で書き換えた\n"


def test_noop_save_does_not_create_backup(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⑦ の「初回保存前バックアップ」も取られない（書かないので不要）。"""
    path = tmp_path / "doc.txt"
    path.write_text("そのまま\n", encoding="utf-8")
    window.open_path(path)

    backups: list[object] = []
    monkeypatch.setattr(
        file_commands, "backup_file", lambda *a, **k: backups.append(a)
    )

    assert window.save_file() is True
    assert backups == []
