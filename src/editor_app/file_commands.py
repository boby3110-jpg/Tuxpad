"""ファイルを開く・保存する処理のミックスイン（機能 2・3・8）.

``MainWindow`` から「開く」「保存 / 名前を付けて保存」だけを切り出したもの
（`view_settings.py` / `file_watch.py` / `search_replace.py` / `tab_transfer.py`
と同じ切り出し方）。**ファイルの読み書きまわりを触るときは、`main_window.py`
ではなくこのモジュールに書くこと。**

役割の分かれ目は「ディスクとの往復に GUI が絡むかどうか」:

- 実際の読み書き（文字コードの判定・保持・改行コード）は Qt 非依存の
  :mod:`editor_app.fileio` が受け持つ。
- **どのファイルを・どのタブへ読むか、失敗したことをどう伝えるか**を
  このモジュールが受け持つ。

**このモジュールを触るときの注意**（過去に実機で不具合として出たもの）:

- **同じファイルを 2 つのタブで開かせないこと。** あとから保存した方が先の
  変更を黙って消してしまう。「開く」側（:meth:`FileCommandsMixin.open_path`）
  だけでなく、**「名前を付けて保存」側**
  （:meth:`FileCommandsMixin._warn_path_open_in_another_tab`）も塞いである。
- 保存できない文字（Shift-JIS に絵文字など）は、**理由だけを出しても直せない**。
  何行目の何かを並べ、最初の 1 つを選択してから知らせること
  （:meth:`FileCommandsMixin._report_unencodable_characters`）。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・
``self.editors()``・``self.current_editor()``・``self._add_editor()``・
``self._refresh_file_watches()``・``self.open_windows()`` に依存する。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from .dialogs import show_message_box
from .editor import EditorWidget, center_cursor_vertically
from .fileio import (
    EncodingError,
    describe_encoding,
    read_text_file,
    resolve_path,
    write_text_file,
)
from .textsearch import (
    line_number_at,
    surrogate_extra,
    utf16_length,
)

#: 「開く」ダイアログのファイル種別フィルタ
FILE_DIALOG_FILTER = (
    "テキストファイル (*.txt *.text *.md *.csv *.tsv *.log *.ini *.json *.xml);;"
    "すべてのファイル (*)"
)

#: 「その文字コードでは保存できない文字」を保存エラーの本文に何個まで
#: 並べるか。全部並べるとダイアログが画面からはみ出すので、先頭から
#: これだけ挙げて、残りは件数だけ伝える。
MAX_LISTED_UNENCODABLE_SPOTS = 5


def raise_window(window: QWidget) -> None:
    """ウィンドウを最前面に出す（最小化されていれば元に戻してから）。

    別ウィンドウで既に開いているファイルを見つけたときや、他プロセスから
    ファイルパスが転送されてきたとき（``app.py`` の ``_handle_forwarded_paths``
    も同じ実装を使う）に使う共通処理。
    """
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()


class FileCommandsMixin:
    """ファイルを開く・保存する処理を受け持つミックスイン."""

    # ------------------------------------------------------------------
    # 機能 2: 開く
    # ------------------------------------------------------------------
    def open_file_dialog(self) -> list[EditorWidget]:
        """「開く」ダイアログを表示し、選ばれたファイルをタブで開く。"""
        dialog = QFileDialog(self, "開く", str(self._dialog_directory()), FILE_DIALOG_FILTER)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return []
        return self.open_paths(dialog.selectedFiles())

    def open_paths(self, paths) -> list[EditorWidget]:
        """複数のファイルを順に開き、開けたタブのリストを返す。"""
        opened: list[EditorWidget] = []
        for path in paths:
            editor = self.open_path(path)
            if editor is not None:
                opened.append(editor)
        return opened

    def open_dropped_urls(self, urls) -> list[EditorWidget]:
        """D&D で渡された URL のうち、ローカルファイルだけを開く。

        ``QDropEvent`` 本体に依存しない形にしてあるのは、PySide6 では
        テストコードから ``QDropEvent`` を直接組み立てると ``QMimeData`` の
        参照が保持されず落ちることがあるため（実機テスト側での既知の問題）。
        """
        paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        return self.open_paths(paths)

    def open_path(self, path: Path | str) -> EditorWidget | None:
        """1 つのファイルをタブで開く。失敗した場合は None を返す。

        - 既に同じファイルを開いているタブがあれば、それに切り替えるだけにする。
          **他のウィンドウで開いていた場合は、そのウィンドウを前面に出す**
          （実機フィードバックにより追加。以前はこのウィンドウ内しか
          探しておらず、別ウィンドウで既に開いているファイルを Ctrl+O や
          D&D で開くと重複してタブができてしまい、片方で保存したあと
          もう片方（古い内容）を保存すると先の保存が失われる不具合が
          あった）。
        - 「新規作成」直後のまっさらなタブが現在表示中なら、それを使い回す。
        """
        path = Path(path)
        resolved = resolve_path(path)

        owner, existing = self._find_editor_in_any_window(resolved)
        if existing is not None:
            owner.tabs.setCurrentWidget(existing)
            existing.setFocus()
            if owner is not self:
                raise_window(owner)
            return existing

        try:
            text, encoding, newline = read_text_file(resolved)
        except Exception as exc:  # noqa: BLE001 - 失敗理由はそのまま利用者に見せる
            show_message_box(
                self,
                QMessageBox.Icon.Critical,
                "開けません",
                f"{path} を開けませんでした。\n\n{exc}",
            )
            return None

        editor = self._reusable_editor()
        if editor is None:
            editor = self._add_editor()
        editor.encoding = encoding
        editor.newline = newline
        editor.set_content(text)
        editor.path = resolved

        self.tabs.setCurrentWidget(editor)
        editor.setFocus()
        self._refresh_file_watches()
        return editor

    def find_editor_by_path(self, path: Path | str) -> EditorWidget | None:
        """同じファイルを開いているタブを探す（このウィンドウ内のみ）。無ければ None。"""
        target = Path(path)
        for editor in self.editors():
            if editor.path is not None and editor.path == target:
                return editor
        return None

    def _find_editor_in_any_window(
        self, path: Path | str
    ) -> tuple["FileCommandsMixin", EditorWidget | None]:
        """同じファイルを開いているタブを、まずこのウィンドウ、次に他の
        ウィンドウの順に探す。見つかったウィンドウとタブを返す
        （無ければ ``(self, None)``）。
        """
        target = Path(path)
        existing = self.find_editor_by_path(target)
        if existing is not None:
            return self, existing
        for win in self.open_windows():
            if win is self:
                continue
            existing = win.find_editor_by_path(target)
            if existing is not None:
                return win, existing
        return self, None

    def _reusable_editor(self) -> EditorWidget | None:
        """ファイルを開くときに使い回してよい、空の新規タブ。

        「起動直後の空タブ」がそのままファイルで置き換わるのが自然なので、
        現在のタブが空の無題タブならそれを返す。
        """
        editor = self.current_editor()
        if editor is not None and editor.is_empty_untitled():
            return editor
        return None

    def _dialog_directory(self) -> Path:
        """ファイルダイアログの初期ディレクトリ。

        現在のタブのファイルがある場所、無ければホームディレクトリ。
        """
        editor = self.current_editor()
        if editor is not None and editor.path is not None:
            return editor.path.parent
        return Path.home()

    # ------------------------------------------------------------------
    # 機能 3: 保存 / 上書き保存
    # ------------------------------------------------------------------
    def save_file(self) -> bool:
        """現在のタブを上書き保存する (Ctrl+S)。保存できたら True。"""
        editor = self.current_editor()
        return False if editor is None else self.save_editor(editor)

    def save_file_as(self) -> bool:
        """現在のタブを名前を付けて保存する (Ctrl+Shift+S)。"""
        editor = self.current_editor()
        return False if editor is None else self.save_editor_as(editor)

    def save_editor(self, editor: EditorWidget) -> bool:
        """指定タブを保存する。まだ保存先が無ければ「名前を付けて保存」に回す。

        文字コード・改行コードは読み込んだときのものをそのまま使う (機能 8)。
        """
        if editor.path is None:
            return self.save_editor_as(editor)
        return self._write_editor(editor, editor.path)

    def save_editor_as(self, editor: EditorWidget) -> bool:
        """保存先をダイアログで選んでから保存する。キャンセルなら False。

        選んだ保存先を **別のタブが既に開いている場合は保存しない**
        （:meth:`_warn_path_open_in_another_tab` を参照）。
        """
        dialog = QFileDialog(
            self, "名前を付けて保存", str(self._save_dialog_path(editor)), FILE_DIALOG_FILTER
        )
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return False
        selected = dialog.selectedFiles()
        if not selected:
            return False
        target = resolve_path(selected[0])
        if self._warn_path_open_in_another_tab(target, editor):
            return False
        return self._write_editor(editor, target)

    def _warn_path_open_in_another_tab(
        self, path: Path, editor: EditorWidget
    ) -> bool:
        """保存先が別のタブで開かれていたら警告して True を返す（＝保存しない）。

        同じファイルを 2 つのタブで開いた状態になると、あとから保存した方が
        先の変更を黙って消してしまう。「開く」側はこの状態を作らないように
        してある（:meth:`open_path`）が、**「名前を付けて保存」で既に開いて
        いるファイルを選ぶ**と同じ状態を作れてしまい、実際に先の保存が
        失われることを確認したため、こちら側でも塞ぐ。

        止めるだけにして「それでも保存する」は用意していない。押せば必ず
        どちらかの内容が消える選択肢になってしまうため、先にそのタブを
        閉じてもらう（閉じるときに保存するか捨てるかを選べる）。

        警告を出すだけで、そのタブへ切り替えたりはしない（保存しようと
        していたタブから勝手に離れると、戻ってくるのが手間なため）。
        """
        owner, existing = self._find_editor_in_any_window(path)
        if existing is None or existing is editor:
            return False

        where = "このウィンドウの別のタブ" if owner is self else "別のウィンドウのタブ"
        show_message_box(
            self,
            QMessageBox.Icon.Warning,
            "保存できません",
            f"「{path.name}」は{where}で開いています。\n\n"
            "このまま保存すると同じファイルを 2 つのタブで編集することになり、"
            "あとから保存した方で先の変更が消えてしまいます。\n"
            "先に開いている方のタブを閉じてから保存してください。",
        )
        return True

    def _write_editor(self, editor: EditorWidget, path: Path) -> bool:
        """実際にファイルへ書き出し、成功したらタブの状態を更新する。"""
        resolved = resolve_path(path)

        try:
            write_text_file(
                resolved,
                editor.toPlainText(),
                editor.encoding,
                editor.newline,
            )
        except EncodingError as exc:
            # 「この文字コードでは保存できない文字が本文にある」場合だけは、
            # 理由をそのまま出しても利用者は直しようがない（長い実際のファイルの
            # どこに絵文字や ♡ が紛れ込んだのかは探せない）。何行目に何がある
            # かを並べ、その場所を選択した状態にしてから知らせる。
            self._report_unencodable_characters(editor, path, exc)
            return False
        except Exception as exc:  # noqa: BLE001 - 失敗理由はそのまま利用者に見せる
            show_message_box(
                self,
                QMessageBox.Icon.Critical,
                "保存できません",
                f"{path} に保存できませんでした。\n\n{exc}",
            )
            return False

        editor.path = resolved
        editor.set_modified(False)
        self._refresh_file_watches()
        return True

    def _report_unencodable_characters(
        self, editor: EditorWidget, path: Path, exc: EncodingError
    ) -> None:
        """「その文字コードでは表せない文字」で保存できなかったことを知らせる。

        文字コードを保持したまま保存する (機能 8) 以上、Shift-JIS のファイルに
        絵文字や ♡・— を貼り付けると保存できなくなるのは避けられない。問題は
        **どこに紛れ込んだのかが分からない**ことなので、

        1. 該当する文字を「何行目の何」の形で並べ、
        2. 最初の 1 つを選択した状態にして（そのタブへも切り替える）、

        直しにいける状態にしてから知らせる。
        """
        if exc.spots:
            self._select_text_range(editor, *exc.spots[0])

        name = describe_encoding(exc.encoding)
        text = editor.toPlainText()
        lines = [
            f"{path} に保存できませんでした。",
            "",
            f"このファイルの文字コードは {name} です。この文字コードで表せない"
            "文字（絵文字・♡ などの記号・一部の漢字など）が本文にあるため、"
            "元の文字コードのままでは保存できません。",
        ]

        if exc.spots:
            listed = exc.spots[:MAX_LISTED_UNENCODABLE_SPOTS]
            count = (
                f" {len(exc.spots)} 個以上" if exc.truncated else f" {len(exc.spots)} 個"
            )
            lines.append("")
            lines.append(f"該当する文字が{count}あります:")
            lines.extend(
                f"　　{line_number_at(text, position)} 行目: 「{char}」"
                for position, char in listed
            )
            if len(exc.spots) > len(listed) or exc.truncated:
                lines.append("　　…")
            lines.append("")
            lines.append(
                "最初の 1 つを選択しました。別の文字に置き換えるか削除してから、"
                "もう一度保存してください。"
            )
        else:  # 位置を特定できなかった場合（通常は起きない）
            lines.extend(["", str(exc)])

        show_message_box(
            self,
            QMessageBox.Icon.Critical,
            "保存できません",
            "\n".join(lines),
        )

    def _select_text_range(self, editor: EditorWidget, position: int, text: str) -> None:
        """本文の ``position`` から ``text`` の分だけを選択して、そこへスクロールする。

        ``position`` は **Python の文字位置**（``toPlainText()`` 上）で受け取り、
        Qt の文書内位置（UTF-16 コード単位）へ直してからカーソルに渡す。
        絵文字などの BMP 外の文字は数え方が違うため、直さずに渡すと
        位置がずれる（詳しくは :mod:`editor_app.textsearch`）。
        """
        index = self.tabs.indexOf(editor)
        if index >= 0:
            self.tabs.setCurrentWidget(editor)

        document = editor.toPlainText()
        qt_position = position + surrogate_extra(document[:position])
        cursor = editor.textCursor()
        cursor.setPosition(qt_position)
        cursor.setPosition(
            qt_position + utf16_length(text), QTextCursor.MoveMode.KeepAnchor
        )
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        center_cursor_vertically(editor)

    def _save_dialog_path(self, editor: EditorWidget) -> Path:
        """「名前を付けて保存」ダイアログに最初に出すパス。

        既存ファイルならそのパス、無題タブなら「開く」と同じディレクトリに
        タブ名をファイル名として並べる。
        """
        if editor.path is not None:
            return editor.path
        return self._dialog_directory() / editor.display_name
