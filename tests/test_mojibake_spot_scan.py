"""誤読の跡を探す「下ごしらえ」のテスト（67 回目）。

「文字化けしたままではないか」の判定そのもの（しきい値・誤検知の率）は
`tests/test_suspicious_text_before_save.py` が縛っている。ここで縛るのは
その手前の下ごしらえ——**半角カナの並びをどこからどこまでと数えるか**
(:func:`~editor_app.fileio._halfwidth_runs`)、**句読点の割合を何で割るか**、
**該当箇所にどの並びを挙げるか**。

既存のテストが見ていたのは「改行で終わる短い本文」ばかりで、次の形が
裸だった。どれも**利用者から見ると「警告が出るべきときに出ない」
（＝化けたまま上書きされる）**という、いちばん重い形になる:

- **改行で終わらない本文**。並びが本文の最後まで続いていると、その並びを
  そもそも数えていなかった場合に取りこぼす（エディタの本文は必ずしも
  改行で終わらない）。
- **誤読した行が長いファイルに 1 行だけ混ざっている形**。割合の分母を
  本文全体にすると、その 1 行は薄まって見えなくなる。
- **本物の半角カナのファイル**。句読点の個数を見ないと、`｡` が 1 つ
  あるだけで毎回警告が出る（うるさい警告は読まれなくなる）。
"""

from __future__ import annotations

from editor_app import fileio


def misread(text: str, written: str, read: str) -> str:
    """``written`` で書かれた文を ``read`` として読んでしまった本文を作る。"""
    return text.encode(written).decode(read)


# ----------------------------------------------------------------------
# 本文の最後まで続く並び（改行で終わらない本文）
# ----------------------------------------------------------------------
def test_a_misread_run_reaching_the_end_of_the_text_is_still_found() -> None:
    """全角と半角カナの交互が**本文の最後まで続く**形でも拾う。

    エディタの本文は必ずしも改行で終わらない（最後の行を打っている途中、
    改行を入れない運用のファイル）。並びの終わりを本文の終端で閉じないと、
    **最後の並びが数えられず**、交互の回数が足りなくなって黙る。
    """
    text = "焦ｻﾒ聚ﾎ礁ｳ"  # 末尾に改行が無い
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_MIXED_HALFWIDTH
    (position, excerpt), = found.spots
    # 最後の 1 文字まで抜粋に入っていること（終端で閉じ損なうと 1 文字短くなる）。
    assert excerpt == "ｻﾒ聚ﾎ礁ｳ"
    assert position + len(excerpt) == len(text)


def test_the_punctuation_pattern_is_found_without_a_trailing_newline() -> None:
    """EUC-JP を Shift-JIS として読んだ跡が、改行の無い本文でも拾えること。

    ``､ｳ､ﾋ､ﾁ､ﾏ`` は EUC-JP のひらがなを Shift-JIS として読んだときの形
    （1 バイト目 0xA4 が ``､`` になる）。この形では本文がまるごと半角の
    並び 1 つなので、終端で閉じていないと**並びが 1 つも無いことになり、
    割合を出す分母が 0 になって黙る**。
    """
    text = "､ｳ､ﾋ､ﾁ､ﾏ"  # 末尾に改行が無い
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_HALFWIDTH_PUNCTUATION
    (position, excerpt), = found.spots
    assert position == 0
    assert excerpt == text  # 本文全体が 1 つの並び


# ----------------------------------------------------------------------
# 割合の分母（誤読した行が 1 行だけ混ざった長いファイル）
# ----------------------------------------------------------------------
def test_the_punctuation_ratio_is_measured_inside_the_halfwidth_runs() -> None:
    """長いファイルに誤読した行が 1 行混ざっただけでも拾うこと。

    割合の分母は**半角の並びの長さ**でなければならない。本文全体の長さで
    割ると、まともな行が多いほど跡が薄まり、**大きいファイルでは何も
    警告が出なくなる**（実際のファイルは 1 行だけではない）。
    """
    ordinary = "これはふつうの日本語の行です。\n" * 20
    text = ordinary + misread("こんにちは\n", "euc_jp", "cp932")

    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_HALFWIDTH_PUNCTUATION
    # 該当箇所は誤読した行の中（まともな行の側ではない）。
    position, _excerpt = found.spots[0]
    assert position >= len(ordinary)


