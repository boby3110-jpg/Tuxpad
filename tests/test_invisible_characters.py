"""目に見えない文字（NBSP など）を、開いて保存しただけで失わないことのテスト。

``QTextDocument.toPlainText()`` は **改行しない空白 (U+00A0, NBSP) を普通の
半角空白へ黙って置き換える**（Qt 側の仕様）。本文の取り出しにこれを使うと、
NBSP を含むファイルを**開いて、何も触らずに保存しただけで**全ての NBSP が
普通の空白に変わってしまう。NBSP と空白は画面上で見分けが付かないため、
利用者は変わったことに気づけない。ウェブや Word から貼り付けた日本語の
文書にはよく紛れ込む文字なので、実際のファイルで起こりうる。

そこで本文の取り出しは :func:`editor_app.editor.document_text` に一本化して
ある。ここではその約束（NBSP は残す／段落区切りと Shift+Enter の行区切りは
``\\n`` にする）と、そこにぶら下がる保存・外部変更・検索の挙動を固定する。

**Shift+Enter を ``\\n`` にする方は「直しすぎない」ための固定**でもある。
QTextEdit は Shift+Enter で U+2028 を入れるので、これを残すと「利用者は
改行したつもりなのに、ファイルには目に見えない文字が 1 つ書かれる」ことに
なる（Shift-JIS では保存すらできなくなる）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from editor_app import fileio
from editor_app.editor import EditorWidget, document_text
from editor_app.main_window import MainWindow

NBSP = "\u00a0"
ZERO_WIDTH_SPACE = "\u200b"
LINE_SEPARATOR = "\u2028"
PARAGRAPH_SEPARATOR = "\u2029"


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


# ----------------------------------------------------------------------
# document_text（本文の取り出し）
# ----------------------------------------------------------------------
def test_document_text_keeps_the_no_break_space(qtbot) -> None:
    """NBSP はそのまま残す（``toPlainText()`` は空白に変えてしまう）。"""
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_content(f"あ{NBSP}い\n")

    assert document_text(editor) == f"あ{NBSP}い\n"
    # Qt の toPlainText() が実際に置き換えていること（前提の確認）。
    assert editor.toPlainText() == "あ い\n"


def test_document_text_turns_paragraph_separators_into_newlines(qtbot) -> None:
    """読み込んだ改行は Qt の中では U+2029 になっている。``\\n`` で返すこと。"""
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_content("1行目\n2行目\n")

    assert PARAGRAPH_SEPARATOR in editor.document().toRawText()
    assert document_text(editor) == "1行目\n2行目\n"


def test_document_text_turns_shift_enter_into_a_newline(qtbot) -> None:
    """Shift+Enter で入る U+2028 は、ファイルには改行として書く。

    残してしまうと「改行したつもりが、目に見えない文字が 1 つ入っている」
    ファイルができる（Shift-JIS では保存もできなくなる）。
    """
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_content("あ")
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    editor.insertPlainText("い")

    assert LINE_SEPARATOR in editor.document().toRawText()
    assert document_text(editor) == "あ\nい"


def test_document_text_counts_the_same_as_to_plain_text(qtbot) -> None:
    """置き換えは 1 文字 → 1 文字。文字位置の計算をそのまま使える前提。"""
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_content(f"あ{NBSP}い\nう🍣え\n")

    assert len(document_text(editor)) == len(editor.toPlainText())


def test_document_text_of_an_empty_document_is_empty(qtbot) -> None:
    editor = EditorWidget()
    qtbot.addWidget(editor)

    assert document_text(editor) == ""


# ----------------------------------------------------------------------
# 開いて保存する（実際に起きていた本文の書き換わり）
# ----------------------------------------------------------------------
def test_saving_an_untouched_file_keeps_the_no_break_space(
    window: MainWindow, tmp_path: Path
) -> None:
    """NBSP のあるファイルを開いて保存しても、1 バイトも変わらない。"""
    path = tmp_path / "nbsp.txt"
    original = f"氏名{NBSP}太郎\n住所{NBSP}東京\n".encode("utf-8")
    path.write_bytes(original)

    editor = window.open_path(path)
    editor.set_modified(True)  # 「保存」を実際に走らせるため
    assert window.save_editor(editor) is True

    assert path.read_bytes() == original


def test_saving_keeps_the_no_break_space_after_editing(
    window: MainWindow, tmp_path: Path
) -> None:
    """編集した後の保存でも、触っていない行の NBSP は残る。"""
    path = tmp_path / "nbsp.txt"
    path.write_bytes(f"氏名{NBSP}太郎\n".encode("utf-8"))

    editor = window.open_path(path)
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    editor.insertPlainText("備考\n")

    assert window.save_editor(editor) is True
    assert path.read_bytes() == f"氏名{NBSP}太郎\n備考\n".encode("utf-8")


def test_shift_enter_is_saved_as_a_newline(window: MainWindow, tmp_path: Path) -> None:
    """Shift+Enter で改行した本文は、普通の改行としてファイルに入る。"""
    path = tmp_path / "shift_enter.txt"
    path.write_bytes("あ".encode("utf-8"))

    editor = window.open_path(path)
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    editor.insertPlainText("い")

    assert window.save_editor(editor) is True
    assert path.read_bytes() == "あ\nい".encode("utf-8")


# ----------------------------------------------------------------------
# 外部変更の検知（同じ中身を「変わった」と誤解しない）
# ----------------------------------------------------------------------
def test_external_change_check_does_not_trip_on_a_no_break_space(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NBSP を含むファイルを、中身が同じなのに「変わった」と言わない。

    本文の取り出しが NBSP を空白に変えていると、ディスクの中身と本文が
    永遠に食い違い、**触ってもいないのに毎回「再読み込みしますか」**に
    なる（保存も止められる）。
    """
    path = tmp_path / "nbsp.txt"
    path.write_bytes(f"氏名{NBSP}太郎\n".encode("utf-8"))
    editor = window.open_path(path)

    asked: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *a, **k: asked.append("asked"),
    )
    # ディスク側は同じ中身のまま書き直された（同期ソフト等）という状況。
    path.write_bytes(f"氏名{NBSP}太郎\n".encode("utf-8"))
    window._pending_reload_paths.add(str(fileio.resolve_path(path)))
    window._process_pending_reloads()

    assert asked == []
    assert editor.is_modified is False


