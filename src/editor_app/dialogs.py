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

import difflib

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFontDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

#: 「保存直前に外部変更が見つかった」ダイアログで選ばれた行動。
#: キャンセルは他のダイアログと同じく ``None``。
EXTERNAL_CHANGE_RELOAD = "reload"
EXTERNAL_CHANGE_OVERWRITE = "overwrite"

#: 差分を何行まで見せるか。長いファイルどうしの差分をそのまま流し込むと
#: ダイアログが画面からはみ出す（かつ、そこまで読む人はいない）ので、
#: 先頭からこれだけ出して残りは行数だけ伝える。
MAX_DIFF_LINES = 200

#: 差分の見出しに出す名前。``---`` 側がディスク、``+++`` 側がタブの本文。
DISK_LABEL = "ディスクの内容（外部で変更されたもの）"
BUFFER_LABEL = "このタブの内容（保存しようとしたもの）"

#: 差が 1 行も無かったときに差分の代わりに見せる説明。更新日時や大きさは
#: 動いたのに中身は同じ、という場合（rclone の再同期など）に出る。
NO_DIFFERENCE_TEXT = (
    "本文の差はありませんでした。\n"
    "（中身は同じですが、ファイルの更新日時か大きさが変わっています）"
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


def build_unified_diff(
    disk_text: str,
    buffer_text: str,
    *,
    max_lines: int = MAX_DIFF_LINES,
) -> str:
    """ディスクの内容とタブの本文の差を、統一差分 (unified diff) の文字列にする。

    Qt に依存しない純粋関数（差分の中身だけをテストで固定できるように、
    ダイアログの組み立てから切り離してある）。

    - **改行コードの表現を先にそろえる。** 本文側は LF に正規化されている
      が、ディスクから読み直した側と食い違うと**改行コードの違いだけで
      全行が差分に見えてしまう**（それは利用者が知りたい差ではない）。
    - ``splitlines()`` ではなく ``split("\\n")`` で分けるのは、**末尾の改行の
      有無も差として見せる**ため（``splitlines()`` だと ``"あ\\n"`` と ``"あ"``
      が同じ行の並びになり、差が消える）。
    - ``max_lines`` を超えたら打ち切り、残りの行数だけ伝える。
    - 差が 1 行も無ければ空文字列を返す（呼び出し側が説明に差し替える）。
    """
    disk_lines = _diff_lines(disk_text)
    buffer_lines = _diff_lines(buffer_text)
    lines = list(
        difflib.unified_diff(
            disk_lines,
            buffer_lines,
            fromfile=DISK_LABEL,
            tofile=BUFFER_LABEL,
            lineterm="",
        )
    )
    if not lines:
        return ""
    if len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = [*lines[:max_lines], f"…（以下 {omitted} 行を省略）"]
    return "\n".join(lines)


def _diff_lines(text: str) -> list[str]:
    """差分を取るための行の並び（改行コードの表現をそろえてから分ける）。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


class ExternalChangeDialog(QDialog):
    """保存直前に見つかった外部変更を、差分付きで見せて行動を選ばせるダイアログ。

    ``QMessageBox`` では差分を等幅・スクロール可能で見せられないので、
    このダイアログだけは自前で組み立てている（``QMessageBox`` ではないため
    ``DontUseNativeDialog`` の決まりは関係しない）。

    テストから押した結果を確かめられるよう、押されたボタンは
    :attr:`choice` に残し、ボタン自身も属性として持っておく。
    """

    def __init__(self, parent, *, name: str, diff_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("保存できません")
        #: 選ばれた行動（``EXTERNAL_CHANGE_*``）。キャンセルなら None のまま。
        self.choice: str | None = None

        layout = QVBoxLayout(self)
        message = QLabel(
            f"「{name}」は、開いたあとで外部で変更されています。\n"
            "このまま保存すると、その変更が失われます。\n\n"
            "ディスクの内容と、保存しようとした内容の差は次のとおりです"
            "（先頭が - の行がディスク、+ の行がこのタブ）。",
            self,
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        self.diff_view = QPlainTextEdit(diff_text or NO_DIFFERENCE_TEXT, self)
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        layout.addWidget(self.diff_view)

        buttons = QDialogButtonBox(self)
        self.reload_button = buttons.addButton(
            "ディスクの内容を読み込む", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.overwrite_button = buttons.addButton(
            "自分の内容のまま上書き保存する", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        self.cancel_button = buttons.addButton(
            "キャンセル", QDialogButtonBox.ButtonRole.RejectRole
        )
        # 既定は「キャンセル」。Enter や Esc を反射的に押しても、
        # どちらかの内容が消える方へは倒れないようにする。
        self.cancel_button.setDefault(True)
        self.reload_button.clicked.connect(
            lambda: self._choose(EXTERNAL_CHANGE_RELOAD)
        )
        self.overwrite_button.clicked.connect(
            lambda: self._choose(EXTERNAL_CHANGE_OVERWRITE)
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(720, 480)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


def prompt_external_change(parent, *, name: str, diff_text: str) -> str | None:
    """外部変更の差分を見せ、``EXTERNAL_CHANGE_*`` を返す。キャンセルなら None。"""
    dialog = ExternalChangeDialog(parent, name=name, diff_text=diff_text)
    dialog.exec()
    return dialog.choice


def prompt_destructive(parent, title: str, text: str, action_label: str) -> bool:
    """「押すと何かが失われる」操作の確認。続行なら True。

    **ボタンの文言は呼び出し側が決める**（``StandardButton.Ok`` の「OK」や
    ``Save`` の「保存」では、ふつうの操作と同じに見えてしまう。ここは
    「何が失われるか承知の上で続ける」ボタンなので、そう読める文言にする）。

    既定は必ず「キャンセル」——Enter や Esc を反射的に押しても、
    何かが失われる方へは倒れないようにする。
    """
    box = QMessageBox(
        QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.NoButton, parent
    )
    box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    proceed = box.addButton(action_label, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is proceed


def prompt_suspicious_text(parent, title: str, text: str) -> bool:
    """「文字化けしたまま保存しようとしていないか」の確認。続行なら True。

    :func:`~editor_app.fileio.find_suspicious_text` が怪しいと見たときに出す
    （引き継ぎ ⑥）。**確実な検知ではなく目安**なので、止めるのではなく
    「このまま保存する」を必ず用意する（誤検知したときに保存できなくなると、
    それ自体が損失になる）。
    """
    return prompt_destructive(parent, title, text, "このまま保存する")


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


def prompt_choice(
    parent,
    title: str,
    label: str,
    items: list[str],
    current: int,
) -> int | None:
    """一覧から 1 つ選ばせ、選ばれた**位置**を返す。キャンセルなら None。

    ``QInputDialog.getItem()`` が返すのは表示文字列だが、呼び出し側が欲しいの
    は「一覧の何番目か」（＝表示名ではなくコーデック名などの実体）なので、
    ここで位置に直して返す。自由入力は許さない（一覧に無い値を受け取っても
    呼び出し側が扱えないため）。
    """
    text, ok = QInputDialog.getItem(parent, title, label, items, current, False)
    if not ok:
        return None
    return items.index(text)


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
