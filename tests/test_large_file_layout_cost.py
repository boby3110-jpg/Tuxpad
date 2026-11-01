"""大きなファイルでの「配置計算」の重さを見張るテスト（引き継ぎ ⑯）。

本文欄が ``QTextEdit`` だった頃、Qt は**表示・移動の前に文書の頭から
目的の場所までの折り返しを全部計算**していた。そのため遠くへ飛ぶ・
ウィンドウの幅が変わるたびに、**ファイルの大きさに比例して**止まった
（実機の記録: 5 万行で 200〜270ms、ゲームで負荷をかけていたときは
0.88 秒）。``QPlainTextEdit`` は**見えている行だけ**を計算するので、
大きさがほとんど効かない。

**判定は「桁」で行う。** マシンの速さで 2〜3 倍は平気で揺れるので、
細かい ms を固定しても意味が無い（遅いマシンで赤くなるだけ）。
20 万行での実測は次のとおり（この環境、offscreen）:

===================  ==============  ==================
操作                 ``QTextEdit``   ``QPlainTextEdit``
===================  ==============  ==================
幅を変えて端↔端       約 7.6 秒        約 74ms
===================  ==============  ==================

100 倍の開きがあるので、その**間**に線を引けば、どちらの作りかを
取り違えずに判定できる。
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtGui import QFont, QTextCursor

from editor_app.editor import WRAP_FIXED, WRAP_WINDOW, EditorWidget

#: 測りやすさのために日本語と半角を混ぜた 1 行（実際に扱うファイルに近い形）。
_LINE = "これはテスト用の日本語の行です。実際のテキストを模しています。abc123\n"

#: 「大きなファイル」として使う行数。実機の 5 万行級より更に 4 倍にして、
#: 比例して遅くなる作りなら必ず秒単位になるようにしてある。
_BIG_LINES = 200_000

#: 比較用の「小さいファイル」。10 分の 1。
_SMALL_LINES = 20_000

#: 20 万行で「幅を変えて端↔端」に許す上限 (秒)。``QTextEdit`` の作りでは
#: 7 秒を超えるので、1 秒なら取り違えようがない。``QPlainTextEdit`` の
#: 実測 (約 0.07 秒) に対しても 10 倍以上の余裕がある。
_BIG_FILE_BUDGET_SECONDS = 1.0

#: 行数を 10 倍にしたときに許す時間の伸び。比例して遅くなる作りなら
#: 10 倍前後になる（実測 10.4 倍）。見えている行だけ計算する作りなら
#: ほとんど伸びない（実測 4.2 倍——測る時間自体が短く、固定の手間が
#: 相対的に大きいので 1 倍にはならない）。
_MAX_GROWTH_FACTOR = 7.0


def _editor(qtbot, lines: int) -> EditorWidget:
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.setFont(QFont("monospace", 9))
    editor.resize(900, 600)
    editor.set_wrap_mode(WRAP_WINDOW)
    editor.set_content(_LINE * lines)
    editor.show()
    editor.viewport().repaint()
    return editor


def _width_change_and_jump_to_both_ends(editor: EditorWidget) -> float:
    """「幅を変えたあと先頭と末尾を行き来する」のにかかった秒数。

    引き継ぎ ⑯ の表と同じ測り方。``QTextEdit`` はここで文書の頭から
    目的の場所までを計算し直すので、行数に比例して遅くなる。
    """
    started = time.perf_counter()
    editor.resize(700, 600)
    editor.viewport().repaint()
    cursor = editor.textCursor()
    for operation in (QTextCursor.MoveOperation.Start, QTextCursor.MoveOperation.End):
        cursor.movePosition(operation)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        editor.viewport().repaint()
    return time.perf_counter() - started


@pytest.mark.slow
def test_a_huge_file_does_not_stall_on_a_width_change(qtbot) -> None:
    """20 万行でも、幅を変えて端↔端が**秒単位にならない**こと。"""
    editor = _editor(qtbot, _BIG_LINES)

    elapsed = _width_change_and_jump_to_both_ends(editor)

    assert elapsed < _BIG_FILE_BUDGET_SECONDS, (
        f"20 万行で {elapsed:.2f} 秒かかった。"
        "文書の頭から全部計算する作り（QTextEdit 相当）に戻っていないか"
    )


@pytest.mark.slow
def test_the_cost_barely_grows_with_the_number_of_lines(qtbot) -> None:
    """行数を 10 倍にしても、かかる時間はほとんど伸びないこと。

    上の上限だけだと「速いマシンなら、比例して遅くなる作りでも通って
    しまう」余地が残る。**伸び方そのもの**を見ておけば、マシンの速さに
    左右されずに作りの違いを捕まえられる。
    """
    small = _width_change_and_jump_to_both_ends(_editor(qtbot, _SMALL_LINES))
    big = _width_change_and_jump_to_both_ends(_editor(qtbot, _BIG_LINES))

    assert big < small * _MAX_GROWTH_FACTOR, (
        f"行数 10 倍で {big / small:.1f} 倍に伸びた"
        f"（{_SMALL_LINES} 行 {small * 1000:.0f}ms → {_BIG_LINES} 行 {big * 1000:.0f}ms）。"
        "行数に比例して遅くなる作りに戻っていないか"
    )


def test_fixed_column_mode_is_also_lazy_on_a_huge_file(qtbot) -> None:
    """「指定文字数で折り返す」でも、大きさに引きずられないこと。

    上の 2 本は「ウィンドウ幅で折り返す」しか測っていない。**自前の組み
    (``_EditorDocumentLayout._layout_block_in_columns``) が走るのは
    「指定文字数」のときだけ**なので、そこが文書全体を舐める作りに
    なっていても誰も気づけなかった（2026-10-25、82 回目のもう 1 本が
    独立に検証して見つけた穴）。

    **組んだ回数も数える。** 時間だけだと、たまたま速いマシンで
    「全部組んでいるのに上限に収まる」形を見逃す。行数が 10 倍でも
    組む回数がほとんど変わらない＝見えているぶんしか組んでいない。
    """
    counts = {}
    elapsed = {}
    for lines in (_SMALL_LINES, _BIG_LINES):
        editor = EditorWidget()
        qtbot.addWidget(editor)
        editor.setFont(QFont("monospace", 9))
        editor.resize(900, 600)
        editor.set_wrap_mode(WRAP_FIXED, column=40)
        editor.set_content(_LINE * lines)
        editor.show()
        editor.viewport().repaint()

        layout = editor.document().documentLayout()
        laid_out = []
        original = layout._layout_block_in_columns
        layout._layout_block_in_columns = lambda block: (
            laid_out.append(block.blockNumber()) or original(block)
        )

        started = time.perf_counter()
        editor.resize(700, 600)
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        editor.viewport().repaint()
        elapsed[lines] = time.perf_counter() - started
        counts[lines] = len(laid_out)

    assert elapsed[_BIG_LINES] < _BIG_FILE_BUDGET_SECONDS, (
        f"{_BIG_LINES:,} 行の「指定文字数」で {elapsed[_BIG_LINES]:.2f} 秒かかった"
    )
    # 行数は 10 倍。見えているぶんしか組まないなら、回数はほぼ同じはず。
    assert counts[_BIG_LINES] <= counts[_SMALL_LINES] * 2, (
        f"行数を 10 倍にしたら組む回数が {counts[_SMALL_LINES]} → "
        f"{counts[_BIG_LINES]} 回に増えた（見えているぶん以上に組んでいる）"
    )
