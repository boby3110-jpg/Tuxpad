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
    """どちらでも読めるバイト列は、読んだ中身が日本語らしい方を採る。

    日本語レガシー文字コードは「無効なバイト列なら例外になる」ことを頼りに
    試しているが、**両方とも例外にならないバイト列は実在する**。ここが
    入れ替わると、Shift-JIS のファイルが黙って半角カタカナの羅列に
    化けて開き、**そのまま保存すると化けた内容でファイルが上書きされる**
    （読めない訳ではないので、エラーにもならない）。

    この文面では cp932 の読み（全角 2 文字）の方が euc_jp の読み（半角カナ
    2 文字）より点が高いので cp932 が採られる。**「先に試したから」ではない**
    ——順序で決めていた頃の穴は
    :func:`test_euc_jp_kana_text_is_not_read_as_shift_jis` を参照。
    """
    data = AMBIGUOUS_SJIS_TEXT.encode("cp932")
    # 前提: euc_jp としても「読めてしまう」こと（読めないなら見分けは要らない）。
    assert data.decode("euc_jp") != AMBIGUOUS_SJIS_TEXT

    path = tmp_path / "ambiguous.txt"
    path.write_bytes(data)
    text, encoding, _newline = fileio.read_text_file(path)

    assert encoding == "cp932"
    assert text == AMBIGUOUS_SJIS_TEXT


#: cp932 としても**エラーにならずに読めてしまう** EUC-JP の日本語。
#: EUC-JP のひらがなは 1 バイト目が ``0xA4`` で、cp932 ではこれが半角の
#: 読点 ``､`` 1 文字として成立するため、**ひらがなを含む EUC-JP のファイルは
#: ほぼ必ず cp932 としても「読めて」しまう**（この文面は ``､ｳ､ﾋ…`` に化ける）。
AMBIGUOUS_EUCJP_TEXT = "こんにちは、世界。\n"


def test_euc_jp_kana_text_is_not_read_as_shift_jis(tmp_path: Path) -> None:
    """ひらがなを含む EUC-JP のファイルが、黙って cp932 に化けないこと。

    判定を「先に試して読めた方」で決めていた頃（2026-08-16 まで）の穴。
    cp932 を先に試していたので、**cp932 としても読めてしまう EUC-JP の
    ファイルは全て cp932 と判定され**、``､ｳ､ﾋ､ﾁ､ﾏ`` のような半角記号の
    羅列として開いていた。例外にならないので「開けません」にもならず、
    化けていることにしか気づけない。しかも**このアプリ自身が「文字コードを
    指定して保存」で EUC-JP を選ばせる**ので、自分で保存したファイルを
    自分で開けない状態だった（JIS の不具合と同じ形）。

    :func:`fileio.detect_encoding` を直接呼ぶ。``read_text_file`` 越しでは
    その先の受け皿に助けられて素通りしうるため。
    """
    data = AMBIGUOUS_EUCJP_TEXT.encode("euc_jp")
    # 前提: cp932 でも「読めてしまう」こと。ここが成り立たないと、この
    # テストは見分けの仕組みを一切通らなくなり、意味を失う。
    assert data.decode("cp932") != AMBIGUOUS_EUCJP_TEXT

    assert fileio.detect_encoding(data) == "euc_jp"

    path = tmp_path / "eucjp-kana.txt"
    path.write_bytes(data)
    text, encoding, _newline = fileio.read_text_file(path)
    assert (text, encoding) == (AMBIGUOUS_EUCJP_TEXT, "euc_jp")


def test_euc_jp_round_trip_via_write_and_read(tmp_path: Path) -> None:
    """EUC-JP で保存したファイルを開き直すと、同じ本文・同じ文字コードになる。

    「文字コードを指定して保存」で EUC-JP を選べる以上、**このアプリが
    書いたファイルをこのアプリが読めない**のは通してはいけない。
    """
    path = tmp_path / "eucjp-round.txt"
    fileio.write_text_file(path, AMBIGUOUS_EUCJP_TEXT, "euc_jp", "\n")

    text, encoding, newline = fileio.read_text_file(path)

    assert (text, encoding, newline) == (AMBIGUOUS_EUCJP_TEXT, "euc_jp", "\n")


