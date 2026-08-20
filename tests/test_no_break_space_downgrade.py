"""「改行しない空白」(NBSP) を、表せない文字コードでだけ半角空白へ落とす。

27 回目に本文の取り出し (:func:`editor_app.editor.document_text`) で NBSP を
残すようにした結果、**Shift-JIS のファイルに web や Word から貼り付けると
「保存できません」で止まる**ようになった。利用者の回答（2026-08-18）は
折衷案の採用で、**NBSP をそもそも表せない文字コードで保存するときだけ**
普通の半角空白へ黙って落とす。

ここで固定するのは 4 つ:

1. **落とすのは NBSP だけ**。同じく表せない ♡ や絵文字は従来どおり止める。
2. **落とすのは表せない文字コードのときだけ**。UTF-8 系・UTF-16/32 では
   今までどおり NBSP がそのまま残る（``test_invisible_characters.py`` 側）。
3. **本文の側もディスクに合わせて揃える**。揃えないと、本文とディスクが
   永久に食い違って「触ってもいないのに毎回『再読み込みしますか？』」に
   なる。
4. **揃えるのは書き込みが成功した後だけ**。取りやめた保存で本文の NBSP を
   落としてしまうと、UTF-8 で保存し直す逃げ道を選んでも戻らなくなる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_app import fileio
from editor_app.editor import document_text
from editor_app.main_window import MainWindow

NBSP = "\u00a0"

#: NBSP を表せない文字コード（＝落とす対象）。
LOSSY = ("cp932", "euc_jp", "iso2022_jp")

#: NBSP をそのまま表せる文字コード（＝触らない）。
LOSSLESS = ("utf-8", "utf-8-sig", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（他のテストと同じやり方）。"""
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def no_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """保存まわりのダイアログを捕まえる（「黙って」落とすことの確認用）。"""
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, text, *a, **k: messages.append((title, text)),
    )
    return messages


# ----------------------------------------------------------------------
# fileio.downgrade_no_break_spaces（規則そのもの）
# ----------------------------------------------------------------------
@pytest.mark.parametrize("encoding", LOSSY)
def test_downgrades_where_the_encoding_cannot_hold_it(encoding: str) -> None:
    """表せない文字コードでは、NBSP が普通の半角空白になる。"""
    text, positions = fileio.downgrade_no_break_spaces(f"氏名{NBSP}太郎", encoding)

    assert text == "氏名 太郎"
    assert positions == [2]
    # 落とした後は、その文字コードで本当に書ける。
    assert text.encode(encoding)


@pytest.mark.parametrize("encoding", LOSSLESS)
def test_leaves_it_alone_where_the_encoding_can_hold_it(encoding: str) -> None:
    """表せる文字コードでは 1 文字も触らない。"""
    original = f"氏名{NBSP}太郎"

    assert fileio.downgrade_no_break_spaces(original, encoding) == (original, [])


def test_reports_every_position() -> None:
    """位置は Python の文字位置で、全ての NBSP を挙げる。"""
    text, positions = fileio.downgrade_no_break_spaces(
        f"{NBSP}あ{NBSP}い{NBSP}", "cp932"
    )

    assert text == " あ い "
    assert positions == [0, 2, 4]


def test_positions_are_unaffected_by_astral_characters() -> None:
    """絵文字（BMP 外）が混ざっても、位置は Python の文字位置のまま。

    Qt の文書内位置（UTF-16 コード単位）とはずれるが、直すのは
    :meth:`~editor_app.file_commands.FileCommandsMixin.
    _apply_no_break_space_downgrade` の仕事。ここで先回りして
    直してしまうと二重に足すことになる。
    """
    _text, positions = fileio.downgrade_no_break_spaces(f"🍣{NBSP}", "cp932")

    assert positions == [1]


def test_text_without_a_no_break_space_is_returned_as_is() -> None:
    assert fileio.downgrade_no_break_spaces("氏名 太郎", "cp932") == ("氏名 太郎", [])


