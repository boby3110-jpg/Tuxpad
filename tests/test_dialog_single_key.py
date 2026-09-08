"""確認ダイアログを ``Alt`` 無しの単独キーでも操作できることのテスト（引き継ぎ ⑫）.

実機フィードバック：未保存の確認ダイアログ（保存／破棄／キャンセル）で、
Windows の多くのアプリは ``S`` ``N`` ``C`` を**単独で押すだけ**で反応するのに、
Tuxpad（Qt/Linux）では ``Alt`` を押しながらでないと効かない。

``dialogs.py`` は**このアプリの全モーダルダイアログの共通入口**なので、
そこに仕組みを 1 つ入れれば、保存確認だけでなくアップデート確認や
外部変更の再読み込み確認（Yes/No 系）にも同じように効く。

**この環境には Qt の日本語訳が入っていない**ので、``Save``/``Cancel`` の
表示は英語のまま（実機では ``保存(&S)``/``キャンセル(&C)``）。
単独キーの割り当ては表示テキストの ``&`` からではなく
:data:`dialogs.SINGLE_KEY_SHORTCUTS` の対応表から決めているので、
訳の有無に関わらず同じように効く。ここではその「効くこと」を見る。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from editor_app import dialogs

SAVE_DISCARD_CANCEL = (
    QMessageBox.StandardButton.Save
    | QMessageBox.StandardButton.Discard
    | QMessageBox.StandardButton.Cancel
)
YES_NO = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No


def shown_box(qtbot, buttons: QMessageBox.StandardButton) -> QMessageBox:
    """単独キーを仕込んだうえで実際に表示したダイアログ。

    ``exec()`` は使わない（モーダルに入ると誰もキーを送れない）。
    ``show()`` でも ``QShortcut`` は同じように働く。
    """
    box = QMessageBox(QMessageBox.Icon.Warning, "題", "本文", buttons)
    box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    qtbot.addWidget(box)
    dialogs.install_single_key_shortcuts(box)
    box.show()
    QApplication.processEvents()
    return box


def press(box: QMessageBox, key: Qt.Key, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(box, key, modifier)
    QApplication.processEvents()


# ----------------------------------------------------------------------
# 保存確認（保存／破棄／キャンセル）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (Qt.Key.Key_S, QMessageBox.StandardButton.Save),
        (Qt.Key.Key_N, QMessageBox.StandardButton.Discard),
        (Qt.Key.Key_C, QMessageBox.StandardButton.Cancel),
    ],
)
def test_single_key_picks_the_button(qtbot, key: Qt.Key, expected) -> None:
    """``Alt`` を押さずに S / N / C を打つだけで、そのボタンを押したのと同じになる。"""
    box = shown_box(qtbot, SAVE_DISCARD_CANCEL)

    press(box, key)

    assert QMessageBox.StandardButton(box.result()) == expected


def test_discard_is_labelled_with_n_not_d(qtbot) -> None:
    """「破棄」のショートカット文字は D ではなく N（Windows の並びに寄せる）。

    ボタンの**表示**を変えるだけで、``StandardButton.Discard`` という enum は
    変わらない（呼び出し側の分岐は無改修）。そこも一緒に確かめる。
    """
    box = shown_box(qtbot, SAVE_DISCARD_CANCEL)

    discard = box.button(QMessageBox.StandardButton.Discard)
    assert discard.text() == dialogs.DISCARD_BUTTON_TEXT
    assert "&N" in discard.text()
    assert box.standardButton(discard) == QMessageBox.StandardButton.Discard


def test_the_mnemonics_are_left_in_place(qtbot) -> None:
    """従来の ``Alt`` + ニーモニックを壊していない（単独キーは「足した」もの）。

    **``Alt`` を押した動作そのものは、この環境では確かめられない。**
    ``offscreen`` プラットフォームはニーモニックを処理しないので、
    単独キーを入れる前の素の ``QMessageBox`` でも ``Alt+Y`` は無反応
    （実測して確かめた）。そこで「``Alt`` が効く材料＝ボタンテキストの
    ``&`` を消していないこと」までを見張り、``Alt`` 自体の効きは
    実機で確認していただく（PROGRESS.md に記載）。
    """
    save_discard_cancel = shown_box(qtbot, SAVE_DISCARD_CANCEL)
    yes_no = shown_box(qtbot, YES_NO)

    assert "&" in save_discard_cancel.button(QMessageBox.StandardButton.Discard).text()
    for standard in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No):
        assert "&" in yes_no.button(standard).text()


def test_an_unrelated_key_does_nothing(qtbot) -> None:
    """割り当てていないキーでは閉じない（打ち間違いで勝手に決まらない）。"""
    box = shown_box(qtbot, SAVE_DISCARD_CANCEL)

    press(box, Qt.Key.Key_D)
    press(box, Qt.Key.Key_X)

    assert box.isVisible()


# ----------------------------------------------------------------------
# Yes/No 系（アップデート確認・外部変更の再読み込み確認など）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (Qt.Key.Key_Y, QMessageBox.StandardButton.Yes),
        (Qt.Key.Key_N, QMessageBox.StandardButton.No),
    ],
)
def test_single_key_works_for_yes_no_dialogs(qtbot, key: Qt.Key, expected) -> None:
    """保存確認以外（Yes/No の確認）でも単独キーが効く。

    ``dialogs.py`` が全モーダルダイアログの共通入口なので、保存確認だけに
    仕込むのではなく共通処理として入れた、というのがこのテストの主眼。
    """
    box = shown_box(qtbot, YES_NO)

    press(box, key)

    assert QMessageBox.StandardButton(box.result()) == expected


def test_discard_and_no_share_n_without_clashing(qtbot) -> None:
    """``Discard`` と ``No`` はどちらも ``N`` だが、同じダイアログには同時に出ない。

    万一同時に出しても、あいまいなショートカット（どちらも動かない）には
    ならず、対応表の先に来る方だけが割り当てられることを固定する。
    """
    box = QMessageBox(QMessageBox.Icon.Warning, "題", "本文", SAVE_DISCARD_CANCEL | YES_NO)
    box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    qtbot.addWidget(box)

    assigned = dialogs.install_single_key_shortcuts(box)

    assert assigned[Qt.Key.Key_N] == QMessageBox.StandardButton.Discard
    # N のショートカットは 1 つだけ（2 つ作ると Qt がどちらも動かさない）。
    n_shortcuts = [
        shortcut
        for shortcut in box.findChildren(QShortcut)
        if shortcut.key().toString() == "N"
    ]
    assert len(n_shortcuts) == 1


# ----------------------------------------------------------------------
# 共通入口（``show_message_box`` / ``show_message_box_with_checkbox``）
# ----------------------------------------------------------------------


def test_both_helpers_install_the_shortcuts(window, monkeypatch) -> None:
    """2 つの入口の**どちらから出したダイアログにも**単独キーが付く。

    片方だけに入れると、チェックボックス付きの確認（「今後は訊かない」）
    だけ ``Alt`` が要るという分かりにくい差が残る。
    """
    captured: list[QMessageBox] = []

    def spy_exec(self):
        captured.append(self)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", spy_exec)

    dialogs.show_message_box(
        window,
        QMessageBox.Icon.Warning,
        "題",
        "本文",
        SAVE_DISCARD_CANCEL,
        QMessageBox.StandardButton.Save,
    )
    dialogs.show_message_box_with_checkbox(
        window,
        QMessageBox.Icon.Warning,
        "題",
        "本文",
        SAVE_DISCARD_CANCEL,
        QMessageBox.StandardButton.Save,
        "今後は訊かない",
    )

    assert len(captured) == 2
    for box in captured:
        keys = {
            shortcut.key().toString() for shortcut in box.findChildren(QShortcut)
        }
        assert {"S", "N", "C"} <= keys
        assert box.button(QMessageBox.StandardButton.Discard).text() == (
            dialogs.DISCARD_BUTTON_TEXT
        )


def test_the_shortcuts_stay_inside_the_dialog(qtbot) -> None:
    """単独キーはダイアログの中だけで効く（背後のウィンドウと取り合わない）。

    ``ApplicationShortcut`` にすると、``C`` などが本文の編集や他の
    ショートカットとぶつかる。範囲を狭めてあることを固定する。
    """
    box = shown_box(qtbot, SAVE_DISCARD_CANCEL)

    shortcuts = box.findChildren(QShortcut)
    assert shortcuts
    for shortcut in shortcuts:
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
