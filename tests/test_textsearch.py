"""`editor_app.textsearch` の検索本体（純粋関数）のテスト。

`find_matches()` / `line_number_at()` / `line_snippet()` は Qt にもウィンドウにも
依存しないので、`MainWindow` を作らずにここで直接固定しておく。
`MainWindow.search_all_tabs()` 越しの振る舞い（複数タブ・ジャンプ・置換）は
`test_cross_tab_search.py` / `test_search_position_safety.py` 側で見ている。
"""

from __future__ import annotations

import pytest

from editor_app.textsearch import (
    LineNumbers,
    MatchSpan,
    find_matches,
    line_number_at,
    line_snippet,
)

#: 小文字化すると 2 文字になる文字（トルコ語の I）。
DOTTED_I = "İ"
#: BMP 外の文字（絵文字）。Qt では 2 文字ぶんとして数えられる。
EMOJI = "\U0001f600"

#: `line_snippet()` に渡す、畳んだ改行の代わりの文字（本体では search_panel の
#: NEWLINE_GLYPH を渡している）。テストでは取り違えに気づけるよう別の文字にする。
GLYPH = "|"


# ----------------------------------------------------------------------
# find_matches: 基本
# ----------------------------------------------------------------------
def test_find_matches_returns_every_occurrence() -> None:
    spans = find_matches("abcabcabc", "abc")
    assert [span.py_position for span in spans] == [0, 3, 6]


def test_find_matches_empty_query_returns_nothing() -> None:
    assert find_matches("何か本文", "") == []


def test_find_matches_no_hit_returns_nothing() -> None:
    assert find_matches("本文", "見つからない語") == []


def test_find_matches_is_case_insensitive() -> None:
    spans = find_matches("Foo foo FOO", "foo")
    assert [span.py_position for span in spans] == [0, 4, 8]


def test_find_matches_does_not_overlap() -> None:
    """重なる位置は返さない（"aa" は "aaaa" の 0 と 2 の 2 件）。"""
    spans = find_matches("aaaa", "aa")
    assert [span.py_position for span in spans] == [0, 2]


def test_find_matches_spans_newlines() -> None:
    spans = find_matches("1行目\n2行目\n3行目", "1行目\n2行目")
    assert len(spans) == 1
    assert spans[0].py_position == 0
    assert spans[0].py_length == len("1行目\n2行目")


def test_find_matches_japanese() -> None:
    spans = find_matches("これは検索語を含む本文です", "検索語")
    assert [span.py_position for span in spans] == [3]


# ----------------------------------------------------------------------
# find_matches: Python の文字位置と Qt の文書内位置のずれ
# ----------------------------------------------------------------------
def test_find_matches_ascii_positions_agree() -> None:
    """BMP 外の文字が無ければ 2 つの数え方は一致する。"""
    (span,) = find_matches("xxhitxx", "hit")
    assert span == MatchSpan(py_position=2, py_length=3, position=2, length=3)


def test_find_matches_qt_position_counts_emoji_as_two() -> None:
    (span,) = find_matches(f"{EMOJI}{EMOJI}hit", "hit")
    assert span.py_position == 2  # Python では絵文字は 1 文字
    assert span.position == 4  # Qt では 2 文字ぶん


def test_find_matches_qt_position_accumulates_across_matches() -> None:
    """2 件目以降のずれも、前のマッチまでの絵文字を数え落とさない。"""
    text = f"{EMOJI}hit{EMOJI}hit"
    first, second = find_matches(text, "hit")
    assert (first.py_position, first.position) == (1, 2)
    assert (second.py_position, second.position) == (5, 7)


def test_find_matches_qt_length_counts_emoji_in_query() -> None:
    (span,) = find_matches(f"前{EMOJI}後", EMOJI)
    assert span.py_length == 1
    assert span.length == 2


