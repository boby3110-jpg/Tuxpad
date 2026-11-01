"""「指定文字数で折り返す」が、以前の ``QTextEdit`` と**同じ位置で折り返す**
ことを、本物の ``QTextEdit`` と突き合わせて確かめる（引き継ぎ ⑯）。

本文欄は 2026-10-25（82 回目）に ``QTextEdit`` から ``QPlainTextEdit`` へ
戻した。``QPlainTextEdit`` には「指定文字数」が無いので、
``editor_app.editor._EditorDocumentLayout`` が自前で組んでいる。

**このテストだけは、期待値を人が書き写さない。** ``QTextEdit`` を隣に
並べて、同じ本文・同じフォント・同じ幅・同じ文字数で**実際に折り返した
位置を 1 行ずつ突き合わせる**。人が書き写した期待値は、書き写した時点の
思い込み（たとえば「N 文字ぶんのピクセル幅で折り返しているのだろう」）
ごと固定してしまうが、こちらは**利用者がこれまで見ていた画面そのもの**が
基準になる。実際、引き継ぎ ⑯ の節にあった見立ては 2 つとも誤りだった
（折り返しは文字数そのもので決まる／入り切らないときは横スクロールに
ならず幅で頭打ちになる）。

**2026-10-26、利用者の判断で「頭打ち」はやめた**（ウィンドウが N 文字より
狭くても N 文字で折り返し、はみ出す分は横スクロールで見る。EmEditor と
同じ形）。そのため基準の ``QTextEdit`` は**頭打ちが起きない十分広い
ウィンドウ**（:data:`_REFERENCE_WIDTH`）で組ませ、本文欄の方は狭い窓も
含めて比べる。「N 文字ちょうどで、どこで折り返すか」（全角/半角・単語の
切れ目・タブ）の基準は、引き続き本物の ``QTextEdit`` である。

``QTextEdit`` は Qt が持っているので、**この基準は将来も隣に置いておける**。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QTextEdit

from editor_app.editor import WRAP_FIXED, EditorWidget

#: 突き合わせに使う本文。折り返しの決まり方が変わりやすいものを選んである。
TEXTS = {
    # 半角と全角で 1 文字の幅が違う（文字数で切るのか幅で切るのかが出る）
    "混在": (
        "abcdefghijklmnopqrstuvwxyz0123456789\n"
        "あいうえおかきくけこさしすせそ\n"
        "あaいbうcえdおeかfきgくhけiこj\n"
        # 単語の切れ目を優先するかどうかが出る
        "hello world this is a long english sentence to see wrapping\n"
    ),
    # 全角だけ。狭いウィンドウで N 文字がはみ出す（横スクロールになる）
    "全角のみ": "あいうえおかきくけこさしすせそたちつてと\n" * 6,
    # タブ・空行・行頭の空白
    "タブと空白": "a\tb\tcdefghij\tklmn\n\n  indented line with spaces here\n",
}


#: 基準の ``QTextEdit`` を組ませる幅。どの組み合わせでも N 文字が
#: 入り切る（＝``QTextEdit`` が幅で頭打ちにしない）広さ。
_REFERENCE_WIDTH = 4000


def _visual_lines(document) -> list[list[str]]:
    """**組まれているブロックだけ**の、画面上の折り返し位置。"""
    lines = []
    for number in range(document.blockCount()):
        block = document.findBlockByNumber(number)
        layout = block.layout()
        text = block.text()
        lines.append(
            [
                text[
                    layout.lineAt(index).textStart() : layout.lineAt(index).textStart()
                    + layout.lineAt(index).textLength()
                ]
                for index in range(layout.lineCount())
            ]
        )
    return lines


@pytest.mark.parametrize("name", sorted(TEXTS))
@pytest.mark.parametrize("family,size", [("monospace", 11), ("sans-serif", 9)])
@pytest.mark.parametrize("width", [150, 200, 400, 900, 2000])
@pytest.mark.parametrize("columns", [5, 10, 20, 80])
def test_fixed_column_wraps_where_qtextedit_wrapped(
    qtbot, name: str, family: str, size: int, width: int, columns: int
) -> None:
    text = TEXTS[name]

    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_font_settings(family, size)
    editor.resize(width, 400)
    editor.set_wrap_mode(WRAP_FIXED, column=columns)
    editor.set_content(text)
    editor.show()

    reference = QTextEdit()
    qtbot.addWidget(reference)
    reference.setFont(QFont(family, size))
    # タブの幅は本文欄がフォントに合わせて決めている。揃えないと、
    # 「折り返しの作り」ではなく「タブの幅」の違いを見ることになる。
    reference.setTabStopDistance(editor.tabStopDistance())
    # 本文欄は狭い窓でも N 文字で折り返す（頭打ちしない）ので、基準の
    # QTextEdit は頭打ちが起きない広さで組ませる。
    reference.resize(_REFERENCE_WIDTH, 400)
    reference.setLineWrapMode(QTextEdit.LineWrapMode.FixedColumnWidth)
    reference.setLineWrapColumnOrWidth(columns)
    reference.setPlainText(text)
    reference.show()

    # **落ち着くまで待ってから比べる。** 出したばかりのウィジェットは、
    # 縦スクロールバーが要るかどうかがまだ決まっておらず、ビューポートの
    # 幅が 1 回ぶん違う（実測: 出した直後 384px → 落ち着いて 398px）。
    # その途中の姿を比べると、折り返しの作りではなく**間の悪さ**を
    # 見ることになる。
    QApplication.processEvents()
    editor.viewport().repaint()
    reference.repaint()

    expected = _visual_lines(reference.document())
    actual = _visual_lines(editor.document())

    # ``QPlainTextEdit`` は**画面に出たブロックしか組まない**（それが
    # 大きなファイルで速い理由そのもの）。まだ組まれていないブロックは
    # 比べようが無いので外す——ここを外さずに比べると、「遅延して組む」
    # 仕組みを壊したときだけ緑になる、逆さまのテストになる。
    laid_out = [index for index, lines in enumerate(actual) if lines]
    assert laid_out, "1 ブロックも組まれていない"

    assert [actual[index] for index in laid_out] == [expected[index] for index in laid_out]


# ----------------------------------------------------------------------
# 組んだあとに本文・文字数・フォントが変わる場面（2026-10-25、82 回目の
# もう 1 本が、相手の実装を独立に検証して見つけた穴）
#
# 上の突き合わせは「開いた直後」しか見ていなかったため、**本文を 1 文字
# 打った瞬間に、その行だけウィンドウ幅で折り返し直される**不具合が
# 全 2,232 件緑のまま通っていた。基底クラス (QPlainTextDocumentLayout) は
# 変わったブロックを**捨てるのではなくその場で組み直す**ので、
# 「組みが捨てられているか」だけを組み直しの印にしていると、自前の
# 組みが黙って上書きされる。
#
# 窓の大きさを変えると直ってしまう（基底クラスがそこでは確かに捨てる）
# ため、**操作のあとに resize を挟まないこと**がこのテスト群の肝。
# ----------------------------------------------------------------------
_COLUMNS = 20
_WIDE_ENOUGH = 900
_FULL_WIDTH_LINE = "あ" * 120


def _fixed_editor(qtbot, column: int = _COLUMNS) -> EditorWidget:
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_font_settings("monospace", 11)
    editor.resize(_WIDE_ENOUGH, 400)
    editor.set_wrap_mode(WRAP_FIXED, column=column)
    editor.set_content(_FULL_WIDTH_LINE)
    editor.show()
    QApplication.processEvents()
    editor.viewport().repaint()
    return editor


def _first_block_lengths(editor: EditorWidget) -> list[int]:
    layout = editor.document().firstBlock().layout()
    return [layout.lineAt(index).textLength() for index in range(layout.lineCount())]


def _settle(editor: EditorWidget) -> None:
    """**窓の大きさは変えずに**落ち着かせる（変えると穴が隠れる）。"""
    QApplication.processEvents()
    editor.viewport().repaint()


def test_fixed_column_survives_typing_a_character(qtbot) -> None:
    """本文を 1 文字打っても、その行が指定文字数で折り返し続けること。

    いちばん普通の操作。壊れていると**打った行だけ**が窓いっぱいまで
    伸び、他の行は指定どおりのままなので、編集中の行が急に伸びて見える。
    """
    editor = _fixed_editor(qtbot)
    assert _first_block_lengths(editor)[:3] == [_COLUMNS] * 3

    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("う")
    _settle(editor)

    assert _first_block_lengths(editor)[:3] == [_COLUMNS] * 3


def test_fixed_column_survives_replacing_the_whole_text(qtbot) -> None:
    """本文をまるごと入れ替えても（＝再読み込み）、指定文字数のままであること。"""
    editor = _fixed_editor(qtbot)

    editor.set_content("い" * 120)
    _settle(editor)

    assert _first_block_lengths(editor)[:3] == [_COLUMNS] * 3


def test_changing_the_column_count_takes_effect_without_a_resize(qtbot) -> None:
    """文字数を変えたら、窓の大きさを変えなくてもすぐ効くこと。

    メニューの「指定文字数で折り返す...」で数を入れ直す操作がこれ。
    """
    editor = _fixed_editor(qtbot)

    editor.set_wrap_mode(WRAP_FIXED, column=10)
    _settle(editor)

    assert _first_block_lengths(editor)[:3] == [10] * 3


def test_changing_the_font_keeps_the_column_count(qtbot) -> None:
    """フォントを変えても、1 行の文字数は指定どおりのままであること。

    指定文字数は**文字の幅とは無関係**（文字数そのもので折り返す）なので、
    フォントを大きくしても 1 行に入る文字数は変わらない（窓に入り切ら
    なくなっても、頭打ちにせず横スクロールになる。2026-10-26〜）。
    """
    editor = _fixed_editor(qtbot)

    editor.set_font_settings("monospace", 22)
    _settle(editor)

    assert _first_block_lengths(editor)[:3] == [_COLUMNS] * 3


def test_repainting_does_not_keep_re_laying_out_the_same_blocks(qtbot) -> None:
    """同じ画面を描き直すだけなら、組み直しは 1 回も起きないこと。

    「自分が組んだ組みか」の見分け方を間違えると、**毎回の描画で組み
    直す**空回りになる（画面は正しいので気づけず、スクロールだけが重く
    なる）。とくに**サロゲートペア（絵文字）**は、``textLength()`` が
    UTF-16 の個数を返す＝指定文字数より大きくなるため、「行が指定より
    長ければ組み直す」という見分け方だと必ず空回りする（実測: 10 文字
    指定で ``textLength()`` が 20）。だから絵文字でも確かめる。
    """
    for text in ("あ" * 120, "😀" * 120, "葛\U000E0100" * 80):
        editor = _fixed_editor(qtbot)
        editor.set_content(text)
        _settle(editor)

        layout = editor.document().documentLayout()
        calls = []
        original = layout._layout_block_in_columns
        layout._layout_block_in_columns = lambda block: (
            calls.append(block.blockNumber()) or original(block)
        )
        for _ in range(5):
            editor.viewport().repaint()

        assert calls == [], f"{text[:2]!r} で描き直すたびに組み直している: {len(calls)} 回"
