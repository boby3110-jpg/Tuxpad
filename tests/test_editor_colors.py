"""エディタ本文の配色（背景色・文字色）のテスト。実機フィードバックにより追加。

テーマ（ダーク等）とは独立に、エディタ部分だけ固定色にできる機能。
アプリ全体で共有する設定として扱い、変更すると開いている全ウィンドウに
即座に反映される。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QColorDialog

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
