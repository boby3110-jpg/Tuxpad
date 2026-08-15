"""`editor_app.dialogs` の小さなダイアログヘルパーのテスト.

ダイアログそのものは表示せず、Qt 側の静的メソッドを差し替えて
「戻り値をどう均しているか」だけを確認する。ここで固定したいのは 2 点:

- キャンセルされたときの表現を ``None`` に統一していること
  （``QColorDialog`` は無効な ``QColor``、``QInputDialog`` /
  ``QFontDialog`` は ``ok`` フラグと、Qt 側の表現がばらばらなため）
- ``QFontDialog`` がピクセル指定のフォントを返したときに
  ``pointSize()`` が -1 になる癖を、呼び出し側に漏らさないこと
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QColorDialog, QFontDialog, QInputDialog, QMessageBox

from editor_app import dialogs


def test_prompt_int_returns_value_when_accepted(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QInputDialog, "getInt", staticmethod(lambda *a, **k: (42, True))
    )
    assert dialogs.prompt_int(window, "題", "ラベル", 10, 1, 100) == 42


def test_prompt_int_returns_none_when_cancelled(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QInputDialog, "getInt", staticmethod(lambda *a, **k: (42, False))
    )
    assert dialogs.prompt_int(window, "題", "ラベル", 10, 1, 100) is None


def test_prompt_int_passes_initial_value_and_range(window, monkeypatch) -> None:
    """初期値・最小・最大がそのまま Qt 側に渡ること。"""
    captured: dict = {}

    def fake_get_int(parent, title, label, value, minimum, maximum, step):
        captured.update(
            value=value, minimum=minimum, maximum=maximum, title=title, label=label
        )
        return value, True

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(fake_get_int))

    dialogs.prompt_int(window, "タブの幅", "タブの幅 (px):", 120, 40, 800)

    assert captured == {
        "value": 120,
        "minimum": 40,
        "maximum": 800,
        "title": "タブの幅",
        "label": "タブの幅 (px):",
    }


def test_prompt_color_returns_chosen_color(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("red"))
    )
    color = dialogs.prompt_color(window, "色", QColor("blue"))
    assert color is not None and color.name() == QColor("red").name()


def test_prompt_color_returns_none_for_invalid_color(window, monkeypatch) -> None:
    """キャンセル時、QColorDialog は「無効な QColor」を返す。None に均す。"""
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor()))
    assert dialogs.prompt_color(window, "色", QColor("blue")) is None


def test_prompt_font_returns_family_and_size(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (True, QFont("Courier", 16)))
    )
    assert dialogs.prompt_font(window, "Monospace", 11) == ("Courier", 16)


def test_prompt_font_returns_none_when_cancelled(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (False, QFont("Serif", 30)))
    )
    assert dialogs.prompt_font(window, "Monospace", 11) is None


def test_prompt_font_keeps_current_size_for_pixel_font(window, monkeypatch) -> None:
    """ピクセル指定のフォントは pointSize() が -1。今までのサイズを維持する。"""
    pixel_font = QFont("Courier")
    pixel_font.setPixelSize(20)
    assert pixel_font.pointSize() < 1  # 前提の確認

    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (True, pixel_font))
    )
    assert dialogs.prompt_font(window, "Monospace", 13) == ("Courier", 13)


def test_prompt_font_sets_monospace_hint_only_when_asked(window, monkeypatch) -> None:
    captured: list[QFont] = []

    def fake_get_font(initial, parent, title):
        captured.append(QFont(initial))
        return False, initial

    monkeypatch.setattr(QFontDialog, "getFont", staticmethod(fake_get_font))

    dialogs.prompt_font(window, "Monospace", 11, monospace_hint=True)
    dialogs.prompt_font(window, "Serif", 11, monospace_hint=False)

    assert captured[0].styleHint() == QFont.StyleHint.Monospace
    assert captured[0].family() == "Monospace"
    assert captured[1].styleHint() != QFont.StyleHint.Monospace


def test_message_box_helpers_disable_native_dialog(window, monkeypatch) -> None:
    """どちらのヘルパーも DontUseNativeDialog を立てること（KDE の SIGSEGV 回避）。

    ``show_message_box`` 側の詳細は ``test_message_box_dialog_option.py``
    にもあるが、``dialogs.py`` へ移したあともチェックボックス付きの方を
    含めて 2 つとも同じ扱いであることを、この 1 か所で押さえておく。
    """
    captured: list[QMessageBox] = []

    def spy_exec(self):
        captured.append(self)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", spy_exec)

    dialogs.show_message_box(
        window, QMessageBox.Icon.Warning, "題", "本文", QMessageBox.StandardButton.Cancel
    )
    dialogs.show_message_box_with_checkbox(
        window,
        QMessageBox.Icon.Warning,
        "題",
        "本文",
        QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
        "チェック",
    )

    assert len(captured) == 2
    assert all(box.testOption(QMessageBox.Option.DontUseNativeDialog) for box in captured)
