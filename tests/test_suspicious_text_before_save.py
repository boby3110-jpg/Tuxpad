"""保存直前に「文字化けしたままではないか」を目安で警告するテスト（引き継ぎ ⑥）.

開いた時点の一の矢（判定の拮抗を知らせる印）に対する二の矢。**編集した後の
本文には「本来どの文字コードだったか」という正解がもう無い**ため、これは
確実な検知ではなく「怪しい」という目安でしかない。

ここで固定しているのは 2 つ:

- **拾いたい形を拾えること**（実際に起きた 2 つの不具合の跡——JIS を別の
  文字コードとして読んだ跡の制御文字と、EUC-JP を Shift-JIS として読んだ跡の
  半角カナ）。
- **誤検知しないこと**。目安の判定なので、うるさければ利用者はいずれ
  読まなくなる。ふつうの日本語・本物の半角カナのファイルで黙ることは、
  拾えることと同じくらい大事。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from editor_app import fileio
from editor_app.main_window import MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（test_save_file.py と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def misread(text: str, written: str, read: str) -> str:
    """``written`` で書かれた文を ``read`` として読んでしまった本文を作る。"""
    return text.encode(written).decode(read)


@pytest.fixture
def answered(monkeypatch: pytest.MonkeyPatch):
    """「怪しい」警告を捕まえ、押すボタンを決めておくフィクスチャ。

    ``calls`` に (タイトル, 本文) が積まれる。``calls.answer`` を True に
    すると「このまま保存する」、False なら「キャンセル」を押したことになる。
    """

    class Answered(list):
        answer = False

    calls = Answered()

    def fake(_parent, title, text):
        calls.append((title, text))
        return calls.answer

    monkeypatch.setattr("editor_app.file_commands.prompt_suspicious_text", fake)
    return calls


# ----------------------------------------------------------------------
# fileio: 判定そのもの
# ----------------------------------------------------------------------
def test_control_characters_are_suspicious_in_any_encoding() -> None:
    """JIS のファイルを別の文字コードとして読んだ跡（生の ESC）を拾う。

    UTF-8 だけを見ていると取りこぼす。ISO-2022-JP は 7bit なので、
    cp932 や euc_jp としても「読めて」しまうため。
    """
    text = misread("日本語\n", "iso2022_jp", "utf-8")
    assert "\x1b" in text  # 前提: 生のエスケープが本文に残っている

    for encoding in ("utf-8", "cp932", "euc_jp", "utf-16-le"):
        found = fileio.find_suspicious_text(text, encoding)
        assert found is not None, encoding
        assert found.reason == fileio.SUSPICIOUS_CONTROL


def test_ordinary_control_characters_are_left_alone() -> None:
    """タブ・改行・復帰・改ページは本文にあってよい（改ページは区切りに使う）。"""
    assert fileio.find_suspicious_text("一\t二\r\n三\f四\n", "utf-8") is None


def test_control_spot_shows_the_escape_sequence() -> None:
    """該当箇所は ``<ESC>$B`` の形で見せる（そのままでは何も見えない）。"""
    text = misread("日本語\n", "iso2022_jp", "utf-8")
    found = fileio.find_suspicious_text(text, "utf-8")

    position, excerpt = found.spots[0]
    assert text[position] == "\x1b"
    assert fileio.describe_text_spot(excerpt).startswith("<ESC>$B")


def test_control_spots_stop_at_the_limit() -> None:
    found = fileio.find_suspicious_text("\x1b" * 50, "utf-8", limit=3)

    assert len(found.spots) == 3
    assert found.truncated is True


def test_halfwidth_punctuation_pattern_is_suspicious() -> None:
    """EUC-JP のひらがなを Shift-JIS として読んだ跡（``､`` が 1 文字おき）。"""
    text = misread("こんにちは\n", "euc_jp", "cp932")
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_HALFWIDTH_PUNCTUATION
    assert found.spots
    for position, excerpt in found.spots:
        assert text[position : position + len(excerpt)] == excerpt


def test_halfwidth_mixed_with_fullwidth_is_suspicious() -> None:
    """全角と半角カナが 1 文字おきに近い細かさで交互に現れる形。"""
    text = "焦ｻﾒ聚ﾎ礁ｳ\n"
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_MIXED_HALFWIDTH
    # 束ねた範囲をまとめて 1 か所として見せる（塊ごとにばらすと読めない）。
    (position, excerpt), = found.spots
    assert excerpt == "ｻﾒ聚ﾎ礁ｳ"
    assert text[position : position + len(excerpt)] == excerpt


def test_ordinary_japanese_is_not_suspicious() -> None:
    for text in (
        "これはふつうの日本語の文章です。\n改行もあります。\n",
        "漢字仮名交じり文\n",
        "abc 123 ASCII だけの行\n",
        "",
    ):
        for encoding in ("cp932", "euc_jp", "utf-8"):
            assert fileio.find_suspicious_text(text, encoding) is None, (text, encoding)


def test_genuine_halfwidth_kana_file_is_not_suspicious() -> None:
    """本物の半角カナのファイル（レガシーな固定長データ等）では黙る。

    句読点が何個あるかではなく、**半角の並びに占める割合**で見ていること
    （個数だけで決めると、句点で区切った半角カナの文が毎回引っかかる）。
    """
    for text in (
        "ｱｲｳｴｵ\nｶｷｸｹｺ\n",
        "ﾃﾞｰﾀ1,ﾃﾞｰﾀ2,ﾃﾞｰﾀ3\n",
        "ｺﾝﾆﾁﾊ｡ｻﾖｳﾅﾗ｡\n",
        "ｵﾊﾖｳｺﾞｻﾞｲﾏｽ｡ｺﾝﾆﾁﾊ｡ｺﾝﾊﾞﾝﾊ｡ｵﾔｽﾐﾅｻｲ｡\n",
        "ｱｲｳ､ｴｵ､ｶｷ､ｸｹ\n",
    ):
        assert fileio.find_suspicious_text(text, "cp932") is None, text


def test_halfwidth_kana_words_inside_fullwidth_text_are_not_suspicious() -> None:
    """全角の文に半角カナの語が混じるのは、実際にあるので黙る。

    「混ざっていたら怪しい」にすると、この形が毎回引っかかる。**塊どうしが
    離れているか**が本物と誤読を分ける。
    """
    for text in (
        "この行には ﾃﾞｰﾀ という語が入っています。\n",
        "ﾃﾞｰﾀは ﾌｧｲﾙ に ｼｽﾃﾑ で保存します。\n",
        "氏名　　カナ　　金額\n山田太郎　ﾔﾏﾀﾞﾀﾛｳ　12345\n鈴木花子　ｽｽﾞｷﾊﾅｺ　67890\n",
        "【顧客名簿】\nｱｵﾔﾏｹﾝｲﾁ,0001234\nｲﾉｳｴﾐｻｷ,0005678\n",
    ):
        assert fileio.find_suspicious_text(text, "cp932") is None, text


def test_known_false_positives_are_recorded() -> None:
    """**分かっていて誤検知する形**を、隠さずここに固定しておく。

    誤読の跡と見分けがつかない書き方は実際に存在する。黙らせることも
    できるが、そうすると**同じ理由で本物の誤読も黙る**ので、利用者の
    最優先事項（ファイルを壊さない）に従って知らせる側に倒してある
    （引き継ぎ ⑤ で同じ判断をしたのと同じ理由）。

    ここが赤くなったら、それは「誤検知が減った」のかもしれないし
    「拾えなくなった」のかもしれない。**必ず両方を測り直してから**
    期待値を書き換えること。
    """
    # 半角カナの語を助詞 1 文字だけで繋いだ文。EUC-JP の誤読と同じ形になる。
    found = fileio.find_suspicious_text("ﾃﾞｰﾀをﾌｧｲﾙにｺﾋﾟｰする\n", "cp932")
    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_MIXED_HALFWIDTH

    # 半角の読点 ､ を区切りに使った、1 文字の項目が続く半角カナの CSV。
    found = fileio.find_suspicious_text("ｱ､ｲ､ｳ､ｴ､ｵ\n", "cp932")
    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_HALFWIDTH_PUNCTUATION


def test_japanese_patterns_are_only_checked_for_the_two_legacy_encodings() -> None:
    """半角カナの判定は cp932 / euc_jp のときだけ（既知の実バグに絞る）。"""
    text = misread("こんにちは\n", "euc_jp", "cp932")

    assert fileio.find_suspicious_text(text, "cp932") is not None
    assert fileio.find_suspicious_text(text, "euc_jp") is not None
    assert fileio.find_suspicious_text(text, "utf-8") is None
    assert fileio.find_suspicious_text(text, "utf-16-le") is None


def japanese_pool() -> list[str]:
    """それらしい日本語の文を作るための文字の袋（ひらがな多め）。

    漢字は **EUC-JP で書ける**ものだけに絞る。絞らないと、80 文字の見本は
    ほぼ必ず EUC-JP に無い漢字を含んでしまい、「EUC-JP のファイルを
    読み違えた本文」を作れなくなる（実際のファイルにも、その文字コードで
    書けない文字は入っていない）。
    """
    hira = [chr(c) for c in range(0x3042, 0x3094)]
    kata = [chr(c) for c in range(0x30A2, 0x30F4)]
    kanji = []
    for code in range(0x4E00, 0x9FA0):
        try:
            chr(code).encode("euc_jp")
        except UnicodeEncodeError:
            continue
        kanji.append(chr(code))
    return hira * 6 + kanji * 3 + kata + list("、。「」・ー")


def japanese_samples(count: int, pool: list[str]) -> list[str]:
    rng = random.Random(20260816)
    return [
        "".join(rng.choice(pool) for _ in range(rng.randint(4, 80)))
        for _ in range(count)
    ]


def test_no_false_positive_over_many_generated_japanese_texts() -> None:
    """無作為に作った日本語で誤検知しないこと（作り込みすぎの歯止め）。

    しきい値をいじったときに、静かに誤検知が増えていないかを見張る。
    件数を抑えてあるのは、テスト 1 件に何秒もかけないため
    （設計時は 2 万通りずつで測った。詳しくは
    :func:`~editor_app.fileio.find_suspicious_text` の説明）。
    """
    for text in japanese_samples(2000, japanese_pool()):
        assert fileio.find_suspicious_text(text, "cp932") is None, text
        assert fileio.find_suspicious_text(text, "euc_jp") is None, text


def test_it_catches_a_good_share_of_real_misreads() -> None:
    """EUC-JP を cp932 として読んだ本文を、半分より多く拾えること。

    100% は原理的に無理（漢字だけの EUC-JP は半角カナだけに化け、本物の
    半角カナのファイルと見分けられない）。**取りこぼしがあること自体は
    仕様**なので、ここでは「目安として役に立つ程度には拾える」ことだけを
    縛る。設計時の実測は 65%。
    """
    caught = total = 0
    for text in japanese_samples(2000, japanese_pool()):
        try:
            broken = misread(text, "euc_jp", "cp932")
        except UnicodeDecodeError:
            continue  # cp932 では読めない＝そもそも化けずにエラーになる
        total += 1
        if fileio.find_suspicious_text(broken, "cp932") is not None:
            caught += 1

    assert total > 100, total
    assert caught / total > 0.5, f"{caught}/{total}"


# ----------------------------------------------------------------------
# 保存の流れ: 警告を出す・出さない
# ----------------------------------------------------------------------
def test_saving_a_mojibake_buffer_asks_first(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, misread("こんにちは\n", "euc_jp", "cp932"))

    assert window.save_editor(editor) is False

    title, text = answered[0]
    assert title == "文字化けしているかもしれません"
    assert "文字化けしたままに見えます" in text
    assert "1 行目: " in text
    # 押さなければ書き込まない（ファイルはそのまま）。
    assert fileio.read_text_file(path)[0] == "もとの内容\n"


def test_save_anyway_actually_writes(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    answered.answer = True
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    broken = misread("こんにちは\n", "euc_jp", "cp932")
    edit_text(editor, broken)

    assert window.save_editor(editor) is True

    assert len(answered) == 1
    assert fileio.read_text_file_as(path, "cp932")[0] == broken
    assert editor.is_modified is False


def test_cancelling_leaves_the_file_and_the_tab_alone(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, misread("こんにちは\n", "euc_jp", "cp932"))

    assert window.save_editor(editor) is False

    assert fileio.read_text_file(path)[0] == "もとの内容\n"
    assert editor.is_modified is True  # 未保存のまま（保存済みに見せない）


def test_the_first_spot_is_selected(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    """最初の 1 つを選択しておく（保存できない文字の案内と同じ作り）。"""
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    broken = misread("こんにちは\n", "euc_jp", "cp932")
    edit_text(editor, broken)

    window.save_editor(editor)

    found = fileio.find_suspicious_text(broken, "cp932")
    assert editor.textCursor().selectedText() == found.spots[0][1]


def test_an_ordinary_save_is_not_interrupted(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, "ふつうの日本語に書き換えました。\n")

    assert window.save_editor(editor) is True

    assert answered == []
    assert fileio.read_text_file(path)[0] == "ふつうの日本語に書き換えました。\n"


def test_saving_a_jis_file_that_was_read_as_utf8_asks_first(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    """JIS の中身を UTF-8 のタブで保存しようとした場合も止める。"""
    path = tmp_path / "sample.txt"
    path.write_bytes("日本語\n".encode("iso2022_jp"))
    editor = window.open_path(path)
    # 判定は JIS を正しく見分けるので、誤判定した状態を作って確かめる。
    editor.encoding = "utf-8"
    edit_text(editor, misread("日本語\n", "iso2022_jp", "utf-8"))

    assert window.save_editor(editor) is False

    _title, text = answered[0]
    assert "制御文字" in text
    assert "<ESC>" in text


# ----------------------------------------------------------------------
# 他の関門との順序（ダイアログが 2 つ重ならないこと）
# ----------------------------------------------------------------------
def test_external_change_is_asked_before_the_suspicious_warning(
    window: MainWindow, tmp_path: Path, answered, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外部変更（③④）の方が先。怪しい警告は出さない。"""
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, misread("こんにちは\n", "euc_jp", "cp932"))

    external: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_external_change",
        lambda _parent, *, name, diff_text: external.append(name),
    )
    # 外部で書き換わったことにする（見分け札を食い違わせる）。
    fileio.write_text_file(path, "外部で書き換えた内容\n", "cp932", "\n")

    assert window.save_editor(editor) is False

    assert external == [path.name]
    assert answered == []