# ----------------------------------------------------------------------
# 検索（検索欄に貼り付けた NBSP でも見つかる）
# ----------------------------------------------------------------------
def test_searching_for_a_no_break_space_finds_it(window: MainWindow) -> None:
    """検索欄に貼り付けた NBSP が、本文の NBSP に当たる。

    本文だけ NBSP を残して検索欄を空白に変えてしまうと、**画面では同じに
    見えるのに絶対に見つからない**という状態になる。
    """
    window.new_file()
    edit_text(window.current_editor(), f"氏名{NBSP}太郎\n氏名 花子\n")
    window.search_panel._input.setPlainText(f"氏名{NBSP}")

    matches = window.search_all_tabs(window.search_panel.query())

    assert [match.line for match in matches] == [1]


# ----------------------------------------------------------------------
# describe_char（見えない文字を名前で伝える）
# ----------------------------------------------------------------------
def test_describe_char_names_the_no_break_space() -> None:
    assert fileio.describe_char(NBSP) == "改行しない空白 (U+00A0)"


def test_describe_char_leaves_ordinary_characters_alone() -> None:
    assert fileio.describe_char("♡") == "♡"
    assert fileio.describe_char("あ") == "あ"
    assert fileio.describe_char("🍣") == "🍣"


def test_describe_char_falls_back_to_the_unicode_name() -> None:
    """一覧に無い見えない文字でも、コードポイントだけは伝える。"""
    described = fileio.describe_char("\u2003")  # EM SPACE

    assert described == "EM SPACE (U+2003)"


def test_saving_shift_jis_names_the_invisible_character(
    window: MainWindow, tmp_path: Path, captured_message: list[tuple[str, str]]
) -> None:
    """Shift-JIS に保存できない見えない文字は、「空白」ではなく名前で知らせる。

    NBSP を残すようにした結果、**貼り付けた見えない文字のせいで Shift-JIS の
    ファイルが保存できなくなる**場面が現実に起こりうるようになった。
    そのとき「1 行目:「 」」とだけ出しても、利用者には空白にしか見えず
    直しようがない。

    NBSP そのものは、利用者の指示で**黙って半角空白に落とす**ように
    変わった（``test_no_break_space_downgrade.py``）。ここでは同じく
    Shift-JIS で表せない「幅の無い空白」(U+200B) で確かめる——特例は
    NBSP だけで、他の見えない文字は今までどおり止めて名前で知らせる。
    """
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "氏名 太郎\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, f"氏名{ZERO_WIDTH_SPACE}太郎\n")

    assert window.save_editor(editor) is False

    (_title, body), = captured_message
    assert "幅の無い空白 (U+200B)" in body
    # 元のファイルは壊れていない。
    assert path.read_bytes() == "氏名 太郎\n".encode("cp932")
