"""`editor_app.dialogs` の小さなダイアログヘルパーのテスト.

ダイアログそのものは表示せず、Qt 側の静的メソッドを差し替えて
「戻り値をどう均しているか」だけを確認する。ここで固定したいのは 2 点:

- キャンセルされたときの表現を ``None`` に統一していること
  （``QColorDialog`` は無効な ``QColor``、``QInputDialog`` /
  ``QFontDialog`` は ``ok`` フラグと、Qt 側の表現がばらばらなため）
- ``QFontDialog`` がピクセル指定のフォントを返したときに
  ``pointSize()`` が -1 になる癖を、呼び出し側に漏らさないこと

加えて、保存直前の外部変更を見せる差分ダイアログ
(:class:`~editor_app.dialogs.ExternalChangeDialog`) もここで確かめる。
こちらは ``QMessageBox`` ではなく自前で組み立てた ``QDialog`` なので、
**差分の文字列を組み立てる純粋関数** (:func:`~editor_app.dialogs.build_unified_diff`)
と、**どのボタンがどの値になるか**の 2 つに分けて縛る（機能そのものの
流れは ``test_external_change_before_save.py`` 側）。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QColorDialog,
    QFontDialog,
    QInputDialog,
    QMessageBox,
    QPlainTextEdit,
)

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


def test_prompt_choice_returns_index_of_selection(window, monkeypatch) -> None:
    """選ばれた**位置**を返すこと（表示文字列ではなく）。"""
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Shift-JIS", True))
    )
    items = ["UTF-8", "Shift-JIS", "EUC-JP"]
    assert dialogs.prompt_choice(window, "題", "ラベル", items, 0) == 1


def test_prompt_choice_returns_none_when_cancelled(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Shift-JIS", False))
    )
    assert dialogs.prompt_choice(window, "題", "ラベル", ["UTF-8", "Shift-JIS"], 0) is None


def test_prompt_choice_passes_items_and_forbids_free_text(window, monkeypatch) -> None:
    """一覧・初期選択がそのまま Qt 側に渡り、自由入力は許していないこと。"""
    captured: dict = {}

    def fake_get_item(parent, title, label, items, current, editable):
        captured.update(
            title=title, label=label, items=items, current=current, editable=editable
        )
        return items[current], True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(fake_get_item))

    items = ["UTF-8", "Shift-JIS (cp932)"]
    assert dialogs.prompt_choice(window, "文字コード", "選んで:", items, 1) == 1
    assert captured == {
        "title": "文字コード",
        "label": "選んで:",
        "items": items,
        "current": 1,
        "editable": False,
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


def test_message_box_helpers_use_the_requested_default_button(window, monkeypatch) -> None:
    """呼び出し側が指定した既定のボタンが、そのまま既定になること。

    Enter を押したときにどこへ倒れるかは、**場面ごとに呼び出し側が決めて
    いる**（保存の確認は「保存」、外部変更の再読み込みは「はい」、何かが
    失われる確認は「キャンセル」）。ここで指定を捨てても、**Qt が
    それらしいボタンを勝手に既定にしてしまう**ため、素直な指定を渡すだけ
    では捨てられたことに気づけない。**Qt が自分では選ばないボタン**
    （``Discard``）を指定して、指定そのものが効いていることを見る。
    """
    captured: list[QMessageBox] = []

    def spy_exec(self):
        captured.append(self)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", spy_exec)

    buttons = (
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    dialogs.show_message_box(
        window,
        QMessageBox.Icon.Warning,
        "題",
        "本文",
        buttons,
        QMessageBox.StandardButton.Discard,
    )
    dialogs.show_message_box_with_checkbox(
        window,
        QMessageBox.Icon.Warning,
        "題",
        "本文",
        buttons,
        QMessageBox.StandardButton.Cancel,
        "チェック",
    )

    plain, with_checkbox = captured
    assert plain.defaultButton() is plain.button(QMessageBox.StandardButton.Discard)
    assert with_checkbox.defaultButton() is with_checkbox.button(
        QMessageBox.StandardButton.Cancel
    )


# ----------------------------------------------------------------------
# 保存直前の外部変更を見せる差分（純粋関数）
# ----------------------------------------------------------------------


def test_build_unified_diff_marks_each_side() -> None:
    """ディスク側が ``-``、タブ側が ``+``。見出しにどちらか分かる名前が出る。"""
    diff = dialogs.build_unified_diff("共通\nディスク側\n", "共通\nタブ側\n")

    assert "-ディスク側" in diff
    assert "+タブ側" in diff
    assert " 共通" in diff
    assert f"--- {dialogs.DISK_LABEL}" in diff
    assert f"+++ {dialogs.BUFFER_LABEL}" in diff


def test_build_unified_diff_is_empty_when_the_texts_match() -> None:
    """差が無ければ空。呼び出し側が説明文に差し替えられるようにする。"""
    assert dialogs.build_unified_diff("同じ内容\n", "同じ内容\n") == ""


def test_build_unified_diff_ignores_newline_style() -> None:
    """改行コードの違いだけで全行が差分に見えないこと。

    ディスクから読み直した側と本文側で改行の表現が食い違うと、中身は
    同じなのに全行が差分になり、利用者が見たい差が埋もれてしまう。
    """
    assert dialogs.build_unified_diff("あ\r\nい\r\n", "あ\nい\n") == ""
    assert dialogs.build_unified_diff("あ\rい\r", "あ\nい\n") == ""


def test_build_unified_diff_shows_a_missing_trailing_newline() -> None:
    """末尾の改行の有無は、見える差として残すこと（``splitlines()`` だと消える）。"""
    assert dialogs.build_unified_diff("あ\n", "あ") != ""


def test_build_unified_diff_truncates_long_diffs() -> None:
    """長すぎる差分は打ち切り、残りの行数を伝える（ダイアログが破綻しないため）。"""
    disk = "".join(f"ディスク {i}\n" for i in range(500))
    buffer_text = "".join(f"タブ {i}\n" for i in range(500))

    diff = dialogs.build_unified_diff(disk, buffer_text, max_lines=20)
    lines = diff.split("\n")

    assert len(lines) == 21  # 20 行 + 省略の 1 行
    assert lines[-1].startswith("…（以下 ")
    assert lines[-1].endswith(" 行を省略）")


def test_build_unified_diff_keeps_short_diffs_whole() -> None:
    """上限に届かない差分には、省略の行を足さないこと。"""
    diff = dialogs.build_unified_diff("あ\n", "い\n", max_lines=20)
    assert "省略" not in diff


# ----------------------------------------------------------------------
# 保存直前の外部変更を見せるダイアログ（ボタンと戻り値）
# ----------------------------------------------------------------------


def make_external_change_dialog(window, qtbot, diff_text: str = "-古い\n+新しい"):
    dialog = dialogs.ExternalChangeDialog(window, name="sample.txt", diff_text=diff_text)
    qtbot.addWidget(dialog)
    return dialog


def test_external_change_dialog_reports_the_reload_button(window, qtbot) -> None:
    dialog = make_external_change_dialog(window, qtbot)
    dialog.reload_button.click()
    assert dialog.choice == dialogs.EXTERNAL_CHANGE_RELOAD


def test_external_change_dialog_reports_the_overwrite_button(window, qtbot) -> None:
    dialog = make_external_change_dialog(window, qtbot)
    dialog.overwrite_button.click()
    assert dialog.choice == dialogs.EXTERNAL_CHANGE_OVERWRITE


def test_external_change_dialog_reports_cancel_as_none(window, qtbot) -> None:
    """キャンセルは他のダイアログと同じく None（何も選ばれていない）。"""
    dialog = make_external_change_dialog(window, qtbot)
    dialog.cancel_button.click()
    assert dialog.choice is None


def test_external_change_dialog_defaults_to_cancel(window, qtbot) -> None:
    """Enter を反射的に押しても、どちらかの内容が消える方へは倒れないこと。"""
    dialog = make_external_change_dialog(window, qtbot)
    assert dialog.cancel_button.isDefault() is True
    assert dialog.reload_button.isDefault() is False
    assert dialog.overwrite_button.isDefault() is False


def test_external_change_dialog_shows_the_diff_read_only(window, qtbot) -> None:
    dialog = make_external_change_dialog(window, qtbot, "-古い\n+新しい")
    assert dialog.diff_view.toPlainText() == "-古い\n+新しい"
    assert dialog.diff_view.isReadOnly() is True


def test_external_change_dialog_shows_the_diff_monospaced_and_unwrapped(
    window, qtbot
) -> None:
    """差分は等幅・折り返し無しで見せること（このダイアログを自前で組んだ理由）。

    ``QMessageBox`` を使わずに :class:`~editor_app.dialogs.ExternalChangeDialog`
    を自前で組み立てているのは、まさに**差分をこの形で見せるため**。
    どちらが欠けても「``-`` と ``+`` の桁が揃わない」「どこまでが 1 行か
    分からない」形になり、**これから消える内容を読み違えたまま
    「上書き保存する」を選ばせる**ことになる。
    """
    dialog = make_external_change_dialog(window, qtbot)

    assert dialog.diff_view.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    assert dialog.diff_view.font().family() == fixed.family()


def test_external_change_dialog_explains_an_empty_diff(window, qtbot) -> None:
    """差が 1 行も無かったときは、空欄ではなく理由を見せる。"""
    dialog = make_external_change_dialog(window, qtbot, "")
    assert dialog.diff_view.toPlainText() == dialogs.NO_DIFFERENCE_TEXT


def capture_external_change_dialog(monkeypatch, press: str | None = None) -> list:
    """``exec()`` を差し替えて、``prompt_external_change`` が組み立てた
    ダイアログを捕まえる（``press`` にボタンの属性名を渡すとそれを押す）。"""
    captured: list[dialogs.ExternalChangeDialog] = []

    def spy_exec(self):
        captured.append(self)
        if press is not None:
            getattr(self, press).click()
        return 0

    monkeypatch.setattr(dialogs.ExternalChangeDialog, "exec", spy_exec)
    return captured


def test_prompt_external_change_returns_what_was_pressed(window, monkeypatch) -> None:
    """押されたボタンの値がそのまま呼び出し元（保存の経路）へ返ること。

    ここが返す値で**上書きするか読み直すかが決まる**ので、ダイアログ本体と
    呼び出し口の間で取り違えると、押したのと逆のことが起きる。
    """
    capture_external_change_dialog(monkeypatch, "overwrite_button")
    assert (
        dialogs.prompt_external_change(window, name="a.txt", diff_text="-古い\n+新しい")
        == dialogs.EXTERNAL_CHANGE_OVERWRITE
    )

    capture_external_change_dialog(monkeypatch, "reload_button")
    assert (
        dialogs.prompt_external_change(window, name="a.txt", diff_text="-古い\n+新しい")
        == dialogs.EXTERNAL_CHANGE_RELOAD
    )


def test_prompt_external_change_returns_none_when_cancelled(window, monkeypatch) -> None:
    """キャンセル（どのボタンも押されなかった）は None＝保存もしない。"""
    capture_external_change_dialog(monkeypatch)

    assert dialogs.prompt_external_change(window, name="a.txt", diff_text="-古い") is None


def test_prompt_external_change_shows_the_name_and_the_diff(window, monkeypatch) -> None:
    """ファイル名と差分が、そのままダイアログへ渡っていること。"""
    from PySide6.QtWidgets import QLabel

    captured = capture_external_change_dialog(monkeypatch)

    dialogs.prompt_external_change(window, name="議事録.txt", diff_text="-古い\n+新しい")

    (dialog,) = captured
    assert dialog.diff_view.toPlainText() == "-古い\n+新しい"
    assert any("議事録.txt" in label.text() for label in dialog.findChildren(QLabel))


# ----------------------------------------------------------------------
# 保存直前の「文字化けしているかもしれません」（引き継ぎ ⑥）
# ----------------------------------------------------------------------


def capture_message_box(monkeypatch, press: str | None = None) -> list[QMessageBox]:
    """``exec()`` を差し替えて、組み立てられた QMessageBox を捕まえる。

    ``press`` にボタンの文言を渡すとそれを押す。渡さなければ、どのボタンも
    押されずに閉じられた（× で閉じた）ことになる。
    """
    captured: list[QMessageBox] = []

    def spy_exec(self):
        captured.append(self)
        for button in self.buttons():
            if button.text() == press:
                button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", spy_exec)
    return captured


def test_suspicious_prompt_returns_true_when_saving_anyway(window, monkeypatch) -> None:
    capture_message_box(monkeypatch, "このまま保存する")

    assert dialogs.prompt_suspicious_text(window, "題", "本文") is True


def test_suspicious_prompt_returns_false_when_cancelled(window, monkeypatch) -> None:
    capture_message_box(monkeypatch, "キャンセル")

    assert dialogs.prompt_suspicious_text(window, "題", "本文") is False


def test_suspicious_prompt_returns_false_when_closed(window, monkeypatch) -> None:
    """× で閉じられた（どのボタンも押されなかった）ときも保存しない。"""
    capture_message_box(monkeypatch)

    assert dialogs.prompt_suspicious_text(window, "題", "本文") is False


def test_destructive_prompt_marks_the_action_button_as_destructive(
    window, monkeypatch
) -> None:
    """「押すと何かが失われる」ボタンは、ふつうの「OK」と同じ扱いにしないこと。

    文言を変えるだけでは足りない。``AcceptRole`` にすると、デスクトップ環境に
    よっては**ふつうの決定ボタンと同じ位置・同じ見た目**に並び、流れ作業の
    中で押してしまう。役割そのものを ``DestructiveRole`` にしておくことで、
    環境側が「これは戻せない操作だ」と扱えるようにする。
    """
    captured = capture_message_box(monkeypatch)

    dialogs.prompt_destructive(window, "題", "本文", "消して続ける")

    box, = captured
    roles = {button.text(): box.buttonRole(button) for button in box.buttons()}
    assert roles["消して続ける"] == QMessageBox.ButtonRole.DestructiveRole
    assert roles["キャンセル"] == QMessageBox.ButtonRole.RejectRole


def test_suspicious_prompt_defaults_to_cancel(window, monkeypatch) -> None:
    """Enter や Esc を反射的に押しても、化けたまま上書きする方へ倒れないこと。"""
    captured = capture_message_box(monkeypatch)

    dialogs.prompt_suspicious_text(window, "題", "本文")

    box, = captured
    assert box.testOption(QMessageBox.Option.DontUseNativeDialog) is True
    assert box.defaultButton().text() == "キャンセル"
    assert box.escapeButton().text() == "キャンセル"
    # 「保存」ではなく「このまま保存する」——ふつうの保存と見分けが付く文言。
    assert sorted(button.text() for button in box.buttons()) == sorted(
        ["このまま保存する", "キャンセル"]
    )
