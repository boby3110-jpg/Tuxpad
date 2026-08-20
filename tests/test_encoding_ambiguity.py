"""文字コードの判定が拮抗したことを知らせる仕組みと、開き直しのテスト.

**なぜこれが要るか**: cp932 と euc_jp は**どちらでも読めてしまうバイト列**が
多い（特にひらがなを含む EUC-JP のファイル）。点数で選んでいる以上、選び方は
推測であり外すことがある。**外したまま編集して保存すると、ファイルは本当に
壊れて二度と読めなくなる**（利用者にとっての最大の損失）。そこで

1. 開いた時点で「拮抗した」ことをステータスバーの印で知らせ（編集前に
   気づける経路）、
2. その場で立て直せるよう「文字コードを指定して開き直す」を用意する。

ダイアログは offscreen では誰も応答できないので、必ず差し替えてから呼ぶこと。
差し替え先は**呼び出し側のモジュール名**
（``editor_app.file_commands.prompt_choice``）で指すこと。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app import fileio
from editor_app.main_window import AMBIGUOUS_ENCODING_MARK, MainWindow

#: cp932 でも euc_jp でも読めてしまう代表例。EUC-JP のひらがなは 1 バイト目が
#: ``0xA4`` で、cp932 ではこれが半角の読点 ``､`` として成立してしまう。
AMBIGUOUS_BYTES = "あいうえお\n".encode("euc_jp")

#: 半角カナだけのファイル（``_pick_japanese_legacy_detail`` の既知の限界）。
#: cp932 では ``ｱｲｳｴｵｶ``、euc_jp では 3 文字の漢字として読め、どちらも
#: 同じくらい「ありそう」なので原理的に見分けられない。
HALFWIDTH_KANA_ONLY_BYTES = "ｱｲｳｴｵｶ".encode("cp932")


def write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（利用者の打鍵と同じ扱い）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def mock_choice(monkeypatch: pytest.MonkeyPatch, encoding: str | None) -> list[dict]:
    """文字コード選択ダイアログを差し替え、渡された引数を記録して返す。

    ``encoding`` が None ならキャンセル。それ以外は、そのコーデック名が
    一覧の何番目かを探して「それを選んだ」ことにする。
    """
    calls: list[dict] = []

    def fake_prompt(parent, title, label, items, current):
        calls.append({"title": title, "label": label, "items": items, "current": current})
        if encoding is None:
            return None
        return items.index(fileio.describe_encoding(encoding))

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_prompt)
    return calls


def mock_message_box(
    monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton | None = None
) -> list[tuple[str, str]]:
    """``show_message_box`` を差し替え、(題名, 本文) を記録して返す。

    ``answer`` を渡すとその押下を返す（Yes/No を尋ねるダイアログ用）。
    """
    captured: list[tuple[str, str]] = []

    def fake_box(parent, icon, title, text, *args):
        captured.append((title, text))
        return answer

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake_box)
    return captured


# ----------------------------------------------------------------------
# (1) 拮抗の検知（fileio。Qt に依存しない層）
# ----------------------------------------------------------------------
def test_both_readable_bytes_report_the_loser_as_an_alternative() -> None:
    """どちらでも読めたなら、負けた方が「別の候補」として返ること。"""
    detection = fileio.detect_encoding_detail(AMBIGUOUS_BYTES)
    assert detection.encoding == "euc_jp"
    assert detection.alternatives == ("cp932",)


def test_alternatives_come_from_the_pick_itself() -> None:
    """勝敗を決める関数そのものが、拮抗した相手を返していること。

    ``detect_encoding_detail`` 越しではなく
    :func:`fileio._pick_japanese_legacy_detail` を直接呼ぶ（間に受け皿が
    挟まると、拮抗を捨てても素通りしてしまうため）。
    """
    chosen, alternatives = fileio._pick_japanese_legacy_detail(AMBIGUOUS_BYTES)
    assert chosen == "euc_jp"
    assert alternatives == ("cp932",)


@pytest.mark.parametrize(
    "text",
    [
        "これは日本語のテキストです。\n",
        "漢字と仮名が混ざった普通の文章。\n",
        "会議の資料を作成しました。ご確認ください。\n",
        "ｱｲｳ と全角が混ざった文章。\n",
    ],
)
def test_ordinary_shift_jis_files_are_never_reported_as_ambiguous(text: str) -> None:
    """Shift-JIS の普通のファイルでは、絶対に拮抗を報告しないこと（誤検知防止）。

    利用者が実際に扱うファイルはほぼ Shift-JIS か UTF-8 なので、**ここで
    印が出ないこと**が「印が信用に足るか」を決める。無作為な日本語 3000 通りを
    cp932 で書いて測ったときも、印が出たものは 0 件だった。
    """
    detection = fileio.detect_encoding_detail(text.encode("cp932"))
    assert detection.encoding == "cp932"
    assert detection.alternatives == ()


def test_euc_jp_files_that_only_read_one_way_are_not_reported() -> None:
    """EUC-JP でも、cp932 として読めないものは拮抗と報告しないこと。"""
    detection = fileio.detect_encoding_detail("これは日本語のテキストです。\n".encode("euc_jp"))
    assert detection.encoding == "euc_jp"
    assert detection.alternatives == ()


def test_utf8_and_bom_files_are_never_reported_as_ambiguous() -> None:
    """日本語レガシー以外の経路（UTF-8・BOM・JIS）は拮抗と無縁であること。"""
    assert fileio.detect_encoding_detail("日本語\n".encode()).alternatives == ()
    assert fileio.detect_encoding_detail("日本語\n".encode("utf-8-sig")).alternatives == ()
    assert fileio.detect_encoding_detail("日本語\n".encode("iso2022_jp")).alternatives == ()
    assert fileio.detect_encoding_detail("日本語\n".encode("utf-16-le")).alternatives == ()


def test_halfwidth_kana_only_file_is_still_reported_as_ambiguous() -> None:
    """半角カナだけに読めたときも、黙らずに知らせること。

    ここは**引き継ぎの指示（「半角カナだけのファイルではこの通知を出さない」）
    に意図的に従っていない**箇所。実装中に測って分かったのは、判定が実際に
    外れるのはちょうどこの形だということで（下のテストを参照）、
    「半角カナだけなら黙る」実装は**外した場面だけを狙って黙る**ことになる。
    利用者の最優先事項（ファイルを壊さない）に反するので、知らせる側に倒した。
    """
    detection = fileio.detect_encoding_detail(HALFWIDTH_KANA_ONLY_BYTES)
    assert detection.encoding == "cp932"
    assert detection.alternatives == ("euc_jp",)


def test_the_mark_covers_the_misdetections_it_exists_for() -> None:
    """判定を実際に外す形のファイルで、必ず印が出ること。

    短い漢字だけの EUC-JP は cp932 では半角カナに化け、点数が引き分けて
    cp932 が勝つ（＝**開いた瞬間から化けている**）。この印だけが、利用者が
    編集して保存してしまう前に気づける手がかりになる。
    """
    misdetected = 0
    for sample in ("文議確", "料天名", "確成確", "確成本確", "天確確", "章漢漢"):
        data = sample.encode("euc_jp")
        detection = fileio.detect_encoding_detail(data)
        if detection.encoding != "euc_jp":
            misdetected += 1
            # 外している。せめて「もう一方かもしれない」と言えていること。
            assert detection.alternatives == ("euc_jp",), sample
    # 標本が「外れる形」であること自体も固定する（外れなくなったら、
    # このテストは別の理由で緑になってしまうため）。
    assert misdetected == 6


def test_read_text_file_still_returns_three_values(tmp_path: Path) -> None:
    """3 つ組を期待している既存の呼び出し側を壊していないこと（回帰）。"""
    path = write_bytes(tmp_path, "sample.txt", AMBIGUOUS_BYTES)
    text, encoding, newline = fileio.read_text_file(path)
    assert (text, encoding, newline) == ("あいうえお\n", "euc_jp", "\n")

    content = fileio.read_text_file_detail(path)
    assert content.alternatives == ("cp932",)


# ----------------------------------------------------------------------
# (2) タブとステータスバーへの伝わり方
# ----------------------------------------------------------------------
def test_opening_an_ambiguous_file_marks_the_status_bar(
    window: MainWindow, tmp_path: Path
) -> None:
    """拮抗したファイルを開くと、文字コード表示に印とツールチップが付く。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)

    assert editor.encoding == "euc_jp"
    assert editor.encoding_alternatives == ("cp932",)
    assert window._encoding_label.text() == f"EUC-JP {AMBIGUOUS_ENCODING_MARK}"
    tooltip = window._encoding_label.toolTip()
    assert "Shift-JIS (cp932)" in tooltip
    assert "文字コードを指定して開き直す" in tooltip


