"""開いて保存し直したとき、ファイルのバイト列が 1 バイトも変わらないことを見張る.

機能 8 (文字コードの自動判定・保持) が利用者に約束していることを、
**いちばん厳しい形**——「読んで書き戻したら元のバイト列と完全に一致する」——
で縛るためのテスト。

既にある :func:`tests.test_save_file.test_round_trip` は**書いてから読む**
向き（``write_text_file`` → ``read_text_file`` で本文が戻るか）を見ている。
こちらは**読んでから書く**向きで、しかも**バイト列そのもの**を比べる。
向きが逆なので、片方だけでは次のような取りこぼしが起きうる:

- BOM を読むときに剥がしていても、書くときに付け直し忘れる（本文は一致する）。
- 改行コードを判定していても、書き戻すときに使い忘れる（本文は LF に
  統一されているので、本文どうしの比較では気づけない）。

**利用者にとっての意味**は 2 つある。

1. **ファイルを壊さない。** 利用者の最優先事項は「ファイルが壊れて
   開けなくなる」を避けること。開いて保存しただけでバイト列が変わるなら、
   それは（見た目には気づけない形で）ファイルを書き換えている。
2. **無駄な差分を出さない。** 利用者は rclone / NAS で同期しているので、
   中身が同じはずのファイルに差分が出ると同期の邪魔になる（引き継ぎ ⑧ と
   同じ動機）。

**唯一の例外が「改行コードが混ざったファイル」**で、それは
:class:`TestMixedNewlinesAreUnified` に切り出して**わざとそう決めた**動きと
して縛ってある（気づかないまま壊れているのではないことを示すため）。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from editor_app import fileio
from editor_app.main_window import MainWindow

#: 本文の形のいろいろ（改行は LF で書き、後で各改行コードへ置き換える）。
#: 実際の日本語のテキストファイルで出会う形を並べてある。
TEXT_SHAPES: dict[str, str] = {
    "ascii": "hello world\nsecond line\n",
    "japanese": "日本語のテキストです。\n二行目：漢字とかなとカタカナ。\n",
    "no_trailing_newline": "改行で終わらない日本語",
    "halfwidth_kana": "ﾊﾝｶｸｶﾅ ｱｲｳｴｵ｡\n",
    "mixed_widths": "abc 日本語 123 ﾊﾝｶｸ\n",
    "empty": "",
    "only_newline": "\n",
    "tab_and_formfeed": "a\tb\n\x0c\nc\n",
    "backslash": "path\\to\\file C:\\Users\n",
    "long": ("あいうえお漢字テスト。" * 200) + "\n",
}

#: 判定できる文字コード全部（:data:`fileio.ENCODING_DISPLAY_NAMES` と同じ顔ぶれ）。
ENCODINGS: tuple[str, ...] = tuple(fileio.ENCODING_DISPLAY_NAMES)

#: 改行コード全部。
NEWLINES: dict[str, str] = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}


def build_bytes(text: str, encoding: str, newline: str) -> bytes | None:
    """``text`` (改行は LF) を、そのまま置いてあるファイルのバイト列にする。

    その文字コードで表現できない本文なら ``None``（試す組み合わせから外す）。
    BOM は :data:`fileio.BOM_BY_ENCODING` と同じ表から付ける
    （``utf-8-sig`` はコーデック自身が付けるのでここでは足さない）。
    """
    try:
        data = text.replace("\n", newline).encode(encoding)
    except UnicodeEncodeError:
        return None
    return fileio.BOM_BY_ENCODING.get(encoding, b"") + data


def roundtrip_cases() -> list[tuple[str, str, str]]:
    """``(本文の名前, 文字コード, 改行コードの名前)`` の総当り（表現できる組だけ）。"""
    cases = []
    for shape, encoding, newline in itertools.product(TEXT_SHAPES, ENCODINGS, NEWLINES):
        if build_bytes(TEXT_SHAPES[shape], encoding, NEWLINES[newline]) is not None:
            cases.append((shape, encoding, newline))
    return cases


ROUNDTRIP_CASES = roundtrip_cases()


class TestOpenAndSaveKeepsEveryByte:
    """読んで書き戻すと、バイト列が完全に一致する。"""

    @pytest.mark.parametrize(("shape", "encoding", "newline"), ROUNDTRIP_CASES)
    def test_bytes_are_identical(
        self, tmp_path: Path, shape: str, encoding: str, newline: str
    ) -> None:
        original = build_bytes(TEXT_SHAPES[shape], encoding, NEWLINES[newline])
        assert original is not None
        path = tmp_path / "sample.txt"
        path.write_bytes(original)

        text, detected, detected_newline = fileio.read_text_file(path)
        fileio.write_text_file(path, text, detected, detected_newline)

        assert path.read_bytes() == original

    def test_matrix_is_not_empty(self) -> None:
        """総当りが空振りしていないことを見張る。

        ``build_bytes`` が（例えば BOM の表の名前が変わって）常に ``None`` を
        返すようになると、上のテストは**1 件も走らないまま緑**になる。
        文字コード 9 種類・改行 3 種類・本文 10 通りのうち、**表現できない
        組み合わせを除いても大半は残る**はずなので、そこを下限として見る。
        """
        assert len(ROUNDTRIP_CASES) > 200
        # どの文字コードも最低 1 件は試されている（丸ごと落ちていない）。
        assert {encoding for _shape, encoding, _newline in ROUNDTRIP_CASES} == set(ENCODINGS)

    @pytest.mark.parametrize("encoding", ["cp932", "euc_jp", "iso2022_jp"])
    def test_japanese_legacy_file_survives_open_and_save(
        self, window: MainWindow, tmp_path: Path, encoding: str
    ) -> None:
        """日本語レガシー文字コードのファイルは、アプリ経由でも 1 バイトも変わらない。

        :class:`TestOpenAndSaveKeepsEveryByte` の総当りは :mod:`~editor_app.fileio`
        だけを見ているので、**実際の保存の経路**（本文をエディタから取り出し、
        NBSP の置き換えなどを通ってから書く）でも同じことが言えるかを別に見る。
        判定を間違えるとファイルが壊れうるのはこの 3 つだけなので、
        ここだけはアプリ経由でも縛っておく。
        """
        original = build_bytes(TEXT_SHAPES["japanese"], encoding, "\r\n")
        assert original is not None
        path = tmp_path / "legacy.txt"
        path.write_bytes(original)

        editor = window.open_path(path)
        assert editor.encoding == encoding
        # 本文を「同じ内容で」入れ直す（＝編集したことにして保存を通す。
        # 引き継ぎ ⑧ のガードは、変更が無いと書き込み自体を止めるため）。
        editor.selectAll()
        editor.insertPlainText(editor.toPlainText())
        assert window.save_file() is True

        assert path.read_bytes() == original


class TestMixedNewlinesAreUnified:
    """**改行コードが混ざったファイルは、保存すると全部そろえられる**（唯一の例外）。

    これは取りこぼしではなく、そう決めた動き。本文は編集しやすいように
    いったん全部 LF へ直され (:func:`fileio._decode_text`)、保存時には
    **最初に現れた改行コード 1 つ**で書き戻される (:func:`fileio.detect_newline`)。
    そのため混在は保てない。

    利用者から見た影響を正直に書くと、**混在したファイルを 1 文字でも編集して
    保存すると、全ての行の行末が書き換わる**（rclone / NAS では全行の差分に
    見える）。**編集していなければ引き継ぎ ⑧ のガードが働いて書き込み自体が
    起きない**ので、開いただけで起きることはない。

    ここで縛っておくのは、この動きが**気づかないうちに変わってしまわない**
    ようにするため（変えるなら、意識して変えることになる）。
    """

    @pytest.mark.parametrize(
        ("original_text", "expected_text"),
        [
            # 最初が LF なので全部 LF になる
            ("one\ntwo\r\nthree\n", "one\ntwo\nthree\n"),
            # 最初が CRLF なので全部 CRLF になる
            ("one\r\ntwo\nthree\r\n", "one\r\ntwo\r\nthree\r\n"),
            # 最初が単独の CR (古い Mac 形式) なので全部 CR になる
            ("one\rtwo\nthree\n", "one\rtwo\rthree\r"),
        ],
    )
    def test_first_newline_wins(
        self, tmp_path: Path, original_text: str, expected_text: str
    ) -> None:
        path = tmp_path / "mixed.txt"
        path.write_bytes(original_text.encode("utf-8"))

        text, encoding, newline = fileio.read_text_file(path)
        fileio.write_text_file(path, text, encoding, newline)

        assert path.read_bytes() == expected_text.encode("utf-8")

    def test_editing_a_mixed_file_rewrites_every_line_ending(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        """混在したファイルを編集して保存すると、行末が全部そろう（アプリ経由）。"""
        path = tmp_path / "mixed.txt"
        path.write_bytes(b"\xef\xbb\xbf" + "一\r\n二\n三\r\n".encode("utf-8"))

        editor = window.open_path(path)
        assert (editor.encoding, editor.newline) == ("utf-8-sig", "\r\n")
        editor.selectAll()
        editor.insertPlainText("一\n二\n三\n四\n")
        assert window.save_file() is True

        assert path.read_bytes() == b"\xef\xbb\xbf" + "一\r\n二\r\n三\r\n四\r\n".encode("utf-8")

    def test_unedited_mixed_file_is_not_touched(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        """混在したファイルでも、編集していなければ上書き保存は何も書かない。

        引き継ぎ ⑧ のガードが、この「そろえてしまう」動きの手前に立っている
        ことを示す（＝開いて `Ctrl+S` しただけでは行末は変わらない）。
        """
        path = tmp_path / "mixed.txt"
        original = "一\r\n二\n三\r\n".encode("utf-8")
        path.write_bytes(original)

        window.open_path(path)
        assert window.save_file() is True

        assert path.read_bytes() == original
