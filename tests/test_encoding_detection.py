"""機能 8「文字コードの自動判定・保持」のテスト。

BOM 判定・UTF-8 の試し読みで足りない場合の charset-normalizer によるフォールバック
(Shift-JIS / EUC-JP など) を中心に確認する。BOM 付きファイルや UTF-8 の判定は
tests/test_open_file.py に既存のテストがあるので、ここでは重複させない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_app import fileio
from editor_app.main_window import MainWindow

#: charset-normalizer の判定を安定させるための、ある程度まとまった長さの日本語文。
#: 短すぎる文字列だと統計的な判定が不安定になる（例: 「日本語」3文字だけだと
#: 中国語系の文字コードに誤判定されることを確認済み）。
LONG_JAPANESE_TEXT = (
    "漢字・ひらがな・カタカナが混在した日本語の実際のテキストファイルを想定した動作確認用の文章です。"
    "文字コードの自動判定と保持ができているかを確かめます。"
) * 3

#: 「UTF-8 でも日本語レガシー文字コードでもない」ファイルの例。
#: :func:`fileio.detect_encoding` の 4 段目（charset-normalizer による推定）まで
#: 落ちてこないと読めない。日本語のバイト列だと 3 段目で決まってしまうので、
#: 判定の最後の砦を通すにはこういう非日本語のファイルが要る。
RUSSIAN_TEXT = (
    "Это пример русского текста для проверки автоматического определения "
    "кодировки текстового файла в редакторе. "
) * 4


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def fake_from_bytes(encoding: str):
    """``charset_normalizer.from_bytes`` の差し替え（``best()`` が返す名前を固定する）。

    charset-normalizer は現実的なバイト列にはほぼ必ず何かしら答えるので、
    「答えは返ってきたが、その名前では実際には読めなかった」という場面は
    差し替えないと作れない。
    """

    class _Match:
        def __init__(self, name: str) -> None:
            self.encoding = name

    class _Result:
        def best(self):
            return _Match(encoding)

    return lambda _data: _Result()


# ----------------------------------------------------------------------
# fileio.detect_encoding / 往復（charset-normalizer フォールバック）
# ----------------------------------------------------------------------
def test_shift_jis_round_trip_via_write_and_read(tmp_path: Path) -> None:
    path = tmp_path / "sjis_roundtrip.txt"
    fileio.write_text_file(path, LONG_JAPANESE_TEXT, "cp932", "\n")

    text, encoding, newline = fileio.read_text_file(path)

    assert text == LONG_JAPANESE_TEXT
    assert encoding == "cp932"
    assert newline == "\n"
    # 書き出したバイト列そのものが cp932 であることも直接確認する。
    assert path.read_bytes() == LONG_JAPANESE_TEXT.encode("cp932")


def test_euc_jp_is_detected_with_enough_text(tmp_path: Path) -> None:
    path = tmp_path / "eucjp.txt"
    path.write_bytes(LONG_JAPANESE_TEXT.encode("euc_jp"))

    text, encoding, _newline = fileio.read_text_file(path)

    assert text == LONG_JAPANESE_TEXT
    # euc_jis_2004 は euc_jp の上位互換で、この文面ではどちらでも同じ結果になる。
    assert LONG_JAPANESE_TEXT.encode(encoding) == LONG_JAPANESE_TEXT.encode("euc_jp")


#: cp932 としても euc_jp としても**エラーにならずに読めてしまう**日本語。
#: cp932 では「実写」、euc_jp では半角カタカナの「ﾀﾊ」になる
#: （cp932 の 0x8E は 2 バイト文字の先導バイトだが、euc_jp では
#: 「次は半角カタカナ」の印 (SS2) という、意味が正面から食い違うバイト）。
AMBIGUOUS_SJIS_TEXT = "実写\n"


def test_shift_jis_wins_over_euc_jp_when_both_can_read_it(tmp_path: Path) -> None:
    """どちらでも読めるバイト列は Shift-JIS として読む（判定の順序）。

    日本語レガシー文字コードは「無効なバイト列なら例外になる」ことを頼りに
    順に試しているが、**両方とも例外にならないバイト列は実在する**。その
    場合は先に試した方で決まってしまうので、順序そのものが判定結果になる。
    ここが入れ替わると、Shift-JIS のファイルが黙って半角カタカナの羅列に
    化けて開き、**そのまま保存すると化けた内容でファイルが上書きされる**
    （読めない訳ではないので、エラーにもならない）。

    実際に扱うファイルはほぼ Shift-JIS なので、cp932 を先に試すこと。
    """
    data = AMBIGUOUS_SJIS_TEXT.encode("cp932")
    # 前提: euc_jp としても「読めてしまう」こと（読めないなら順序は関係ない）。
    assert data.decode("euc_jp") != AMBIGUOUS_SJIS_TEXT

    path = tmp_path / "ambiguous.txt"
    path.write_bytes(data)
    text, encoding, _newline = fileio.read_text_file(path)

    assert encoding == "cp932"
    assert text == AMBIGUOUS_SJIS_TEXT


def test_detect_encoding_empty_bytes_is_utf8() -> None:
    assert fileio.detect_encoding(b"") == "utf-8"


# ----------------------------------------------------------------------
# 判定の 4 段目（charset-normalizer による推定）
# ----------------------------------------------------------------------
@pytest.mark.parametrize("encoding", ["cp1251", "koi8-r"])
def test_non_japanese_file_is_read_via_charset_normalizer(
    tmp_path: Path, encoding: str
) -> None:
    """日本語でないファイルは、判定の最後の砦（統計的な推定）で読める。

    1 段目（BOM）・2 段目（UTF-8）・3 段目（日本語レガシー）は全て外れる
    バイト列なので、ここが効いていないと **開いた瞬間に「文字コードを判定
    できませんでした」で開けない**。日本語のファイルだけを見ていると
    気づけない経路なので、ロシア語の 1 バイト系文字コードを 2 つ使って固定する。
    """
    data = RUSSIAN_TEXT.encode(encoding)
    # 前提: 上の 3 段では判定できないバイト列であること（そうでないと
    # このテストが 4 段目を通らなくなり、意味を失う）。
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    for japanese in fileio._STRICT_JAPANESE_ENCODINGS:
        with pytest.raises(UnicodeDecodeError):
            data.decode(japanese)

    path = tmp_path / f"russian-{encoding}.txt"
    path.write_bytes(data)

    text, detected, _newline = fileio.read_text_file(path)

    assert text == RUSSIAN_TEXT
    # コーデック名そのものは charset-normalizer の版によって別名になりうるので、
    # 「本文が元どおり読めた」ことの方を固定する。
    assert detected not in ("utf-8", *fileio._STRICT_JAPANESE_ENCODINGS)


def test_non_japanese_file_survives_a_save_unchanged(tmp_path: Path) -> None:
    """推定で開いたファイルは、そのまま保存してもバイト列が変わらない。

    機能 8 は「判定した文字コードを保存時に保つ」ことまでが本体で、
    判定だけ通っても保存で化けるなら実際のファイルは壊れる。
    """
    path = tmp_path / "russian.txt"
    original = RUSSIAN_TEXT.encode("cp1251")
    path.write_bytes(original)

    text, encoding, newline = fileio.read_text_file(path)
    fileio.write_text_file(path, text, encoding, newline)

    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("guess", "why"),
    [
        # charset-normalizer が返す名前は、Python の codecs に必ずあるとは限らない。
        ("そんなコーデックは無い", "Python の知らないコーデック名"),
        # 名前は正しいが、そのバイト列は実際にはその文字コードで読めない。
        ("ascii", "実際には decode できない"),
    ],
)
def test_a_guess_that_does_not_actually_work_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, guess: str, why: str
) -> None:
    """推定の答えは、実際に decode できるか検算してから採用する。

    検算せずに信用すると、その名前は**タブの文字コードとして保持され**
    （機能 8）、保存のときに初めて破綻する。判定の時点で
    :class:`fileio.EncodingDetectionError` にして、開かせない方が安全。
    ここは :func:`fileio.detect_encoding` を直接呼んで、この検算そのものを
    固定する（:func:`fileio.read_text_file` 越しだと、その先の
    ``UnicodeDecodeError`` の受け皿に助けられて素通りしてしまう）。
    """
    monkeypatch.setattr(fileio, "from_bytes", fake_from_bytes(guess))

    with pytest.raises(fileio.EncodingDetectionError):
        fileio.detect_encoding(b"\x80\x81\x82\x83")


def test_a_guess_that_works_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """検算が通れば、推定された文字コードをそのまま採用する。"""
    monkeypatch.setattr(fileio, "from_bytes", fake_from_bytes("cp1251"))

    assert fileio.detect_encoding(b"\x80\x81\x82\x83") == "cp1251"


# ----------------------------------------------------------------------
# BOM は付いているのに中身が壊れている（転送・コピーの失敗で実際に起こる）
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "data"),
    [
        # UTF-16/32 は 2・4 バイト単位なので、末尾が欠けると decode できない。
        ("utf-16-le", b"\xff\xfe" + "あいう".encode("utf-16-le") + b"\x41"),
        ("utf-16-be", b"\xfe\xff" + "あいう".encode("utf-16-be") + b"\x41"),
        ("utf-32-le", b"\xff\xfe\x00\x00" + "あ".encode("utf-32-le")[:3]),
        # BOM は UTF-8 なのに、続くバイト列が UTF-8 として不正な場合。
        ("utf-8-sig", b"\xef\xbb\xbf" + "あ".encode() + b"\x80\x81"),
    ],
)
def test_broken_file_with_a_bom_raises_a_readable_error(
    tmp_path: Path, name: str, data: bytes
) -> None:
    """BOM で文字コードは決まったのに読めない場合、理由の分かる例外にする。

    BOM があると判定は 1 段目で確定するので、あとは decode するだけになる。
    そこで失敗したときに ``UnicodeDecodeError`` をそのまま飛ばすと、
    開く側のダイアログには Python の生の文言（``'utf-16-le' codec can't
    decode ...``）が出る。NAS や rclone 越しのコピーが途中で切れたファイルで
    実際に起こりうるので、何の文字コードとして読もうとしたかを見せる。
    """
    path = tmp_path / "broken.txt"
    path.write_bytes(data)

    assert fileio.detect_encoding(data) == name  # BOM で確定している
    with pytest.raises(fileio.EncodingDetectionError) as excinfo:
        fileio.read_text_file(path)
    assert name in str(excinfo.value)


def test_opening_a_broken_file_reports_it_and_adds_no_tab(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """壊れたファイルを開こうとしても、中途半端なタブを作らない。

    ここが素通りすると、**空のタブに壊れたファイルのパスが結びついた状態**
    ができてしまい、そのまま保存すると元のファイルを空で上書きしてしまう。
    """
    path = tmp_path / "truncated.txt"
    path.write_bytes(b"\xff\xfe" + "あいう".encode("utf-16-le") + b"\x41")

    shown: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, *a, **k: shown.append(title),
    )

    before = window.tabs.count()
    assert window.open_path(path) is None
    assert shown == ["開けません"]
    assert window.tabs.count() == before
    assert [e for e in window.editors() if e.path == path.resolve()] == []
    assert path.read_bytes().startswith(b"\xff\xfe")  # ファイルには触っていない


# ----------------------------------------------------------------------
# MainWindow を通した end-to-end（開く → 編集 → 保存で Shift-JIS を維持する）
# ----------------------------------------------------------------------
def test_open_edit_save_keeps_shift_jis_encoding(
    window: MainWindow, tmp_path: Path
) -> None:
    path = tmp_path / "sample-log.txt"
    original = LONG_JAPANESE_TEXT + "\n"
    path.write_bytes(original.encode("cp932"))

    editor = window.open_path(path)
    assert editor is not None
    assert editor.encoding == "cp932"

    edit_text(editor, original + "追記した行\n")
    assert window.save_editor(editor) is True

    saved_bytes = path.read_bytes()
    assert saved_bytes == (original + "追記した行\n").encode("cp932")
