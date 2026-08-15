"""改行記号の表示（EmEditor 相当）のテスト。実機フィードバックにより追加。

記号は Qt 標準の「¶ 風」ではなく、``paintEvent()`` で自前に描く「↓」
（薄い青）。ヘッドレスでも
(1) 位置決めの規則（``line_break_mark_points()``）と
(2) 実際に薄い青のピクセルが出ること（オフスクリーン描画）
を固定できる。濃さが「薄っすら」として適切かの最終判断は実機の目視。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QTextCursor, QTextOption

from editor_app.editor import (
    DEFAULT_SHOW_LINE_BREAKS,
    LINE_BREAK_MARK,
    LINE_BREAK_MARK_COLOR,
    WRAP_FIXED,
    EditorWidget,
)
from editor_app.main_window import MainWindow
from editor_app.settings import load_show_line_breaks


def _prepare(editor: EditorWidget, text: str) -> None:
    """描画結果を安定させるため、白背景・黒文字に固定してから表示する。"""
    editor.set_editor_colors(QColor("white"), QColor("black"))
    editor.set_content(text)
    editor.resize(400, 200)


def _mark_pixels(editor: EditorWidget) -> list[tuple[int, int, QColor, QColor]]:
    """改行記号として描き足されたピクセルの一覧。

    要素は ``(x, y, 記号なしの色, 記号ありの色)``。本文のアンチエイリアス
    にも青みがかったピクセルは現れるので、「青いピクセルを探す」のでは
    なく**表示 ON/OFF の描画結果の差分**を取る。こうすれば描き足された
    ぶんだけが確実に取れる。
    """
    was_enabled = editor.show_line_breaks()
    editor.set_show_line_breaks(False)
    without_marks = editor.grab().toImage()
    editor.set_show_line_breaks(True)
    with_marks = editor.grab().toImage()
    editor.set_show_line_breaks(was_enabled)

    found = []
    for y in range(without_marks.height()):
        for x in range(without_marks.width()):
            before = without_marks.pixelColor(x, y)
            after = with_marks.pixelColor(x, y)
            if before != after:
                found.append((x, y, before, after))
    return found


def _end_of_block_rect(editor: EditorWidget, block_number: int):
    block = editor.document().findBlockByNumber(block_number)
    cursor = QTextCursor(block)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    return editor.cursorRect(cursor)


def test_editor_default_does_not_show_line_breaks(window: MainWindow) -> None:
    editor = window.current_editor()
    assert editor.show_line_breaks() is DEFAULT_SHOW_LINE_BREAKS is False
    assert editor.line_break_mark_points() == []


def test_line_break_mark_is_down_arrow() -> None:
    """記号は EmEditor に合わせた下向き矢印であること（¶ ではない）。"""
    assert LINE_BREAK_MARK == "↓"


def test_line_break_mark_color_is_faint_blue() -> None:
    """薄い青（青が赤より強く、アルファで薄くしてある）であること。"""
    assert LINE_BREAK_MARK_COLOR.blue() > LINE_BREAK_MARK_COLOR.red()
    assert LINE_BREAK_MARK_COLOR.alpha() < 255


def test_no_mark_points_while_disabled(window: MainWindow) -> None:
    editor = window.current_editor()
    _prepare(editor, "あいう\nかきく\n")

    assert editor.line_break_mark_points() == []


def test_one_mark_point_per_newline(window: MainWindow) -> None:
    """改行の数だけ描く（＝最後のブロックには描かない）。"""
    editor = window.current_editor()
    _prepare(editor, "あいう\nかきく\nさしす")
    editor.set_show_line_breaks(True)

    assert len(editor.line_break_mark_points()) == 2


def test_no_mark_point_for_single_line_without_newline(window: MainWindow) -> None:
    editor = window.current_editor()
    _prepare(editor, "あいう")
    editor.set_show_line_breaks(True)

    assert editor.line_break_mark_points() == []


def test_trailing_newline_gets_a_mark(window: MainWindow) -> None:
    """末尾の改行にも記号が付く（そのあとに空のブロックがあるため）。"""
    editor = window.current_editor()
    _prepare(editor, "あいう\n")
    editor.set_show_line_breaks(True)

    assert len(editor.line_break_mark_points()) == 1


def test_mark_point_sits_at_the_end_of_its_line(window: MainWindow) -> None:
    editor = window.current_editor()
    _prepare(editor, "あいう\nかきく")
    editor.set_show_line_breaks(True)

    (point,) = editor.line_break_mark_points()
    rect = _end_of_block_rect(editor, 0)

    assert rect.left() <= point.x() <= rect.left() + 4
    # ベースラインなので、その行の上端より下・下端より少し上に来る。
    assert rect.top() < point.y() <= rect.bottom()


def test_soft_wrap_boundary_does_not_get_a_mark(window: MainWindow) -> None:
    """折り返しは「改行」ではないので記号を描かない。

    折り返された行では、最後の視覚行の行末に 1 つだけ描かれる。
    """
    editor = window.current_editor()
    _prepare(editor, "abcdefghijklmnopqrstuvwxyz\nnext")
    editor.set_wrap_mode(WRAP_FIXED, column=10)
    editor.set_show_line_breaks(True)

    points = editor.line_break_mark_points()

    assert len(points) == 1
    # 折り返しの 1 行目ではなく、最後の視覚行（＝実際の行末）に付く。
    first_line_rect = editor.cursorRect(editor.textCursor())
    assert points[0].y() > first_line_rect.bottom()


def test_only_visible_blocks_are_scanned(window: MainWindow) -> None:
    """大きなファイルでも、画面に見えている行ぶんしか位置を計算しない。"""
    editor = window.current_editor()
    _prepare(editor, "\n".join(f"行 {i}" for i in range(5000)))
    editor.set_show_line_breaks(True)

    points = editor.line_break_mark_points()

    assert 0 < len(points) < 100


def test_marks_follow_the_scroll_position(window: MainWindow) -> None:
    """スクロールしても、そのとき見えている行の行末に付くこと。"""
    editor = window.current_editor()
    _prepare(editor, "\n".join(f"行 {i}" for i in range(5000)))
    editor.set_show_line_breaks(True)
    scrollbar = editor.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() // 2)

    points = editor.line_break_mark_points()

    assert points
    # ビューポート座標になっている（スクロール量を足したままの文書座標だと
    # 数万ピクセルになる）。上下の端の行は半分だけ見えていることがあるので、
    # 1 行ぶんのはみ出しは許す。
    line_height = editor.fontMetrics().height()
    assert all(
        -line_height <= point.y() <= editor.viewport().height() + line_height for point in points
    )


def test_marks_are_actually_painted(window: MainWindow) -> None:
    """表示 ON で実際に描画結果が変わること（＝記号が描かれている）。"""
    editor = window.current_editor()
    _prepare(editor, "あいう\nかきく")

    assert _mark_pixels(editor)


def test_painted_marks_are_faint_blue(window: MainWindow) -> None:
    """描き足された色が、元の色より青く・背景より薄いこと。"""
    editor = window.current_editor()
    _prepare(editor, "あいう\nかきく")

    changed = _mark_pixels(editor)

    # 矢印の線そのものにあたるピクセルははっきり青い（縁のアンチエイリアス
    # には、サブピクセル描画のせいで青くならないものも混じる）。
    assert any(after.blue() - after.red() > 20 for _x, _y, _before, after in changed)
    # 「薄っすら」なので、真っ黒な本文のような濃さにはならない。
    assert all(after.lightness() > 100 for _x, _y, _before, after in changed)


def test_nothing_painted_for_text_without_newline(window: MainWindow) -> None:
    editor = window.current_editor()
    _prepare(editor, "あいう")

    assert _mark_pixels(editor) == []


def test_painted_mark_stays_on_the_line_that_has_the_newline(window: MainWindow) -> None:
    """2 行のうち、改行があるのは 1 行目だけ。記号も 1 行目に留まること。"""
    editor = window.current_editor()
    _prepare(editor, "あいう\nかきく")

    second_line_rect = _end_of_block_rect(editor, 1)
    assert all(y < second_line_rect.top() for _x, y, _before, _after in _mark_pixels(editor))


def test_does_not_use_qt_standard_separator_flag(window: MainWindow) -> None:
    """Qt 標準の記号（色も形も変えられない）は使わないこと。"""
    editor = window.current_editor()
    editor.set_show_line_breaks(True)

    flags = editor.document().defaultTextOption().flags()
    assert not (flags & QTextOption.Flag.ShowLineAndParagraphSeparators)


def test_set_show_line_breaks_does_not_disturb_other_text_option_flags(
    window: MainWindow,
) -> None:
    """他のテキストオプション（折り返し等）を巻き込んで消さないこと。"""
    editor = window.current_editor()
    option = editor.document().defaultTextOption()
    option.setFlags(option.flags() | QTextOption.Flag.ShowTabsAndSpaces)
    editor.document().setDefaultTextOption(option)

    editor.set_show_line_breaks(True)

    flags = editor.document().defaultTextOption().flags()
    assert bool(flags & QTextOption.Flag.ShowTabsAndSpaces)


def test_window_set_show_line_breaks_applies_to_all_existing_tabs(window: MainWindow) -> None:
    window.new_file()
    window.new_file()

    window.set_show_line_breaks(True)

    for editor in window.editors():
        assert editor.show_line_breaks() is True


def test_window_set_show_line_breaks_updates_menu_checked_state(window: MainWindow) -> None:
    assert window.action_show_line_breaks.isChecked() is False

    window.set_show_line_breaks(True)

    assert window.action_show_line_breaks.isChecked() is True


def test_set_show_line_breaks_persists_across_new_windows(
    window: MainWindow, make_window
) -> None:
    window.set_show_line_breaks(True)

    other = make_window()

    assert other.show_line_breaks() is True
    assert other.current_editor().show_line_breaks() is True
    assert other.action_show_line_breaks.isChecked() is True


def test_show_line_breaks_saved_value_survives_reload(window: MainWindow) -> None:
    window.set_show_line_breaks(True)

    assert load_show_line_breaks() is True
