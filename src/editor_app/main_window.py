"""エディタのメインウィンドウ."""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
)

from . import APP_NAME
from .dialogs import (
    show_message_box,
    show_message_box_with_checkbox,
)
from .editor import EditorWidget
from .file_commands import FileCommandsMixin, raise_window
from .fileio import describe_encoding
from .file_watch import FileWatchMixin
from .menus import create_actions, create_menus
from .search_replace import SearchReplaceMixin
from .settings import (
    load_theme,
    load_window_geometry,
    save_window_geometry,
)
from .theme import apply_theme_to_window
from .update_check import UpdateCheckMixin
from .tab_bar import TAB_MIME_TYPE
from .tab_transfer import TabTransferMixin
from .tab_widget import MultiRowTabWidget
from .view_settings import ViewSettingsMixin

UNTITLED_PREFIX = "無題-"

#: 終了時の保存確認ダイアログに出す「残りにも同じ操作を適用する」の文言
REMEMBER_CHOICE_TEXT = "残りの未保存タブにも同じ操作を適用する"

#: 未保存の変更があるタブの名前の先頭に付ける印
MODIFIED_MARK = "*"

#: 文字コードの判定が拮抗した（別の文字コードでも読めてしまった）ときに、
#: ステータスバーの文字コード表示に付ける印。**作業を止めるダイアログには
#: しない**（毎回止められては煩わしいだけで、いずれ読まずに閉じるようになる）。
#: 気づきのきっかけとして印だけを出し、詳しい説明はツールチップに置く。
AMBIGUOUS_ENCODING_MARK = "?"