def test_unencodable_characters_are_reported_before_the_suspicious_warning(
    window: MainWindow, tmp_path: Path, answered, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存できない文字（既存の EncodingError）の方が先。両方は出さない。"""
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)

    messages: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, _text, *a, **k: messages.append(title),
    )
    # 化けた本文に、cp932 で表せない文字を混ぜる。
    edit_text(editor, misread("こんにちは\n", "euc_jp", "cp932") + "♡")

    assert window.save_editor(editor) is False

    assert messages == ["保存できません"]
    assert answered == []


def test_nothing_is_written_when_the_warning_is_cancelled_on_save_as(
    window: MainWindow, tmp_path: Path, answered, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「名前を付けて保存」でも同じ関門を通る（書き込みは 1 か所に合流する）。"""
    target = tmp_path / "新しい名前.txt"
    editor = window.current_editor()
    editor.encoding = "cp932"
    edit_text(editor, misread("こんにちは\n", "euc_jp", "cp932"))

    monkeypatch.setattr(
        "editor_app.file_commands.QFileDialog.exec", lambda _self: 1
    )
    monkeypatch.setattr(
        "editor_app.file_commands.QFileDialog.selectedFiles",
        lambda _self: [str(target)],
    )

    assert window.save_editor_as(editor) is False

    assert len(answered) == 1
    assert target.exists() is False


def test_only_the_first_few_spots_are_listed(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    """該当が多いときは先頭だけ並べて「…」で打ち切る（ダイアログがはみ出さないように）。"""
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, misread("こんにちは\n" * 20, "euc_jp", "cp932"))

    assert window.save_editor(editor) is False

    _title, text = answered[0]
    listed = [line for line in text.splitlines() if " 行目: " in line]
    assert len(listed) == 5
    assert "　　…" in text


def test_a_long_spot_is_shortened(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    """1 か所の抜粋が長すぎるときも、ダイアログが横に伸びきらないよう切り詰める。"""
    path = tmp_path / "sample.txt"
    fileio.write_text_file(path, "もとの内容\n", "cp932", "\n")
    editor = window.open_path(path)
    edit_text(editor, "､ｱ" * 40 + "\n")

    assert window.save_editor(editor) is False

    _title, text = answered[0]
    listed, = [line for line in text.splitlines() if " 行目: " in line]
    excerpt = listed.split("「", 1)[1].rsplit("」", 1)[0]
    assert excerpt.endswith("…")
    assert len(excerpt) == 31  # 上限 30 文字 ＋ 省略記号


def test_the_ambiguity_mark_survives_a_stopped_save(
    window: MainWindow, tmp_path: Path, answered
) -> None:
    """⑤ の「拮抗の印」と ⑥ の警告が噛み合っていること。

    普通の上書き保存は「書いた以上その文字コードで確定」として印を消す。
    印に気づかないまま Ctrl+S された場合、**壊した直後に印だけが消える**
    のではないか、というのが ⑤ の実装中に残された宿題（PROGRESS.md）。
    ⑥ がその手前で止めるので、**印は消えずに残る**——これを固定しておく。
    """
    path = tmp_path / "sample.txt"
    path.write_bytes("こんにちは\n".encode("euc_jp"))
    editor = window.open_path(path)
    editor.encoding_alternatives = ("cp932",)  # 拮抗した状態を作る
    edit_text(editor, misread("こんにちは\n", "euc_jp", "cp932"))
    editor.encoding = "cp932"

    assert window.save_editor(editor) is False

    assert len(answered) == 1
    assert editor.encoding_alternatives == ("cp932",)  # 印は消えていない
