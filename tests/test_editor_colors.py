"""エディタ本文の配色（背景色・文字色）のテスト。実機フィードバックにより追加。

テーマ（ダーク等）とは独立に、エディタ部分だけ固定色にできる機能。
アプリ全体で共有する設定として扱い、変更すると開いている全ウィンドウに
即座に反映される。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QColorDialog

from editor_app.main_window import MainWindow
from editor_app.settings import load_editor_colors, save_editor_colors


def test_default_editor_colors_are_unset(window: MainWindow) -> None:
    assert load_editor_colors() == (None, None)
    assert window.current_editor().editor_colors() == (None, None)


def test_editor_set_editor_colors_updates_palette(window: MainWindow) -> None:
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))

    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")
    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("black")
    assert editor.editor_colors() == (QColor("white"), QColor("black"))


def test_editor_reset_clears_custom_palette_attribute(window: MainWindow) -> None:
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))
    editor.set_editor_colors(None, None)

    assert editor.editor_colors() == (None, None)
    assert editor.testAttribute(Qt.WidgetAttribute.WA_SetPalette) is False


def test_set_editor_colors_applies_to_all_open_windows(
    window: MainWindow, make_window
) -> None:
    other = make_window()

    window.set_editor_colors(QColor("white"), QColor("black"))

    for win in (window, other):
        for editor in win.editors():
            assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")
            assert editor.palette().color(QPalette.ColorRole.Text) == QColor("black")


def test_set_editor_colors_persists_for_new_windows(
    window: MainWindow, make_window
) -> None:
    window.set_editor_colors(QColor("white"), QColor("black"))

    assert load_editor_colors() == (QColor("white"), QColor("black"))

    new_window = make_window()
    for editor in new_window.editors():
        assert editor.editor_colors() == (QColor("white"), QColor("black"))


def test_new_tab_inherits_current_editor_colors(window: MainWindow) -> None:
    window.set_editor_colors(QColor("white"), QColor("black"))

    editor = window.new_file()

    assert editor.editor_colors() == (QColor("white"), QColor("black"))


def test_prompt_editor_background_color_applies_selected_color(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("red"))
    )

    window._prompt_editor_background_color()

    assert window.current_editor().editor_colors()[0] == QColor("red")


def test_prompt_editor_text_color_applies_selected_color(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("blue"))
    )

    window._prompt_editor_text_color()

    assert window.current_editor().editor_colors()[1] == QColor("blue")


def test_color_dialog_opens_on_the_colors_now_in_use(
    window: MainWindow, monkeypatch
) -> None:
    """色を選び直すとき、ダイアログは**今の色**から始まること。

    毎回まっさらな既定色から始まると、少しだけ濃くしたいような選び直しが
    やり直しになる（実機は自分で決めた配色で使っている）。
    """
    initial: list[QColor] = []
    monkeypatch.setattr(
        QColorDialog,
        "getColor",
        staticmethod(lambda color, *a, **k: initial.append(color) or QColor()),
    )
    window.set_editor_colors(QColor("#102030"), QColor("#405060"))

    window._prompt_editor_background_color()
    window._prompt_editor_text_color()

    assert initial == [QColor("#102030"), QColor("#405060")]


def test_color_dialog_shows_the_custom_colors_even_without_tabs(
    window: MainWindow, monkeypatch
) -> None:
    """タブが 1 つも無くても、設定してある色は設定してある色として見せる。

    「今の色」をタブの実際の配色から拾うだけにすると、タブを閉じた
    瞬間に設定済みの色を見失い、選び直しが既定色から始まってしまう。
    """
    initial: list[QColor] = []
    monkeypatch.setattr(
        QColorDialog,
        "getColor",
        staticmethod(lambda color, *a, **k: initial.append(color) or QColor()),
    )
    window.set_editor_colors(QColor("#102030"), QColor("#405060"))
    window.tabs.clear()

    window._prompt_editor_background_color()
    window._prompt_editor_text_color()

    assert initial == [QColor("#102030"), QColor("#405060")]


def test_color_dialog_falls_back_to_the_application_default_without_tabs(
    window: MainWindow, monkeypatch
) -> None:
    """タブが 1 つも無いときでも、色の選び直しは開けること（落ちない）。

    ウィンドウは最後のタブを閉じても残るので、その状態でメニューから
    呼べてしまう。見せる色が無いので、アプリ既定のパレットの色にする。
    """
    initial: list[QColor] = []
    monkeypatch.setattr(
        QColorDialog,
        "getColor",
        staticmethod(lambda color, *a, **k: initial.append(color) or QColor()),
    )
    window.tabs.clear()
    assert window.current_editor() is None

    window._prompt_editor_background_color()

    assert initial == [QPalette().color(QPalette.ColorRole.Base)]


def test_reset_editor_colors_clears_customization(window: MainWindow) -> None:
    window.set_editor_colors(QColor("white"), QColor("black"))

    window._reset_editor_colors()

    assert load_editor_colors() == (None, None)
    assert window.current_editor().editor_colors() == (None, None)


# ----------------------------------------------------------------------
# viewport のパレット同期（実機フィードバックにより追加）
#
# QTextEdit は QAbstractScrollArea なので、本文の背景を実際に描画して
# いるのは self ではなく self.viewport()。「起動直後、既にカスタム配色が
# 設定されている状態でリセットすると反映されない／その後また配色を
# 変えても背景色だけ反映されない」という不具合が実機で見つかった。
# 原因は viewport() 側のパレットが self 側の変更に自動追従しなくなる
# ケースがあったこと（widget 側は正しく更新されるが、実際に見えている
# viewport 側が古い色のまま取り残される）。
# ----------------------------------------------------------------------
def test_viewport_palette_matches_widget_palette_after_set(window: MainWindow) -> None:
    editor = window.current_editor()
    editor.set_editor_colors(QColor("cyan"), QColor("black"))

    assert editor.viewport().palette().color(QPalette.ColorRole.Base) == QColor("cyan")


def test_viewport_palette_matches_widget_palette_after_reset(window: MainWindow) -> None:
    editor = window.current_editor()
    editor.set_editor_colors(QColor("cyan"), QColor("black"))

    editor.set_editor_colors(None, None)

    assert editor.viewport().testAttribute(Qt.WidgetAttribute.WA_SetPalette) is False
    assert editor.viewport().palette().color(
        QPalette.ColorRole.Base
    ) == editor.palette().color(QPalette.ColorRole.Base)


def test_viewport_background_updates_after_reset_then_reapply_at_startup(
    make_window,
) -> None:
    """実際に報告された手順をそのまま再現する回帰テスト。

    (1) 前回終了時にカスタム配色を設定していた状態で起動
    (2) 起動直後にリセット
    (3) 続けてまた別の色を設定
    のそれぞれで、self（widget）と viewport() のパレットが常に一致する
    こと（＝背景色がちゃんと画面に反映されること）を確認する。
    """
    save_editor_colors(QColor("white"), QColor("black"))

    window = make_window()  # (1) 起動直後の状態を再現
    editor = window.current_editor()
    assert editor.viewport().palette().color(
        QPalette.ColorRole.Base
    ) == editor.palette().color(QPalette.ColorRole.Base)

    window._reset_editor_colors()  # (2)
    assert editor.viewport().palette().color(
        QPalette.ColorRole.Base
    ) == editor.palette().color(QPalette.ColorRole.Base)

    window.set_editor_colors(QColor("magenta"), QColor("yellow"))  # (3)
    assert editor.viewport().palette().color(QPalette.ColorRole.Base) == QColor("magenta")
    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("magenta")


# ----------------------------------------------------------------------
# polish のやり直しで取り消された配色を貼り直す経路（changeEvent）
#
# QApplication.setStyleSheet() を呼ぶと Qt は全ウィジェットを polish し
# 直し、その過程でウィジェットへ個別に設定してあったパレットが取り消され
# る。EditorWidget.changeEvent() はそれを検知して自分の配色を貼り直す。
#
# この経路は「テーマを切り替えても配色が消えない」ために書かれたものだが、
# **既存のテーマ切り替えのテスト
# (test_theme.py::test_custom_editor_colors_survive_theme_switch) では
# 一度も通っていない**。offscreen では set_theme() を呼んでもエディタへ
# StyleChange / PaletteChange が届かず、配色が残るのは単に self のパレット
# を明示設定してあるからだった（この 2 件を消しても全テストが緑のまま
# 通る状態だった）。実機の style では届きうるので、ここで経路そのものを
# 直接叩いて固定する。
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "event_type",
    [QEvent.Type.StyleChange, QEvent.Type.PaletteChange],
    ids=["StyleChange", "PaletteChange"],
)
def test_custom_colors_are_reapplied_after_a_style_or_palette_change(
    window: MainWindow, event_type: QEvent.Type
) -> None:
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))

    # polish のやり直しで配色が別のものに置き換わった状況を作る。
    # ``setPalette()`` そのものも changeEvent を起こすので、ここで貼り直されて
    # しまっては「イベントで戻った」ことにならない。貼り直しの再入ガードを
    # 立てて、**取り消されたまま**の状態を作る。
    editor._reapplying_editor_colors = True
    try:
        clobbered = editor.palette()
        clobbered.setColor(QPalette.ColorRole.Base, QColor("red"))
        clobbered.setColor(QPalette.ColorRole.Text, QColor("green"))
        editor.setPalette(clobbered)
    finally:
        editor._reapplying_editor_colors = False
    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("red")

    QApplication.sendEvent(editor, QEvent(event_type))

    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")
    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("black")


@pytest.mark.parametrize(
    "event_type",
    [QEvent.Type.StyleChange, QEvent.Type.PaletteChange],
    ids=["StyleChange", "PaletteChange"],
)
def test_theme_following_is_not_re_customized_by_a_style_or_palette_change(
    window: MainWindow, event_type: QEvent.Type
) -> None:
    """テーマ任せ（カスタム配色なし）のときは、貼り直しで固定してしまわないこと。"""
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))
    editor.set_editor_colors(None, None)

    QApplication.sendEvent(editor, QEvent(event_type))

    assert editor.editor_colors() == (None, None)
    assert editor.testAttribute(Qt.WidgetAttribute.WA_SetPalette) is False
