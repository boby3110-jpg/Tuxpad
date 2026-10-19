"""「ファイル → 再読み込み」のテスト（引き継ぎ ⑭）.

外部変更の自動検知が入っても、**手で確実に読み直せる道**は別に欲しい、
という利用者の要望で足した項目。それまでは「文字コードを指定して開き直す」で
代用していたが、**そのままでよいのに毎回文字コードを選ばされるのが余計**
だった。

ここで見張るのは、依頼にあった 6 点である。

(a) 選ぶだけで（文字コードを訊かれずに）新しい内容に置き換わる
(b) **いまの文字コードが保たれる**（勝手に判定し直さない）
(c) 未保存の変更があるタブでは確認が出て、「いいえ」なら
    **本文もカーソル位置も一切変わらない**
(d) 無題タブでは「読み直す元がありません」と伝えるだけで落ちない
(e) 読めなかったとき（ファイルが消えた等）に**タブの中身を空にしない**
(f) メニュー項目に**ショートカットが設定されていない**

(b) がいちばん効くのは、判定が拮抗したファイルを利用者が自分で開き直した
後である。そこで読み直すたびに判定し直すと、せっかく選んだ読み方が
戻ってしまい、「再読み込みしたら文字化けした」という形で現れる。

ダイアログの差し替え先は ``editor_app.file_commands``（中核がそこにある）。
**間違えると本物のモーダルが開き、ヘッドレスのテストが固まる。**
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import AMBIGUOUS_ENCODING_MARK, MainWindow

#: cp932 でも euc_jp でも読めるバイト列（`test_encoding_ambiguity.py` と同じ）。
#: 判定は euc_jp、負けた cp932 が「拮抗の印」の元になる。
AMBIGUOUS_BYTES = "あいうえお\n".encode("euc_jp")


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "メモ.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    return path


def _capture_messages(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """``show_message_box`` を差し替え、(題, 本文) を記録する。"""
    captured: list[tuple[str, str]] = []

    def fake(_parent, _icon, title, message, *_args, **_kwargs):
        captured.append((title, message))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return captured


def _answer(monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton) -> list[str]:
    """確認ダイアログに決まった答えを返させ、出た本文を記録する。"""
    messages: list[str] = []

    def fake(_parent, _icon, _title, message, *_args, **_kwargs):
        messages.append(message)
        return answer

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return messages


def _forbid_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """文字コードの選択ダイアログが出たら、その場で失敗させる。

    「再読み込み」の存在理由そのもの（文字コードを訊かれないこと）なので、
    黙って通り過ぎないように**呼ばれたら落ちる**形にしておく。
    """

    def fake(*_args, **_kwargs):
        raise AssertionError("再読み込みで文字コードを訊いてはいけない")

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake)


# ----------------------------------------------------------------------
# (a) 選ぶだけで新しい内容に置き換わる
# ----------------------------------------------------------------------
def test_reload_replaces_the_content_without_asking_for_an_encoding(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = window.open_path(sample)
    _forbid_choice(monkeypatch)

    sample.write_text("外部で書き換わった内容\n", encoding="utf-8")
    assert window.reload_file_from_disk() is True

    assert editor.toPlainText() == "外部で書き換わった内容\n"
    assert editor.is_modified is False


def test_reload_keeps_the_cursor_position(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読み直してもカーソル位置はなるべく保つ（外部変更の再読み込みと同じ扱い）。"""
    editor = window.open_path(sample)
    _forbid_choice(monkeypatch)
    cursor = editor.textCursor()
    cursor.setPosition(3)
    editor.setTextCursor(cursor)

    sample.write_text("外部で書き換わった内容\n", encoding="utf-8")
    assert window.reload_file_from_disk() is True

    assert editor.textCursor().position() == 3