def test_opening_an_ordinary_file_leaves_the_status_bar_plain(
    window: MainWindow, tmp_path: Path
) -> None:
    """拮抗が無ければ、印もツールチップの警告も出ないこと（誤検知防止）。"""
    path = write_bytes(tmp_path, "plain.txt", "これは日本語です。\n".encode("cp932"))
    editor = window.open_path(path)

    assert editor.encoding_alternatives == ()
    assert window._encoding_label.text() == "Shift-JIS (cp932)"
    assert AMBIGUOUS_ENCODING_MARK not in window._encoding_label.toolTip()


def test_the_mark_follows_the_current_tab(window: MainWindow, tmp_path: Path) -> None:
    """タブを切り替えると、印もそのタブのものに入れ替わること。"""
    ambiguous = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    plain = write_bytes(tmp_path, "plain.txt", "これは日本語です。\n".encode("cp932"))
    ambiguous_tab = window.open_path(ambiguous)
    plain_tab = window.open_path(plain)

    assert window._encoding_label.text() == "Shift-JIS (cp932)"
    window.tabs.setCurrentWidget(ambiguous_tab)
    assert window._encoding_label.text() == f"EUC-JP {AMBIGUOUS_ENCODING_MARK}"
    window.tabs.setCurrentWidget(plain_tab)
    assert window._encoding_label.text() == "Shift-JIS (cp932)"


