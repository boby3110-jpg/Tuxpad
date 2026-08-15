"""検索・置換の「文字位置」が Qt の文書内位置とずれないことのテスト。

`search_all_tabs()` は Python の文字列で本文を検索し、その結果を
`QTextCursor` に渡して選択・置換する。Python と Qt では文字数の数え方が
違う箇所が 2 つあり、素直に書くと **本文の別の場所を書き換えてしまう**
（詳細は `editor_app/textsearch.py` の説明を参照）:

1. `str.lower()` で文字数が変わる文字（例: "İ" U+0130 → 2 文字の "i̇"）
2. BMP 外の文字（絵文字など）は Python で 1 文字、Qt で 2 と数えられる

どちらも日本語の実際のファイルでも起こりうる（絵文字の貼り付け等）ため、
「ジャンプ先の選択範囲」と「置換結果」の両方を回帰テストで固定する。
"""

from __future__ import annotations

import pytest

from editor_app.main_window import MainWindow
from editor_app.textsearch import fold_case, surrogate_extra, utf16_length

#: 小文字化すると 2 文字になる文字（トルコ語の I）。
DOTTED_I = "İ"
#: BMP 外の文字（絵文字）。Qt では 2 文字ぶんとして数えられる。
EMOJI = "\U0001f600"


def set_text(window: MainWindow, text: str) -> None:
    window.current_editor().setPlainText(text)


def selected_text_after_jump(window: MainWindow, query: str, index: int = 0) -> str:
    matches = window.search_all_tabs(query)
    window.jump_to_match(matches[index])
    return window.current_editor().textCursor().selectedText()


def replace_all(window: MainWindow, query: str, replacement: str) -> None:
    window.show_replace_panel()
    window.search_panel._input.setPlainText(query)
    window.search_panel._replace_input.setPlainText(replacement)
    window._replace_all_matches()


# ----------------------------------------------------------------------
# textsearch の純粋関数
# ----------------------------------------------------------------------
def test_fold_case_lowercases_ascii() -> None:
    assert fold_case("AbC") == "abc"


@pytest.mark.parametrize(
    "text",
    ["", "abc", "日本語のテキスト", DOTTED_I, f"{DOTTED_I}stanbul", EMOJI, "ẞ"],
)
def test_fold_case_never_changes_length(text: str) -> None:
    assert len(fold_case(text)) == len(text)


def test_fold_case_keeps_length_changing_char_as_is() -> None:
    # "İ".lower() は 2 文字になるので、位置を保つために元の文字を残す。
    assert fold_case(f"A{DOTTED_I}B") == f"a{DOTTED_I}b"


def test_fold_case_still_folds_around_length_changing_char() -> None:
    assert fold_case(f"AB{DOTTED_I}CD") == f"ab{DOTTED_I}cd"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("abc", 3), ("日本語", 3), (EMOJI, 2), (f"あ{EMOJI}い", 4)],
)
def test_utf16_length(text: str, expected: int) -> None:
    assert utf16_length(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"), [("", 0), ("日本語", 0), (EMOJI, 1), (f"{EMOJI}{EMOJI}", 2)]
)
def test_surrogate_extra(text: str, expected: int) -> None:
    assert surrogate_extra(text) == expected


# ----------------------------------------------------------------------
# 小文字化で文字数が変わる文字（U+0130 など）を含む本文
# ----------------------------------------------------------------------
def test_jump_selects_the_match_after_a_length_changing_char(
    window: MainWindow,
) -> None:
    set_text(window, f"{DOTTED_I}stanbul\nテスト対象\n")

    assert selected_text_after_jump(window, "テスト") == "テスト"


def test_all_matches_after_a_length_changing_char_are_correct(
    window: MainWindow,
) -> None:
    set_text(window, f"{DOTTED_I}\nテスト対象テスト\n")

    assert selected_text_after_jump(window, "テスト", 0) == "テスト"
    assert selected_text_after_jump(window, "テスト", 1) == "テスト"


def test_replace_all_after_a_length_changing_char_does_not_corrupt_text(
    window: MainWindow,
) -> None:
    set_text(window, f"aBc {DOTTED_I} aBc")

    replace_all(window, "abc", "Z")

    assert window.current_editor().toPlainText() == f"Z {DOTTED_I} Z"


def test_length_changing_char_does_not_shift_line_numbers(window: MainWindow) -> None:
    set_text(window, f"{DOTTED_I}\n\nテスト\n")

    assert window.search_all_tabs("テスト")[0].line == 3


# ----------------------------------------------------------------------
# BMP 外の文字（絵文字）を含む本文
# ----------------------------------------------------------------------
def test_jump_selects_the_match_after_an_emoji(window: MainWindow) -> None:
    set_text(window, f"{EMOJI}あいうテストえお")

    assert selected_text_after_jump(window, "テスト") == "テスト"


def test_jump_selects_the_match_after_several_emoji(window: MainWindow) -> None:
    set_text(window, f"{EMOJI}あ{EMOJI}い{EMOJI}うテスト")

    assert selected_text_after_jump(window, "テスト") == "テスト"


def test_replace_all_after_an_emoji_does_not_corrupt_text(window: MainWindow) -> None:
    set_text(window, f"{EMOJI}あいうテストえおテスト")

    replace_all(window, "テスト", "XY")

    assert window.current_editor().toPlainText() == f"{EMOJI}あいうXYえおXY"


def test_emoji_in_the_query_is_replaced_as_a_whole(window: MainWindow) -> None:
    set_text(window, f"前{EMOJI}後")

    replace_all(window, EMOJI, "ニコ")

    assert window.current_editor().toPlainText() == "前ニコ後"


def test_search_finds_an_emoji_and_reports_its_qt_length(window: MainWindow) -> None:
    set_text(window, f"あ{EMOJI}")
    match = window.search_all_tabs(EMOJI)[0]

    # Qt の数え方では "あ" が 1、絵文字が 2。
    assert (match.position, match.length) == (1, 2)


def test_emoji_does_not_break_the_snippet(window: MainWindow) -> None:
    set_text(window, f"{EMOJI} テスト行\n")

    assert window.search_all_tabs("テスト")[0].snippet == f"{EMOJI} テスト行"


# ----------------------------------------------------------------------
# 複数タブ・複数行でも同じであること
# ----------------------------------------------------------------------
def test_positions_stay_correct_across_tabs(window: MainWindow) -> None:
    set_text(window, f"{EMOJI}テスト")
    window.new_file()
    set_text(window, f"{DOTTED_I}テスト")

    assert selected_text_after_jump(window, "テスト", 0) == "テスト"
    assert selected_text_after_jump(window, "テスト", 1) == "テスト"


def test_multiline_query_after_an_emoji_is_replaced_correctly(
    window: MainWindow,
) -> None:
    set_text(window, f"{EMOJI}\nあ\nい\n")

    replace_all(window, "あ\nい", "X")

    assert window.current_editor().toPlainText() == f"{EMOJI}\nX\n"


# ----------------------------------------------------------------------
# 普通のテキストが今までどおり動くこと（回帰）
# ----------------------------------------------------------------------
def test_plain_japanese_text_is_unaffected(window: MainWindow) -> None:
    set_text(window, "普通のテキストのテスト\n")

    replace_all(window, "テスト", "確認")

    assert window.current_editor().toPlainText() == "普通のテキストの確認\n"
