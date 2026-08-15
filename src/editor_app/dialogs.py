"""利用者に一言尋ねるための小さなモーダルダイアログをまとめたモジュール.

**このアプリのモーダルダイアログは、必ずこのモジュールを通して出すこと。**

``QMessageBox`` には「静的メソッドを使わず、``DontUseNativeDialog`` を
立ててから ``exec()`` する」という守らなければならない決まりがあり
(:func:`show_message_box` の説明を参照)、これが呼び出し側に散らばっていると
新しいダイアログを足したときに簡単に抜け落ちる。決まりごとと、各ダイアログの
癖 (``QFontDialog`` がピクセル指定のフォントを返すと ``pointSize()`` が -1 に
なる等) をこの 1 か所に閉じ込めておく。

呼び出し側は「キャンセルされたら ``None``」という同じ形だけを見ればよい
（``QMessageBox`` 系だけは押されたボタンを返す）。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFontDialog,
    QInputDialog,
    QMessageBox,
)


def show_message_box(
    parent,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    """``QMessageBox`` を表示する。

    ``QMessageBox.warning()`` 等の静的メソッドではなく、あえてインスタンスを
    直接組み立てて ``DontUseNativeDialog`` を指定している。KDE のネイティブな
    メッセージダイアログ統合には、ウィンドウの × ボタンで閉じたときに
    ``QDialogPrivate::setNativeDialogVisible()`` 内で SIGSEGV する既知の
    不具合があることを実機のクラッシュダンプ（coredumpctl）で確認したため、
    このアプリでは常にネイティブ描画を無効化して回避する。
    ``QFileDialog``・``QColorDialog`` のネイティブ表示はこの影響を受けず、
    そちらは問題なく動作している（個別に確認済み）ので、他はそのままにして
    ある。
    """
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    if default_button != QMessageBox.StandardButton.NoButton:
        box.setDefaultButton(default_button)
    return QMessageBox.StandardButton(box.exec())


def show_message_box_with_checkbox(
    parent,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton,
    checkbox_text: str | None,
) -> tuple[QMessageBox.StandardButton, bool]:
    """チェックボックス付きの ``QMessageBox`` を出し、(押されたボタン, チェック状態) を返す。

    ``checkbox_text`` が None ならチェックボックスは出さず、常に False を返す。
    :func:`show_message_box` と同じく静的メソッドを避け、
    ``DontUseNativeDialog`` を設定してから ``exec()`` する
    （KDE のネイティブダイアログ統合による SIGSEGV を避けるため。
    チェックボックスを足す都合で :func:`show_message_box` は再利用できない）。
    """
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    if default_button != QMessageBox.StandardButton.NoButton:
        box.setDefaultButton(default_button)

    checkbox = None
    if checkbox_text is not None:
        checkbox = QCheckBox(checkbox_text, box)
        box.setCheckBox(checkbox)

    answer = QMessageBox.StandardButton(box.exec())
    return answer, bool(checkbox is not None and checkbox.isChecked())


def prompt_int(
    parent,
    title: str,
    label: str,
    value: int,
    minimum: int,
    maximum: int,
) -> int | None:
    """整数を 1 つ尋ねる。キャンセルされたら None。"""
    result, ok = QInputDialog.getInt(parent, title, label, value, minimum, maximum, 1)
    return result if ok else None


def prompt_color(parent, title: str, initial: QColor) -> QColor | None:
    """色を 1 つ尋ねる。キャンセルされたら None。

    ``QColorDialog`` はキャンセル時に「無効な ``QColor``」を返すので、
    呼び出し側が毎回 ``isValid()`` を確かめなくて済むよう None に均す。
    """
    color = QColorDialog.getColor(initial, parent, title)
    return color if color.isValid() else None


def prompt_font(
    parent,
    family: str,
    size: int,
    *,
    monospace_hint: bool = False,
) -> tuple[str, int] | None:
    """本文フォントを尋ね、(ファミリー, サイズ pt) を返す。キャンセルなら None。

    ``monospace_hint`` を立てると、ダイアログに最初に見せるフォントに
    等幅のヒントを付ける（既定の等幅フォントを使っているときに、
    ダイアログ側で似た等幅フォントが選ばれた状態で開くようにするため）。

    ダイアログがピクセル指定のフォントを返した場合 ``pointSize()`` は -1 に
    なる。その場合は今までのサイズ ``size`` を維持する。
    """
    initial = QFont(family, size)
    if monospace_hint:
        initial.setStyleHint(QFont.StyleHint.Monospace)

    ok, font = QFontDialog.getFont(initial, parent, "フォント")
    if not ok:
        return None

    chosen_size = font.pointSize()
    if chosen_size < 1:
        chosen_size = size
    return font.family(), chosen_size
