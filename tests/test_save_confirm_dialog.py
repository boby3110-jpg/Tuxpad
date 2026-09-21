"""「保存しますか？」の**訊き方**を固定するテスト（2026-09-13・60 回目）。

答えたあとの振る舞い（保存する・破棄する・中止する・残りにも同じ操作を
適用する）は ``test_close_remember_choice.py`` などが手厚く見張っているが、
**訊き方そのもの**——どのボタンを出すか、Enter がどこに当たるか、
どのファイルの話かを名乗るか——は、どのテストも固定していなかった。

ここが外れると、**Enter を反射的に押しただけで未保存の変更が消える**
（既定のボタンが「破棄」になる）、**閉じるのをやめられない**
（「キャンセル」が消える）といった、利用者がいちばん避けたい形の損失に
なる。しかもどれも「ダイアログが出た」ところまでは今までどおりなので、
画面を見ても気づけない。

**ダイアログのヘルパー（``show_message_box`` 等）を差し替えずに、
``QMessageBox.exec()`` だけを差し替えている**のがこのファイルの要点。
ヘルパーを差し替えてしまうと「呼び出し側が何を渡したか」しか見えないが、
利用者が実際に押すのは組み上がった ``QMessageBox`` のボタンなので、
**組み上がった箱そのもの**を捕まえて見る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

SAVE = QMessageBox.StandardButton.Save
DISCARD = QMessageBox.StandardButton.Discard
CANCEL = QMessageBox.StandardButton.Cancel


@pytest.fixture
def captured_boxes(monkeypatch):
    """``exec()`` を差し替えて、組み上がった ``QMessageBox`` を捕まえる。

    返り値は (捕まえた箱の一覧, 押したことにするボタンを決める関数)。
    既定では「キャンセル」を押したことにする（何も保存させないため）。
    """
    boxes: list[QMessageBox] = []
    answer: list[QMessageBox.StandardButton] = [CANCEL]

    def spy_exec(self) -> int:
        boxes.append(self)
        return answer[0]

    monkeypatch.setattr(QMessageBox, "exec", spy_exec)

    def press(button: QMessageBox.StandardButton) -> None:
        answer[0] = button

    return boxes, press


def make_modified_tab(window, tmp_path: Path, name: str):
    """保存先が決まっている「未保存の変更あり」のタブを 1 つ作る。"""
    path = tmp_path / name
    path.write_text("元の内容\n", encoding="utf-8")
    editor = window.open_path(path)
    assert editor is not None
    editor.insertPlainText("追記\n")
    assert editor.is_modified
    return editor


# ----------------------------------------------------------------------
# タブを 1 つ閉じるとき（maybe_save）
# ----------------------------------------------------------------------


def test_closing_a_tab_offers_save_discard_and_cancel(window, tmp_path, captured_boxes):
    """3 択（保存・破棄・キャンセル）を必ず出すこと。

    とくに**「キャンセル」が要る**。無いと、閉じかけて気が変わったときに
    「保存」か「破棄」のどちらかを選ばされ、やめる道が無くなる。
    """
    boxes, _ = captured_boxes
    editor = make_modified_tab(window, tmp_path, "確認.txt")

    assert window.close_editor(editor) is False  # キャンセルしたので閉じない

    (box,) = boxes
    assert box.standardButtons() == SAVE | DISCARD | CANCEL


def test_closing_a_tab_defaults_to_save(window, tmp_path, captured_boxes):
    """Enter を押したときに当たるのは「保存」であること。

    ここが「破棄」になると、**確認ダイアログを読まずに Enter を押した
    だけで未保存の変更が消える**。Qt は既定のボタンを勝手に選ぶので、
    「指定を捨てても、それらしいボタンが既定になっている」形で
    静かに壊れうる（``dialogs.py`` 側の同じ趣旨のテストは
    ``test_dialogs.py`` にある）。
    """
    boxes, _ = captured_boxes
    editor = make_modified_tab(window, tmp_path, "既定.txt")

    window.close_editor(editor)

    (box,) = boxes
    assert box.defaultButton() is box.button(SAVE)


def test_closing_a_tab_names_the_file_in_question(window, tmp_path, captured_boxes):
    """どのファイルの話かを本文で名乗ること。

    タブが何枚も開いている状態で「変更は保存されていません」とだけ
    出ても、**どれを捨てようとしているのか分からない**。表示中のタブを
    そのファイルに切り替えることは既に見張られている（``mw-close-tab-asks-
    without-switching``）が、本文の名乗りは別に要る。
    """
    boxes, _ = captured_boxes
    make_modified_tab(window, tmp_path, "ほかのタブ.txt")
    editor = make_modified_tab(window, tmp_path, "これを閉じる.txt")

    window.close_editor(editor)

    (box,) = boxes
    assert "これを閉じる.txt" in box.text()
    assert "ほかのタブ.txt" not in box.text()


# ----------------------------------------------------------------------
# ウィンドウを閉じるとき（_confirm_save_with_remember_option）
# ----------------------------------------------------------------------


def test_closing_a_window_offers_save_discard_and_cancel(
    make_window, tmp_path, captured_boxes
):
    """ウィンドウを閉じる側の 3 択も同じであること。

    こちらはチェックボックス付きの別のヘルパーを通るので、タブ 1 つを
    閉じる側とは**別に**縛る必要がある。
    """
    boxes, _ = captured_boxes
    win = make_window()
    make_modified_tab(win, tmp_path, "窓を閉じる.txt")

    assert win.close() is False  # キャンセルしたので閉じない

    (box,) = boxes
    assert box.standardButtons() == SAVE | DISCARD | CANCEL


def test_closing_a_window_defaults_to_save(make_window, tmp_path, captured_boxes):
    """ウィンドウを閉じる側でも Enter は「保存」に当たること。

    未保存のタブが複数あると**この確認が続けて出る**ので、既定が
    「破棄」になっていると Enter の連打で次々に消える。1 つ閉じる側より
    被害が大きい。
    """
    boxes, _ = captured_boxes
    win = make_window()
    make_modified_tab(win, tmp_path, "窓の既定.txt")

    win.close()

    (box,) = boxes
    assert box.defaultButton() is box.button(SAVE)


def test_closing_a_window_names_each_file_in_question(
    make_window, tmp_path, captured_boxes
):
    """1 枚ずつ、そのタブの名前を名乗ること。"""
    boxes, press = captured_boxes
    press(DISCARD)  # 最後まで訊かせるため、破棄で進める
    win = make_window()
    make_modified_tab(win, tmp_path, "一枚目.txt")
    make_modified_tab(win, tmp_path, "二枚目.txt")

    win.close()

    assert [box.text() for box in boxes] == [
        "「一枚目.txt」の変更は保存されていません。\n保存しますか？",
        "「二枚目.txt」の変更は保存されていません。\n保存しますか？",
    ]
