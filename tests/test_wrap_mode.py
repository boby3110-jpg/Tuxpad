"""折り返しモード（なし/ウィンドウ幅/指定文字数）のテスト。実機フィードバックで追加。"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QInputDialog

from editor_app.editor import (
    DEFAULT_WRAP_COLUMN,
    DEFAULT_WRAP_MODE,
    WRAP_FIXED,
    WRAP_NONE,
    WRAP_WINDOW,
    EditorWidget,
)
from editor_app.main_window import MainWindow
from editor_app.settings import load_wrap_settings

#: 折り返しの確認に使う本文。**半角だけの行・全角だけの行・混ざった行**を
#: 入れてあるのは、「指定文字数」が**文字数**で折り返す（ピクセル幅では
#: ない）ことを、全角と半角の幅の違いで浮かび上がらせるため。
WRAP_SAMPLE = (
    "abcdefghijklmnopqrstuvwxyz0123456789\n"
    "あいうえおかきくけこさしすせそ\n"
    "あaいbうcえdおeかfきgくhけiこj\n"
    "hello world this is a long english sentence to see wrapping\n"
)


def visual_lines(editor: EditorWidget) -> list[list[str]]:
    """**画面上で実際にどこで折り返されたか**を、論理行ごとに取り出す。

    折り返しモードの確認は「ウィジェットにどの設定が入っているか」では
    なく、ここで取れる**見た目**で行う。引き継ぎ ⑯（本文欄を
    ``QTextEdit`` から ``QPlainTextEdit`` へ）で中の仕組みがそっくり
    入れ替わっても、**利用者から見た折り返し位置が同じなら合格**と
    言えるようにするため（中の設定を見るテストは、作りを変えた瞬間に
    「壊れていないのに赤くなる」か、もっと悪いことに「壊れているのに
    緑のまま」になる）。
    """
    document = editor.document()
    lines: list[list[str]] = []
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


def shown(editor: EditorWidget, text: str, *, width: int = 2000) -> None:
    """折り返しを実際に組ませる（組むのは描画のときなので表示まで行う）。"""
    editor.setFont(QFont("monospace", 11))
    editor.resize(width, 400)
    editor.set_content(text)
    editor.show()
    editor.viewport().repaint()


def test_editor_default_wrap_mode_is_none(window: MainWindow) -> None:
    editor = window.current_editor()
    assert editor.wrap_mode() == WRAP_NONE

    shown(editor, WRAP_SAMPLE, width=120)  # どの行も入り切らない狭さ

    # 折り返さない＝どの論理行もちょうど 1 本の視覚行。
    assert visual_lines(editor) == [
        ["abcdefghijklmnopqrstuvwxyz0123456789"],
        ["あいうえおかきくけこさしすせそ"],
        ["あaいbうcえdおeかfきgくhけiこj"],
        ["hello world this is a long english sentence to see wrapping"],
        [""],
    ]


def test_editor_set_wrap_window(window: MainWindow) -> None:
    """ウィンドウ幅での折り返しは**幅（ピクセル）**で決まる。

    半角だけの行は 1 行に多く載り、全角だけの行は少ししか載らない——
    「文字数」ではなく「幅」で切れていることが、この差で分かる。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_WINDOW)

    shown(editor, WRAP_SAMPLE, width=120)

    lines = visual_lines(editor)
    assert all(len(line) > 1 for line in lines[:4]), lines
    # 半角と全角で幅が違うことが、この確かめ方の前提。日本語フォントの
    # 無い環境（公開リポの CI に以前そうだった）では全角が半角と同じ幅で
    # 描かれ、前提が崩れる（2026-11-01 に実際にここで落ちた）。そのときは
    # 理由を出して飛ばす——Tuxpad の不具合ではなく、環境の違いなので。
    metrics = editor.fontMetrics()
    if metrics.horizontalAdvance("あ") <= metrics.horizontalAdvance("a"):
        pytest.skip("日本語フォントが無く、全角と半角の幅に差が出ない環境")
    # 半角 (9px) の行は、全角 (15px) の行より 1 行に多く入る。
    assert len(lines[0][0]) > len(lines[1][0]), lines
    # 折り返しても文字は 1 つも落ちない。
    assert ["".join(line) for line in lines[:4]] == WRAP_SAMPLE.split("\n")[:4]


