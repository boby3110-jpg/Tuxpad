"""マウスホイール 1 ノッチで本文が 1 行ずつ動くこと（2026-10-26 実機フィードバック）.

引き継ぎ ⑯ で本文欄を ``QPlainTextEdit`` に戻したら、ホイール 1 ノッチで
**3 行**動くようになり（Qt の既定はデスクトップの設定に従い、KDE は 3 行）、
利用者から「使い勝手が悪い、1 行ずつにしてほしい」と要望があった。

- 1 ノッチ（``angleDelta`` 120）で 1 行。上下どちら向きも。
- タッチパッドのように 1 ノッチより細かく届く回転は、貯めて 1 ノッチに
  なったところで 1 行進む（細かい回転が全部捨てられて動かない、とならない）。
- 修飾キー付き（Ctrl＝文字の拡大縮小など）と横向きの回転は、今までどおり
  Qt に任せる。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from editor_app import editor as editor_module
from editor_app.editor import EditorWidget


@pytest.fixture
def editor(qtbot) -> EditorWidget:
    widget = EditorWidget()
    qtbot.addWidget(widget)
    widget.setPlainText("\n".join(f"{n} 行目" for n in range(1, 401)))
    widget.resize(400, 300)
    widget.show()
    QApplication.processEvents()
    widget.verticalScrollBar().setValue(100)
    return widget


def _wheel(
    widget: EditorWidget,
    dy: int,
    *,
    dx: int = 0,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QWheelEvent:
    pos = QPointF(10, 10)
    event = QWheelEvent(
        pos,
        QPointF(widget.viewport().mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget.viewport(), event)
    return event


def _top(widget: EditorWidget) -> int:
    return widget.verticalScrollBar().value()


def test_one_notch_down_scrolls_one_line(editor: EditorWidget) -> None:
    _wheel(editor, -120)  # 手前に回す＝下へ
    assert _top(editor) == 101


def test_one_notch_up_scrolls_one_line(editor: EditorWidget) -> None:
    _wheel(editor, 120)
    assert _top(editor) == 99


def test_notch_after_notch_moves_one_line_each(editor: EditorWidget) -> None:
    """1 ノッチずつ回せば 1 行ずつ。前の回転が次へ上乗せされない。"""
    for expected in (101, 102, 103):
        _wheel(editor, -120)
        assert _top(editor) == expected


def test_a_fast_spin_of_several_notches_moves_that_many_lines(editor: EditorWidget) -> None:
    """1 回のイベントで 3 ノッチぶん届いたら 3 行（1 行に丸めない）。"""
    _wheel(editor, -360)
    assert _top(editor) == 103


def test_fine_grained_deltas_add_up_to_a_line(editor: EditorWidget) -> None:
    """タッチパッドの細かい回転は、貯まって 1 ノッチになったら 1 行進む。"""
    _wheel(editor, -60)
    assert _top(editor) == 100, "半ノッチではまだ動かない"
    _wheel(editor, -60)
    assert _top(editor) == 101


def test_leftover_does_not_leak_into_the_other_direction(editor: EditorWidget) -> None:
    """下へ半ノッチ→上へ 1 ノッチは、差し引き上へ半ノッチ＝まだ動かない。"""
    _wheel(editor, -60)
    _wheel(editor, 120)
    assert _top(editor) == 100


def test_the_wheel_event_is_consumed(editor: EditorWidget) -> None:
    """受け取った回転は親へ流さない（親のスクロール領域まで一緒に動かない）。"""
    event = _wheel(editor, -120)
    assert event.isAccepted()


def test_scroll_amount_is_one_line() -> None:
    assert editor_module.WHEEL_SCROLL_LINES == 1
    assert editor_module.WHEEL_NOTCH_DELTA == 120


def test_ctrl_wheel_is_left_to_qt(editor: EditorWidget, monkeypatch) -> None:
    """Ctrl+ホイールは自分で縦に送らず、Qt（文字の拡大縮小）に任せる。"""
    seen: list[int] = []
    monkeypatch.setattr(
        editor_module.QPlainTextEdit,
        "wheelEvent",
        lambda self, event: seen.append(event.angleDelta().y()),
    )
    _wheel(editor, -120, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert seen == [-120]
    assert _top(editor) == 100


def test_horizontal_wheel_is_left_to_qt(editor: EditorWidget, monkeypatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(
        editor_module.QPlainTextEdit,
        "wheelEvent",
        lambda self, event: seen.append(event.angleDelta().x()),
    )
    _wheel(editor, 0, dx=-120)
    assert seen == [-120]
    assert _top(editor) == 100


# ----------------------------------------------------------------------
# 量を設定で変えられる（2026-10-26、「表示 → ホイールのスクロール量...」）
# ----------------------------------------------------------------------
def test_editor_uses_the_configured_amount(editor: EditorWidget) -> None:
    editor.set_wheel_scroll_lines(3)
    _wheel(editor, -120)
    assert _top(editor) == 103
    assert editor.wheel_scroll_lines() == 3


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (-5, 1), (editor_module.MAX_WHEEL_SCROLL_LINES + 1, editor_module.MAX_WHEEL_SCROLL_LINES)],
)
def test_editor_clamps_the_amount(editor: EditorWidget, requested: int, expected: int) -> None:
    """0 行（動かない）や極端な値にはならない。"""
    editor.set_wheel_scroll_lines(requested)
    assert editor.wheel_scroll_lines() == expected


def test_max_amount_is_sane() -> None:
    assert 5 <= editor_module.MAX_WHEEL_SCROLL_LINES <= 50


def test_setting_applies_to_every_window_and_is_saved(make_window, monkeypatch) -> None:
    """全ウィンドウの全タブへ即座に効き、保存もされる。"""
    from editor_app import view_settings

    saved: list[int] = []
    monkeypatch.setattr(view_settings, "save_wheel_scroll_lines", saved.append)
    first = make_window()
    second = make_window()
    second.new_file()

    first.set_wheel_scroll_lines(4)

    for window in (first, second):
        assert window.wheel_scroll_lines() == 4
        assert all(e.wheel_scroll_lines() == 4 for e in window.editors())
    assert saved == [4]


def test_new_tabs_get_the_current_amount(make_window, monkeypatch) -> None:
    from editor_app import view_settings

    monkeypatch.setattr(view_settings, "save_wheel_scroll_lines", lambda lines: None)
    window = make_window()
    window.set_wheel_scroll_lines(5)

    window.new_file()

    assert window.current_editor().wheel_scroll_lines() == 5


def test_prompt_sets_the_amount(make_window, monkeypatch) -> None:
    from editor_app import view_settings

    asked: list[tuple] = []

    def fake_prompt(parent, title, label, value, minimum, maximum):
        asked.append((value, minimum, maximum))
        return 2

    monkeypatch.setattr(view_settings, "prompt_int", fake_prompt)
    monkeypatch.setattr(view_settings, "save_wheel_scroll_lines", lambda lines: None)
    window = make_window()
    before = window.wheel_scroll_lines()

    window.action_wheel_scroll_lines.trigger()

    # 今の値を初期値に、1〜上限の範囲で尋ねる。
    assert asked == [(before, 1, editor_module.MAX_WHEEL_SCROLL_LINES)]
    assert window.wheel_scroll_lines() == 2


def test_cancelling_the_prompt_changes_nothing(make_window, monkeypatch) -> None:
    from editor_app import view_settings

    saved: list[int] = []
    monkeypatch.setattr(view_settings, "prompt_int", lambda *args: None)
    monkeypatch.setattr(view_settings, "save_wheel_scroll_lines", saved.append)
    window = make_window()
    before = window.wheel_scroll_lines()

    window.action_wheel_scroll_lines.trigger()

    assert window.wheel_scroll_lines() == before
    assert saved == []


def test_amount_is_restored_in_a_new_window(make_window) -> None:
    """保存した量は、次に開くウィンドウ（＝次回起動）にも引き継がれる。"""
    first = make_window()
    first.set_wheel_scroll_lines(6)

    second = make_window()

    assert second.wheel_scroll_lines() == 6
    assert second.current_editor().wheel_scroll_lines() == 6