class MainWindow(
    FileCommandsMixin,
    ViewSettingsMixin,
    FileWatchMixin,
    SearchReplaceMixin,
    UpdateCheckMixin,
    TabTransferMixin,
    QMainWindow,
):
    """タブ型エディタのウィンドウ.

    1 ウィンドウが複数のタブ (EditorWidget) を持ち、検索・置換は
    このウィンドウ内の全タブを対象に行う (機能 6・7、Ctrl+F / Ctrl+H)。

    タブはウィンドウ間でドラッグ&ドロップして移動できる（機能追加）。
    そのため、いま開いている全ウィンドウを :attr:`_open_windows` で
    追跡しておき、ドロップ先の判定に使う。

    ファイルを開く・保存する処理（機能 2・3。文字コードの保持も含む）は
    :class:`~editor_app.file_commands.FileCommandsMixin` が受け持つ。
    **ファイルの読み書きまわりを触るときは、このクラスではなく
    `file_commands.py` に書くこと。**

    見た目に関する設定（折り返し・フォント・配色・テーマ等）の保持と適用は
    :class:`~editor_app.view_settings.ViewSettingsMixin` が受け持つ。
    **表示設定を足すときは、このクラスではなく `view_settings.py` に書くこと。**

    開いているファイルがアプリの外で書き換わったときの検知と再読み込みの
    提案は :class:`~editor_app.file_watch.FileWatchMixin` が受け持つ
    （``QFileSystemWatcher`` まわりを足すときは `file_watch.py` に書くこと）。

    Ctrl+F の検索・Ctrl+H の置換（機能 6・7）は
    :class:`~editor_app.search_replace.SearchReplaceMixin` が受け持つ。
    **検索・置換を触るときは、このクラスではなく `search_replace.py` に書くこと。**

    タブを別のウィンドウへ移す／新しいウィンドウへ切り離す処理（ドラッグ&
    ドロップ・ダブルクリック・右クリックメニュー）は
    :class:`~editor_app.tab_transfer.TabTransferMixin` が受け持つ
    （**ウィンドウ間のタブ移動を触るときは `tab_transfer.py` に書くこと**）。

    アプリ内での更新確認（git clone 導入時）は
    :class:`~editor_app.update_check.UpdateCheckMixin` が受け持つ
    （git の操作そのものは Qt 非依存の `updater.py`）。
    """

    #: 現在開いている全ての MainWindow（ドラッグ先の判定に使う）。
    _open_windows: list["MainWindow"] = []

    #: ウィンドウがアクティブになるたびに増える通し番号。「最後にアクティブ
    #: にしたウィンドウ」（:meth:`most_recently_active_window`）の判定に使う
    #: （実機フィードバックにより追加）。
    _activation_counter = 0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        #: このウィンドウが最後にアクティブになった時点の
        #: :attr:`_activation_counter`。0 のまま（一度もアクティブに
        #: なっていない）なら、生成順で一番古いものが選ばれる。
        self._last_activated_at = 0
        # 前回終了時点のウィンドウ位置・サイズを復元する（実機フィードバック
        # により追加）。保存が無ければ従来通りの既定サイズにする。
        geometry = load_window_geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1000, 700)
        self.setAcceptDrops(True)

        # テーマ（ライト/ダーク）のパレットをこのウィンドウへ直接適用しておく。
        # KDE のプラットフォームテーマ統合が QApplication のパレットを上書き
        # してくることがあり、アプリ全体のパレットだけに頼ると「起動直後から
        # テーマが効かない」状態になるため（theme.apply_theme() の説明を参照）。
        apply_theme_to_window(self, load_theme())

        # 表示設定（折り返し・改行記号・フォント・エディタ配色・タブ配色）を
        # 前回終了時の状態で読み込む。どの設定がウィンドウ単位でどれがアプリ
        # 全体かは view_settings.py の説明を参照。ここでは読むだけで、タブへの
        # 適用は _apply_window_settings()（タブを作るたび）が行う。
        self._load_view_settings()

        # タブが 1 段に収まらない場合は自動的に複数段へ折り返される (機能 5)。
        self.tabs = MultiRowTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.currentChanged.connect(self._on_current_tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabTearOffRequested.connect(self._on_tab_tear_off_requested)
        self.tabs.tabDragFinished.connect(self._on_tab_drag_finished)
        self.tabs.tabDoubleClicked.connect(self._on_tab_double_clicked)
        self.tabs.tabContextMenuRequested.connect(self._on_tab_context_menu)

        # 読み込み済みのタブ配色は、タブバーができてから反映する。
        self._apply_loaded_tab_colors()

        # ウィンドウ内の全タブを横断する検索・置換パネル (機能 6・7)。
        # 中身は search_replace.py が受け持つ。
        self._setup_search_panel()

        # 開いているファイルが外部で書き換わったら再読み込みを提案する
        # （実機フィードバックにより追加）。中身は file_watch.py が受け持つ。
        self._setup_file_watching()

        # アプリ内アップデート確認の準備（実際の確認はここでは走らせない。
        # 起動時の自動確認は app.main() から 1 回だけ）。
        self._setup_update_check()

        self.setCentralWidget(self.tabs)

        # 今のタブの文字コードを常時表示するステータスバー（実機
        # フィードバックにより追加）。「文字コードを指定して保存」で
        # 選び直したあと、いま何で保存されるのかが分かるようにするため。
        self._setup_status_bar()

        # メニューとアクションの組み立ては menus.py が受け持つ
        # （どの操作がどのメニューにあるかを 1 か所で読めるようにするため）。
        # チェック付きの項目の初期状態だけはウィンドウ側の状態から決める。
        create_actions(self)
        self._sync_view_action_states()
        create_menus(self)

        # 起動直後は空の新規タブを 1 つ開いておく。
        self.new_file()

        MainWindow._open_windows.append(self)

    # ------------------------------------------------------------------
    # 複数ウィンドウの管理
    # ------------------------------------------------------------------
    @classmethod
    def open_windows(cls) -> list["MainWindow"]:
        """現在開いている全ての MainWindow（生成順）。

        **Qt 側 (C++) が既に片付けられたウィンドウは、ここで一覧から
        落とす。** 一覧から外れるのは :meth:`closeEvent` の中なので、
        閉じる経路を通らずに片付けられた場合（アプリ終了時に Qt が
        まとめて消す、テストの後始末など）は Python 側の窓口だけが
        残る。それを配り続けると、受け取った側が ``self.tabs`` などに
        触った瞬間に ``Internal C++ object ... already deleted`` で
        落ちる（タブの移動先メニュー・IPC の転送先の判定が該当する）。
        """
        alive = [window for window in cls._open_windows if shiboken6.isValid(window)]
        if len(alive) != len(cls._open_windows):
            cls._open_windows[:] = alive
        return list(alive)

    @classmethod
    def most_recently_active_window(cls) -> "MainWindow | None":
        """最後にアクティブになったウィンドウ（無ければ None）。

        一度もアクティブになったことのないウィンドウしか無い場合
        （起動直後など）は、生成順で一番古いものを返す
        （:attr:`_last_activated_at` が全て 0 のときの ``max()`` の
        タイブレークが :meth:`open_windows` の順番＝生成順になるため）。
        """
        windows = cls.open_windows()
        if not windows:
            return None
        return max(windows, key=lambda window: window._last_activated_at)

    @classmethod
    def open_new_empty_window(cls, near: "MainWindow | None" = None) -> "MainWindow":
        """無題タブ 1 つだけの新しいウィンドウを開いて、前面に出す。

        起動中に（アイコンの再クリックや ``tuxpad`` コマンドで）ファイル
        指定なしでもう一度起動されたときに使う（``app._handle_forwarded_paths``。
        利用者の要望、2026-08-09）。

        ``__init__`` が前回終了時のウィンドウ位置を復元するため、そのままだと
        既存ウィンドウにぴったり重なって「開いたことに気づかない」ので、
        ``near`` を渡すとそのウィンドウから少し右下へずらして出す
        （Wayland では :meth:`move` が効かないことがあるが、その場合も
        ウィンドウマネージャ側がずらしてくれる）。

        テーマ・エディタ配色・メニューバーの表示といったアプリ全体の設定は、
        通常の ``MainWindow()`` 生成と同じ経路で反映される。
        """
        window = cls()
        if near is not None:
            window.move(max(0, near.x() + 40), max(0, near.y() + 40))
        window.show()
        raise_window(window)
        return window

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """ウィンドウがアクティブになったタイミングを記録する。

        「最後にアクティブにしたウィンドウ」（ファイルマネージャー等から
        別のファイルを開いたときの読み込み先の選択肢の 1 つ、実機
        フィードバックにより追加）の判定に使う。
        """
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            MainWindow._activation_counter += 1
            self._last_activated_at = MainWindow._activation_counter
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # タブへのアクセス
    # ------------------------------------------------------------------
    def editors(self) -> list[EditorWidget]:
        """開いている全タブのエディタをタブ順に返す。"""
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def current_editor(self) -> EditorWidget | None:
        """現在表示中のタブのエディタ。タブが 1 つも無ければ None。"""
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, EditorWidget) else None

    def activate_next_tab(self) -> None:
        """次のタブへ切り替える（末尾なら先頭へ折り返す）。"""
        self._activate_tab_by_offset(1)

    def activate_previous_tab(self) -> None:
        """前のタブへ切り替える（先頭なら末尾へ折り返す）。"""
        self._activate_tab_by_offset(-1)

    def _activate_tab_by_offset(self, offset: int) -> None:
        count = self.tabs.count()
        if count < 2:
            return
        index = (self.tabs.currentIndex() + offset) % count
        self.tabs.setCurrentIndex(index)
        editor = self.current_editor()
        if editor is not None:
            editor.setFocus()

    # ------------------------------------------------------------------
    # 機能 1: 新規作成
    # ------------------------------------------------------------------
    def new_file(self) -> EditorWidget:
        """空の新規タブを作成して、そのタブに切り替える。"""
        editor = self._add_editor()
        self.tabs.setCurrentWidget(editor)
        editor.setFocus()
        return editor

    def _add_editor(self) -> EditorWidget:
        """新しい EditorWidget をタブとして追加する (切り替えはしない)。"""
        editor = EditorWidget(untitled_name=self._next_untitled_name())
        self._connect_editor(editor)
        return self._attach_editor(editor)

    def _attach_editor(self, editor: EditorWidget) -> EditorWidget:
        """設定を適用したエディタを、このウィンドウのタブとして並べる。

        シグナルの接続だけは呼び出し側で済ませておくこと（新規タブと
        移ってきたタブとで、先に古い接続を切るかどうかが違うため）。
        """
        self._apply_window_settings(editor)
        index = self.tabs.addTab(editor, self._tab_title(editor))
        self.tabs.setTabToolTip(index, self._tab_tooltip(editor))
        self._on_tab_set_changed()
        self._refresh_file_watches()
        return editor

    def _connect_editor(self, editor: EditorWidget) -> None:
        editor.display_state_changed.connect(
            lambda ed=editor: self._on_editor_display_state_changed(ed)
        )
        # 本文が変わったら検索結果を作り直す（古い結果のまま置換して本文の
        # 別の場所を書き換えてしまうのを防ぐ）。タブはウィンドウ間を移動する
        # ので、外すときに迷わないよう接続そのものを覚えておく
        # （:meth:`_disconnect_editor` 参照）。
        editor.search_refresh_connection = editor.textChanged.connect(
            self._on_editor_text_changed
        )

    def _disconnect_editor(self, editor: EditorWidget) -> None:
        """このウィンドウから離れる EditorWidget との接続を切る。

        ``display_state_changed`` はこのアプリ独自のシグナルなので
        まとめて外してよいが、``textChanged`` は ``EditorWidget`` の
        親クラス (``QTextEdit``) のシグナルなので、引数なしの
        ``disconnect()`` で全部切ってしまわないようにする。
        """
        editor.display_state_changed.disconnect()
        # textChanged をつないだのは（ウィンドウ間の移動なら）別のウィンドウ
        # なので、スロットを名指しで外すことはできない。接続そのものを
        # 覚えてあるので、それを使って外す。
        connection = getattr(editor, "search_refresh_connection", None)
        if connection is not None:
            QObject.disconnect(connection)
            editor.search_refresh_connection = None

    def _adopt_editor(self, editor: EditorWidget) -> EditorWidget:
        """他のウィンドウから移動されてきた EditorWidget を、このウィンドウの
        タブとして組み込む（ドラッグ&ドロップでのタブ移動用）。"""
        self._disconnect_editor(editor)
        self._connect_editor(editor)
        return self._attach_editor(editor)

    def _next_untitled_name(self) -> str:
        """未使用の「無題-N」を返す (小さい番号から詰めて再利用する)。"""
        used = set()
        for editor in self.editors():
            name = editor.untitled_name
            if editor.is_untitled and name.startswith(UNTITLED_PREFIX):
                suffix = name[len(UNTITLED_PREFIX) :]
                if suffix.isdigit():
                    used.add(int(suffix))
        number = 1
        while number in used:
            number += 1
        return f"{UNTITLED_PREFIX}{number}"

    # ------------------------------------------------------------------
    # ウィンドウへのドラッグ&ドロップ（ファイル / 他ウィンドウのタブ）
    #
    # 落とされた物によって受け持ちが違うので、ここでは振り分けだけを行う。
    # ファイルは `file_commands.py`、タブは `tab_transfer.py` へ渡す。
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """ファイル（単一・複数）と、他ウィンドウのタブを受け取れるようにする。"""
        if event.mimeData().hasFormat(TAB_MIME_TYPE):
            self._handle_tab_drag_event(event, drop=False)
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """ドラッグ中もずっと「受け取れる」と返し続ける（既定は受け取らない）。"""
        if event.mimeData().hasFormat(TAB_MIME_TYPE):
            self._handle_tab_drag_event(event, drop=False)
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        if event.mimeData().hasFormat(TAB_MIME_TYPE):
            self._handle_tab_drag_event(event, drop=True)
            return
        urls = event.mimeData().urls()
        opened = self.open_dropped_urls(urls)
        if opened:
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ------------------------------------------------------------------
    # 未保存の変更の確認
    # ------------------------------------------------------------------
    def maybe_save(self, editor: EditorWidget) -> bool:
        """タブを閉じてよいか確認する。閉じてよければ True、中止なら False。

        未編集ならそのまま True。編集済みなら保存するか尋ね、
        「保存」を選ばれた場合は保存が成功したときだけ True を返す。
        """
        if not editor.is_modified:
            return True

        self.tabs.setCurrentWidget(editor)
        answer = show_message_box(
            self,
            QMessageBox.Icon.Warning,
            "保存の確認",
            f"「{editor.display_name}」の変更は保存されていません。\n保存しますか？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_editor(editor)
        return answer == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------------
    # 機能 4: タブを閉じる
    # ------------------------------------------------------------------
    def close_tab(self, index: int) -> bool:
        """指定位置のタブを閉じる (タブの × ボタンから呼ばれる)。"""
        editor = self.tabs.widget(index)
        if not isinstance(editor, EditorWidget):
            return False
        return self.close_editor(editor)

    def close_current_tab(self) -> bool:
        """現在のタブを閉じる (Ctrl+W)。"""
        editor = self.current_editor()
        return False if editor is None else self.close_editor(editor)

    def close_editor(self, editor: EditorWidget) -> bool:
        """タブを閉じる。未保存なら確認し、中止されたら False を返す。

        **最後の 1 つを閉じてタブが 0 個になったら、ウィンドウも閉じる**
        （実機フィードバックにより変更。タブをドラッグで移してタブが 0 個に
        なったときと同じ挙動に揃えた。空のウィンドウが取り残されないように
        するため。なお、それが最後のウィンドウなら Qt の
        ``quitOnLastWindowClosed`` によりアプリが終了する）。
        """
        if not self.maybe_save(editor):
            return False

        index = self.tabs.indexOf(editor)
        if index < 0:
            return False

        self.tabs.removeTab(index)
        editor.setParent(None)
        editor.deleteLater()
        self._update_window_title()
        self._on_tab_set_changed()
        self._refresh_file_watches()
        if self.tabs.count() == 0:
            self.close()
        return True

    def _confirm_save_with_remember_option(
        self, editor: EditorWidget, *, has_more_unsaved: bool
    ) -> tuple[QMessageBox.StandardButton, bool]:
        """保存の確認ダイアログを出し、(選ばれたボタン, 残りにも適用するか) を返す。

        ウィンドウを閉じるとき専用。未保存のタブが後にもまだ有る場合だけ
        「残りの未保存タブにも同じ操作を適用する」チェックボックスを出す
        （適用する「残り」が無いときに出しても意味が無いため）。
        1 タブだけ閉じるときの :meth:`maybe_save` は従来のまま。
        """
        return show_message_box_with_checkbox(
            self,
            QMessageBox.Icon.Warning,
            "保存の確認",
            f"「{editor.display_name}」の変更は保存されていません。\n保存しますか？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
            REMEMBER_CHOICE_TEXT if has_more_unsaved else None,
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """ウィンドウを閉じる前に、未保存のタブがあれば確認する。

        未保存のタブが複数ある場合、ダイアログのチェックボックスで
        「残りにも同じ操作を適用する」を選べる（実機フィードバックで追加）。
        覚えるのはこの 1 回の closeEvent の間だけで、永続化はしない。
        """
        remembered: QMessageBox.StandardButton | None = None
        editors = self.editors()

        for position, editor in enumerate(editors):
            if not editor.is_modified:
                continue

            if remembered is None:
                self.tabs.setCurrentWidget(editor)
                has_more_unsaved = any(
                    other.is_modified for other in editors[position + 1 :]
                )
                answer, remember = self._confirm_save_with_remember_option(
                    editor, has_more_unsaved=has_more_unsaved
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    # キャンセルはチェックの有無に関わらず即中止。
                    event.ignore()
                    return
                if remember and answer in (
                    QMessageBox.StandardButton.Save,
                    QMessageBox.StandardButton.Discard,
                ):
                    remembered = answer
            else:
                answer = remembered

            if answer == QMessageBox.StandardButton.Save:
                if not self.save_editor(editor):
                    # 保存に失敗（保存先のキャンセル・書き込みエラー）。
                    event.ignore()
                    return
            elif answer != QMessageBox.StandardButton.Discard:
                # 想定外の回答（× で閉じた等）は中止扱いにする。
                event.ignore()
                return
        event.accept()
        # 次回起動時に復元できるよう、閉じる時点の位置・サイズを保存する
        # （実機フィードバックにより追加。複数ウィンドウの場合は最後に
        # 閉じたウィンドウの状態が残る）。
        save_window_geometry(self.saveGeometry())
        # 閉じると決まった以上、保留中の「外部変更の再読み込み」は要らない。
        # 止めておかないと、デバウンスの待ち時間のあいだに閉じられたとき、
        # 閉じたはずのウィンドウが後から確認ダイアログを出そうとする。
        self._cancel_pending_reloads()
        self.search_panel.close()
        if self in MainWindow._open_windows:
            MainWindow._open_windows.remove(self)

    # ------------------------------------------------------------------
    # タブ表示の更新
    # ------------------------------------------------------------------
    def _tab_title(self, editor: EditorWidget) -> str:
        """タブに表示する文字列 (未保存なら先頭に * を付ける)。

        タブバーは独自実装 (MultiRowTabBar) に差し替わっているが、
        表示名の組み立ては引き続きこのメソッドに集約しておくこと。
        """
        mark = MODIFIED_MARK if editor.is_modified else ""
        return f"{mark}{editor.display_name}"

    def _tab_tooltip(self, editor: EditorWidget) -> str:
        """タブにマウスを乗せたときに出す文字列 (フルパス)。

        タブ名はファイル名だけなので、同名ファイルを複数開いたときに
        どれがどれか分かるようにフルパスを出す。
        """
        if editor.path is not None:
            return str(editor.path)
        return f"{editor.display_name} (未保存の新規文書)"

    def _on_editor_display_state_changed(self, editor: EditorWidget) -> None:
        index = self.tabs.indexOf(editor)
        if index >= 0:
            self.tabs.setTabText(index, self._tab_title(editor))
            self.tabs.setTabToolTip(index, self._tab_tooltip(editor))
            if index == self.tabs.currentIndex():
                self._update_window_title()
                self._update_encoding_status()

    def _on_current_tab_changed(self, _index: int) -> None:
        self._update_window_title()
        self._update_encoding_status()

    # ------------------------------------------------------------------
    # ステータスバー（今のタブの文字コード）
    # ------------------------------------------------------------------
    def _setup_status_bar(self) -> None:
        """ステータスバーに「今のタブの文字コード」の表示欄を用意する。

        ``addPermanentWidget`` で右端に置くのは、メニュー項目にマウスを
        乗せたときに出る説明（``QAction.setStatusTip``）で消されないように
        するため（通常のメッセージ領域に置くと上書きされる）。
        """
        self._encoding_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._encoding_label)
        self._update_encoding_status()

    def _update_encoding_status(self) -> None:
        """文字コードの表示を、いま表示しているタブのものに合わせる。

        呼び出し元は「タブが切り替わったとき」と「タブの表示状態が
        変わったとき」の 2 か所だけ。文字コードの代入
        （:attr:`~editor_app.editor.EditorWidget.encoding`）は後者の
        シグナルを出すので、開く・再読み込み・文字コードを指定して保存の
        どれで変わってもここへ流れてくる。
        """
        editor = self.current_editor()
        if editor is None:
            self._encoding_label.setText("")
            self._encoding_label.setToolTip("")
            return

        name = describe_encoding(editor.encoding)
        alternatives = editor.encoding_alternatives
        if not alternatives:
            self._encoding_label.setText(name)
            self._encoding_label.setToolTip(
                f"このタブの文字コード（保存もこの文字コードで行われます）: {name}"
            )
            return

        others = "・".join(describe_encoding(enc) for enc in alternatives)
        self._encoding_label.setText(f"{name} {AMBIGUOUS_ENCODING_MARK}")
        self._encoding_label.setToolTip(
            f"このタブの文字コード（保存もこの文字コードで行われます）: {name}\n\n"
            f"{AMBIGUOUS_ENCODING_MARK} このファイルは {others} としても読めるため、"
            f"{name} と判定したのは推測です。\n"
            "本文が文字化けして見える場合は、「ファイル」→「文字コードを指定して"
            f"開き直す」で {others} を選んでください。\n"
            "（化けたまま編集して保存すると、ファイルが元に戻せなくなります）"
        )

    def _update_window_title(self) -> None:
        editor = self.current_editor()
        if editor is None:
            self.setWindowTitle(APP_NAME)
        else:
            self.setWindowTitle(f"{self._tab_title(editor)} - {APP_NAME}")