def test_editor_set_wrap_fixed_column(window: MainWindow) -> None:
    """「指定文字数」は**文字数ちょうど**で折り返す（幅ではない）。

    全角も半角も 1 文字は 1 文字として数える。これは
    ``QTextEdit.LineWrapMode.FixedColumnWidth`` が実際にやっていたこと
    （Qt 内部の ``QTextLine.setNumColumns()``）で、引き継ぎ ⑯ で本文欄を
    ``QPlainTextEdit`` へ移したあとも**同じ位置で折り返すこと**を守る。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_FIXED, column=10)
    assert editor.wrap_column() == 10

    shown(editor, WRAP_SAMPLE)

    assert visual_lines(editor) == [
        ["abcdefghij", "klmnopqrst", "uvwxyz0123", "456789"],
        ["あいうえおかきくけこ", "さしすせそ"],
        ["あaいbうcえdおe", "かfきgくhけiこj"],
        # 英文も**単語の途中で**切れる。文字数で切る以上そうなるのが
        # ``QTextEdit`` の「指定文字数」だった（実測で突き合わせ済み）。
        # ここを単語の切れ目で折り返す作りにすると、同じ設定でも
        # 見た目が変わってしまう。
        ["hello worl", "d this is ", "a long eng", "lish sente", "nce to see ", "wrapping"],
        [""],
    ]


def test_fixed_column_keeps_n_columns_when_the_window_is_narrower(
    window: MainWindow,
) -> None:
    """N 文字がウィンドウに入り切らなくても、**N 文字で**折り返す。

    ``QTextEdit`` の「指定文字数」は使える幅で頭打ちにしていた（狭い窓では
    ウィンドウ幅で折り返す）が、2026-10-26 に利用者の判断で改めた:
    「指定文字数」と言う以上、窓の幅で折り返し位置が変わるのはおかしい。
    はみ出す分は横スクロールで見る（EmEditor と同じ形）。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_FIXED, column=20)

    # 全角 20 文字 = 300px 前後。ビューポートはそれよりずっと狭い。
    shown(editor, "あいうえおかきくけこさしすせそたちつてと" * 2 + "\n", width=200)

    lines = visual_lines(editor)[0]
    assert [len(line) for line in lines] == [20, 20], lines


def test_fixed_column_scrolls_sideways_only_when_the_window_is_narrower(
    qtbot,
) -> None:
    """N 文字ぶんより窓が狭いときだけ横スクロールバーが出る。広げれば消える。

    **広いウィンドウで開いてから狭め、また広げる**手順で見る。82 回目に
    「組んだ行の幅の最大値」を文書の幅にしたところ、一度広がった幅が
    縮まずに要らない横スクロールバーが残った。文書の幅はフォントと
    文字数だけで決めている（中身も窓の幅も見ない）ので、残らないこと。

    ウィンドウの中のタブではなく**単独の本文欄**で見る（タブを直接
    小さくしても、ウィンドウのレイアウトが元の大きさへ戻してしまう）。
    """
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_wrap_mode(WRAP_FIXED, column=20)
    shown(editor, "あいうえおかきくけこさしすせそたちつてと\n" * 20, width=1200)
    assert editor.horizontalScrollBar().maximum() == 0

    editor.resize(200, 300)
    QApplication.processEvents()
    editor.viewport().repaint()
    assert editor.horizontalScrollBar().maximum() > 0, "狭いのに横へ送れない"

    editor.resize(1200, 300)
    QApplication.processEvents()
    editor.viewport().repaint()
    assert editor.horizontalScrollBar().maximum() == 0, "広げたのに横スクロールが残った"


def test_fixed_column_document_width_is_n_full_width_characters(
    window: MainWindow,
) -> None:
    """文書の幅は「全角 1 文字の幅 × N」＋両脇の余白（中身には左右されない）。"""
    from PySide6.QtGui import QFontMetricsF

    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_FIXED, column=30)
    shown(editor, "abc\n", width=1200)

    layout = editor.document().documentLayout()
    metrics = QFontMetricsF(editor.document().defaultFont())
    margin = editor.document().documentMargin()
    assert layout.documentSize().width() == pytest.approx(
        30 * metrics.horizontalAdvance("あ") + 2 * margin
    )