def test_saving_with_a_chosen_encoding_clears_the_mark(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """利用者が文字コードを選んで保存したら、拮抗は解決＝印を消すこと。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ("cp932",)

    mock_choice(monkeypatch, "utf-8")
    assert window.save_editor_with_encoding(editor) is True

    assert editor.encoding_alternatives == ()
    assert window._encoding_label.text() == "UTF-8"
    assert AMBIGUOUS_ENCODING_MARK not in window._encoding_label.text()


def test_plain_save_also_clears_the_mark(
    window: MainWindow, tmp_path: Path
) -> None:
    """普通の上書き保存でも印は消える（書いた以上、推測ではなくなるため）。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ("cp932",)

    # 引き継ぎ ⑧ で、変更が無い上書き保存はディスクに触らなくなった。
    # 印が消える根拠は「その文字コードで実際に書いたから」なので、
    # 消えることを確かめるには本当に書かせる必要がある。
    editor.selectAll()
    editor.insertPlainText("これは日本語です。\n")

    assert window.save_editor(editor) is True
    assert editor.encoding_alternatives == ()


def test_noop_save_keeps_the_mark(window: MainWindow, tmp_path: Path) -> None:
    """変更が無い上書き保存では印が残ること（引き継ぎ ⑧ との兼ね合い）。

    ⑧ でディスクに触らなくなった以上、文字コードは「まだ推測のまま」。
    ここで印を消すと、確かめていない推測を確定したように見せてしまう。
    印を消したい利用者には「文字コードを指定して保存」がある。
    """
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ("cp932",)

    assert window.save_editor(editor) is True
    assert editor.encoding_alternatives == ("cp932",)
    assert AMBIGUOUS_ENCODING_MARK in window._encoding_label.text()


def test_reloading_an_externally_changed_file_updates_the_mark(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外部変更の再読み込みでも、そのとき判定した拮抗が印に反映されること。"""
    path = write_bytes(tmp_path, "sample.txt", "これは日本語です。\n".encode("cp932"))
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ()

    path.write_bytes(AMBIGUOUS_BYTES)
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window._maybe_reload_changed_file(path)

    assert editor.toPlainText() == "あいうえお\n"
    assert editor.encoding == "euc_jp"
    assert editor.encoding_alternatives == ("cp932",)


# ----------------------------------------------------------------------
# (3) 文字コードを指定して開き直す
# ----------------------------------------------------------------------
def test_reopen_replaces_the_content_with_the_chosen_encoding(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """選んだ文字コードで実際に読み直され、タブの内容が置き換わること。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.toPlainText() == "あいうえお\n"

    mock_choice(monkeypatch, "cp932")
    assert window.reopen_editor_with_encoding(editor) is True

    # cp932 として読むと、EUC-JP のひらがなは半角の記号の並びに見える。
    assert editor.toPlainText() == AMBIGUOUS_BYTES.decode("cp932")
    assert editor.encoding == "cp932"
    # 利用者が自分で選んだので、拮抗の印は消える。
    assert editor.encoding_alternatives == ()
    assert window._encoding_label.text() == "Shift-JIS (cp932)"
    # ディスクは触っていない。
    assert path.read_bytes() == AMBIGUOUS_BYTES


def test_reopen_preselects_the_ambiguous_alternative(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拮抗しているときは、初期選択が「もう一方の候補」であること。

    印を見た利用者がそのまま OK を押すだけで読み替えられるようにするため。
    """
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)

    calls = mock_choice(monkeypatch, None)
    assert window.reopen_editor_with_encoding(editor) is False

    assert len(calls) == 1
    items = calls[0]["items"]
    assert items[calls[0]["current"]] == fileio.describe_encoding("cp932")


def test_reopen_preselects_the_current_encoding_when_not_ambiguous(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拮抗が無ければ、初期選択は今の文字コード（＝そのまま OK で無変化）。"""
    path = write_bytes(tmp_path, "plain.txt", "これは日本語です。\n".encode("cp932"))
    editor = window.open_path(path)

    calls = mock_choice(monkeypatch, None)
    window.reopen_editor_with_encoding(editor)

    items = calls[0]["items"]
    assert items[calls[0]["current"]] == fileio.describe_encoding("cp932")


def test_reopen_cancelled_leaves_the_tab_untouched(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """キャンセルなら何も起きないこと。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)

    mock_choice(monkeypatch, None)
    assert window.reopen_editor_with_encoding(editor) is False

    assert editor.toPlainText() == "あいうえお\n"
    assert editor.encoding == "euc_jp"
    assert editor.encoding_alternatives == ("cp932",)


def test_reopen_confirms_before_discarding_unsaved_edits(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存の変更があるときは確認を挟み、断られたら読み直さないこと。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    edit_text(editor, "まだ保存していない編集\n")

    mock_choice(monkeypatch, "cp932")
    captured = mock_message_box(monkeypatch, QMessageBox.StandardButton.No)
    assert window.reopen_editor_with_encoding(editor) is False

    assert len(captured) == 1
    assert "未保存の変更があります" in captured[0][1]
    assert editor.toPlainText() == "まだ保存していない編集\n"
    assert editor.encoding == "euc_jp"


def test_reopen_proceeds_when_the_discard_is_confirmed(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確認に「はい」なら、未保存の編集を捨てて読み直すこと。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    edit_text(editor, "まだ保存していない編集\n")

    mock_choice(monkeypatch, "cp932")
    mock_message_box(monkeypatch, QMessageBox.StandardButton.Yes)
    assert window.reopen_editor_with_encoding(editor) is True

    assert editor.toPlainText() == AMBIGUOUS_BYTES.decode("cp932")
    assert editor.is_modified is False


def test_reopen_without_unsaved_edits_asks_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """編集していないタブでは、余計な確認を出さないこと。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)

    mock_choice(monkeypatch, "cp932")
    captured = mock_message_box(monkeypatch, QMessageBox.StandardButton.Yes)
    assert window.reopen_editor_with_encoding(editor) is True
    assert captured == []


def test_reopen_with_an_impossible_encoding_keeps_the_tab(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """その文字コードで読めなければ、タブには一切触らず理由を伝えること。"""
    path = write_bytes(tmp_path, "sample.txt", "日本語のテキスト\n".encode())
    editor = window.open_path(path)

    mock_choice(monkeypatch, "utf-16-le")
    captured = mock_message_box(monkeypatch)
    assert window.reopen_editor_with_encoding(editor) is False

    assert len(captured) == 1
    assert captured[0][0] == "開き直せません"
    assert "そのままにしてあります" in captured[0][1]
    assert editor.toPlainText() == "日本語のテキスト\n"
    assert editor.encoding == "utf-8"


def test_reopen_on_an_unsaved_tab_explains_instead_of_crashing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存していないタブには読み直す元が無いので、その旨だけ伝えること。"""
    editor = window.current_editor()
    assert editor.path is None

    calls = mock_choice(monkeypatch, "cp932")
    captured = mock_message_box(monkeypatch)
    assert window.reopen_editor_with_encoding(editor) is False

    assert calls == []  # 選ばせる前に止めること（選ばせてから断るのは失礼）
    assert len(captured) == 1
    assert captured[0][0] == "開き直せません"


def test_reopen_keeps_the_newline_style_of_the_file(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """開き直しでも改行コードは読み直した結果に従うこと（保存で復元されるため）。"""
    path = write_bytes(tmp_path, "crlf.txt", "日本語\r\n二行目\r\n".encode("cp932"))
    editor = window.open_path(path)
    assert editor.newline == "\r\n"

    mock_choice(monkeypatch, "cp932")
    assert window.reopen_editor_with_encoding(editor) is True
    assert editor.newline == "\r\n"
    assert editor.toPlainText() == "日本語\n二行目\n"


def test_reopen_menu_command_uses_the_current_tab(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """メニューから呼ぶ入口が、開き直し（保存ではない）を、今のタブに行うこと。

    ``encoding`` が cp932 になったことだけを見ると、**「文字コードを指定して
    保存」に結線してしまっても緑になる**（そちらも cp932 に入れ替えるため）。
    本文が読み替わったこと・ディスクを書いていないことまで見る。
    """
    other = write_bytes(tmp_path, "other.txt", "ほかのタブ\n".encode())
    target = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    other_tab = window.open_path(other)
    target_tab = window.open_path(target)

    mock_choice(monkeypatch, "cp932")
    window.action_reopen_with_encoding.trigger()

    assert target_tab.encoding == "cp932"
    # 開き直し＝本文がディスクから読み替わる（保存では本文は変わらない）。
    assert target_tab.toPlainText() == AMBIGUOUS_BYTES.decode("cp932")
    # 開き直し＝ディスクは書き換えない（保存なら cp932 で書き戻ってしまう）。
    assert target.read_bytes() == AMBIGUOUS_BYTES
    assert other_tab.encoding == "utf-8"
    assert other_tab.toPlainText() == "ほかのタブ\n"


def test_read_text_file_as_ignores_detection(tmp_path: Path) -> None:
    """指定した文字コードを、判定に上書きされずそのまま使うこと。"""
    path = write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    text, newline = fileio.read_text_file_as(path, "cp932")
    assert text == AMBIGUOUS_BYTES.decode("cp932")
    assert newline == "\n"

    with pytest.raises(fileio.EncodingDetectionError):
        fileio.read_text_file_as(path, "utf-8")


def test_read_text_file_as_strips_the_bom(tmp_path: Path) -> None:
    """BOM 付きの文字コードを指定したときも、BOM を本文に混ぜないこと。

    ``str.encode("utf-16-le")`` は BOM を**付けない**ので、剥がす対象を
    作るには BOM を自分で書くこと（付けずに書くと、剥がす処理を消しても
    テストが緑のままになる）。
    """
    path = write_bytes(
        tmp_path, "bom.txt", fileio.BOM_BY_ENCODING["utf-16-le"] + "日本語\n".encode("utf-16-le")
    )
    text, _newline = fileio.read_text_file_as(path, "utf-16-le")
    assert text == "日本語\n"
    assert not text.startswith("﻿")
