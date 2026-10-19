"""本文の配色を「背景色だけ」「文字色だけ」指定したときの取り扱い。

エディタ本文の配色は**背景色と文字色を別々のメニュー項目から選ぶ**ので、
実際の使い方では「片方だけカスタム、もう片方はテーマ任せ」という状態に
なる（`表示 → エディタの背景色...` だけを使えばそうなる）。
`tests/test_editor_colors.py` のテストはどれも**両方を同時に**設定して
いたため、片方だけの経路には次のような穴が残っていた（66 回目に変異で
確かめた）:

* 片方だけ指定すると、その指定を捨ててテーマへ戻してしまっても気づけない
  （`set_editor_colors()` の「両方 None ならテーマ任せ」の判定）
* テーマ切り替え (`changeEvent`) のあと、**片方だけ**取り消された配色を
  貼り直さなくても気づけない（`_reapply_editor_colors()` の
  「もう合っているなら何もしない」の判定）
* 背景色を選び直したときに、指定してあった文字色が黙って外れても
  気づけない（`_prompt_editor_background_color()` の引数）

いずれも**画面には何も出ない**（配色の小窓は普通に開いて閉じる）ので、
気づくのは「選んだ色にならない」「別の色が消えた」という使用中の形になる。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QColorDialog

from editor_app.main_window import MainWindow


def _clobber_palette(editor, base: QColor, text: QColor) -> None:
    """polish のやり直しで配色が別のものへ置き換わった状況を作る。

    ``setPalette()`` そのものも ``changeEvent`` を起こすので、そのまま呼ぶと
    その場で貼り直されてしまう。貼り直しの再入ガードを立てて
    **取り消されたまま**の状態を作る（`test_editor_colors.py` と同じ手）。
    """
    editor._reapplying_editor_colors = True
    try:
        palette = editor.palette()
        palette.setColor(QPalette.ColorRole.Base, base)
        palette.setColor(QPalette.ColorRole.Text, text)
        editor.setPalette(palette)
    finally:
        editor._reapplying_editor_colors = False


# ----------------------------------------------------------------------
# 片方だけ指定したときに、その色が本文へ効くこと
# ----------------------------------------------------------------------
def test_setting_only_the_background_paints_the_body(window: MainWindow) -> None:
    """背景色だけを選んだら、本文の背景が実際にその色になること。

    `editor_colors()` が覚えているかだけでは足りない——覚えていても
    パレットへ貼っていなければ画面は変わらない。
    """
    editor = window.current_editor()

    editor.set_editor_colors(QColor("red"), None)

    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("red")
    assert editor.viewport().palette().color(QPalette.ColorRole.Base) == QColor("red")
    assert editor.editor_colors() == (QColor("red"), None)


def test_setting_only_the_text_colour_paints_the_body(window: MainWindow) -> None:
    """文字色だけを選んだら、本文の文字が実際にその色になること。"""
    editor = window.current_editor()

    editor.set_editor_colors(None, QColor("blue"))

    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("blue")
    assert editor.viewport().palette().color(QPalette.ColorRole.Text) == QColor("blue")
    assert editor.editor_colors() == (None, QColor("blue"))


def test_viewport_is_repainted_even_when_it_stopped_inheriting(
    window: MainWindow,
) -> None:
    """viewport が親からの伝播をやめていても、選んだ背景色が届くこと。

    実機で報告された不具合（「背景色だけ反映されない」）の状態を直接作る。
    `QTextEdit` の背景を描いているのは `self` ではなく `viewport()` で、
    一度 `viewport()` 側へ明示設定すると `self` の変更に追従しなくなる。
    """
    editor = window.current_editor()
    stale = editor.viewport().palette()
    stale.setColor(QPalette.ColorRole.Base, QColor("#123456"))
    editor.viewport().setPalette(stale)  # 伝播が止まった状態
    assert editor.viewport().palette().color(QPalette.ColorRole.Base) == QColor("#123456")

    editor.set_editor_colors(QColor("cyan"), QColor("black"))

    assert editor.viewport().palette().color(QPalette.ColorRole.Base) == QColor("cyan")


# ----------------------------------------------------------------------
# テーマ切り替えのあと、片方だけ取り消された配色を貼り直すこと
# ----------------------------------------------------------------------
def test_only_a_custom_background_is_reapplied_after_a_style_change(
    window: MainWindow,
) -> None:
    """背景色だけカスタムのとき、テーマ切り替えで消えた背景色が戻ること。

    貼り直すかどうかの判定は「背景色・文字色の**両方**が既に合っているなら
    何もしない」でなければならない。片方（ここでは文字色＝テーマ任せなので
    常に「合っている」）で決めてしまうと、**背景色が取り消されたまま
    残る**。取り消された文字色の側をわざと**カスタム背景色と同じ値**に
    しているのは、貼り直しの判定が見比べる色の役割（Base / Text）を
    取り違えていても赤くなるようにするため。
    """
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), None)

    _clobber_palette(editor, QColor("red"), QColor("white"))
    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("red")

    QApplication.sendEvent(editor, QEvent(QEvent.Type.StyleChange))

    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")


def test_only_a_custom_text_colour_is_reapplied_after_a_style_change(
    window: MainWindow,
) -> None:
    """文字色だけカスタムのとき、テーマ切り替えで消えた文字色が戻ること。"""
    editor = window.current_editor()
    editor.set_editor_colors(None, QColor("blue"))

    _clobber_palette(editor, QColor("blue"), QColor("green"))
    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("green")

    QApplication.sendEvent(editor, QEvent(QEvent.Type.StyleChange))

    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("blue")


def test_a_half_clobbered_pair_of_colours_is_reapplied_after_a_style_change(
    window: MainWindow,
) -> None:
    """両方カスタムでも、**片方だけ**取り消されたときに貼り直すこと。

    テーマの QSS が背景だけを塗り替える形もあるので、「片方が合っていれば
    何もしない」にしてはいけない。
    """
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))

    _clobber_palette(editor, QColor("red"), QColor("black"))

    QApplication.sendEvent(editor, QEvent(QEvent.Type.StyleChange))

    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")
    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("black")


def test_a_leaked_custom_palette_is_cancelled_on_a_style_change(
    window: MainWindow,
) -> None:
    """テーマ任せに戻したあと、polish で復活したカスタム配色を取り消すこと。

    `QApplication.setStyleSheet()` は全ウィジェットを polish し直す。その
    拍子に、以前ウィジェットへ個別に設定してあったパレットが復元されて
    しまうことがある（`WA_SetPalette` が立ったまま）。そのままだと
    **テーマを切り替えたのに本文だけ古い配色のまま**になり、以後の
    テーマ切り替えにも一切追従しなくなる。
    """
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))
    editor.set_editor_colors(None, None)
    theme_base = editor.palette().color(QPalette.ColorRole.Base)
    assert editor.testAttribute(Qt.WidgetAttribute.WA_SetPalette) is False

    _clobber_palette(editor, QColor("#ff00ff"), QColor("#00ff00"))
    assert editor.testAttribute(Qt.WidgetAttribute.WA_SetPalette) is True

    QApplication.sendEvent(editor, QEvent(QEvent.Type.StyleChange))

    assert editor.testAttribute(Qt.WidgetAttribute.WA_SetPalette) is False
    assert editor.viewport().testAttribute(Qt.WidgetAttribute.WA_SetPalette) is False
    assert editor.palette().color(QPalette.ColorRole.Base) == theme_base
    assert editor.editor_colors() == (None, None)


# ----------------------------------------------------------------------
# 片方を選び直したときに、もう片方の指定を落とさないこと
# ----------------------------------------------------------------------
def test_choosing_a_background_keeps_a_custom_text_colour(
    window: MainWindow, monkeypatch
) -> None:
    """文字色を指定したあとに背景色を選び直しても、文字色が残ること。"""
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("blue"))
    )
    window._prompt_editor_text_color()
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("red"))
    )

    window._prompt_editor_background_color()

    editor = window.current_editor()
    assert editor.editor_colors() == (QColor("red"), QColor("blue"))
    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("blue")


def test_choosing_a_text_colour_keeps_a_custom_background(
    window: MainWindow, monkeypatch
) -> None:
    """背景色を指定したあとに文字色を選び直しても、背景色が残ること。"""
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("red"))
    )
    window._prompt_editor_background_color()
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("blue"))
    )

    window._prompt_editor_text_color()

    editor = window.current_editor()
    assert editor.editor_colors() == (QColor("red"), QColor("blue"))
    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("red")