def test_only_the_runs_that_contain_halfwidth_punctuation_are_listed() -> None:
    """該当箇所に挙げるのは、**半角の句読点がある並び**だけ。

    本物の半角カナの並びまで挙げると、ダイアログが跡と無関係な場所を並べ、
    「最初の 1 つ」へ飛ばしたカーソルも見当違いの行に着く。
    """
    text = "ｱｲｳ\n､ｳ､ﾋ､ﾁ､ﾏ"  # 1 行目は本物の半角カナ、2 行目が誤読の跡
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_HALFWIDTH_PUNCTUATION
    # 2 行目だけが挙がること（1 行目の本物の並びは挙げない）。
    assert [(position, excerpt) for position, excerpt in found.spots] == [
        (len("ｱｲｳ\n"), "､ｳ､ﾋ､ﾁ､ﾏ")
    ]


# ----------------------------------------------------------------------
# 本物の半角カナのファイルで黙ること
# ----------------------------------------------------------------------
def test_one_or_two_full_stops_in_a_halfwidth_file_stay_silent() -> None:
    """句読点が 1〜2 個しかない半角カナの本文では黙る（割合だけで決めない）。

    短い本文では、句点 1 つでも割合が 3 割を超えてしまう。個数も見ていないと
    **半角カナの短いメモを開くたびに警告が出る**。目安の警告がうるさいと、
    利用者はいずれ本物の警告も読まなくなる。
    """
    for text in ("ｱｲ｡", "ｱｲｳ｡ｴ｡", "ｶ｡\nｷ｡\n"):
        assert fileio.find_suspicious_text(text, "cp932") is None, text

    # 3 個以上そろって、かつ割合が 3 割以上なら警告する（境目の確認）。
    found = fileio.find_suspicious_text("ｱ｡ｲ｡ｳ｡", "cp932")
    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_HALFWIDTH_PUNCTUATION


def test_small_kana_and_the_long_vowel_mark_are_not_punctuation() -> None:
    """拗音・促音・長音 (``ｮ ｯ ｰ``) は句読点に数えない。

    句読点の範囲を 1 文字でも広げると、``ｼｮｰﾄｶｯﾄ`` のようなふつうの半角カナの
    語が「句読点が 3 割以上」に見えてしまう。**本物の半角カナのファイルを
    開くたびに警告が出る**形なので、範囲の端はここで固定しておく。
    """
    for text in ("ｼｮｰﾄｶｯﾄ\n", "ﾎﾟｲﾝﾄ\n", "ﾃﾞｰﾀｺﾋﾟｰ\n", "ﾒﾆｭｰ\n"):
        assert fileio.find_suspicious_text(text, "cp932") is None, text


# ----------------------------------------------------------------------
# 並びの切れ目（どの文字を半角カナとみなすか）
# ----------------------------------------------------------------------
def test_the_semi_voiced_mark_stays_inside_the_halfwidth_run() -> None:
    """半濁点 ﾟ (U+FF9F) も半角カナの並びの一部（範囲の端を落とさない）。

    ﾟ で並びが切れると、半濁音を含む跡（``ﾊﾟ`` など）はそこで 2 つに割れ、
    **全角を挟んだ交互と見なせなくなって黙る**。
    """
    text = "焦ﾊﾟ聚ﾎ礁ｳ"
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_MIXED_HALFWIDTH
    (_position, excerpt), = found.spots
    assert excerpt == "ﾊﾟ聚ﾎ礁ｳ"  # ﾊ と ﾟ が 1 つの並びのまま


def test_fullwidth_characters_break_a_halfwidth_run() -> None:
    """全角英数字は半角カナに数えない（挟まっているものが跡を見分ける鍵）。

    「全角を 1 文字挟んだだけで隣り合う並び」を数えるのが誤読の跡の見分け方
    なので、全角まで半角カナに数えてしまうと**全体が 1 つの並びになり、
    交互が 1 度も無いことになって黙る**。
    """
    text = "焦ｻﾒ１ﾎ１ｳ"
    found = fileio.find_suspicious_text(text, "cp932")

    assert found is not None
    assert found.reason == fileio.SUSPICIOUS_MIXED_HALFWIDTH
    (_position, excerpt), = found.spots
    assert excerpt == "ｻﾒ１ﾎ１ｳ"