def test_other_wrap_modes_keep_qt_document_width(window: MainWindow) -> None:
    """指定文字数以外（折り返さない・ウィンドウ幅）は、文書の幅に手を出さない。"""
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_WINDOW)
    shown(editor, "あいうえお\n", width=1200)
    assert editor.horizontalScrollBar().maximum() == 0


def test_fixed_column_follows_a_font_change(window: MainWindow) -> None:
    """フォントを変えても、文字数での折り返し位置は変わらない。

    「指定文字数」は幅ではなく文字数なので、**大きくしても小さくしても
    1 行の文字数は同じ**（入り切らなくなっても頭打ちにせず、横スクロール）。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_FIXED, column=10)
    shown(editor, WRAP_SAMPLE)
    before = visual_lines(editor)

    editor.set_font_settings("monospace", 20)
    editor.viewport().repaint()

    # 本文のある 4 行だけを見る。字を大きくすると画面に入る行が減り、
    # **画面の外のブロックはまだ組まれない**（組むのは見えたときだけ、
    # というのが引き継ぎ ⑯ で速くなった理由そのもの）。末尾の空行は
    # そこへ落ちるので、比較に混ぜると「遅延して組む」仕組みを
    # 壊したときだけ緑になる、逆さまのテストになってしまう。
    assert visual_lines(editor)[:4] == before[:4]


def test_fixed_column_change_re_wraps_already_shown_text(window: MainWindow) -> None:
    """文字数を変えたら、**もう組んである本文も**組み直される。

    本文の書き換えやウィンドウの幅の変更と違って、文字数の変更は Qt
    側が「組み直し」を促してくれない。自分で捨てないと、設定を変えた
    のに画面が変わらない（次に本文を触るまで古い折り返しのまま）。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_FIXED, column=10)
    shown(editor, WRAP_SAMPLE)
    assert visual_lines(editor)[0][0] == "abcdefghij"

    editor.set_wrap_mode(WRAP_FIXED, column=5)
    editor.viewport().repaint()

    assert visual_lines(editor)[0][0] == "abcde"


