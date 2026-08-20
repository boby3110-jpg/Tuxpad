"""その文字コードでは保存できない文字が本文にあるときの案内のテスト.

機能 8 は「読み込んだときの文字コードを保って保存する」ので、Shift-JIS の
実際のファイルに絵文字や ♡・— を貼り付けると保存できなくなる。これ自体は
仕様どおりだが、以前は「cp932 では表現できない文字 '♡' が含まれています」と
言うだけで、**長いファイルのどこにあるのかを探す手立てが無かった**。

ここでは「何行目に何があるか」を伝え、その文字を選択した状態にする、という
案内の方を固定している。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from editor_app import fileio
from editor_app.main_window import MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（test_save_file.py と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def captured_message(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """保存エラーのダイアログを (タイトル, 本文) として捕まえる。"""
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, text, *a, **k: messages.append((title, text)),
    )
    return messages


def make_sjis_file(path: Path, text: str, newline: str = "\n") -> Path:
    fileio.write_text_file(path, text, "cp932", newline)
    return path


# ----------------------------------------------------------------------
# fileio: 該当箇所の洗い出し
# ----------------------------------------------------------------------
def test_find_unencodable_lists_every_spot() -> None:
    text = "1行目\n2行目♡です\n3行目\n4行目🍣と—\n"
    spots, truncated = fileio.find_unencodable(text, "cp932")

    assert [char for _position, char in spots] == ["♡", "🍣", "—"]
    assert [text[position] for position, _char in spots] == ["♡", "🍣", "—"]
    assert truncated is False


def test_find_unencodable_stops_at_the_limit() -> None:
    """該当が多すぎる場合は打ち切り、「まだ先にもある」ことだけ伝える。"""
    spots, truncated = fileio.find_unencodable("♡" * 50, "cp932", limit=3)

    assert len(spots) == 3
    assert truncated is True


def test_find_unencodable_returns_nothing_for_a_clean_text() -> None:
    assert fileio.find_unencodable("普通の日本語です\n", "cp932") == ([], False)


def test_encoding_error_carries_the_spots() -> None:
    with pytest.raises(fileio.EncodingError) as excinfo:
        fileio.encode_text("あ\nい♡う\n", "cp932", "\n")

    error = excinfo.value
    assert error.encoding == "cp932"
    assert [char for _position, char in error.spots] == ["♡"]
    assert error.truncated is False


def test_spot_positions_are_counted_on_the_lf_text() -> None:
    """位置は「改行を LF に統一した本文」の上で数える。

    ``encode_text`` は保存直前に改行を CRLF へ戻すが、その後の位置で
    返してしまうと、エディタ本文（改行は LF）の別の場所を指してしまう。
    """
    text = "1行目\n2行目\n3行目♡\n"
    with pytest.raises(fileio.EncodingError) as excinfo:
        fileio.encode_text(text, "cp932", "\r\n")

    (position, char), = excinfo.value.spots
    assert char == "♡"
    assert text[position] == "♡"


def test_describe_encoding_uses_a_familiar_name() -> None:
    assert fileio.describe_encoding("cp932") == "Shift-JIS (cp932)"
    # 表に無い文字コードはコーデック名のまま返す。
    assert fileio.describe_encoding("koi8-r") == "koi8-r"


# ----------------------------------------------------------------------
# MainWindow: 保存できなかったときの案内
# ----------------------------------------------------------------------
def test_save_reports_line_numbers_of_unencodable_characters(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    path = make_sjis_file(tmp_path / "sample.txt", "1行目\n2行目\n3行目\n")
    editor = window.open_path(path)
    edit_text(editor, "1行目\n2行目♡\n3行目\n4行目🍣\n")

    assert window.save_editor(editor) is False

    title, text = captured_message[0]
    assert title == "保存できません"
    assert "Shift-JIS (cp932)" in text
    assert "該当する文字が 2 個あります" in text
    assert "2 行目: 「♡」" in text
    assert "4 行目: 「🍣」" in text


def test_save_selects_the_first_unencodable_character(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    """最初の 1 つを選択しておく（利用者がそのまま打ち直せるように）。"""
    path = make_sjis_file(tmp_path / "選択.txt", "本文\n")
    editor = window.open_path(path)
    edit_text(editor, "1行目\n2行目♡です\n")

    assert window.save_editor(editor) is False
    assert editor.textCursor().selectedText() == "♡"


def test_selection_is_not_shifted_by_earlier_emoji(window: MainWindow) -> None:
    """BMP 外の文字が手前にあっても、選択位置がずれない。

    Python は絵文字を 1 文字、Qt は 2 と数えるため、数え方を直さずに
    カーソルへ渡すと、絵文字の数だけ後ろの文字を選んでしまう。
    ここでは選択そのものを直接確かめる（cp932 で表せない文字は
    どれも BMP 外か否かに関わらず「最初の 1 件」で止まるため）。
    """
    editor = window.current_editor()
    text = "🍣🍣あいう♡おわり\n"
    editor.insertPlainText(text)

    window._select_text_range(editor, text.index("♡"), "♡")
    assert editor.textCursor().selectedText() == "♡"

    window._select_text_range(editor, text.index("あ"), "あいう")
    assert editor.textCursor().selectedText() == "あいう"

    window._select_text_range(editor, 0, "🍣")
    assert editor.textCursor().selectedText() == "🍣"


def test_save_switches_to_the_tab_that_cannot_be_saved(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    """裏のタブが保存できなかったときは、そのタブへ切り替えてから知らせる。

    ウィンドウを閉じるときの一括保存など、表に出ていないタブを保存する
    経路があるため（選択だけしても、そのタブが見えていないと意味がない）。
    """
    path = make_sjis_file(tmp_path / "裏.txt", "本文\n")
    hidden = window.open_path(path)
    edit_text(hidden, "うしろのタブ♡\n")

    front = window.new_file()
    assert window.current_editor() is front

    assert window.save_editor(hidden) is False
    assert window.current_editor() is hidden


def test_file_and_tab_are_left_untouched(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    """保存できなかったときは、ファイルもタブの状態もそのまま。"""
    path = make_sjis_file(tmp_path / "無傷.txt", "元の内容\n")
    editor = window.open_path(path)
    edit_text(editor, "書き換え♡\n")

    assert window.save_editor(editor) is False
    assert path.read_bytes() == "元の内容\n".encode("cp932")
    assert editor.is_modified is True
    assert [p.name for p in tmp_path.iterdir()] == ["無傷.txt"]


def test_many_unencodable_characters_are_summarised(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    """該当が多いときは、先頭だけ並べて残りは件数で伝える。"""
    path = make_sjis_file(tmp_path / "大量.txt", "本文\n")
    editor = window.open_path(path)
    edit_text(editor, "".join(f"{i}行目♡\n" for i in range(1, 40)))

    assert window.save_editor(editor) is False

    _title, text = captured_message[0]
    assert f"{fileio.MAX_UNENCODABLE_SPOTS} 個以上あります" in text
    # 並べるのは先頭の数件だけ（ダイアログが画面からはみ出さないように）。
    assert text.count(" 行目: ") == 5
    assert "…" in text


def test_save_as_reports_the_same_way(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_message
) -> None:
    """「名前を付けて保存」でも同じ案内が出る（文字コードは引き継がれるため）。"""
    path = make_sjis_file(tmp_path / "元.txt", "本文\n")
    editor = window.open_path(path)
    edit_text(editor, "別名で保存♡\n")

    target = tmp_path / "別名.txt"
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(target)])

    assert window.save_editor_as(editor) is False
    assert not target.exists()
    assert captured_message[0][0] == "保存できません"


def test_utf8_files_are_not_affected(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    """UTF-8 のファイルなら絵文字も ♡ もそのまま保存できる。"""
    path = tmp_path / "utf8.txt"
    path.write_text("本文\n", encoding="utf-8")
    editor = window.open_path(path)
    edit_text(editor, "絵文字🍣も♡も大丈夫\n")

    assert window.save_editor(editor) is True
    assert captured_message == []
    assert path.read_text(encoding="utf-8") == "絵文字🍣も♡も大丈夫\n"


def test_message_falls_back_to_the_raw_reason_without_spots(
    window: MainWindow, tmp_path: Path, captured_message
) -> None:
    """該当箇所を特定できなかった場合は、例外の文言をそのまま出すこと。

    ``find_unencodable`` が何も返さないのは通常は起きない（encode で
    失敗した以上どこかにある）が、受け皿として理由だけは必ず伝わるように
    してある。そこが空振りしていないことを固定しておく。
    """
    path = tmp_path / "sjis.txt"
    editor = window.open_path(make_sjis_file(path, "本文\n"))
    error = fileio.EncodingError("理由だけの例外です。", encoding="cp932")

    window._report_unencodable_characters(editor, path, error)

    title, text = captured_message[0]
    assert title == "保存できません"
    assert "理由だけの例外です。" in text
    assert "該当する文字が" not in text