def test_an_unknown_codec_name_is_left_alone() -> None:
    """知らないコーデック名では本文を触らない（判断できないため）。

    「表せない」と決めつけて書き換えるより、何もせずこの後の
    :func:`~editor_app.fileio.encode_text` に同じ ``LookupError`` で
    止めてもらう方が安全。
    """
    original = f"氏名{NBSP}太郎"

    assert fileio.downgrade_no_break_spaces(original, "no-such-codec") == (original, [])

    with pytest.raises(LookupError):
        fileio.encode_text(original, "no-such-codec", "\n")


def test_only_the_no_break_space_is_special_cased() -> None:
    """同じく表せない見えない文字（幅の無い空白）は落とさない。"""
    original = "氏名\u200b太郎"  # 幅の無い空白 (U+200B)

    assert fileio.downgrade_no_break_spaces(original, "cp932") == (original, [])


# ----------------------------------------------------------------------
# 保存（ディスクに書かれる中身）
# ----------------------------------------------------------------------
@pytest.mark.parametrize("encoding", LOSSY)
def test_saving_writes_a_plain_space(
    window: MainWindow,
    tmp_path: Path,
    no_dialogs: list[tuple[str, str]],
    encoding: str,
) -> None:
    """貼り付けた NBSP があっても保存でき、ファイルには半角空白が入る。"""
    path = tmp_path / "legacy.txt"
    fileio.write_text_file(path, "氏名 太郎\n", encoding, "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎\n")

    assert window.save_editor(editor) is True

    assert path.read_bytes() == "氏名 太郎\n".encode(encoding)
    # 「黙って」落とす＝ダイアログは 1 つも出ない。
    assert no_dialogs == []


def test_saving_syncs_the_buffer_with_what_was_written(
    window: MainWindow, tmp_path: Path, no_dialogs: list[tuple[str, str]]
) -> None:
    """本文の側もディスクに合わせて揃え、「未保存」の印は消える。

    揃えないと本文とディスクが永久に食い違い、``file_watch`` が
    **触ってもいないのに毎回「再読み込みしますか？」**を出す。
    """
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎\n")

    assert window.save_editor(editor) is True

    assert document_text(editor) == "氏名 太郎\n"
    assert editor.is_modified is False


