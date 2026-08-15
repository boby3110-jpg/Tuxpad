"""開いているファイルの外部変更を監視するミックスイン.

``MainWindow`` から「開いているファイルが、アプリの外で書き換わったときの
処理」だけを切り出したもの（`view_settings.py` と同じ切り出し方）。
別の PC・別のエディタ・同期ソフト (rclone/NAS) が同じファイルを書き換えた
ときに、黙って古い本文のままにせず、再読み込みを提案するためのもの。

流れは 4 段階になっている。

1. :meth:`FileWatchMixin._refresh_file_watches` … 開いているタブのパスを
   ``QFileSystemWatcher`` の監視対象と一致させる（開く・保存・タブを閉じる・
   タブがウィンドウ間を移動する、のたびに呼ぶ）
2. :meth:`FileWatchMixin._on_watched_file_changed` … 変更の通知を受け取り、
   保留リストに積んでデバウンス用のタイマーを起こす
3. :meth:`FileWatchMixin._process_pending_reloads` … デバウンス後に、
   溜まったパスをまとめて処理する
4. :meth:`FileWatchMixin._maybe_reload_changed_file` … ディスクの内容と
   本文を見比べて、本当に違うときだけ確認ダイアログを出す

**このモジュールを触るときの注意**（過去に実機で不具合として出たもの）:

- ``stat`` を増やさないこと。rclone/FUSE のような遅いファイルシステムでは
  ``stat`` 1 回が高くつき、「タブが増えるほどファイルを開くのが遅くなる」
  形で効いてしまう（`docs/performance-notes.md`）。
- 既に閉じたファイルの通知が遅れて届くことがある。開いているかどうかを
  必ず確かめてから扱うこと。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・
``self.editors()``・``self.find_editor_by_path()`` に依存する。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtWidgets import QMessageBox

from .dialogs import show_message_box
from .editor import EditorWidget
from .fileio import read_text_file, resolve_path

#: 開いているファイルが外部で変わってから、再読み込みの確認を出すまでの
#: 待ち時間 (ms)。連続した書き込みや temp→rename 方式の保存を 1 回に
#: まとめるため、少しだけ待つ（利用者の「1〜2 秒ほどで」に合わせる）。
RELOAD_CHECK_DELAY_MS = 1200


class FileWatchMixin:
    """開いているファイルの外部変更を監視して再読み込みを提案する。"""

    def _setup_file_watching(self) -> None:
        """監視用の ``QFileSystemWatcher`` とタイマーを用意する。

        ``MainWindow.__init__`` から、タブ (``self.tabs``) を作ったあとに
        1 度だけ呼ぶこと。
        """
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.fileChanged.connect(self._on_watched_file_changed)
        self._reload_check_timer = QTimer(self)
        self._reload_check_timer.setSingleShot(True)
        self._reload_check_timer.setInterval(RELOAD_CHECK_DELAY_MS)
        self._reload_check_timer.timeout.connect(self._process_pending_reloads)
        #: 変更を検知したが、まだ確認ダイアログを出していないファイルのパス。
        self._pending_reload_paths: set[str] = set()
        #: 再読み込みの確認ダイアログを表示中のパス（多重表示を防ぐ）。
        self._reload_prompt_active: set[str] = set()

    def _refresh_file_watches(self) -> None:
        """このウィンドウの開いているファイルのパスを、監視対象と一致させる。

        開く・保存・タブを閉じる・タブがウィンドウ間を移動する、などの
        タイミングで呼ぶ。存在するファイルだけを監視する（未保存の無題タブや、
        まだ書き出されていないパスは対象外）。temp→rename 方式の保存で
        監視が外れたパスも、ここで在れば再登録される。

        **``is_file()`` を呼ぶのは「これから監視に足すパス」だけにしてある**
        （パフォーマンス対応、2026-08-08）。以前は開いている全タブに対して
        毎回 ``is_file()`` していたため、ファイルを 1 つ開くたびに
        「タブの数 × 2 回」の stat が走っていた（このメソッドは
        ``_attach_editor`` と ``open_path`` の両方から呼ばれる）。
        rclone/FUSE のように stat 1 回が遅いファイルシステムでは、これが
        「タブが増えるほどファイルを開くのが遅くなる」形で効いてしまう。
        既に監視できているパスは ``QFileSystemWatcher`` 自身が面倒を見て
        いるので、こちらから改めて存在を確かめ直す必要はない。
        """
        wanted = {
            str(editor.path) for editor in self.editors() if editor.path is not None
        }
        current = set(self._file_watcher.files())
        to_remove = current - wanted
        # 監視されていないパスだけ「実在するか」を確かめる。無題タブのように
        # まだ書き出されていないパスと、外部で消されたファイルはここで落ちる。
        to_add = [path for path in (wanted - current) if Path(path).is_file()]
        if to_remove:
            self._file_watcher.removePaths(list(to_remove))
        if to_add:
            self._file_watcher.addPaths(to_add)

    def _find_editor_for_watched_path(self, path: str) -> EditorWidget | None:
        """``QFileSystemWatcher`` から渡されたパスのタブを探す。

        監視に登録するのは :meth:`_refresh_file_watches` が作った
        ``str(editor.path)``（＝既に :func:`~editor_app.fileio.resolve_path` を
        通したパス）なので、まずはそのまま比較してみる。``Path.resolve()`` は
        パスの各階層に ``lstat`` するため、rclone/FUSE のような遅い
        ファイルシステムでは 1 回が高くつく。**そのままで見つからなかった
        ときだけ** resolve して探し直す（Qt 側がパスを正規化して返す
        可能性への保険）。
        """
        editor = self.find_editor_by_path(Path(path))
        if editor is not None:
            return editor
        return self.find_editor_by_path(resolve_path(path))

    def _on_watched_file_changed(self, path: str) -> None:
        """監視中のファイルが変わった（``QFileSystemWatcher.fileChanged``）。"""
        # そのファイルがこのウィンドウでもう開かれていないなら、何もしない。
        # 監視から外し、保留中の再読み込みも取り消す。
        # （閉じたファイルにまで再読み込みの確認が出た、という実機報告への対応。
        # QFileSystemWatcher は removePaths の直後でも、既にカーネルが積んだ
        # 変更イベントを届けてくることがあり、そのとき下の「まだ在れば監視し直す」
        # が閉じたファイルを再登録してしまっていた。ここで開いているかどうかを
        # 見て、開いていなければ確実に手を引く。）
        if self._find_editor_for_watched_path(path) is None:
            if path in self._file_watcher.files():
                self._file_watcher.removePaths([path])
            self._pending_reload_paths.discard(path)
            return

        # temp→rename 方式の保存では、元ファイルが置き換わって監視が外れる
        # ことがある。まだ開いていてファイルが在れば監視し直す。
        if Path(path).is_file() and path not in self._file_watcher.files():
            self._file_watcher.addPath(path)
        self._pending_reload_paths.add(path)
        self._reload_check_timer.start()

    def _process_pending_reloads(self) -> None:
        """デバウンス後。変更が来ていたファイルを 1 つずつ確認する。"""
        paths = list(self._pending_reload_paths)
        self._pending_reload_paths.clear()
        for path in paths:
            self._maybe_reload_changed_file(Path(path))

    def _maybe_reload_changed_file(self, path: Path) -> None:
        """外部で変わったファイルについて、必要なら確認して再読み込みする。

        - このウィンドウで開いていないファイルは無視する。
        - ファイルが（削除等で）読めない・書き込み途中で読めない場合は
          何もしない（次の変更イベントでまた来る）。
        - ディスクの内容が今の本文と同じなら、実質変わっていない
          （＝自分で保存した直後など）ので確認しない。
        - 内容が違うときだけ「再読み込みしますか？」を出す。未保存の編集が
          あるタブでも必ず確認する（勝手に上書きして編集を失わないため）。
        """
        # 先にタブを探す。見つかったタブが持つパスは必ず
        # :func:`~editor_app.fileio.resolve_path` を通してあるので、それを
        # そのまま正規化済みのパスとして使い回す
        # （パス解決のための余分な lstat を増やさないため）。
        editor = self._find_editor_for_watched_path(str(path))
        if editor is None or editor.path is None:
            return
        resolved = editor.path
        key = str(resolved)
        if key in self._reload_prompt_active:
            return
        if not resolved.is_file():
            return

        try:
            text, encoding, newline = read_text_file(resolved)
        except Exception:  # noqa: BLE001 - 書き込み途中等で読めないだけなら黙って見送る
            return

        if text == editor.toPlainText():
            return

        self.tabs.setCurrentWidget(editor)
        if editor.is_modified:
            message = (
                f"「{editor.display_name}」が外部で変更されました。\n"
                "このタブには未保存の変更があります。再読み込みすると、"
                "その編集は破棄されます。\n\n再読み込みしますか？"
            )
        else:
            message = (
                f"「{editor.display_name}」が外部で変更されました。\n\n"
                "再読み込みしますか？"
            )

        self._reload_prompt_active.add(key)
        try:
            answer = show_message_box(
                self,
                QMessageBox.Icon.Question,
                "再読み込み",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
        finally:
            self._reload_prompt_active.discard(key)

        if answer == QMessageBox.StandardButton.Yes:
            self._reload_editor_content(editor, text, encoding, newline)

    def _reload_editor_content(
        self, editor: EditorWidget, text: str, encoding: str, newline: str
    ) -> None:
        """エディタの本文をディスクの内容で置き換える（カーソル位置は保つ）。"""
        cursor_position = editor.textCursor().position()
        editor.encoding = encoding
        editor.newline = newline
        editor.set_content(text)
        cursor = editor.textCursor()
        cursor.setPosition(min(cursor_position, len(text)))
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        # 保存し直しで監視が外れている場合の再登録。
        self._refresh_file_watches()
