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

- 保存直前に外部変更が見つかったとき、**「保存できません」で終わらせない
  こと。** それでは編集内容を残す道が無く行き止まりになる。差分を見せて
  「読み込む / 上書きする / やめる」を選ばせる
  （:meth:`FileCommandsMixin._resolve_external_change`）。
- **保存直前の関門を足すときは、ダイアログが重ならないよう順番に評価する
  こと**（:meth:`FileCommandsMixin._write_editor` に 3 つ並んでいる）。
  また、**目安でしかない判定で保存を止めきってはいけない**——誤検知したら
  保存できなくなり、それ自体が損失になる
  （:meth:`FileCommandsMixin._confirm_suspicious_text`）。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・
``self.editors()``・``self.current_editor()``・``self._add_editor()``・
``self._refresh_file_watches()``・``self.open_windows()``・
``self._reload_editor_content()``（:mod:`editor_app.file_watch` 側。
外部変更の再読み込みを 1 か所に保つため、こちらでも同じものを使う）
に依存する。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from .backups import backup_file, describe_entry, list_backups, should_back_up
from .dialogs import (
    EXTERNAL_CHANGE_OVERWRITE,
    EXTERNAL_CHANGE_RELOAD,
    build_unified_diff,
    prompt_choice,
    prompt_destructive,
    prompt_external_change,
    prompt_suspicious_text,
    show_message_box,
)
from .editor import EditorWidget, center_cursor_vertically, document_text
from .fileio import (
    NBSP_FALLBACK,
    NO_BREAK_SPACE,
    SUSPICIOUS_CONTROL,
    SUSPICIOUS_HALFWIDTH_PUNCTUATION,
    SUSPICIOUS_MIXED_HALFWIDTH,
    EncodingError,
    describe_char,
    describe_encoding,
    describe_text_spot,
    downgrade_no_break_spaces,
    encode_text,
    encoding_choices,
    file_signature,
    find_suspicious_text,
    read_text_file_as,
    read_text_file_detail,
    resolve_path,
    write_encoded_file,
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

#: 「文字化けしたまま保存しようとしていないか」の警告
#: (:meth:`FileCommandsMixin._confirm_suspicious_text`) に該当箇所を何個まで
#: 並べるか。:data:`MAX_LISTED_UNENCODABLE_SPOTS` と同じ理由。
MAX_LISTED_SUSPICIOUS_SPOTS = 5

#: 該当箇所の抜粋をダイアログに出すときの、1 個あたりの文字数の上限。
#: 化けた本文は 1 行がとても長いことがあるので、そのまま並べるとダイアログが
#: 横に伸びきってしまう。
MAX_SUSPICIOUS_SPOT_LENGTH = 30

#: 「怪しい」理由ごとの、利用者への説明。
#: :func:`~editor_app.fileio.find_suspicious_text` が返す ``SUSPICIOUS_*``
#: に対応する。
SUSPICIOUS_REASON_TEXTS: dict[str, str] = {
    SUSPICIOUS_CONTROL: (
        "本文に、ふつうのテキストには入らない制御文字（ESC など）があります。\n"
        "JIS (ISO-2022-JP) のファイルを別の文字コードとして開いてしまったときに"
        "この形になります。"
    ),
    SUSPICIOUS_MIXED_HALFWIDTH: (
        "本文で、全角の日本語と半角カナが 1 文字おきに近い細かさで"
        "混ざっています。\n"
        "EUC-JP のファイルを Shift-JIS として開いてしまったときにこの形に"
        "なります。"
    ),
    SUSPICIOUS_HALFWIDTH_PUNCTUATION: (
        "本文に、半角の読点や中点（､ ･ など）が規則的に並んでいます。\n"
        "EUC-JP のひらがな・カタカナを Shift-JIS として開いてしまったときに"
        "この形になります。"
    ),
}

#: 保存直前の外部変更チェック (:meth:`FileCommandsMixin._check_external_change`)
#: の判定。「書いてよい」「書かずに片が付いた」「保存を取りやめる」の 3 通りが
#: あり、``bool`` では表せないので値で返す。
#:
#: - :data:`SAVE_PROCEED` … そのまま書き込んでよい（外部変更が無い / 利用者が
#:   「自分の内容のまま上書き保存する」を選んだ）
#: - :data:`SAVE_RESOLVED` … 書き込まないが、このタブの用は済んでいる
#:   （利用者が「ディスクの内容を読み込む」を選んだ）。終了処理の途中なら
#:   そのまま続けてよい、という意味になる
#: - :data:`SAVE_CANCELLED` … 保存しない。終了処理の途中ならそれも中断する
SAVE_PROCEED = "proceed"
SAVE_RESOLVED = "resolved"
SAVE_CANCELLED = "cancelled"


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
            content = read_text_file_detail(resolved)
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
        editor.encoding = content.encoding
        # 判定が拮抗していたなら、その事実もタブに預ける（ステータスバーの
        # 印になる）。開いた直後＝まだ編集していない、いちばん被害の小さい
        # うちに気づいてもらうための経路。
        editor.encoding_alternatives = content.alternatives
        editor.newline = content.newline
        editor.set_content(content.text)
        editor.path = resolved
        # 「このタブが知っているディスクの状態」の起点。以後の保存は
        # ここからの変化を見て、外部の変更を踏み潰さないようにする。
        editor.remember_disk_state()

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
    # 文字コードを指定して開き直す（機能 8 の逃げ道）
    # ------------------------------------------------------------------
    def reopen_file_with_encoding(self) -> bool:
        """現在のタブを、文字コードを選び直して開き直す。"""
        editor = self.current_editor()
        return False if editor is None else self.reopen_editor_with_encoding(editor)

    def reopen_editor_with_encoding(self, editor: EditorWidget) -> bool:
        """指定タブを、選んだ文字コードでディスクから読み直す（成功したら True）。

        「文字コードを指定して**保存**」の“開く”版。文字化けに気づいた
        利用者が、その場で正しい文字コードに切り替えられるようにするための
        逃げ道（ステータスバーの拮抗の印
        :data:`~editor_app.main_window.AMBIGUOUS_ENCODING_MARK` を見て
        「では EUC-JP で開き直そう」とできるように、印とセットで用意した）。

        - **初期選択は、拮抗している別候補があればそれ**。判定が拮抗した
          ファイルで印を見た利用者が、開いてそのまま OK を押せば
          もう一方の読み方になる（いちばん多い使い方を最短にする）。
          拮抗が無ければ今の文字コードを初期選択にする。
        - まだ保存していないタブには読み直す元が無いので、その旨だけ伝える。
        - **未保存の編集は捨てることになる**ので、必ず確認を挟む
          （:mod:`editor_app.file_watch` の「外部変更の再読み込み」と同じ扱い）。
        - 選んだ文字コードで読めなければ、**タブには一切触らず**理由を伝える
          （読めないものを空にしてしまうと、それこそ内容を失う）。
        - 置き換えは
          :meth:`~editor_app.file_watch.FileWatchMixin._reload_editor_content`
          を使い回す（カーソル位置の保持・未保存マーク・見分け札の更新が
          再読み込みと食い違わないようにするため）。
        """
        if editor.path is None:
            show_message_box(
                self,
                QMessageBox.Icon.Information,
                "開き直せません",
                f"「{editor.display_name}」はまだファイルに保存されていないため、"
                "読み直す元がありません。\n\n"
                "先に「名前を付けて保存」で保存してください。",
            )
            return False

        choices = encoding_choices(editor.encoding)
        alternatives = [enc for enc in editor.encoding_alternatives if enc in choices]
        initial = choices.index(alternatives[0] if alternatives else editor.encoding)
        chosen = prompt_choice(
            self,
            "文字コードを指定して開き直す",
            f"「{editor.display_name}」を読み直す文字コードを選んでください:",
            [describe_encoding(name) for name in choices],
            initial,
        )
        if chosen is None:
            return False
        encoding = choices[chosen]

        if editor.is_modified:
            answer = show_message_box(
                self,
                QMessageBox.Icon.Question,
                "開き直し",
                f"「{editor.display_name}」には未保存の変更があります。\n"
                f"{describe_encoding(encoding)} で開き直すと、その編集は破棄されます。"
                "\n\n開き直しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        try:
            text, newline = read_text_file_as(editor.path, encoding)
        except Exception as exc:  # noqa: BLE001 - 失敗理由はそのまま利用者に見せる
            show_message_box(
                self,
                QMessageBox.Icon.Critical,
                "開き直せません",
                f"「{editor.display_name}」を {describe_encoding(encoding)} として"
                f"読み直せませんでした。\n\n{exc}\n\n"
                "タブの内容はそのままにしてあります。",
            )
            return False

        # 利用者が自分で選んだ以上、判定の拮抗は解決している（印を消す）。
        self._reload_editor_content(editor, text, encoding, newline)
        return True

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

    def save_file_with_encoding(self) -> bool:
        """現在のタブを、文字コードを選び直して保存する。"""
        editor = self.current_editor()
        return False if editor is None else self.save_editor_with_encoding(editor)

    def save_editor(self, editor: EditorWidget) -> bool:
        """指定タブを保存する。まだ保存先が無ければ「名前を付けて保存」に回す。

        文字コード・改行コードは読み込んだときのものをそのまま使う (機能 8)。

        **変更が無いタブでは、何もせずに成功として返す**（引き継ぎ ⑧）。
        VS Code・Notepad++・gedit などの主要なエディタと同じく、上書き保存は
        変更が無ければディスクに触らない。利用者は rclone / NAS の同期を
        使っており、無用な書き込みはタイムスタンプだけが動いた無駄な差分に
        なってしまうため。

        判定は :meth:`_write_editor` の 3 つの関門（③ 保存直前の外部変更
        チェック・④ 差分表示・⑥ 文字化けの疑いの警告）や ⑦ の初回保存前
        バックアップ**より手前**で行う。それらの副作用（ダイアログ・
        バックアップの書き込み・mtime の変化）も一切起こさない、完全な
        no-op にするため。

        このガードを掛けるのは上書き保存だけで、
        :meth:`save_editor_as`（名前を付けて保存）と
        :meth:`save_editor_with_encoding`（文字コードを指定して保存）は
        変更が無くても従来どおり書き込む。どちらも「新しい保存先へ書きたい」
        「別の文字コードで書き直したい」という、本文の変更とは別に意味のある
        操作だから。
        """
        if editor.path is None:
            return self.save_editor_as(editor)
        if not editor.is_modified:
            return True
        return self._write_editor(editor, editor.path)

    def save_editor_with_encoding(self, editor: EditorWidget) -> bool:
        """文字コードを選び直して保存する（保存できたら True）。

        文字コードを保持したまま保存する (機能 8) と、Shift-JIS のファイルに
        絵文字や ♡ を貼り付けた時点で保存できなくなる。その逃げ道として、
        **この 1 回だけでなく以後もその文字コードで保存する**形で選び直せる
        ようにしたもの（実機フィードバックによる追加）。

        - 初期選択は今のタブの文字コード（＝そのまま OK を押しても
          今までと同じ結果になる）。
        - 無題タブには保存先が無いので、「名前を付けて保存」と同じ流れで
          先に保存先を尋ねる。
        - 選んだ文字コードで**表現できない文字**があれば、上書き保存と
          同じ「保存できません」の一覧を出して保存しない
          （:meth:`_report_unencodable_characters`）。
        """
        choices = encoding_choices(editor.encoding)
        chosen = prompt_choice(
            self,
            "文字コードを指定して保存",
            f"「{editor.display_name}」を保存する文字コードを選んでください:",
            [describe_encoding(name) for name in choices],
            choices.index(editor.encoding),
        )
        if chosen is None:
            return False

        encoding = choices[chosen]
        if editor.path is None:
            return self.save_editor_as(editor, encoding=encoding)
        return self._write_editor(editor, editor.path, encoding=encoding)

    def save_editor_as(self, editor: EditorWidget, *, encoding: str | None = None) -> bool:
        """保存先をダイアログで選んでから保存する。キャンセルなら False。

        選んだ保存先を **別のタブが既に開いている場合は保存しない**
        （:meth:`_warn_path_open_in_another_tab` を参照）。

        ``encoding`` を渡すと、そのタブの文字コードではなくそちらで書き出す
        （「文字コードを指定して保存」を無題タブに対して行った場合）。
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
        return self._write_editor(editor, target, encoding=encoding)

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

    def _write_editor(
        self, editor: EditorWidget, path: Path, *, encoding: str | None = None
    ) -> bool:
        """実際にファイルへ書き出し、成功したらタブの状態を更新する。

        ディスクへ書くのはここ 1 か所だけにしてある（上書き保存・名前を
        付けて保存・文字コードを指定して保存が全てここへ合流する）。

        **書く前に 3 つの関門があり、この順に確かめる**。どれか 1 つでも
        止めるべき理由があれば書き込まない。**ダイアログが 2 つ重ならない
        よう、順に評価して最初に引っかかったものだけ**を出す:

        1. 外部変更（:meth:`_check_external_change`）
        2. その文字コードで表せない文字（:func:`~editor_app.fileio.encode_text`
           が投げる :class:`~editor_app.fileio.EncodingError`）
        3. 文字化けしたまま保存しようとしていないか
           （:meth:`_confirm_suspicious_text`）

        関門 2 の**手前**で、「改行しない空白」(NBSP) だけは表せない文字コード
        でも止めずに普通の半角空白へ落とす
        （:func:`~editor_app.fileio.downgrade_no_break_spaces`。特例の理由は
        そちらに書いてある）。**本文の側を揃えるのは書き込みが成功した後**
        （:meth:`_apply_no_break_space_downgrade`）。

        **3 つの関門を全て通った後、書き込む直前に**、上書きされて消える
        中身を控える（:meth:`_backup_before_first_write`。引き継ぎ ⑦）。
        取りやめた保存で控えが増えないよう、順番はここでなければならない。

        2 と 3 の間に書き込みを挟まないために、``encode`` と書き出しを
        :func:`~editor_app.fileio.encode_text` と
        :func:`~editor_app.fileio.write_encoded_file` に分けて呼んでいる
        （:func:`~editor_app.fileio.write_text_file` は両方やってしまうので
        ここでは使わない。同じ本文を 2 度 encode せずに間へ確認を挟める）。

        ``encoding`` を渡した場合は、そのタブの文字コードではなくそちらで
        書き出し、**書けたらタブの文字コードもそれに入れ替える**
        （以後の上書き保存も新しい文字コードで行われる）。

        **戻り値の True は「書けた」ではなく「このタブの用は済んだ」の意味**
        になる場合がある。保存直前の外部変更チェックで利用者が「ディスクの
        内容を読み込む」を選んだときは、書き込まずに True を返す
        （:data:`SAVE_RESOLVED`）。呼び出し側（タブを閉じる・アプリを終了
        する流れ）が見たいのは「このタブを置いて先へ進んでよいか」であり、
        読み直したタブは未保存の変更を持たないため先へ進んでよい。
        """
        resolved = resolve_path(path)
        chosen_encoding = encoding is not None and encoding != editor.encoding
        target_encoding = editor.encoding if encoding is None else encoding

        decision = self._check_external_change(editor, resolved)
        if decision != SAVE_PROCEED:
            return decision == SAVE_RESOLVED

        text = document_text(editor)
        # 「改行しない空白」(NBSP) だけは、表せない文字コードでも止めずに
        # 普通の半角空白へ落とす（利用者の指示による特例。詳しくは
        # :func:`~editor_app.fileio.downgrade_no_break_spaces`）。本文側も
        # 後で揃えるが、**それは書けた後**（下）——ここで先に書き換えると、
        # 保存を取りやめたのに本文だけ変わってしまい、UTF-8 で保存し直す
        # 逃げ道を選んでも NBSP が戻らなくなる。
        text, downgraded_nbsp = downgrade_no_break_spaces(text, target_encoding)
        try:
            data = encode_text(text, target_encoding, editor.newline)
        except EncodingError as exc:
            # 「この文字コードでは保存できない文字が本文にある」場合だけは、
            # 理由をそのまま出しても利用者は直しようがない（長い実際のファイルの
            # どこに絵文字や ♡ が紛れ込んだのかは探せない）。何行目に何がある
            # かを並べ、その場所を選択した状態にしてから知らせる。
            self._report_unencodable_characters(editor, path, exc, chosen=chosen_encoding)
            return False
        except Exception as exc:  # noqa: BLE001 - 失敗理由はそのまま利用者に見せる
            self._report_save_failure(path, exc)
            return False

        if not self._confirm_suspicious_text(editor, path, text, target_encoding):
            return False

        self._backup_before_first_write(editor, resolved)

        try:
            write_encoded_file(resolved, data)
        except Exception as exc:  # noqa: BLE001 - 失敗理由はそのまま利用者に見せる
            self._report_save_failure(path, exc)
            return False

        # 書けたので、本文の側も今ディスクに書いた姿へ揃える。揃えないと
        # 本文とディスクが永久に食い違い、**触ってもいないのに毎回
        # 「再読み込みしますか？」**になる（`file_watch` はこの 2 つを
        # 突き合わせて判断している）。保存直前の差分表示にも毎回出てしまう。
        self._apply_no_break_space_downgrade(editor, downgraded_nbsp)

        editor.path = resolved
        editor.encoding = target_encoding
        # 書けた以上、このタブの文字コードは「判定した推測」ではなく
        # 「今このバイト列を書いた文字コード」そのもの。拮抗の印は用済み
        # （残すと、利用者が自分で選び直した後もいつまでも「?」が出続ける）。
        editor.encoding_alternatives = ()
        # 次の保存が見比べる基準は「自分が今書いた状態」。ここを忘れると、
        # 自分の保存そのものを外部変更と見なして次の保存が止まる。
        editor.remember_disk_state()
        editor.set_modified(False)
        self._refresh_file_watches()
        return True

    def _apply_no_break_space_downgrade(
        self, editor: EditorWidget, positions: list[int]
    ) -> None:
        """書き出した後の本文から、NBSP を普通の半角空白へ置き換える。

        ``positions`` は :func:`~editor_app.fileio.downgrade_no_break_spaces`
        が返した **Python の文字位置**。Qt の文書内位置（UTF-16 コード単位）へ
        直してから触る（絵文字などの BMP 外の文字があると数え方がずれる。
        詳しくは :mod:`editor_app.textsearch`）。

        **呼ぶのは書き込みが成功した後**。ディスクとタブの中身を揃えるのが
        目的なので、書けていないのに本文だけ変えてはいけない。

        Undo 一回でまとめて戻せるよう ``beginEditBlock()`` でくくる。
        戻せるままにしてあるのは、落とすのが「黙って」である以上、
        利用者が気づいたときに取り返せる道を残しておくため
        （戻したところでディスクは半角空白のままなので、ファイルは壊れない）。

        置き換える直前に「そこが本当に NBSP か」を確かめ、違えば飛ばす。
        位置を数えてから書き込むまでの間に確認ダイアログを挟むので、
        その間に本文が変わっていた場合に**関係ない場所を壊さない**ための歯止め
        （:meth:`~editor_app.search_replace.SearchReplaceMixin._apply_replacement`
        の ``expected`` と同じ考え方）。
        """
        if not positions:
            return

        document = document_text(editor)
        cursor = editor.textCursor()
        cursor.beginEditBlock()
        try:
            # 先頭から順に、間に挟まる BMP 外の文字の分だけ位置をずらしていく
            # （位置ごとに先頭から数え直すと、件数が多いときに二乗になる）。
            extra = 0
            previous = 0
            for position in positions:
                extra += surrogate_extra(document[previous:position])
                previous = position
                qt_position = position + extra
                cursor.setPosition(qt_position)
                cursor.setPosition(
                    qt_position + 1, QTextCursor.MoveMode.KeepAnchor
                )
                if cursor.selectedText() != NO_BREAK_SPACE:
                    continue
                cursor.insertText(NBSP_FALLBACK)
        finally:
            cursor.endEditBlock()

    def _check_external_change(self, editor: EditorWidget, path: Path) -> str:
        """上書きしようとしている先が、読み込み後に外部で変わっていないか調べる。

        戻り値は :data:`SAVE_PROCEED` / :data:`SAVE_RESOLVED` /
        :data:`SAVE_CANCELLED` のいずれか。

        NAS や rclone のようにネットワーク越しのファイルシステムでは、
        :mod:`editor_app.file_watch` の ``QFileSystemWatcher`` に変更の通知が
        そもそも届かない。監視だけに頼ると、別の PC が書き換えたファイルを
        黙って踏み潰してしまうため、**書く直前にもう一度ディスクを見て**
        読み込んだときと同じかどうかを確かめる。変わっていた場合の見せ方は
        :meth:`_resolve_external_change` が受け持つ。

        **誤検知が続いて作業にならない場合に外せるよう、判定はこの
        メソッド 1 つに閉じてある**（先頭で :data:`SAVE_PROCEED` を返せば
        チェックごと無効にできる）。

        対象外にするもの:

        - **別のパスへの「名前を付けて保存」**。控えてある見分け札はこのタブが
          読み込んだ元ファイルのものなので、別のファイルと比べても意味が無い
          （比べてしまうと、必ず不一致になって保存できなくなる）。既に開いて
          いるファイルへ保存しようとした場合は
          :meth:`_warn_path_open_in_another_tab` が別途止める。
        - **保存先が今は存在しないとき**。無題タブの初回保存や、外部で消された
          後の保存がこれにあたる。新しく作るか、消えたファイルを作り直すだけで、
          誰の変更も失わない。
        - **控えが無いとき**。見比べる基準が無い以上「変わった」とは言えない
          ので通す（止めると、そのタブは以後どうやっても保存できなくなる）。
          保存先が無ければ上の条件で先に通るので、ここに掛かるのは
          「**保存先は在るのに控えだけ無い**」——控えを取ろうとした瞬間に
          ファイルが無かった場合だけ。

        ``stat`` は増える（実測で、開く 5→6 回・保存 6→8 回。保存は書く前の
        確認と、書いた後の控え直しで 2 回）。ただし**増えるのは 1 操作につき
        定数回**で、開いているタブの数には比例しない
        （`docs/performance-notes.md` が戒めているのは「タブの数 ×
        ファイルを開くたび」の形。`tests/test_open_filesystem_cost.py` が
        比例しないことを守っている）。
        """
        if editor.path is None or path != editor.path:
            return SAVE_PROCEED

        known = editor.disk_signature
        if known is None:
            return SAVE_PROCEED

        current = file_signature(path)
        if current is None or current == known:
            return SAVE_PROCEED

        return self._resolve_external_change(editor, path)

    def _resolve_external_change(self, editor: EditorWidget, path: Path) -> str:
        """外部変更が見つかったときに、差分を見せて行き先を決めてもらう。

        以前は「保存されませんでした」と伝えるだけで、そのタブはどうやっても
        保存できない行き止まりになっていた。利用者の指定により、**ディスクの
        内容を見てから決められる**ようにしたもの（実機フィードバック）。
        2 段階（見るか尋ねる → 別のダイアログで差分）にはせず、**1 つの
        ダイアログ**で差分と選択肢を同時に出す。

        比べるのは **(a) 今この瞬間にディスクから読み直した内容** と
        **(b) タブの今の本文（＝これから書こうとしていた内容）**。
        検知した時点からさらに変わっている可能性があるので、見せる直前に
        読み直す。読み直しは :func:`~editor_app.fileio.read_text_file` を
        通す（＝開くときとまったく同じ文字コード判定の経路。ここだけ別の
        読み方をすると、差分に出た内容と読み込んだ内容が食い違う）。

        差分を出せないときは、クラッシュさせずに簡単なメッセージへ落とす:

        - **ディスクからファイルが消えていた** … 比べる相手が無いので、
          その旨だけを伝える（:mod:`editor_app.file_watch` の「消えた
          ファイルは再読み込みしようがない」と同じ考え方）。
        - **文字コード判定に失敗する等で読めない** … 従来どおり
          「保存しませんでした」だけを伝える。

        「ディスクの内容を読み込む」を選ばれたときは、
        :meth:`~editor_app.file_watch.FileWatchMixin._reload_editor_content`
        を**そのまま使い回す**（監視が拾って読み直したときと、カーソル位置の
        保持・未保存マークのクリア・見分け札の更新が食い違わないように、
        新しく作り直さないこと）。
        """
        try:
            content = read_text_file_detail(path)
        except FileNotFoundError:
            show_message_box(
                self,
                QMessageBox.Icon.Warning,
                "保存しませんでした",
                f"「{path.name}」は、開いたあとで外部で変更され、"
                "今はディスク上に見つかりません。\n\n"
                "そのまま保存すると新しいファイルとして作り直すことになるため、"
                "保存を中止しました。\n\n"
                "この内容を残したい場合は、「ファイル」→「名前を付けて保存」で"
                "保存先を選び直してください。",
            )
            return SAVE_CANCELLED
        except Exception:  # noqa: BLE001 - 読めない理由は問わず、差分無しに落とす
            self._warn_external_change_without_diff(path)
            return SAVE_CANCELLED

        choice = prompt_external_change(
            self,
            name=path.name,
            diff_text=build_unified_diff(content.text, document_text(editor)),
        )
        if choice == EXTERNAL_CHANGE_RELOAD:
            self._reload_editor_content(
                editor,
                content.text,
                content.encoding,
                content.newline,
                alternatives=content.alternatives,
            )
            return SAVE_RESOLVED
        if choice == EXTERNAL_CHANGE_OVERWRITE:
            # この 1 回だけチェックを通す。書けたあとの見分け札の取り直しは
            # :meth:`_write_editor` が行うので、以後は普通に保存できる。
            return SAVE_PROCEED
        return SAVE_CANCELLED

    def _confirm_suspicious_text(
        self, editor: EditorWidget, path: Path, text: str, encoding: str
    ) -> bool:
        """文字化けしたまま保存しようとしていないか確かめる。続けてよければ True。

        引き継ぎ ⑥（利用者の依頼）。開いた時点で判定の拮抗を知らせる印が
        一の矢だが、**利用者の実体験では、文字化けは編集した後に気づくことが
        多い**。編集した後には「本来どの文字コードだったか」という正解が
        もう残っていないので、ここでできるのは**確実な検知ではなく
        「怪しい」という目安**だけ——この前提は利用者に説明して了承済み。

        判定そのものは Qt に依存しないので
        :func:`~editor_app.fileio.find_suspicious_text` が持つ。ここは
        「どう見せるか」だけを受け持ち、:meth:`_report_unencodable_characters`
        と同じ作り（何行目の何かを並べ、最初の 1 つを選択してからダイアログを
        出す）に揃えてある。

        **誤検知が続いて作業にならない場合に外せるよう、この判定を通る道は
        ここ 1 つに閉じてある**（:meth:`_check_external_change` と同じ設計。
        先頭で ``return True`` すればチェックごと無効にできる）。
        既知の誤検知の形は :func:`~editor_app.fileio.find_suspicious_text`
        の説明にある。
        """
        found = find_suspicious_text(text, encoding)
        if found is None:
            return True

        if found.spots:
            self._select_text_range(editor, *found.spots[0])

        listed = found.spots[:MAX_LISTED_SUSPICIOUS_SPOTS]
        count = f" {len(found.spots)} 個以上" if found.truncated else f" {len(found.spots)} 個"
        lines = [
            f"「{path.name}」の本文が、文字化けしたままに見えます。",
            "",
            SUSPICIOUS_REASON_TEXTS[found.reason],
            "",
            "このまま保存すると、化けた状態でファイルが上書きされ、"
            "元の内容に戻せなくなります。",
        ]
        if listed:
            lines.append("")
            lines.append(f"それらしい箇所が{count}あります:")
            lines.extend(
                f"　　{line_number_at(text, position)} 行目: "
                f"「{self._shorten_spot(excerpt)}」"
                for position, excerpt in listed
            )
            if len(found.spots) > len(listed) or found.truncated:
                lines.append("　　…")
            lines.append("")
            lines.append("最初の 1 つを選択しました。")
        lines.extend(
            [
                "",
                "元のファイルが別の文字コードだった場合は、キャンセルしてから"
                "「ファイル」→「文字コードを指定して開き直す」でやり直してください"
                "（このタブの編集内容は失われます）。",
                "",
                "これは目安の警告で、外れることもあります。"
                "本文が化けていないと分かっているときは、"
                "「このまま保存する」を押してください。",
            ]
        )

        return prompt_suspicious_text(self, "文字化けしているかもしれません", "\n".join(lines))

    @staticmethod
    def _shorten_spot(excerpt: str) -> str:
        """該当箇所の抜粋を、ダイアログに並べられる長さへ切り詰める。"""
        shown = describe_text_spot(excerpt)
        if len(shown) <= MAX_SUSPICIOUS_SPOT_LENGTH:
            return shown
        return shown[:MAX_SUSPICIOUS_SPOT_LENGTH] + "…"

    # ------------------------------------------------------------------
    # 上書き前の控えと、そこからの復元（引き継ぎ ⑦。:mod:`editor_app.backups`）
    # ------------------------------------------------------------------
    def _backup_before_first_write(self, editor: EditorWidget, path: Path) -> None:
        """このタブがこのファイルへ**初めて書き込む直前**に、今の中身を控える。

        引き継ぎ ⑦（利用者の依頼）。開いた時点の印（⑤）と保存直前の警告
        （⑥）はどちらも**目安**で取りこぼしがあるため、**判定の当たり外れに
        一切依存しない最後の安全網**として、上書きされて消える前のバイト列を
        そのまま控えておく。

        ここは「いつ控えるか」だけを決める。実際の複製は Qt 非依存の
        :func:`~editor_app.backups.backup_file` が行う（**テキストとして
        読み書きし直さない**のがその要）。

        控えるのは次を全て満たすときだけ:

        - **日本語レガシー文字コードのタブ**（:func:`~editor_app.backups.should_back_up`）。
          文字コードの誤判定でファイルが壊れうるのはこの範囲だけ。
        - **このタブがこのファイルをまだ控えていない**
          （:attr:`~editor_app.editor.EditorWidget.backed_up_path`）。
          2 回目以降の保存で控え直すと、**上書きされた後の姿ばかりが溜まり、
          いちばん戻したい「開いたときの姿」が容量の掃除で押し出される**。

        **控えが取れなくても保存は続ける**（例外を握り潰す）。キャッシュ
        置き場が書けないというだけで保存できなくなる方が、利用者にとっては
        大きな損失になるため。「印を付けるのは控えられたときだけ」なので、
        次の保存でもう一度試みる。
        """
        if not should_back_up(editor.encoding) or editor.backed_up_path == path:
            return
        try:
            backup_file(path)
        except OSError:
            return
        editor.backed_up_path = path

    def restore_from_backup(self) -> bool:
        """現在のタブを、上書き前の控えから復元する（「ファイル」メニュー）。"""
        editor = self.current_editor()
        return False if editor is None else self.restore_editor_from_backup(editor)

    def restore_editor_from_backup(self, editor: EditorWidget) -> bool:
        """控えを選ばせ、確認してから、そのバイト列でファイルを戻す。

        引き継ぎ ⑦-B。**利用者の明確な指示により、復元は必ずこのアプリの
        中で完結させる**（「バックアップフォルダを開くから、あとは手作業で
        コピー&ペーストしてください」にはしない。手作業は、それ自体が
        復元を失敗させる事故のもとになるため）。

        流れ:

        1. このタブのファイルに対応する控えを**新しい順**に並べて選ばせる
           （:func:`~editor_app.backups.list_backups`）。1 件も無ければ
           その旨だけを伝えて何もしない。
        2. **「今の内容は失われる」ことを確認するダイアログ**を必ず挟む
           （一覧で誤って別の行を選んだまま押してしまう事故を防ぐ）。
        3. **今のファイルの中身も、7-A と同じ仕組みで先に控える**
           （＝この復元操作自体も取り消せる）。ここが失敗したら復元しない
           ——取り消せない上書きになってしまうため。
        4. 選んだ控えの**生のバイト列をそのまま**（再エンコードせずに）
           元のファイルへ書き戻す。
        5. そのファイルを開き直してタブへ入れる。読み直しは
           :meth:`~editor_app.file_watch.FileWatchMixin._reload_editor_content`
           を**そのまま使い回す**（外部変更の再読み込みと経路を分けない）。
           元のバイト列に戻っているので、文字コードの判定もやり直せば
           正しく付く。
        """
        if editor.path is None:
            self._warn_no_backup(editor.display_name, untitled=True)
            return False

        path = resolve_path(editor.path)
        entries = list_backups(path)
        if not entries:
            self._warn_no_backup(path.name)
            return False

        chosen = prompt_choice(
            self,
            "バックアップから復元",
            f"「{path.name}」を、どの時点の内容に戻しますか?\n"
            "（保存で上書きされる直前の内容を控えてあります）",
            [describe_entry(entry) for entry in entries],
            0,
        )
        if chosen is None:
            return False

        entry = entries[chosen]
        if not prompt_destructive(
            self,
            "バックアップから復元",
            f"{entry.saved_at.strftime('%Y-%m-%d %H:%M:%S')} の控えで、"
            f"「{path.name}」の今の内容を置き換えます。\n"
            "今の内容は失われます。\n\n"
            "（今の内容も控えてから置き換えるので、この復元自体もやり直せます）",
            "この控えに戻す",
        ):
            return False

        try:
            data = entry.data_path.read_bytes()
        except OSError as exc:  # 一覧に出してから消えた等
            self._report_restore_failure(path, exc)
            return False

        try:
            # 復元で消える「今の中身」も先に控える。7-A と同じ関数を通す
            # （復元だけの特別扱いを増やさない）。ここは握り潰さない——
            # 控えられないまま上書きすると、やり直せない操作になる。
            backup_file(path)
        except OSError as exc:  # キャッシュ置き場が書けない等
            self._report_restore_failure(path, exc)
            return False
        editor.backed_up_path = path

        try:
            write_encoded_file(path, data)
        except Exception as exc:  # noqa: BLE001 - 失敗理由はそのまま利用者に見せる
            self._report_restore_failure(path, exc)
            return False

        try:
            content = read_text_file_detail(path)
        except Exception as exc:  # noqa: BLE001 - 書き戻せたのに読み直せない場合
            # **ここだけは「ファイルは変更していません」ではない。** 書き戻し
            # 自体は済んでいるので、ファイルの中身は控えのとおりになって
            # いる。タブへ読み込めなかっただけであることを、そのまま伝える。
            show_message_box(
                self,
                QMessageBox.Icon.Critical,
                "復元できません",
                f"「{path.name}」を控えの内容に書き戻しましたが、"
                f"タブへ読み込み直せませんでした。\n\n{exc}\n\n"
                "ファイル自体は控えの内容に戻っています。"
                "このタブを閉じて開き直してください"
                "（このタブの表示は書き戻す前のままです。"
                "そのまま保存するとファイルが元へ戻ってしまいます）。",
            )
            return False

        self._reload_editor_content(
            editor,
            content.text,
            content.encoding,
            content.newline,
            alternatives=content.alternatives,
        )
        return True

    def _warn_no_backup(self, name: str, *, untitled: bool = False) -> None:
        """そのタブに戻せる控えが無いことを伝える。"""
        reason = (
            "このタブはまだファイルに保存されていないので、"
            "上書きされた内容もありません。"
            if untitled
            else "このファイルには、まだ控えがありません。\n\n"
            "控えを取るのは、日本語の文字コード"
            "（Shift-JIS・EUC-JP・JIS）のファイルを開いて、"
            "そのタブで最初に保存する直前の 1 回です。"
        )
        show_message_box(
            self,
            QMessageBox.Icon.Information,
            "バックアップから復元",
            f"「{name}」に戻せる控えはありません。\n\n{reason}",
        )

    def _report_restore_failure(self, path: Path, exc: BaseException) -> None:
        """復元が途中で失敗したことを、理由をそのまま添えて知らせる。"""
        show_message_box(
            self,
            QMessageBox.Icon.Critical,
            "復元できません",
            f"「{path.name}」をバックアップから復元できませんでした。\n\n{exc}\n\n"
            "ファイルは変更していません。",
        )

    def _report_save_failure(self, path: Path, exc: BaseException) -> None:
        """保存が失敗したことを、理由をそのまま添えて知らせる。"""
        show_message_box(
            self,
            QMessageBox.Icon.Critical,
            "保存できません",
            f"{path} に保存できませんでした。\n\n{exc}",
        )

    def _warn_external_change_without_diff(self, path: Path) -> None:
        """差分を出せなかったときの、従来どおりの「保存しませんでした」。"""
        show_message_box(
            self,
            QMessageBox.Icon.Warning,
            "保存しませんでした",
            f"「{path.name}」は、開いたあとで外部で変更されています。\n\n"
            "このまま保存すると、その変更が失われます。"
            "保存を中止しました。\n\n"
            "編集内容を残したい場合は、「ファイル」→「名前を付けて保存」で"
            "別のファイルに保存してください。",
        )

    def _report_unencodable_characters(
        self,
        editor: EditorWidget,
        path: Path,
        exc: EncodingError,
        *,
        chosen: bool = False,
    ) -> None:
        """「その文字コードでは表せない文字」で保存できなかったことを知らせる。

        文字コードを保持したまま保存する (機能 8) 以上、Shift-JIS のファイルに
        絵文字や ♡・— を貼り付けると保存できなくなるのは避けられない。問題は
        **どこに紛れ込んだのかが分からない**ことなので、

        1. 該当する文字を「何行目の何」の形で並べ、
        2. 最初の 1 つを選択した状態にして（そのタブへも切り替える）、

        直しにいける状態にしてから知らせる。

        ``chosen`` は「文字コードを指定して保存」で**利用者が今この文字
        コードを選んだ**場合に立てる。同じダイアログを使い回すが、そのとき
        だけは「このファイルの文字コードは〜」ではなく「選んだ文字コードでは
        〜」と言わないと事実に合わない（元の文字コードは別にある）。
        逆に上書き保存で来た場合は、逃げ道としてその機能を案内する。
        """
        if exc.spots:
            self._select_text_range(editor, *exc.spots[0])

        name = describe_encoding(exc.encoding)
        text = document_text(editor)
        if chosen:
            reason = (
                f"選んだ文字コード {name} では表せない文字"
                "（絵文字・♡ などの記号・一部の漢字など）が本文にあるため、"
                "この文字コードでは保存できません。"
            )
        else:
            reason = (
                f"このファイルの文字コードは {name} です。この文字コードで表せない"
                "文字（絵文字・♡ などの記号・一部の漢字など）が本文にあるため、"
                "元の文字コードのままでは保存できません。"
            )
        lines = [
            f"{path} に保存できませんでした。",
            "",
            reason,
        ]

        if exc.spots:
            listed = exc.spots[:MAX_LISTED_UNENCODABLE_SPOTS]
            count = (
                f" {len(exc.spots)} 個以上" if exc.truncated else f" {len(exc.spots)} 個"
            )
            lines.append("")
            lines.append(f"該当する文字が{count}あります:")
            lines.extend(
                f"　　{line_number_at(text, position)} 行目: 「{describe_char(char)}」"
                for position, char in listed
            )
            if len(exc.spots) > len(listed) or exc.truncated:
                lines.append("　　…")
            lines.append("")
            lines.append(
                "最初の 1 つを選択しました。別の文字に置き換えるか削除してから、"
                "もう一度保存してください。"
            )
            if not chosen:
                lines.append(
                    "そのまま残したい場合は、「ファイル」→"
                    "「文字コードを指定して保存」で UTF-8 を選ぶと保存できます"
                    "（以後このファイルは UTF-8 で保存されます）。"
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

        ``position`` は **Python の文字位置**（``document_text()`` 上）で受け取り、
        Qt の文書内位置（UTF-16 コード単位）へ直してからカーソルに渡す。
        絵文字などの BMP 外の文字は数え方が違うため、直さずに渡すと
        位置がずれる（詳しくは :mod:`editor_app.textsearch`）。
        """
        index = self.tabs.indexOf(editor)
        if index >= 0:
            self.tabs.setCurrentWidget(editor)

        document = document_text(editor)
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