def test_find_matches_length_changing_char_keeps_positions() -> None:
    """小文字化で文字数が変わる文字があっても、後続の位置がずれない。"""
    text = f"{DOTTED_I}hit"
    (span,) = find_matches(text, "hit")
    assert text[span.py_position : span.py_position + span.py_length] == "hit"


# ----------------------------------------------------------------------
# line_number_at
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("position", "expected"),
    [(0, 1), (2, 1), (3, 1), (4, 2), (8, 3)],
)
def test_line_number_at(position: int, expected: int) -> None:
    #            0123 4567 8
    assert line_number_at("aaa\nbbb\nccc", position) == expected


# ----------------------------------------------------------------------
# LineNumbers（行番号を続きから数える方）
# ----------------------------------------------------------------------
#: 行番号を数える対象。空行・末尾の改行・複数行を含めておく。
LINES_TEXT = "aaa\nbbb\n\nccc\nddd\n"


def test_line_numbers_agrees_with_line_number_at_when_ascending() -> None:
    """昇順に訊く限り、1 件ずつ数え直す line_number_at と必ず同じ答えになる。"""
    counter = LineNumbers(LINES_TEXT)
    for position in range(len(LINES_TEXT) + 1):
        assert counter.line_at(position) == line_number_at(LINES_TEXT, position)


def test_line_numbers_agrees_when_asked_out_of_order() -> None:
    """位置が前に戻る訊き方をされても、答えを間違えない（数え直す）。"""
    counter = LineNumbers(LINES_TEXT)
    assert counter.line_at(12) == line_number_at(LINES_TEXT, 12)
    assert counter.line_at(0) == 1
    assert counter.line_at(4) == 2
    # 戻ったあとも、そこから先を続けて正しく答える。
    assert counter.line_at(12) == line_number_at(LINES_TEXT, 12)


def test_line_numbers_repeated_same_position() -> None:
    """同じ位置を何度訊いても、行番号が増えていかない。"""
    counter = LineNumbers(LINES_TEXT)
    assert counter.line_at(5) == 2
    assert counter.line_at(5) == 2
    assert counter.line_at(5) == 2


def test_line_numbers_does_not_rescan_from_the_start() -> None:
    """先頭から数え直していない（＝件数が増えても手間が増え続けない）ことを、
    本文をなぞった総量で見張る。

    これを外すと 2 万件の検索で数秒かかる実装に戻ってしまう
    （引き継ぎ ⑨ の「応答なし」の主因）。本文を 1 度なぞる以上の量を
    数えていたら落ちる。
    """

    class CountingText(str):
        """``count("\n", start, end)`` で見た文字数を積み上げる str。"""

        scanned = 0

        def count(self, sub, start=0, end=None):  # noqa: D102
            end = len(self) if end is None else end
            type(self).scanned += max(0, end - start)
            return str.count(self, sub, start, end)

    text = CountingText("x\n" * 500)
    counter = LineNumbers(text)
    for position in range(0, len(text), 2):
        counter.line_at(position)
    assert CountingText.scanned <= len(text)


# ----------------------------------------------------------------------
# line_snippet
# ----------------------------------------------------------------------
def test_line_snippet_returns_whole_line() -> None:
    text = "1行目\nこの行にヒットがある\n3行目"
    position = text.index("ヒット")
    assert line_snippet(text, position, len("ヒット"), GLYPH) == "この行にヒットがある"


def test_line_snippet_strips_surrounding_whitespace() -> None:
    text = "  \tインデントされた行  \n次の行"
    assert line_snippet(text, text.index("行"), 1, GLYPH) == "インデントされた行"


def test_line_snippet_folds_multiline_match_into_one_line() -> None:
    text = "1行目\n2行目\n3行目"
    assert line_snippet(text, 0, len("1行目\n2行目"), GLYPH) == f"1行目{GLYPH}2行目"


def test_line_snippet_handles_last_line_without_trailing_newline() -> None:
    text = "1行目\n最終行"
    assert line_snippet(text, text.index("最終行"), 3, GLYPH) == "最終行"