def test_reload_updates_the_disk_signature(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読み直した後は「今のディスクを知っている」状態になる。

    ここが更新されないと、読み直した直後に保存しようとしただけで
    保存直前のチェックが「外部で変わっている」と言い出す。
    """
    editor = window.open_path(sample)
    _forbid_choice(monkeypatch)
    before = editor.disk_signature

    sample.write_text("外部で書き換わった内容\n", encoding="utf-8")
    assert window.reload_file_from_disk() is True

    assert editor.disk_signature != before
    assert editor.disk_signature is not None


def test_reload_follows_the_newline_on_disk(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改行コードは読み直した結果に従う（保存でそれが書き戻るため）。"""
    editor = window.open_path(sample)
    _forbid_choice(monkeypatch)
    assert editor.newline == "\n"

    sample.write_bytes("CRLF になった内容\r\n".encode("utf-8"))
    assert window.reload_file_from_disk() is True

    assert editor.newline == "\r\n"


# ----------------------------------------------------------------------
# (b) いまの文字コードが保たれる（判定し直さない）
# ----------------------------------------------------------------------
def test_reload_keeps_the_current_encoding(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """利用者が選んだ文字コードを、読み直しで勝手に変えない。

    拮抗するバイト列を cp932 として開き直した後に再読み込みすると、
    判定し直す作りでは euc_jp に戻ってしまう（＝選んだはずの読み方が
    消え、文字化けして見える）。
    """
    path = tmp_path / "拮抗.txt"
    path.write_bytes(AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding == "euc_jp"

    # 利用者が自分で cp932 を選んで開き直した状態を作る。
    editor.encoding = "cp932"
    _forbid_choice(monkeypatch)

    assert window.reload_file_from_disk() is True

    assert editor.encoding == "cp932"
    # 本文も cp932 として読んだ結果（半角カナ）になっていること。
    assert editor.toPlainText() == AMBIGUOUS_BYTES.decode("cp932")


def test_reload_keeps_the_ambiguity_mark(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拮抗の印は、読み直しただけでは消えない。

    印（ステータスバーの ``?``）は「この文字コードだと判定したが推測である」
    という警告なので、**利用者がまだ文字コードを選んでいないのに**消すと、
    警告だけが黙って失われる。「文字コードを指定して開き直す」で**選んだ**
    ときに消えるのが正しい（そちらは拮抗が解決している）。
    """
    path = tmp_path / "拮抗.txt"
    path.write_bytes(AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding_alternatives  # 開いた時点で印が出ている
    before = editor.encoding_alternatives
    _forbid_choice(monkeypatch)

    assert window.reload_file_from_disk() is True

    assert editor.encoding_alternatives == before
    assert AMBIGUOUS_ENCODING_MARK in window._encoding_label.text()


def test_reopen_with_a_chosen_encoding_still_clears_the_mark(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**対照**: 文字コードを選んで開き直したときは、今までどおり印が消える。

    共通部分を切り出したときに、片方の約束をもう片方へ漏らしていないかを
    見る（印を残す側だけを足したつもりが、選んだ側も残してしまう形）。
    """
    from editor_app.fileio import encoding_choices

    path = tmp_path / "拮抗.txt"
    path.write_bytes(AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding_alternatives

    # 利用者が一覧から cp932 を選んだ、という形にする。
    chosen = encoding_choices(editor.encoding).index("cp932")
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_choice", lambda *a, **k: chosen
    )
    # 読めなかったとき等に本物のモーダルが開いて固まらないよう、
    # 念のため差し替えておく（このテストでは呼ばれないのが正しい）。
    captured = _capture_messages(monkeypatch)

    assert window.reopen_editor_with_encoding(editor) is True

    assert captured == []
    assert editor.encoding == "cp932"
    assert editor.encoding_alternatives == ()


# ----------------------------------------------------------------------
# (c) 未保存の変更があるタブでは確認が出る
# ----------------------------------------------------------------------
def test_reload_asks_before_discarding_unsaved_changes(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = window.open_path(sample)
    editor.setPlainText("編集中の内容\n")
    editor.set_modified(True)
    _forbid_choice(monkeypatch)
    messages = _answer(monkeypatch, QMessageBox.StandardButton.Yes)

    sample.write_text("外部で書き換わった内容\n", encoding="utf-8")
    assert window.reload_file_from_disk() is True

    assert len(messages) == 1
    assert "未保存の変更があります" in messages[0]
    assert editor.toPlainText() == "外部で書き換わった内容\n"


def test_declining_the_reload_changes_nothing_at_all(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「いいえ」なら、本文もカーソル位置も未保存マークも動かない。"""
    editor = window.open_path(sample)
    editor.setPlainText("編集中の内容です\n")
    editor.set_modified(True)
    cursor = editor.textCursor()
    cursor.setPosition(4)
    editor.setTextCursor(cursor)
    before_signature = editor.disk_signature
    _forbid_choice(monkeypatch)
    messages = _answer(monkeypatch, QMessageBox.StandardButton.No)

    sample.write_text("外部で書き換わった内容\n", encoding="utf-8")
    assert window.reload_file_from_disk() is False

    assert len(messages) == 1
    assert editor.toPlainText() == "編集中の内容です\n"
    assert editor.textCursor().position() == 4
    assert editor.is_modified is True
    assert editor.disk_signature == before_signature


def test_unmodified_tab_is_not_asked(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存の変更が無いタブでは、余計な確認を出さない（1 手で読み直せる）。"""
    window.open_path(sample)
    _forbid_choice(monkeypatch)
    captured = _capture_messages(monkeypatch)

    sample.write_text("外部で書き換わった内容\n", encoding="utf-8")
    assert window.reload_file_from_disk() is True

    assert captured == []


# ----------------------------------------------------------------------
# (d) 無題タブでは伝えるだけ
# ----------------------------------------------------------------------
def test_untitled_tab_is_told_there_is_nothing_to_reload(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_choice(monkeypatch)
    captured = _capture_messages(monkeypatch)
    editor = window.current_editor()
    assert editor is not None and editor.path is None
    editor.setPlainText("打ちかけの本文\n")
    editor.set_modified(True)

    assert window.reload_file_from_disk() is False

    assert len(captured) == 1
    title, message = captured[0]
    assert title == "再読み込みできません"
    assert "読み直す元がありません" in message
    # 伝えるだけ。本文には触らない。
    assert editor.toPlainText() == "打ちかけの本文\n"
    assert editor.is_modified is True


def test_reload_without_any_tab_does_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """タブが 1 つも無いときに押しても落ちない。"""
    _forbid_choice(monkeypatch)
    captured = _capture_messages(monkeypatch)
    while window.tabs.count():
        window.tabs.removeTab(0)

    assert window.reload_file_from_disk() is False
    assert captured == []


# ----------------------------------------------------------------------
# (e) 読めなかったときにタブを空にしない
# ----------------------------------------------------------------------
def test_reload_of_a_deleted_file_keeps_the_tab_intact(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ファイルが消えていても、タブの中身は**そのまま**にする。

    ここで空にしてしまうと、「読み直そうとしただけで内容を失う」という
    いちばん避けたい形になる（しかも消えたファイルなので復元元も無い）。
    """
    editor = window.open_path(sample)
    _forbid_choice(monkeypatch)
    captured = _capture_messages(monkeypatch)
    before_encoding = editor.encoding
    before_signature = editor.disk_signature

    sample.unlink()
    assert window.reload_file_from_disk() is False

    assert len(captured) == 1
    title, message = captured[0]
    assert title == "再読み込みできません"
    assert "タブの内容はそのままにしてあります" in message
    assert editor.toPlainText() == "最初の内容\n"
    assert editor.is_modified is False
    assert editor.encoding == before_encoding
    assert editor.disk_signature == before_signature
    assert editor.path == sample


def test_failed_reload_does_not_discard_unsaved_edits(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「はい」と答えた後で読めなかった場合も、編集は残す。"""
    editor = window.open_path(sample)
    editor.setPlainText("まだ保存していない大事な本文\n")
    editor.set_modified(True)
    _forbid_choice(monkeypatch)
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    sample.unlink()
    assert window.reload_file_from_disk() is False

    assert editor.toPlainText() == "まだ保存していない大事な本文\n"
    assert editor.is_modified is True


# ----------------------------------------------------------------------
# (f) ショートカットは付けない
# ----------------------------------------------------------------------
def test_reload_action_has_no_shortcut(window: MainWindow) -> None:
    """押し間違いで編集が消えないよう、ショートカットは割り当てない。

    利用者の明確な指定（``F5`` も割り当てない）。「すべて保存」(引き継ぎ ⑬)
    と同じ扱いである。
    """
    assert window.action_reload.shortcut().isEmpty()
    assert window.action_reload.shortcuts() == []


def test_reload_action_mnemonic_is_unique_in_the_file_menu(window: MainWindow) -> None:
    """ファイルメニューの中でニーモニック (``&U``) が重なっていないこと。

    重なると、その文字を押しても**順番に選ばれるだけで実行されない**
    （利用者からは「キーが効かない」としか見えない）。
    """
    file_menu = next(
        menu
        for menu in (action.menu() for action in window.menuBar().actions())
        if menu is not None and "ファイル" in menu.title()
    )
    keys = [
        action.text().split("&")[1][0].upper()
        for action in file_menu.actions()
        if not action.isSeparator() and "&" in action.text()
    ]
    assert "U" in keys
    assert len(keys) == len(set(keys)), f"ニーモニックが重複: {sorted(keys)}"


def test_reload_action_is_wired_to_the_reload_command(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """メニュー項目が「再読み込み」を呼ぶこと（開き直しではない）。"""
    called: list[str] = []
    monkeypatch.setattr(
        MainWindow, "reload_file_from_disk", lambda self: called.append("reload")
    )
    monkeypatch.setattr(
        MainWindow,
        "reopen_file_with_encoding",
        lambda self: called.append("reopen"),
    )

    window.action_reload.trigger()

    assert called == ["reload"]