def test_the_reload_check_does_not_trip_after_saving(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存した後の外部変更チェックが「変わった」と言わない。

    本文だけ NBSP のままだと、自分で保存した直後に必ずここへ引っかかる。
    """
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎\n")
    assert window.save_editor(editor) is True

    asked: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *a, **k: asked.append("asked"),
    )
    window._pending_reload_paths.add(str(fileio.resolve_path(path)))
    window._process_pending_reloads()

    assert asked == []
    assert editor.is_modified is False


def test_the_buffer_is_synced_around_astral_characters(
    window: MainWindow, tmp_path: Path
) -> None:
    """絵文字を挟んでも、置き換わるのは NBSP だけ（位置の数え方の確認）。

    Qt の文書内位置は UTF-16 コード単位なので、絵文字 1 つにつき 1 ずれる。
    直し忘れると**関係ない場所の文字が空白に化ける**。

    絵文字のある本文は cp932 では保存そのものが止まるので、保存を通さず
    本文の同期だけを直接呼んで確かめる。
    """
    path = tmp_path / "utf8.txt"
    path.write_bytes("最初\n".encode())
    editor = window.open_path(path)
    edit_text(editor, f"🍣{NBSP}🍣{NBSP}あ\n")

    text, positions = fileio.downgrade_no_break_spaces(document_text(editor), "cp932")
    assert text == "🍣 🍣 あ\n"
    window._apply_no_break_space_downgrade(editor, positions)

    assert document_text(editor) == "🍣 🍣 あ\n"


def test_undo_brings_the_no_break_space_back(
    window: MainWindow, tmp_path: Path
) -> None:
    """落としたのは黙ってなので、Ctrl+Z 一回で取り返せるようにしてある。"""
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎{NBSP}様\n")
    assert window.save_editor(editor) is True
    assert document_text(editor) == "氏名 太郎 様\n"

    editor.undo()

    assert document_text(editor) == f"氏名{NBSP}太郎{NBSP}様\n"


def test_a_stale_position_is_skipped(window: MainWindow, tmp_path: Path) -> None:
    """指した先が NBSP でなければ何もしない（関係ない場所を壊さない歯止め）。"""
    path = tmp_path / "utf8.txt"
    path.write_bytes("最初\n".encode())
    editor = window.open_path(path)
    edit_text(editor, "氏名 太郎\n")

    window._apply_no_break_space_downgrade(editor, [0, 1, 2])

    assert document_text(editor) == "氏名 太郎\n"


def test_nothing_happens_without_positions(window: MainWindow, tmp_path: Path) -> None:
    """落とすものが無いときは本文に一切触れない（未保存の印も付かない）。"""
    path = tmp_path / "utf8.txt"
    path.write_bytes("氏名 太郎\n".encode())
    editor = window.open_path(path)

    window._apply_no_break_space_downgrade(editor, [])

    assert document_text(editor) == "氏名 太郎\n"
    assert editor.is_modified is False


# ----------------------------------------------------------------------
# 特例は NBSP だけ（他の保存できない文字は今までどおり止める）
# ----------------------------------------------------------------------
def test_an_emoji_still_stops_the_save(
    window: MainWindow, tmp_path: Path, no_dialogs: list[tuple[str, str]]
) -> None:
    """NBSP と絵文字が両方あっても、絵文字は今までどおり保存を止める。"""
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎🍣\n")

    assert window.save_editor(editor) is False

    (title, body), = no_dialogs
    assert title == "保存できません"
    assert "🍣" in body
    assert "改行しない空白" not in body
    assert path.read_bytes() == "氏名 太郎\n".encode("cp932")


def test_a_stopped_save_leaves_the_no_break_space_in_the_buffer(
    window: MainWindow, tmp_path: Path, no_dialogs: list[tuple[str, str]]
) -> None:
    """止まった保存では本文の NBSP を落とさない。

    落としてしまうと、**UTF-8 で保存し直す逃げ道を選んでも NBSP が
    戻らない**（利用者から見れば、保存できなかったはずなのに本文が
    書き換わっている）。
    """
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎🍣\n")

    assert window.save_editor(editor) is False

    assert document_text(editor) == f"氏名{NBSP}太郎🍣\n"


def test_saving_as_utf8_after_a_stopped_shift_jis_save_keeps_it(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_dialogs: list[tuple[str, str]],
) -> None:
    """逃げ道（UTF-8 を指定して保存）を選べば、NBSP はそのまま残る。"""
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{NBSP}太郎🍣\n")
    assert window.save_editor(editor) is False

    choices = fileio.encoding_choices(editor.encoding)
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_choice",
        lambda *a, **k: choices.index("utf-8"),
    )
    assert window.save_editor_with_encoding(editor) is True

    assert path.read_bytes() == f"氏名{NBSP}太郎🍣\n".encode()
    assert document_text(editor) == f"氏名{NBSP}太郎🍣\n"


def test_choosing_shift_jis_for_a_utf8_tab_downgrades_it(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UTF-8 のタブでも、Shift-JIS を指定して保存すれば落ちる。

    判断の材料は**保存先の文字コード**であって、タブが元々何だったかではない。
    """
    path = tmp_path / "utf8.txt"
    path.write_bytes(f"氏名{NBSP}太郎\n".encode())
    editor = window.open_path(path)

    choices = fileio.encoding_choices(editor.encoding)
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_choice",
        lambda *a, **k: choices.index("cp932"),
    )
    assert window.save_editor_with_encoding(editor) is True

    assert path.read_bytes() == "氏名 太郎\n".encode("cp932")
    assert document_text(editor) == "氏名 太郎\n"
