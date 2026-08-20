"""「文字コードを指定して保存」のテスト（機能 8 の逃げ道）.

文字コードを保持したまま保存する以上、Shift-JIS のファイルに絵文字や ♡ を
貼り付けると保存できなくなる。その逃げ道として「文字コードを選び直して
保存し、以後もその文字コードで保存する」機能を固定する。

ダイアログ（``dialogs.prompt_choice`` / ``QFileDialog``）は offscreen では
誰も応答できないので、必ず差し替えてから呼ぶこと。差し替え先は
**呼び出し側のモジュール名**（``editor_app.file_commands.prompt_choice``）で
指すこと（``dialogs`` 側を差し替えても、import 済みの名前には効かない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from editor_app import fileio


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（利用者の打鍵と同じ扱い）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def mock_choice(monkeypatch: pytest.MonkeyPatch, encoding: str | None) -> list[dict]:
    """文字コード選択ダイアログを差し替え、渡された引数を記録して返す。

    ``encoding`` が None ならキャンセルを再現する。それ以外は、その
    コーデック名が一覧の何番目かを探して「それを選んだ」ことにする
    （テスト側が並び順を決め打ちしないで済むように）。
    """
    calls: list[dict] = []

    def fake_prompt(parent, title, label, items, current):
        calls.append({"title": title, "label": label, "items": items, "current": current})
        if encoding is None:
            return None
        return items.index(fileio.describe_encoding(encoding))

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_prompt)
    return calls


def mock_save_dialog(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> list[str]:
    """「名前を付けて保存」ダイアログを差し替え、開かれたことを記録して返す。"""
    opened: list[str] = []

    def fake_exec(self):
        opened.append(self.windowTitle())
        if path is None:
            return QFileDialog.DialogCode.Rejected
        return QFileDialog.DialogCode.Accepted

    monkeypatch.setattr(QFileDialog, "exec", fake_exec)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(path)])
    return opened


def capture_message_box(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """保存エラーのダイアログを差し替え、(題名, 本文) を記録して返す。"""
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda parent, icon, title, text, *args: captured.append((title, text)),
    )
    return captured


# ----------------------------------------------------------------------
# 選択肢の組み立て（fileio）
# ----------------------------------------------------------------------
def test_encoding_choices_covers_the_documented_set() -> None:
    """UTF-8 系・日本語レガシー・UTF-16/32 が一通り選べること。"""
    choices = fileio.encoding_choices("utf-8")
    for expected in (
        "utf-8",
        "utf-8-sig",
        "cp932",
        "euc_jp",
        "iso2022_jp",
        "utf-16-le",
        "utf-16-be",
        "utf-32-le",
        "utf-32-be",
    ):
        assert expected in choices


def test_encoding_choices_adds_unlisted_current() -> None:
    """一覧に無い文字コード（charset-normalizer 経由）でも初期選択にできること。"""
    choices = fileio.encoding_choices("gb18030")
    assert choices[0] == "gb18030"
    assert choices.count("gb18030") == 1


def test_encoding_choices_does_not_duplicate_listed_current() -> None:
    choices = fileio.encoding_choices("cp932")
    assert choices.count("cp932") == 1


# ----------------------------------------------------------------------
# ダイアログの初期選択
# ----------------------------------------------------------------------
def test_dialog_starts_at_current_encoding(window, tmp_path, monkeypatch) -> None:
    """初期選択は今のタブの文字コードであること。

    ここがずれていると、そのまま OK を押しただけで文字コードが変わって
    しまう（利用者は「確認しただけ」のつもりでいる）。
    """
    path = tmp_path / "sjis.txt"
    path.write_bytes("日本語\n".encode("cp932"))
    editor = window.open_path(path)
    assert editor.encoding == "cp932"

    calls = mock_choice(monkeypatch, None)
    assert window.save_editor_with_encoding(editor) is False

    assert len(calls) == 1
    items = calls[0]["items"]
    assert items[calls[0]["current"]] == "Shift-JIS (cp932)"


def test_cancel_changes_nothing(window, tmp_path, monkeypatch) -> None:
    """キャンセルしたら、書き込みもタブの文字コードの変更も起きないこと。"""
    path = tmp_path / "sjis.txt"
    path.write_bytes("日本語\n".encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "書き換えた\n")

    mock_choice(monkeypatch, None)
    assert window.save_editor_with_encoding(editor) is False

    assert path.read_bytes() == "日本語\n".encode("cp932")
    assert editor.encoding == "cp932"
    assert editor.is_modified is True


def test_menu_entry_targets_the_current_tab(window, tmp_path, monkeypatch) -> None:
    """メニューから呼ぶと、いま表示しているタブが対象になること。

    「名前を付けて保存」のダイアログも塞いでおく。ここを塞がないと、
    メニュー項目の結線を間違えたときに offscreen で本物のファイル
    ダイアログが開き、assert で落ちる代わりに 60 秒固まって死ぬ。
    """
    other_path = tmp_path / "other.txt"
    other_path.write_bytes("ほかのタブ\n".encode())
    other = window.open_path(other_path)

    target_path = tmp_path / "target.txt"
    target_path.write_bytes("いまのタブ\n".encode())
    current = window.open_path(target_path)
    assert window.current_editor() is current

    opened = mock_save_dialog(monkeypatch, None)
    mock_choice(monkeypatch, "cp932")
    window.action_save_with_encoding.trigger()

    assert opened == []
    assert current.encoding == "cp932"
    assert target_path.read_bytes() == "いまのタブ\n".encode("cp932")
    assert other.encoding == "utf-8"
    assert other_path.read_bytes() == "ほかのタブ\n".encode()


def test_no_tab_does_nothing(window, monkeypatch) -> None:
    """タブが 1 つも無いときは、何も尋ねず False を返すこと。"""
    editor = window.current_editor()
    editor.set_modified(False)
    window.close_editor(editor)
    assert window.current_editor() is None

    monkeypatch.setattr(
        "editor_app.file_commands.prompt_choice",
        lambda *args: pytest.fail("タブが無いのに文字コードを尋ねてはいけない"),
    )
    assert window.save_file_with_encoding() is False


# ----------------------------------------------------------------------
# 選んだ文字コードで書かれること
# ----------------------------------------------------------------------
@pytest.mark.parametrize("encoding", fileio.SELECTABLE_ENCODINGS)
def test_writes_in_the_chosen_encoding(window, tmp_path, monkeypatch, encoding) -> None:
    """選んだ文字コードで書かれ、読み直しても同じ本文になること。

    一覧は :data:`fileio.SELECTABLE_ENCODINGS`（＝ダイアログに出す選択肢）
    そのままにしてある。直接書き並べていた頃は 9 種類のうち 3 つが抜けて
    いて、**抜けていた側に不具合があった**（JIS・EUC-JP）。
    """
    path = tmp_path / "target.txt"
    path.write_bytes("最初の内容\n".encode())
    editor = window.open_path(path)
    edit_text(editor, "書き換えた日本語\n")

    mock_choice(monkeypatch, encoding)
    assert window.save_editor_with_encoding(editor) is True

    text, detected, _newline = fileio.read_text_file(path)
    assert text == "書き換えた日本語\n"
    assert detected == encoding
    assert editor.is_modified is False


def test_later_plain_saves_use_the_new_encoding(window, tmp_path, monkeypatch) -> None:
    """保存に成功したら、以後の上書き保存 (Ctrl+S) も新しい文字コードで行われること。"""
    path = tmp_path / "sjis.txt"
    path.write_bytes("日本語\n".encode("cp932"))
    editor = window.open_path(path)

    mock_choice(monkeypatch, "utf-8")
    assert window.save_editor_with_encoding(editor) is True
    assert editor.encoding == "utf-8"

    # 以後はダイアログを出さずに保存する（差し替えを外しても呼ばれないこと）。
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_choice",
        lambda *args: pytest.fail("上書き保存で文字コードを聞き直してはいけない"),
    )
    edit_text(editor, "絵文字🍣も書ける\n")
    assert window.save_editor(editor) is True
    assert path.read_bytes() == "絵文字🍣も書ける\n".encode()


def test_encoding_is_kept_when_plain_save_follows(window, tmp_path, monkeypatch) -> None:
    """普通の上書き保存では文字コードが変わらないこと（機能 8 の回帰）。"""
    path = tmp_path / "sjis.txt"
    path.write_bytes("日本語\n".encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "書き換えた\n")

    assert window.save_editor(editor) is True
    assert editor.encoding == "cp932"
    assert path.read_bytes() == "書き換えた\n".encode("cp932")


# ----------------------------------------------------------------------
# 保存できない文字があった場合
# ----------------------------------------------------------------------
def test_unencodable_reuses_the_save_error_dialog(window, tmp_path, monkeypatch) -> None:
    """選んだ文字コードで表せない文字があれば、既存の保存エラーを出して保存しないこと。"""
    path = tmp_path / "utf8.txt"
    path.write_bytes("最初の内容\n".encode())
    editor = window.open_path(path)
    edit_text(editor, "1行目\n♡が入っている\n")

    captured = capture_message_box(monkeypatch)
    mock_choice(monkeypatch, "cp932")
    assert window.save_editor_with_encoding(editor) is False

    assert len(captured) == 1
    title, text = captured[0]
    assert title == "保存できません"
    assert "Shift-JIS (cp932)" in text
    assert "2 行目: 「♡」" in text
    # 元のファイルは 1 バイトも変わっていないこと。
    assert path.read_bytes() == "最初の内容\n".encode()
    # 文字コードも書き換わっていないこと（保存できていないため）。
    assert editor.encoding == "utf-8"


def test_unencodable_selects_the_first_spot(window, tmp_path, monkeypatch) -> None:
    """該当する文字の 1 つめを選択した状態にしてから知らせること。"""
    path = tmp_path / "utf8.txt"
    path.write_bytes("最初の内容\n".encode())
    editor = window.open_path(path)
    edit_text(editor, "1行目\n♡が入っている\n")

    capture_message_box(monkeypatch)
    mock_choice(monkeypatch, "cp932")
    window.save_editor_with_encoding(editor)

    assert editor.textCursor().selectedText() == "♡"


def test_unencodable_message_does_not_claim_it_is_the_file_encoding(
    window, tmp_path, monkeypatch
) -> None:
    """「このファイルの文字コードは〜」とは言わないこと。

    利用者がいま選んだ文字コードで失敗しただけで、ファイルの文字コードは
    別（この例では UTF-8）。同じダイアログを使い回すが、そこだけは
    事実に合わせて言い分けている。
    """
    path = tmp_path / "utf8.txt"
    path.write_bytes("最初の内容\n".encode())
    editor = window.open_path(path)
    edit_text(editor, "♡\n")

    captured = capture_message_box(monkeypatch)
    mock_choice(monkeypatch, "cp932")
    window.save_editor_with_encoding(editor)

    _title, text = captured[0]
    assert "選んだ文字コード Shift-JIS (cp932)" in text
    assert "このファイルの文字コードは" not in text


def test_plain_save_error_points_at_this_feature(window, tmp_path, monkeypatch) -> None:
    """上書き保存で詰まったときは、この機能を逃げ道として案内すること。"""
    path = tmp_path / "sjis.txt"
    path.write_bytes("日本語\n".encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "♡\n")

    captured = capture_message_box(monkeypatch)
    assert window.save_editor(editor) is False

    _title, text = captured[0]
    assert "このファイルの文字コードは Shift-JIS (cp932) です" in text
    assert "文字コードを指定して保存" in text


# ----------------------------------------------------------------------
# 無題タブ（保存先が無い）
# ----------------------------------------------------------------------
def test_untitled_tab_asks_for_a_path(window, tmp_path, monkeypatch) -> None:
    """無題タブでは保存先を尋ねてから、選んだ文字コードで書くこと。"""
    editor = window.current_editor()
    assert editor.path is None
    edit_text(editor, "新しい日本語\n")

    target = tmp_path / "新規.txt"
    opened = mock_save_dialog(monkeypatch, target)
    mock_choice(monkeypatch, "cp932")

    assert window.save_editor_with_encoding(editor) is True
    assert opened == ["名前を付けて保存"]
    assert target.read_bytes() == "新しい日本語\n".encode("cp932")
    assert editor.path == fileio.resolve_path(target)
    assert editor.encoding == "cp932"


def test_untitled_tab_cancelled_path_writes_nothing(window, tmp_path, monkeypatch) -> None:
    """保存先の選択をキャンセルしたら、何も書かず文字コードも変えないこと。"""
    editor = window.current_editor()
    edit_text(editor, "新しい日本語\n")

    mock_save_dialog(monkeypatch, None)
    mock_choice(monkeypatch, "cp932")

    assert window.save_editor_with_encoding(editor) is False
    assert list(tmp_path.iterdir()) == []
    assert editor.path is None
    assert editor.encoding == "utf-8"


# ----------------------------------------------------------------------
# ステータスバーの表示
# ----------------------------------------------------------------------
def test_status_bar_shows_current_tab_encoding(window, tmp_path) -> None:
    """タブを切り替えたら、そのタブの文字コードに表示が変わること。"""
    utf8_path = tmp_path / "utf8.txt"
    utf8_path.write_bytes("あ\n".encode())
    sjis_path = tmp_path / "sjis.txt"
    sjis_path.write_bytes("あ\n".encode("cp932"))

    utf8_editor = window.open_path(utf8_path)
    assert window._encoding_label.text() == "UTF-8"

    sjis_editor = window.open_path(sjis_path)
    assert window._encoding_label.text() == "Shift-JIS (cp932)"

    window.tabs.setCurrentWidget(utf8_editor)
    assert window._encoding_label.text() == "UTF-8"
    window.tabs.setCurrentWidget(sjis_editor)
    assert window._encoding_label.text() == "Shift-JIS (cp932)"


def test_status_bar_follows_save_with_encoding(window, tmp_path, monkeypatch) -> None:
    """この機能で保存し直したら、表示も新しい文字コードに変わること。"""
    path = tmp_path / "sjis.txt"
    path.write_bytes("日本語\n".encode("cp932"))
    editor = window.open_path(path)
    assert window._encoding_label.text() == "Shift-JIS (cp932)"

    mock_choice(monkeypatch, "utf-8")
    assert window.save_editor_with_encoding(editor) is True
    assert window._encoding_label.text() == "UTF-8"


def test_status_bar_ignores_background_tab(window, tmp_path, monkeypatch) -> None:
    """表示していないタブの文字コードが変わっても、表示は今のタブのままであること。"""
    hidden_path = tmp_path / "hidden.txt"
    hidden_path.write_bytes("あ\n".encode())
    hidden = window.open_path(hidden_path)

    front_path = tmp_path / "front.txt"
    front_path.write_bytes("あ\n".encode("cp932"))
    front = window.open_path(front_path)
    assert window.current_editor() is front

    hidden.encoding = "euc_jp"
    assert window._encoding_label.text() == "Shift-JIS (cp932)"


def test_status_bar_is_empty_without_tabs(window) -> None:
    """タブが 1 つも無いときは何も出さないこと。"""
    editor = window.current_editor()
    editor.set_modified(False)
    window.close_editor(editor)
    assert window.current_editor() is None
    assert window._encoding_label.text() == ""


def test_status_bar_follows_external_reload(window, tmp_path, monkeypatch) -> None:
    """外部で書き換わったファイルを再読み込みしたら、表示も追いつくこと。

    再読み込みは文字コードを判定し直すので、UTF-8 のファイルが
    Shift-JIS に置き換わればタブの文字コードもそこで変わる。
    """
    path = tmp_path / "swapped.txt"
    path.write_bytes("あ\n".encode())
    editor = window.open_path(path)
    assert window._encoding_label.text() == "UTF-8"

    window._reload_editor_content(editor, "あ\n", "cp932", "\n")
    assert window._encoding_label.text() == "Shift-JIS (cp932)"