def test_euc_jp_hiragana_only_file_is_not_read_as_shift_jis() -> None:
    """ひらがなだけの EUC-JP ファイルが cp932 に化けないこと。

    このバイト列を cp932 として読むと ``､｢､､､ｦ､ｨ､ｪ`` になる。**半角の読点
    ``､`` が 1 文字おきに並ぶ**のが EUC-JP のひらがなを誤読した跡で、
    この半角記号を「半角カナと同じく日本語らしさの証拠」として数えて
    しまうと、誤読の読み方が本物と同点になり cp932 に倒れる。
    ``｡｢｣､･`` を点数から外してあるのはそのため。
    """
    data = "あいうえお\n".encode("euc_jp")
    assert data.decode("cp932") != "あいうえお\n"  # 前提: cp932 でも読めてしまう

    assert fileio.detect_encoding(data) == "euc_jp"


def test_euc_jp_kanji_and_kana_mix_is_not_read_as_shift_jis() -> None:
    """漢字とひらがなが 1 文字ずつの短い EUC-JP でも cp932 に化けないこと。

    この長さになると、cp932 の誤読も「全角 1 文字＋半角カナ」という
    それらしい見た目になり、**全角と半角カナが混ざった読み方を減点する規則**
    が無いと本物と同点になって cp932 に倒れる。本物の半角カナのファイルは
    半角カナだけで書かれているので、混ざっている方を誤読とみなしてよい。
    """
    for sample in ("私は\n", "字セ\n", "中え\n"):
        data = sample.encode("euc_jp")
        assert data.decode("cp932") != sample  # 前提: cp932 でも読めてしまう
        assert fileio.detect_encoding(data) == "euc_jp", sample


def test_halfwidth_kana_only_shift_jis_file_stays_shift_jis() -> None:
    """半角カナだけの Shift-JIS ファイルが EUC-JP に化けないこと（引き分けの倒し方）。

    ``ｶﾀｶﾅ`` のような半角カナだけのファイルは、cp932 の読み（半角カナ 4 文字）
    も euc_jp の読み（漢字 2 文字）も**同じ点数**になり、日本語らしさでは
    見分けられない。この引き分けを
    :data:`fileio._STRICT_JAPANESE_ENCODINGS` の並び順（＝ cp932 が先）で
    倒しているので、**並び順を入れ替えると、レガシーな半角カナのファイル
    （固定長データ等）が黙って漢字の羅列に化ける**。

    実際に扱うファイルはほぼ Shift-JIS なので、引き分けは cp932 に倒すこと。
    """
    for sample in ("ｶﾀｶﾅ\n", "ﾊﾝｶｸ\n", "ﾃﾞｰﾀ\n"):
        data = sample.encode("cp932")
        # 前提: euc_jp でも読めてしまうこと（＝引き分けの経路を実際に通る）。
        assert data.decode("euc_jp") != sample
        assert fileio.detect_encoding(data) == "cp932", sample


def test_halfwidth_kana_only_euc_jp_file_is_read_as_shift_jis() -> None:
    """【既知の限界】半角カナだけの EUC-JP ファイルは cp932 と判定する。

    上のテストの裏側。同点になる以上どちらかは必ず外すので、**数の多い方
    （Shift-JIS）を採る**という判断をここに残しておく。負けた側の挙動を
    わざと固定しておくのは、将来この判定をいじった人が「直したつもりで、
    よくある方を壊す」のを防ぐため。
    """
    data = "ｱｲｳｴｵ ｶｷｸｹｺ\n".encode("euc_jp")
    assert data.decode("cp932")  # 前提: 両方で読める

    assert fileio.detect_encoding(data) == "cp932"


def test_detect_encoding_empty_bytes_is_utf8() -> None:
    assert fileio.detect_encoding(b"") == "utf-8"


# ----------------------------------------------------------------------
# ISO-2022-JP（JIS）… 「UTF-8 として読めてしまう」ので順序が全て
# ----------------------------------------------------------------------
def test_iso2022_jp_is_not_mistaken_for_utf8(tmp_path: Path) -> None:
    """JIS のファイルが UTF-8 と判定されないこと。

    ISO-2022-JP は全てのバイトが 0x80 未満なので、**UTF-8 としても必ず
    「読めて」しまう**。「UTF-8 で読めたら UTF-8」と判定すると、JIS の
    ファイルを開いた利用者には ``\\x1b$BF|K\\8l`` がそのまま並んで見える
    （例外にならないので「開けません」にすらならない）。判定の順序が
    入れ替わるとこれが再発するので、前提（UTF-8 でも読めること）ごと固定する。
    """
    data = LONG_JAPANESE_TEXT.encode("iso2022_jp")
    # 前提: UTF-8 としても「読めてしまう」こと（これが無いとこのテストは
    # ただの重複になる）。
    assert data.decode("utf-8") != LONG_JAPANESE_TEXT

    path = tmp_path / "jis.txt"
    path.write_bytes(data)
    text, encoding, _newline = fileio.read_text_file(path)

    assert encoding == "iso2022_jp"
    assert text == LONG_JAPANESE_TEXT