def test_fixed_column_vertical_scroll_range_counts_the_wrapped_lines(
    window: MainWindow,
) -> None:
    """折り返して増えたぶん、縦にスクロールできる範囲も増えること。

    折り返した結果の行数を文書へ教え直すのを忘れると、**画面より下に
    本文があるのにスクロールできない**（見えない行が出る）。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_NONE)
    shown(editor, "あいうえおかきくけこ\n" * 10, width=600)
    editor.resize(600, 120)
    editor.viewport().repaint()
    without_wrapping = editor.verticalScrollBar().maximum()

    editor.set_wrap_mode(WRAP_FIXED, column=2)  # 1 行が 5 行に増える
    editor.viewport().repaint()

    assert editor.verticalScrollBar().maximum() > without_wrapping


def test_window_set_wrap_mode_applies_to_all_existing_tabs(window: MainWindow) -> None:
    window.new_file()
    window.new_file()

    window.set_wrap_mode(WRAP_WINDOW)

    for editor in window.editors():
        assert editor.wrap_mode() == WRAP_WINDOW


def test_new_tab_inherits_window_wrap_mode(window: MainWindow) -> None:
    window.set_wrap_mode(WRAP_FIXED, column=60)

    window.new_file()

    assert window.current_editor().wrap_mode() == WRAP_FIXED
    assert window.current_editor().wrap_column() == 60


def test_set_wrap_mode_checks_matching_action(window: MainWindow) -> None:
    window.set_wrap_mode(WRAP_WINDOW)
    assert window.action_wrap_window.isChecked()
    assert not window.action_wrap_none.isChecked()

    window.set_wrap_mode(WRAP_NONE)
    assert window.action_wrap_none.isChecked()
    assert not window.action_wrap_window.isChecked()


def test_prompt_wrap_fixed_column_applies_entered_value(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (55, True)))

    window._prompt_wrap_fixed_column()

    assert window.current_editor().wrap_mode() == WRAP_FIXED
    assert window.current_editor().wrap_column() == 55
    assert window.action_wrap_fixed.isChecked()


def test_prompt_wrap_fixed_column_cancelled_keeps_previous_mode(
    window: MainWindow, monkeypatch
) -> None:
    window.set_wrap_mode(WRAP_WINDOW)
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (55, False)))

    window._prompt_wrap_fixed_column()

    assert window.current_editor().wrap_mode() == WRAP_WINDOW
    assert window.action_wrap_window.isChecked()
    assert not window.action_wrap_fixed.isChecked()


# ----------------------------------------------------------------------
# 折り返しモードの永続化（前回起動時の状態を復元、実機フィードバックで追加）
# ----------------------------------------------------------------------
def test_load_wrap_settings_defaults_when_nothing_saved() -> None:
    assert load_wrap_settings() == (DEFAULT_WRAP_MODE, DEFAULT_WRAP_COLUMN)


def test_set_wrap_mode_persists_across_new_windows(window: MainWindow, make_window) -> None:
    """あるウィンドウで変更した設定が、後から作る別ウィンドウにも引き継がれる
    （= アプリを再起動したのと同じ経路で読み込まれることの確認）。"""
    window.set_wrap_mode(WRAP_FIXED, column=37)

    other = make_window()

    assert other.current_editor().wrap_mode() == WRAP_FIXED
    assert other.current_editor().wrap_column() == 37


def test_set_wrap_mode_saved_value_survives_reload(window: MainWindow) -> None:
    window.set_wrap_mode(WRAP_WINDOW)

    assert load_wrap_settings() == (WRAP_WINDOW, DEFAULT_WRAP_COLUMN)


def test_switching_wrap_mode_without_a_column_keeps_the_chosen_column(
    window: MainWindow,
) -> None:
    """文字数を指定せずにモードだけ変えても、決めてある文字数は消さない。

    「40 文字で折り返す」に設定 → 「折り返さない」へ切り替え → もう一度
    「指定文字数で折り返す」に戻す、という普通の操作で 40 が残ること。
    `set_wrap_mode()` の `if column is not None:` がこれを守っているが、
    34 回目まで、この行を外して**文字数を None で上書きしても全テストが
    緑のまま**だった（変異 `vs-wrap-column-guard`）。
    """
    window.set_wrap_mode(WRAP_FIXED, column=40)

    window.set_wrap_mode(WRAP_NONE)  # 文字数は指定しない

    assert window._wrap_column == 40
    assert load_wrap_settings() == (WRAP_NONE, 40)

    window.set_wrap_mode(WRAP_FIXED)  # 戻したときも 40 のまま
    assert load_wrap_settings() == (WRAP_FIXED, 40)


def test_prompt_wrap_fixed_column_also_persists(window: MainWindow, monkeypatch) -> None:
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (12, True)))

    window._prompt_wrap_fixed_column()

    assert load_wrap_settings() == (WRAP_FIXED, 12)


def test_unknown_wrap_mode_is_refused_instead_of_ignored(window: MainWindow) -> None:
    """知らない折り返しモードは、黙って素通りさせずその場で失敗させる。

    素通りさせると「設定したのに何も変わらない」だけの症状になり、
    どこで取り違えたのかを追えなくなる（設定ファイルから読んだ値の
    受け止めは ``settings.load_wrap_settings()`` 側で既定へ倒している
    ので、ここまで来る時点で呼び出し側の取り違え）。
    """
    editor = window.current_editor()
    editor.set_wrap_mode(WRAP_WINDOW)

    with pytest.raises(ValueError):
        editor.set_wrap_mode("うずまき")

    # 画面の折り返しは、失敗する前のまま動かない。
    assert editor.wrap_mode() == WRAP_WINDOW
    shown(editor, WRAP_SAMPLE, width=120)
    assert all(len(line) > 1 for line in visual_lines(editor)[:4])


def test_no_wrap_still_scrolls_sideways_for_long_lines(qtbot) -> None:
    """「折り返さない」では、長い行は今までどおり横スクロールで見られる。

    文書の幅を書き換えるのは「指定文字数」のときだけ。他の折り返し方にまで
    手を出すと、Qt が控えている本当の行幅が消えて横に送れなくなる。
    """
    editor = EditorWidget()
    qtbot.addWidget(editor)
    editor.set_wrap_mode(WRAP_NONE)
    editor.resize(200, 300)
    editor.set_content("あいうえおかきくけこ" * 10 + "\n")
    editor.show()
    QApplication.processEvents()
    editor.viewport().repaint()

    assert editor.horizontalScrollBar().maximum() > 0
