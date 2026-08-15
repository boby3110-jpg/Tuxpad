"""ウィンドウの表示設定が「そのウィンドウの全タブ」に必ず効くことのテスト。

折り返しモード・本文フォント・エディタの配色は、あとから実機フィードバックで
1 つずつ足されてきた。足すたびに

- 新しく作ったタブ（``_add_editor``）
- 他のウィンドウから D&D で移ってきたタブ（``_adopt_editor``）

の **両方** に適用する必要があるが、この 2 か所は別々のメソッドなので
片方だけ書き忘れると「ドラッグで移したタブだけ折り返しが元のウィンドウの
まま」といった食い違いが起きる（テストは緑のまま）。現在は
``_apply_window_settings()`` の 1 か所に集約してあるので、そこを通ることを
ここで固定しておく。**ウィンドウ単位の表示設定を増やしたら、この
テストにも 1 項目足すこと。**
"""

from __future__ import annotations

from PySide6.QtGui import QColor

from editor_app.editor import WRAP_FIXED
from editor_app.main_window import MainWindow


def _configure(window: MainWindow) -> None:
    """このウィンドウの表示設定を、既定とは違う値に一通り変えておく。"""
    window.set_wrap_mode(WRAP_FIXED, column=42)
    window.set_font_settings("Courier", 19)
    window.set_editor_colors(QColor("white"), QColor("black"))
    window.set_show_line_breaks(True)


def _assert_configured(editor) -> None:
    assert editor.wrap_mode() == WRAP_FIXED
    assert editor.wrap_column() == 42
    assert editor.font_family() == "Courier"
    assert editor.font_size() == 19
    assert editor.editor_colors() == (QColor("white"), QColor("black"))
    assert editor.show_line_breaks() is True


def test_new_tab_inherits_all_window_settings(window: MainWindow) -> None:
    """あとから開いた新規タブに、ウィンドウの表示設定が全て効く。"""
    _configure(window)

    editor = window.new_file()

    _assert_configured(editor)


def test_opened_file_tab_inherits_all_window_settings(
    window: MainWindow, tmp_path
) -> None:
    """ファイルを開いて増えたタブにも、ウィンドウの表示設定が全て効く。"""
    _configure(window)
    path = tmp_path / "sample.txt"
    path.write_text("あいうえお\n", encoding="utf-8")
    # 起動直後の空タブを使い回されないよう、先に何か書いておく。
    window.current_editor().setPlainText("x")

    editor = window.open_path(path)

    assert editor is not None
    _assert_configured(editor)


def test_adopted_tab_inherits_all_window_settings(
    window: MainWindow, make_window
) -> None:
    """D&D で移ってきたタブには、移動元ではなく移動先の設定が効く。"""
    other = make_window()
    _configure(other)
    editor = window.current_editor()

    window.tabs.removeTab(window.tabs.indexOf(editor))
    other._adopt_editor(editor)

    _assert_configured(editor)


def test_torn_off_tab_keeps_working(window: MainWindow, tmp_path) -> None:
    """タブを新しいウィンドウへ切り離しても、そのタブが壊れないこと。

    切り離し先のウィンドウは新規に作られる（＝設定は保存済みの値から
    始まる）ため、ここで見るのは「移した先でも編集・表示が続けられる」
    ことだけ。移動元の設定を引き継ぐことは期待しない。
    """
    _configure(window)
    editor = window.current_editor()
    editor.setPlainText("かきくけこ")

    from PySide6.QtCore import QPoint

    new_window = window._tear_off_tab_to_new_window(editor, QPoint(300, 300))

    assert new_window is not None
    assert new_window.editors() == [editor]
    assert editor.toPlainText() == "かきくけこ"