def test_iso2022_jp_round_trip_via_write_and_read(tmp_path: Path) -> None:
    """このアプリ自身が JIS で保存したファイルを、自分で開き直せること。

    「文字コードを指定して保存」で JIS を選ばせている以上、ここが通らないと
    **自分で保存したファイルが自分で開けない**（保存はできるのに、開くと化ける）。
    """
    path = tmp_path / "jis_roundtrip.txt"
    fileio.write_text_file(path, LONG_JAPANESE_TEXT, "iso2022_jp", "\n")

    text, encoding, newline = fileio.read_text_file(path)

    assert text == LONG_JAPANESE_TEXT
    assert encoding == "iso2022_jp"
    assert newline == "\n"
    assert path.read_bytes() == LONG_JAPANESE_TEXT.encode("iso2022_jp")


def test_utf8_file_containing_the_escape_stays_utf8(tmp_path: Path) -> None:
    """エスケープ列らしきバイトがあるだけの UTF-8 は、UTF-8 のまま読むこと。

    見分けの目印 (``ESC $``) は UTF-8 のファイルにもたまたま入りうる
    （端末の記録など）。目印だけで決め打ちすると、日本語の UTF-8 ファイルが
    「JIS として読めません」で開けなくなる。実際に ISO-2022-JP として
    読めたときだけ採用すること。
    """
    original = "ESC \x1b$ を含む日本語の UTF-8 ファイル\n"
    path = tmp_path / "utf8_with_escape.txt"
    path.write_bytes(original.encode())

    text, encoding, _newline = fileio.read_text_file(path)

    assert encoding == "utf-8"
    assert text == original


def test_ascii_only_iso2022_jp_file_is_read_as_utf8_without_damage(
    tmp_path: Path,
) -> None:
    """中身が ASCII だけの JIS ファイルは UTF-8 扱いでよい（結果が同じため）。

    2 バイト文字へ切り替えるエスケープ列が 1 つも無いなら、そのファイルの
    バイト列は ASCII そのもの。UTF-8 として読んでも本文は 1 文字も変わらず、
    保存してもバイト列は変わらない。**目印が無いものまで JIS と判定しに
    行く必要は無い**ことの根拠なので、保存まで含めて固定しておく。
    """
    original = "ascii only, no escapes\n"
    path = tmp_path / "ascii.txt"
    path.write_bytes(original.encode("iso2022_jp"))

    text, encoding, newline = fileio.read_text_file(path)
    assert encoding == "utf-8"
    assert text == original

    fileio.write_text_file(path, text, encoding, newline)
    assert path.read_bytes() == original.encode("iso2022_jp")


def test_open_edit_save_keeps_iso2022_jp_encoding(
    window: MainWindow, tmp_path: Path
) -> None:
    """JIS のファイルを開いて編集・保存しても JIS のままであること（機能 8）。"""
    path = tmp_path / "jis-sample.txt"
    original = LONG_JAPANESE_TEXT + "\n"
    path.write_bytes(original.encode("iso2022_jp"))

    editor = window.open_path(path)
    assert editor is not None
    assert editor.encoding == "iso2022_jp"
    assert editor.toPlainText() == original

    edit_text(editor, original + "追記した行\n")
    assert window.save_editor(editor) is True

    assert path.read_bytes() == (original + "追記した行\n").encode("iso2022_jp")


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


def test_open_edit_save_keeps_euc_jp_encoding(
    window: MainWindow, tmp_path: Path
) -> None:
    """cp932 としても読めてしまう EUC-JP のファイルを、開く→編集→保存で保てること。

    判定が cp932 に倒れていた頃は、**開いた時点で本文が半角記号の羅列に
    化けており、保存するとその化けた内容で元のファイルが上書きされる**
    （＝黙ってファイルを壊す）。ここは判定だけでなく、機能 8 の
    「保存時は元の文字コードを保つ」まで通して固定する。
    """
    path = tmp_path / "eucjp-sample.txt"
    original = AMBIGUOUS_EUCJP_TEXT
    path.write_bytes(original.encode("euc_jp"))
    # 前提: cp932 でも読めてしまうファイルであること。
    assert original.encode("euc_jp").decode("cp932") != original

    editor = window.open_path(path)
    assert editor is not None
    assert editor.encoding == "euc_jp"
    assert editor.toPlainText() == original

    edit_text(editor, original + "追記した行\n")
    assert window.save_editor(editor) is True

    assert path.read_bytes() == (original + "追記した行\n").encode("euc_jp")
